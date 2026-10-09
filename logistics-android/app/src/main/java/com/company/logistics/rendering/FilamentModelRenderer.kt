package com.company.logistics.rendering

import android.content.res.AssetManager
import android.view.Choreographer
import android.view.Surface
import com.google.android.filament.Box
import com.google.android.filament.Camera
import com.google.android.filament.Engine
import com.google.android.filament.Filament
import com.google.android.filament.IndexBuffer
import com.google.android.filament.Material
import com.google.android.filament.MaterialInstance
import com.google.android.filament.RenderableManager
import com.google.android.filament.Renderer
import com.google.android.filament.Scene
import com.google.android.filament.Skybox
import com.google.android.filament.SwapChain
import com.google.android.filament.VertexBuffer
import com.google.android.filament.View
import com.google.android.filament.Viewport
import com.google.android.filament.gltfio.AssetLoader
import com.google.android.filament.gltfio.FilamentAsset
import com.google.android.filament.gltfio.Gltfio
import com.google.android.filament.gltfio.ResourceLoader
import com.google.android.filament.gltfio.UbershaderProvider
import com.google.android.filament.EntityManager
import com.google.android.filament.LightManager
import com.google.android.filament.TransformManager
import com.google.android.filament.View.ToneMapping
import com.google.android.filament.filamat.MaterialBuilder
import com.google.android.filament.filamat.MaterialPackage
import java.io.File
import java.lang.reflect.Field
import java.lang.reflect.Method
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.nio.ShortBuffer
import java.nio.channels.FileChannel
import java.nio.file.StandardOpenOption
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** 零件信息：GLB 节点名 + 渲染实体，用于零件清单与定位 */
data class PartInfo(val name: String, val entity: Int)

/** Real Filament GLB renderer; all Filament objects are owned and released here. */
class FilamentModelRenderer(
    private val filamentInitializer: () -> Unit = { Filament.init() },
    private val gltfioInitializer: () -> Unit = { Gltfio.init() },
    private val engineFactory: () -> Engine = { Engine.create() },
    // 深蓝黑场景背景，对齐浏览器 three.js（model_view.html: scene.background = 0x0b1020）
    backgroundArgb: Int = 0xFF0B1020.toInt(),
) : ModelRendererAdapter, Choreographer.FrameCallback {
    // Distance bounds scale with real assemblies: the Gearbox sample sits ~160 units from
    // the origin with a 15.6 radius and needs a ~94 unit viewing distance.
    // 初始视角对齐浏览器 fitCameraToObject：dir=(0.7,0.55,0.7) → yaw≈45°、pitch≈28.8°
    override val camera = OrbitCameraState(
        minDistance = 0.1f,
        maxDistance = 2000f,
        initialYawDegrees = 45f,
        initialPitchDegrees = 28.8f,
    )
    private var modelCenter = floatArrayOf(0f, 0f, 0f)
    private var modelRadius = 1f
    // 爆炸图：每个零件的原始局部中心（模型空间），用于计算爆炸方向
    private var partCenters = mutableListOf<FloatArray>()
    private var partEntities = mutableListOf<Int>()
    private var partNames = mutableListOf<String?>()
    private var explosionFactor = 0f
    // ---- 零件隔离（点选后其余零件半透明）：ghost 材质 + 原始实例缓存 ----
    // originalInstances 存「原始材质实例的原生指针」，0 表示该 primitive 槽位没有材质实例。
    // 不能改回存 Java 包装器：RenderableManager.getMaterialInstanceAt() 对空槽位返回原生 0 后
    // 不做判空，new MaterialInstance(0) 构造时即调 nGetMaterial(0) → 原生 getMaterial()+0
    // 空指针 SIGSEGV（2026-10-01 牛油果测试机点选零件闪退根因，Java 层 catch 不住）。
    private var ghostMaterial: Material? = null
    private val ghostInstances = mutableMapOf<String, MaterialInstance>()
    private val originalInstances = mutableMapOf<Int, LongArray>()
    // 裸指针读写通道（反射 nGet/nSetMaterialInstanceAt），见 RawInstanceApi 注释
    private val rawApi: RawInstanceApi? by lazy { RawInstanceApi.create() }
    private var isolatedName: String? = null
    private var isolatedEntity: Int = 0
    // ---- 浏览器视觉对齐（three.js model_view.html）：运行时编译材质 + 地面网格 ----
    // MaterialBuilder（filamat-android）在设备上程序化编译材质，免去本地 matc 工具链。
    // 任一编译失败即置 null，调用方回退到 assets ghost.filamat / 原材质，不影响既有功能。
    private var runtimeMatBuilderReady = false
    private var runtimeGridMaterial: Material? = null
    private var runtimeHighlightMaterial: Material? = null
    private var runtimeGhostMaterial: Material? = null
    private var runtimeHighlightInstance: MaterialInstance? = null
    private var runtimeGhostInstance: MaterialInstance? = null
    // 地面网格（等价 three.js GridHelper(20, 20, 0x33507a, 0x1b2942)）
    private var gridEntity: Int = 0
    private var gridVertexBuffer: VertexBuffer? = null
    private var gridIndexBuffer: IndexBuffer? = null
    // 自动旋转（浏览器 controls.autoRotateSpeed = 1.2 → 60fps 下约 0.12°/帧）
    @Volatile
    var autoRotate = false
        set(value) {
            field = value
        }
    // ---- 剖面（零件级裁剪 + 蓝色切面指示）----
    private var sectionEnabled = false
    private var sectionAxis = 1 // 0=X 1=Y 2=Z
    private var sectionPos = 0f // [-1,1]，相对模型半径
    private var sectionPlaneEntity: Int = 0
    private var sectionPlaneAxis: Int = -1
    private var sectionPlaneVb: VertexBuffer? = null
    private var sectionPlaneIb: IndexBuffer? = null
    private val hiddenBySection = mutableSetOf<Int>()
    private val backgroundR = RenderMath.srgbToLinear(((backgroundArgb shr 16) and 0xFF) / 255f)
    private val backgroundG = RenderMath.srgbToLinear(((backgroundArgb shr 8) and 0xFF) / 255f)
    private val backgroundB = RenderMath.srgbToLinear((backgroundArgb and 0xFF) / 255f)

    private val choreographer by lazy { Choreographer.getInstance() }
    private var engine: Engine? = null
    private var entityManager: EntityManager? = null
    private var materialProvider: UbershaderProvider? = null
    private var renderer: Renderer? = null
    private var scene: Scene? = null
    private var view: View? = null
    private var cameraComponent: Camera? = null
    private var keyLightEntity: Int? = null
    private var fillLightEntity: Int? = null
    private var hemiLightEntity: Int? = null
    private var skybox: Skybox? = null
    private var assetLoader: AssetLoader? = null
    private var resourceLoader: ResourceLoader? = null
    private var initializationError: Throwable? = null
    private var swapChain: SwapChain? = null
    // attach 时由调用方传入的 Surface（包着 TextureView 的 SurfaceTexture），detach 时由这里释放
    private var attachedSurface: Surface? = null
    private var asset: FilamentAsset? = null
    private var released = false

    /** 已加载模型：surface 重建（锁屏/切后台再回来）时不必重新解析 GLB、重编材质 */
    val hasModel: Boolean get() = asset != null && !released
    private var frameCallbackPosted = false
    private var viewportWidth = 1
    private var viewportHeight = 1
    private var framesScheduled = 0L
    private var framesBegun = 0L
    private var framesRendered = 0L

    /** Diagnostic counters for the debug overlay; cheap increments, no locks needed on the UI thread. */
    internal fun debugFrameCounters(): Triple<Long, Long, Long> = Triple(framesScheduled, framesBegun, framesRendered)

    init {
        var createdEngine: Engine? = null
        var createdEntityManager: EntityManager? = null
        var createdMaterialProvider: UbershaderProvider? = null
        var createdRenderer: Renderer? = null
        var createdScene: Scene? = null
        var createdView: View? = null
        var createdCamera: Camera? = null
        var createdKeyLight: Int? = null
        var createdFillLight: Int? = null
        var createdHemiLight: Int? = null
        var createdSkybox: Skybox? = null
        var createdAssetLoader: AssetLoader? = null
        var createdResourceLoader: ResourceLoader? = null
        try {
            filamentInitializer()
            gltfioInitializer()
            // filamat 运行时材质编译的 native 库初始化；失败只影响程序化材质（网格/高亮），
            // 主体渲染与 assets ghost.filamat 不受影响
            try { MaterialBuilder.init() } catch (_: Throwable) { }
            createdEngine = engineFactory()
            createdEntityManager = EntityManager.get()
            createdMaterialProvider = UbershaderProvider(createdEngine)
            createdRenderer = createdEngine.createRenderer()
            createdScene = createdEngine.createScene()
            createdView = createdEngine.createView()
            createdCamera = createdEngine.createCamera(createdEntityManager.create())
            createdKeyLight = createdEntityManager.create().also { entity ->
                LightManager.Builder(LightManager.Type.DIRECTIONAL)
                    .intensity(30_000f)
                    .color(1.0f, 0.98f, 0.95f)
                    .direction(-0.6f, -1.0f, -0.8f)
                    .build(createdEngine, entity)
                createdScene.addEntity(entity)
            }
            createdFillLight = createdEntityManager.create().also { entity ->
                LightManager.Builder(LightManager.Type.DIRECTIONAL)
                    .intensity(12_000f)
                    // 0x93c5fd（浏览器 fill 光）sRGB→linear
                    .color(0.29f, 0.56f, 0.98f)
                    .direction(0.8f, -0.3f, 0.7f)
                    .build(createdEngine, entity)
                createdScene.addEntity(entity)
            }
            // 模拟浏览器 HemisphereLight(0xffffff, 0x334155, 1.1)：顶部均匀白光补暗部
            createdHemiLight = createdEntityManager.create().also { entity ->
                LightManager.Builder(LightManager.Type.DIRECTIONAL)
                    .intensity(9_000f)
                    .color(1.0f, 1.0f, 1.0f)
                    .direction(0f, -1f, 0f)
                    .build(createdEngine, entity)
                createdScene.addEntity(entity)
            }
            createdSkybox = Skybox.Builder()
                .color(backgroundR, backgroundG, backgroundB, 1.0f)
                .build(createdEngine)
            createdScene.skybox = createdSkybox
            // ACES 色调映射，对齐浏览器 Three.js 效果
            createdView.toneMapping = ToneMapping.ACES
            createdAssetLoader = AssetLoader(createdEngine, createdMaterialProvider, createdEntityManager)
            createdResourceLoader = ResourceLoader(createdEngine)

            engine = createdEngine
            entityManager = createdEntityManager
            materialProvider = createdMaterialProvider
            renderer = createdRenderer
            scene = createdScene
            view = createdView
            cameraComponent = createdCamera
            keyLightEntity = createdKeyLight
            fillLightEntity = createdFillLight
            hemiLightEntity = createdHemiLight
            skybox = createdSkybox
            assetLoader = createdAssetLoader
            resourceLoader = createdResourceLoader
            view?.scene = createdScene
            view?.camera = createdCamera
            view?.isPostProcessingEnabled = true
            applyCamera()
        } catch (failure: Throwable) {
            initializationError = failure
            createdResourceLoader?.destroy()
            createdAssetLoader?.destroy()
            createdView?.let { createdEngine?.destroyView(it) }
            createdScene?.let { createdEngine?.destroyScene(it) }
            createdRenderer?.let { createdEngine?.destroyRenderer(it) }
            createdCamera?.let {
                createdEngine?.destroyCameraComponent(it.getEntity())
                createdEntityManager?.destroy(it.getEntity())
            }
            createdSkybox?.let { createdEngine?.destroySkybox(it) }
            createdFillLight?.let { entity -> createdEngine?.lightManager?.destroy(entity); createdEntityManager?.destroy(entity) }
            createdHemiLight?.let { entity -> createdEngine?.lightManager?.destroy(entity); createdEntityManager?.destroy(entity) }
            createdKeyLight?.let { entity -> createdEngine?.lightManager?.destroy(entity); createdEntityManager?.destroy(entity) }
            createdMaterialProvider?.destroy()
            createdEngine?.destroy()
        }
    }

    fun attach(surface: Surface): Result<Unit> = runCatching {
        check(!released) { "renderer has been released" }
        checkAvailable()
        val activeEngine = engine ?: error("Filament engine is unavailable")
        if (swapChain != null) detachSurface()
        swapChain = activeEngine.createSwapChain(surface)
        attachedSurface = surface
        choreographer.removeFrameCallback(this)
        choreographer.postFrameCallback(this)
        frameCallbackPosted = true
    }

    /**
     * 解绑渲染表面。必须在 TextureView 释放 SurfaceTexture 之前同步完成：
     * destroySwapChain 只是把销毁命令排进 Filament 渲染线程，若不 flushAndWait，
     * 渲染线程可能仍在往已被系统回收的 Surface 上 eglSwapBuffers → native 崩溃（SIGSEGV）。
     * 典型触发：查看一段时间后自动锁屏 / 切到后台 / 来电，surface 被销毁。
     */
    fun detachSurface() {
        if (frameCallbackPosted) choreographer.removeFrameCallback(this)
        frameCallbackPosted = false
        val eng = engine
        swapChain?.let { chain ->
            eng?.destroySwapChain(chain)
            runCatching { eng?.flushAndWait() }
        }
        swapChain = null
        attachedSurface?.let { runCatching { it.release() } }
        attachedSurface = null
    }

    fun onViewportChanged(width: Int, height: Int) {
        viewportWidth = width.coerceAtLeast(1)
        viewportHeight = height.coerceAtLeast(1)
        view?.viewport = Viewport(0, 0, viewportWidth, viewportHeight)
        applyCamera()
    }

    fun loadGlb(file: File): Result<Unit> = runCatching {
        check(!released) { "renderer has been released" }
        checkAvailable()
        require(file.isFile) { "GLB file does not exist: ${file.path}" }
        require(file.length() >= 20L) { "GLB file is truncated" }
        val activeAssetLoader = assetLoader ?: error("Filament asset loader is unavailable")
        val activeResourceLoader = resourceLoader ?: error("Filament resource loader is unavailable")
        val activeScene = scene ?: error("Filament scene is unavailable")
        var candidate: FilamentAsset? = null
        try {
            candidate = FileChannel.open(file.toPath(), StandardOpenOption.READ).use { channel ->
                val buffer = channel.map(FileChannel.MapMode.READ_ONLY, 0, channel.size())
                activeAssetLoader.createAsset(buffer)
                    ?: error("Filament rejected GLB: ${file.name}")
            }
            activeResourceLoader.loadResources(candidate!!)
            candidate!!.releaseSourceData()
            asset?.let { previous ->
                activeScene.removeEntities(previous.entities)
                activeAssetLoader.destroyAsset(previous)
            }
            resetAppearanceState()
            asset = candidate
            activeScene.addEntities(candidate!!.entities)
            val halfExtent = candidate!!.boundingBox.halfExtent
            val radius = halfExtent.maxOrNull() ?: 1f
            // 中心校正：只平移 root 实体一次（列主序矩阵）。
            // 旧实现逐零件平移 -bboxCenter 且用「局部变换减世界中心」——两者不同坐标系，
            // 层级模型（root→组→零件）直接被拆散，爆炸 0% 也是散开的（浏览器无此问题）。
            // root 整体平移保持 GLB 层级原样 → 装配态与浏览器一致紧凑。
            val bboxCenter = candidate!!.boundingBox.center
            modelCenter = floatArrayOf(0f, 0f, 0f)
            modelRadius = if (radius > 0f) radius else 1f
            val rootInst = engine?.transformManager?.getInstance(candidate.root)
            if (rootInst != null && rootInst != 0) {
                engine?.transformManager?.setTransform(
                    rootInst,
                    floatArrayOf(
                        1f, 0f, 0f, 0f,
                        0f, 1f, 0f, 0f,
                        0f, 0f, 1f, 0f,
                        -bboxCenter[0], -bboxCenter[1], -bboxCenter[2], 1f,
                    ),
                )
            }
            // 收集零件中心（用于爆炸图，世界系 + 原局部变换快照）
            collectPartCenters(candidate!!)
            // 应用爆炸系数（0 = 还原原局部变换 → 紧凑装配态）
            applyPartTransforms()
            // 浏览器视觉对齐：程序化材质（网格线/高亮/ghost）+ 模型底部的地面网格
            modelBaseColor = extractDominantBaseColor(file) // 高亮/ghost 用 GLB 主材质原色参数化
            partColorByNode = extractPartColors(file) // 「原色透明」ghost：节点名 → 主材质色
            ensureRuntimeMaterials()
            createGrid(-halfExtent[1] - 0.002f * modelRadius)
            camera.fit(
                radius = modelRadius,
                aspect = viewportWidth.toFloat() / viewportHeight.toFloat(),
            )
            candidate = null
            applyCamera()
        } finally {
            candidate?.let(activeAssetLoader::destroyAsset)
        }
    }

    override fun doFrame(frameTimeNanos: Long) {
        framesScheduled++
        val chain = swapChain
        val activeRenderer = renderer
        val activeView = view
        if (released) {
            frameCallbackPosted = false
            return
        }
        // 自动旋转（浏览器 autoRotateSpeed 1.2 等效值），渲染前推进相机方位角
        if (autoRotate) {
            camera.rotate(AUTO_ROTATE_DELTA_DEGREES, 0f)
            applyCamera()
        }
        if (chain != null && activeRenderer != null && activeView != null && activeRenderer.beginFrame(chain, frameTimeNanos)) {
            framesBegun++
            activeRenderer.render(activeView)
            activeRenderer.endFrame()
            framesRendered++
        }
        // Keep scheduling unconditionally: a skipped frame (beginFrame == false, e.g. the first
        // frames after attach) must not permanently stop the render loop.
        choreographer.postFrameCallback(this)
    }

    override fun onRotate(deltaX: Float, deltaY: Float) {
        camera.rotate(deltaX, deltaY)
        applyCamera()
    }

    override fun onScale(scaleFactor: Float) {
        camera.zoom(scaleFactor)
        applyCamera()
    }

    override fun onPan(deltaX: Float, deltaY: Float) {
        camera.pan(deltaX, deltaY)
        applyCamera()
    }

    override fun resetCamera() {
        camera.reset()
        applyCamera()
    }

    /** 爆炸图：0=装配状态，1=完全散开 */
    override fun setExploded(factor: Float) {
        explosionFactor = factor.coerceIn(0f, 1f)
        applyPartTransforms()
    }

    /** 点选零件：返回零件名称（GLB节点名）与命中实体；点空返回 (null, 0) */
    fun pickPart(x: Float, y: Float, onResult: (name: String?, entity: Int) -> Unit) {
        val vw = view ?: run { onResult(null, 0); return }
        val handler = android.os.Handler(android.os.Looper.getMainLooper())
        vw.pick(x.toInt(), viewportHeight - y.toInt(), handler) { result: View.PickingQueryResult ->
            // Filament pick 的坑：点击位置没有 renderable 时，返回值可能是残留的垃圾实体号
            // （实测返回过引擎初始化期的实体 5/10，而非 0）。所以必须用 partEntities 白名单
            // 校验：不在列表里（含地面网格/剖面平面/垃圾实体）一律按点空处理 → 取消隔离。
            // 异步回调：用户可能已经退出页面、渲染器已释放，此时不能再碰任何 Filament 对象
            if (released) return@pick
            val hit = result.renderable
            val isPart = hit != 0 && partEntities.contains(hit)
            if (isPart) {
                val name = try { asset?.getName(hit) } catch (_: Exception) { null }
                onResult(name?.takeIf { it.isNotBlank() } ?: "零件 #${hit}", hit)
            } else {
                if (hit != 0) {
                    android.util.Log.i(
                        "FilamentModelRenderer",
                        "pick未命中零件: renderable=$hit grid=$gridEntity plane=$sectionPlaneEntity inParts=$isPart",
                    )
                }
                onResult(null, 0)
            }
        }
    }

    /**
     * 加载半透明 ghost 材质（幂等，失败返回 false，isolate/section 将静默不生效）。
     * 需在点选隔离或剖面前调用一次；filamat 来自 assets/ghost.filamat（matc 预编译）。
     */
    fun ensureGhostMaterial(assetManager: AssetManager): Boolean {
        if (ghostMaterial != null) return true
        if (released || initializationError != null) return false
        val eng = engine ?: return false
        return try {
            val bytes = assetManager.open("ghost.filamat").use { it.readBytes() }
            // 版本熔断：版本不符时 Material.Builder.build() 会在原生层 PostconditionPanic →
            // std::terminate → SIGABRT，Java catch 不住。宁可放弃 ghost 功能也不能崩。
            if (!isGhostMaterialVersionSupported(bytes)) return false
            val buffer = ByteBuffer.allocateDirect(bytes.size).order(ByteOrder.nativeOrder())
            buffer.put(bytes)
            buffer.flip()
            ghostMaterial = Material.Builder().payload(buffer, buffer.remaining()).build(eng)
            true
        } catch (_: Exception) {
            false
        }
    }

    /**
     * 材质版本 tripwire。filament 运行时按编译期版本严格校验 payload，旧 matc 编出的材质
     * 直接把进程打死（2026-10-01 闪退根因：material version 71 vs 运行时要求 75）。
     * 升级 filament 后必须用对应版本 matc 重编 assets/ghost.filamat（见 assets/materials/README.md）。
     * filamat 头部布局经 matc 71/75 两版实测一致：offset 12 = uint32 LE 材质版本号。
     */
    private fun isGhostMaterialVersionSupported(bytes: ByteArray): Boolean {
        if (bytes.size < 16) return false
        val version = (bytes[12].toInt() and 0xFF) or
            ((bytes[13].toInt() and 0xFF) shl 8) or
            ((bytes[14].toInt() and 0xFF) shl 16) or
            ((bytes[15].toInt() and 0xFF) shl 24)
        val expected = 75 // 与 build.gradle.kts 锁定的 filament-android 1.75.1 对应（1.76+ 需 compileSdk 37）
        if (version == expected) return true
        android.util.Log.e(
            "FilamentModelRenderer",
            "ghost.filamat 材质版本 $version 与运行时要求的 $expected 不符，已跳过加载（零件隔离/剖面降级，不闪退）",
        )
        return false
    }

    /**
     * 运行时编译「浏览器视觉对齐」所需的三种程序化材质（幂等）：
     *  - GridLines  网格线（unlit + 顶点色，等价 three.js GridHelper）
     *  - PartHighlight  选中零件蓝色高亮（lit + emissive，对齐浏览器 emissive 0x0E5FD8 @0.55）
     *  - PartGhost  未选中零件半透明（对齐浏览器 opacity 0.15）
     * 任一编译失败即保持 null，调用方回退 assets ghost.filamat / 原材质。
     */
    /** 当前模型 GLB 身份基色（glTF baseColorFactor 原值，本身即 linear；alpha=1）；解析失败为 null，材质走 fallback 色 */
    private var modelBaseColor: FloatArray? = null

    /** 节点名 → 该零件主材质 baseColorFactor（glTF linear 原值）；「原色透明」ghost 用（用户指定） */
    private var partColorByNode: Map<String, FloatArray> = emptyMap()

    /** 按颜色缓存的 ghost 材质/实例（GLSL 字面量注入，同色复用、跨模型复用；release 统一销毁） */
    private val ghostMaterialByColor = mutableMapOf<String, Material>()
    private val ghostInstanceByColor = mutableMapOf<String, MaterialInstance>()

    /** 读取 GLB 内嵌 JSON chunk；结构非法/异常返回 null */
    private fun readGlbJson(file: File): org.json.JSONObject? = try {
        val bytes = file.readBytes()
        val bb = java.nio.ByteBuffer.wrap(bytes).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        if (bb.int != 0x46546C67) null else { // "glTF" magic
            bb.int // version
            bb.int // total length
            val jsonLen = bb.int
            val jsonType = bb.int
            if (jsonType != 0x4E4F534A) null else { // "JSON" chunk
                val jsonBytes = ByteArray(jsonLen)
                bb.get(jsonBytes)
                org.json.JSONObject(String(jsonBytes, Charsets.UTF_8))
            }
        }
    } catch (_: Throwable) {
        null
    }

    /**
     * 解析 GLB，取「被最多 mesh primitive 引用的材质」的 baseColorFactor 作为模型
     * 身份色（本模型即 arm-shell 铜色，10/10 零件的主材质）。
     *
     * 两个关键点（fix4m 教训）：
     *  1. 不能取全材质平均——钢件灰、橡胶黑、LED 青、警示黄会把铜色冲成橄榄卡其（实测脏绿）；
     *  2. glTF 规范里 baseColorFactor 本身就是 linear 值，gltfio 也原样使用——
     *     千万不要再做 sRGB→linear 转换（双重转换会把颜色压暗变脏）。
     */
    private fun extractDominantBaseColor(file: File): FloatArray? {
        val js = readGlbJson(file) ?: return null
        val mats = js.optJSONArray("materials") ?: return null
        val meshes = js.optJSONArray("meshes") ?: return null
        // 统计每个材质索引被多少个 primitive 引用
        val usage = IntArray(mats.length())
        for (i in 0 until meshes.length()) {
            val prims = meshes.optJSONObject(i)?.optJSONArray("primitives") ?: continue
            for (j in 0 until prims.length()) {
                val mi = prims.optJSONObject(j)?.optInt("material", -1) ?: -1
                if (mi in usage.indices) usage[mi]++
            }
        }
        // 引用最多的材质即身份色；平票取先出现者（通常即外壳主材质）
        var best = -1
        for (i in usage.indices) if (best < 0 || usage[i] > usage[best]) best = i
        if (best < 0) return null
        val pbr = mats.optJSONObject(best)?.optJSONObject("pbrMetallicRoughness") ?: return null
        val c = pbr.optJSONArray("baseColorFactor") ?: return null
        return floatArrayOf(
            c.optDouble(0, 1.0).toFloat(),
            c.optDouble(1, 1.0).toFloat(),
            c.optDouble(2, 1.0).toFloat(),
            1f,
        )
    }

    /**
     * 解析 GLB，建立「零件节点名 → 主材质基色」映射——「其他零件原色透明」的取色来源。
     * 每个零件（node+mesh）在其 primitives 内统计材质引用次数，取最多者（平票取索引小者，
     * 通常即外壳主材质）。颜色为 glTF linear 原值，不做色彩空间转换。
     */
    private fun extractPartColors(file: File): Map<String, FloatArray> {
        val result = mutableMapOf<String, FloatArray>()
        val js = readGlbJson(file) ?: return result
        val mats = js.optJSONArray("materials") ?: return result
        val meshes = js.optJSONArray("meshes") ?: return result
        val nodes = js.optJSONArray("nodes") ?: return result
        fun colorOf(matIdx: Int): FloatArray? {
            val pbr = mats.optJSONObject(matIdx)?.optJSONObject("pbrMetallicRoughness") ?: return null
            val c = pbr.optJSONArray("baseColorFactor") ?: return null
            return floatArrayOf(
                c.optDouble(0, 1.0).toFloat(),
                c.optDouble(1, 1.0).toFloat(),
                c.optDouble(2, 1.0).toFloat(),
                1f,
            )
        }
        for (ni in 0 until nodes.length()) {
            val node = nodes.optJSONObject(ni) ?: continue
            val name = node.optString("name")
            val meshIdx = node.optInt("mesh", -1)
            if (name.isBlank() || meshIdx !in 0 until meshes.length()) continue
            val prims = meshes.optJSONObject(meshIdx)?.optJSONArray("primitives") ?: continue
            val usage = sortedMapOf<Int, Int>()
            for (pi in 0 until prims.length()) {
                val mi = prims.optJSONObject(pi)?.optInt("material", -1) ?: -1
                if (mi >= 0) usage[mi] = (usage[mi] ?: 0) + 1
            }
            if (usage.isEmpty()) continue
            val dominant = usage.entries.maxWithOrNull(compareBy({ it.value }, { -it.key }))?.key ?: continue
            colorOf(dominant)?.let { result[name] = it }
        }
        return result
    }

    /**
     * 运行时材质编译器（filamat-android 程序化编译，免本地 matc 工具链）。
     * 原 ensureRuntimeMaterials 内局部函数提升为类方法，供「原色透明」ghost 按色编译复用。
     * 任一编译失败返回 null，调用方回退 assets 材质/原材质，不影响既有功能。
     */
    private fun compileRuntimeMaterial(
        name: String,
        glsl: String,
        transparent: Boolean,
        unlit: Boolean,
        needsVertexColor: Boolean = false,
    ): Material? {
        val eng = engine ?: return null
        return try {
            val builder = MaterialBuilder()
                .name(name)
                // 关键：filamat 默认 platform=DESKTOP（生成 desktop GLSL），手机 GLES 上
                // 无匹配 shader 变体 → Material.Builder.build() 抛 "Couldn't create Material"。
                // 必须显式 MOBILE；targetApi=OPENGL 对应 Engine 默认 OpenGL 后端。
                .platform(MaterialBuilder.Platform.MOBILE)
                .targetApi(MaterialBuilder.TargetApi.OPENGL)
                .shading(
                    if (unlit) MaterialBuilder.Shading.UNLIT else MaterialBuilder.Shading.LIT,
                )
            // 顶点色只有网格线用；零件 GLB 没有 COLOR 属性，误 require 会导致材质与网格不兼容
            if (needsVertexColor) builder.require(MaterialBuilder.VertexAttribute.COLOR)
            if (transparent) {
                // TRANSPARENT 必须配 depthWrite(false)（对齐浏览器 mt.depthWrite=false）：
                // filamat 默认深度写入开启，半透明 ghost 表面抢先写深度后互相遮挡，
                // 背面全被裁掉，视觉上发白发闷、近乎不透明。
                builder.blending(MaterialBuilder.BlendingMode.TRANSPARENT)
                builder.depthWrite(false)
            }
            val pkg: MaterialPackage = builder.material(glsl).build()
            if (!pkg.isValid) {
                android.util.Log.e("FilamentModelRenderer", "运行时材质 $name 编译失败（isValid=false）")
                null
            } else {
                val buffer = pkg.buffer
                buffer.position(0) // native 返回的 ByteBuffer position 不可假设，必须归零
                Material.Builder().payload(buffer, buffer.remaining()).build(eng).also {
                    android.util.Log.i("FilamentModelRenderer", "运行时材质 $name 编译成功（${buffer.remaining()} bytes）")
                }
            }
        } catch (failure: Throwable) {
            android.util.Log.e("FilamentModelRenderer", "运行时材质 $name 异常", failure)
            null
        }
    }

    private fun ensureRuntimeMaterials() {
        val eng = engine ?: return
        // 网格材质与选中红色高亮都是常量材质（与模型颜色无关），只编译一次；
        // 高亮实例随材质持久（highlightInstanceRaw 懒创建），不再每次加载销毁重建。
        if (!runtimeMatBuilderReady) {
            runtimeMatBuilderReady = true
            runtimeGridMaterial = compileRuntimeMaterial(
                "GridLines",
                "void material(inout MaterialInputs material) {\n" +
                    "    prepareMaterial(material);\n" +
                    "    material.baseColor = getColor();\n" +
                    "}\n",
                transparent = false,
                unlit = true,
                needsVertexColor = true,
            )
            // 选中零件高亮：用户指定纯红（2026-10-01），常量材质编译一次。
            // emissive 千 nits 量级：key 30000 lux 场景下 0.378 nits 完全不可见（fix4m 结论），
            // 红底 + 红色发光保证深藏青背景下的辨识度。
            runtimeHighlightMaterial = compileRuntimeMaterial(
                "PartHighlight",
                "void material(inout MaterialInputs material) {\n" +
                    "    prepareMaterial(material);\n" +
                    "    material.baseColor = vec4(0.75, 0.04, 0.04, 1.0);\n" +
                    "    material.emissive = vec4(1200.0, 48.0, 48.0, 1.0);\n" + // emissive 是 float4（nits + 曝光权重）
                    "    material.roughness = 0.4;\n" +
                    "    material.metallic = 0.0;\n" + // 场景无 IBL，metallic>0 会死黑
                    "}\n",
                transparent = false,
                unlit = false,
            )
        }
        // identity ghost（兜底色）依赖模型主材质色（loadGlb 时解析），每次模型加载重建。
        // 用 GLSL 字面量注入颜色（uniformParameter 方案实测 isValid=false，原因未查明，
        // 字面量方案 fix4d 已验证可靠；重建一个小材质仅百毫秒级）。
        runtimeGhostInstance?.let { eng.destroyMaterialInstance(it) }
        runtimeGhostInstance = null
        runtimeGhostMaterial?.let { eng.destroyMaterial(it) }
        val bc = modelBaseColor
        // ghost 半透明：原色透明观感（浏览器「原材质 opacity 0.15」）。identity 版用 GLB
        // 主材质原色（arm-shell 橙铜），PBR 参数对齐 arm-shell（roughness 0.38 / metallic 0），
        // lit 后亮度与原零件一致。alpha 0.22（浏览器 0.15 与深背景可见性之间取中）。
        // 逐零件原色版本见 ghostRawForColor；本材质兜底 partColorByNode 未覆盖的零件。
        // fallback 色：黄铜近似 (0.72, 0.45, 0.22)。
        val gR = bc?.get(0) ?: 0.72f
        val gG = bc?.get(1) ?: 0.45f
        val gB = bc?.get(2) ?: 0.22f
        runtimeGhostMaterial = compileRuntimeMaterial(
            "PartGhost",
            String.format(
                java.util.Locale.US,
                "void material(inout MaterialInputs material) {\n" +
                    "    prepareMaterial(material);\n" +
                    "    material.baseColor = vec4(%.4f, %.4f, %.4f, 0.22);\n" +
                    "    material.roughness = 0.38;\n" +
                    "    material.metallic = 0.0;\n" +
                    "}\n",
                gR, gG, gB,
            ),
            transparent = true,
            unlit = false,
        )
    }

    /** 选中零件蓝色高亮的材质实例原生指针；0 = 不可用（编译/反射失败，保持原材质） */
    private fun highlightInstanceRaw(): Long {
        val api = rawApi ?: return 0L
        val mat = runtimeHighlightMaterial ?: return 0L
        val inst = runtimeHighlightInstance ?: mat.createInstance().also { runtimeHighlightInstance = it }
        return try { api.instanceNative(inst) } catch (_: Throwable) { 0L }
    }

    /**
     * 创建地面网格（等价 three.js GridHelper(20, 20, 0x33507a, 0x1b2942)，y=-0.001）：
     * 21+21 条直线段（GL_LINES），中心线用亮蓝灰、其余深蓝，顶点色直接写 linear 值。
     * gridY：模型经中心校正后底部所在高度（世界坐标）。
     */
    private fun createGrid(gridY: Float) {
        val eng = engine ?: return
        destroyGrid() // 幂等：loadGlb 可能因 surface 重建被多次调用，防止残留旧网格实体
        val mat = runtimeGridMaterial ?: return
        destroyGrid()
        val half = 10f
        val step = 1f
        val major = floatArrayOf(0.033f, 0.081f, 0.194f, 1f)   // 0x33507a sRGB → linear
        val minor = floatArrayOf(0.0106f, 0.0227f, 0.055f, 1f) // 0x1b2942 sRGB → linear
        val positions = ArrayList<Float>(2 * 3 * 84)
        val colors = ArrayList<Float>(2 * 4 * 84)
        fun addLine(x1: Float, z1: Float, x2: Float, z2: Float, c: FloatArray) {
            positions.add(x1); positions.add(gridY); positions.add(z1)
            positions.add(x2); positions.add(gridY); positions.add(z2)
            colors.addAll(c.toList())
            colors.addAll(c.toList())
        }
        val n = (half / step).toInt()
        for (i in -n..n) {
            val c = if (i == 0) major else minor
            addLine(i * step, -half, i * step, half, c)
            addLine(-half, i * step, half, i * step, c)
        }
        val vertexCount = positions.size / 3
        val posBuf = ByteBuffer.allocateDirect(positions.size * 4).order(ByteOrder.nativeOrder())
        posBuf.asFloatBuffer().put(positions.toFloatArray())
        val colBuf = ByteBuffer.allocateDirect(colors.size * 4).order(ByteOrder.nativeOrder())
        colBuf.asFloatBuffer().put(colors.toFloatArray())
        val vb = VertexBuffer.Builder()
            .vertexCount(vertexCount)
            .bufferCount(2)
            .attribute(VertexBuffer.VertexAttribute.POSITION, 0, VertexBuffer.AttributeType.FLOAT3, 0, 12)
            .attribute(VertexBuffer.VertexAttribute.COLOR, 1, VertexBuffer.AttributeType.FLOAT4, 0, 16)
            .build(eng)
        vb.setBufferAt(eng, 0, posBuf)
        vb.setBufferAt(eng, 1, colBuf)
        val ib = IndexBuffer.Builder()
            .indexCount(vertexCount)
            .bufferType(IndexBuffer.Builder.IndexType.USHORT)
            .build(eng)
        val idxBuf = ByteBuffer.allocateDirect(vertexCount * 2).order(ByteOrder.nativeOrder())
        val shortIdx = ShortArray(vertexCount) { it.toShort() }
        idxBuf.asShortBuffer().put(shortIdx)
        ib.setBuffer(eng, idxBuf)
        val em = entityManager ?: return
        gridEntity = em.create()
        RenderableManager.Builder(1)
            .geometry(0, RenderableManager.PrimitiveType.LINES, vb, ib)
            .boundingBox(
                Box().also {
                    System.arraycopy(floatArrayOf(0f, gridY, 0f), 0, it.center, 0, 3)
                    System.arraycopy(floatArrayOf(half, 0.01f, half), 0, it.halfExtent, 0, 3)
                },
            )
            .culling(false)
            .build(eng, gridEntity)
        scene?.addEntity(gridEntity)
        gridVertexBuffer = vb
        gridIndexBuffer = ib
        android.util.Log.i(
            "FilamentModelRenderer",
            "地面网格已创建：$vertexCount 顶点，y=$gridY，entity=$gridEntity",
        )
    }

    private fun destroyGrid() {
        val eng = engine ?: return
        if (gridEntity != 0) {
            scene?.remove(gridEntity)
            eng.renderableManager.destroy(gridEntity)
            entityManager?.destroy(gridEntity)
            gridEntity = 0
        }
        gridVertexBuffer?.let { eng.destroyVertexBuffer(it) }
        gridIndexBuffer?.let { eng.destroyIndexBuffer(it) }
        gridVertexBuffer = null
        gridIndexBuffer = null
    }

    /** 点选隔离：保留 [name]/[entity] 对应的零件，其余零件半透明化 */
    fun setIsolatedPart(name: String?, entity: Int) {
        isolatedName = name
        isolatedEntity = entity
        applyAppearance()
    }

    /** 取消隔离，恢复全部零件 */
    fun clearIsolation() {
        isolatedName = null
        isolatedEntity = 0
        applyAppearance()
    }

    /**
     * 零件清单：返回去重后的有效零件（过滤无名/RootNode/ group123 这类无意义节点名）。
     * 按模型内出现顺序返回。
     */
    fun getParts(): List<PartInfo> {
        val seen = LinkedHashSet<String>()
        val result = mutableListOf<PartInfo>()
        for (i in partEntities.indices) {
            val raw = partNames.getOrNull(i)?.trim().orEmpty()
            if (!isMeaningfulPartName(raw)) continue
            if (seen.add(raw)) result.add(PartInfo(raw, partEntities[i]))
        }
        return result
    }

    /** 聚焦零件：把相机目标移到该零件中心，便于工人看清"装在哪" */
    fun focusPart(entity: Int) {
        val idx = partEntities.indexOf(entity)
        if (idx < 0) return
        val c = partCenters.getOrNull(idx) ?: return
        camera.focusAt(c[0], c[1])
        applyCamera()
    }

    /**
     * 绕过 filament-android Java 绑定缺陷的裸指针通道（反射，失败则隔离自动降级为隐藏）：
     *
     * - `getMaterialInstanceAt()` 对「无材质实例」的 primitive 槽位返回原生 0，但 Java 包装器
     *   不判空，`new MaterialInstance(0)` 构造时 `nGetMaterial(0)` → 原生
     *   `MaterialInstance::getMaterial()+0` 空指针 SIGSEGV（fault addr 0x0）；
     * - `setMaterialInstanceAt()` 的 Java 层先调 `instance.getMaterial()` 做属性校验，
     *   对 peer=0 的包装器同样崩；C++ 层 `assert_invariant(mi)` 在 release 包是空操作。
     *
     * 这里直接反射调 nGet/nSet 原生方法并读 mNativeObject 字段，全程不构造可疑包装器。
     * 方法/字段名来自 filament-android 1.75.1（JNI 命名跨版本稳定；若反射失败，
     * ghostInstanceRaw() 返回 0，隔离自动降级为隐藏，不影响其他功能）。
     */
    private class RawInstanceApi private constructor(
        private val nGet: Method,
        private val nSet: Method,
        private val nGetInst: Method,
        private val rmNativeField: Field,
        private val miNativeField: Field,
    ) {
        // 关键：JNI 的 i 参数是 RenderableManager 内部的 EntityInstance（组件句柄），
        // 不是 Entity 全局句柄！直接传 entity 会让 native 层 mManager[entity] 越界读
        // （SIGSEGV fault addr 0x210000）。必须先经 nGetInstance(entity) 转换。
        fun rmInstance(rm: RenderableManager, entity: Int): Int =
            nGetInst.invoke(null, rmNativeField.getLong(rm), entity) as Int

        fun getInstanceRaw(rm: RenderableManager, entity: Int, primitiveIndex: Int): Long =
            nGet.invoke(null, rmNativeField.getLong(rm), rmInstance(rm, entity), primitiveIndex) as Long

        fun setInstanceRaw(rm: RenderableManager, entity: Int, primitiveIndex: Int, raw: Long) {
            nSet.invoke(null, rmNativeField.getLong(rm), rmInstance(rm, entity), primitiveIndex, raw)
        }

        fun instanceNative(mi: MaterialInstance): Long = miNativeField.getLong(mi)

        companion object {
            fun create(): RawInstanceApi? = try {
                val rmClass = RenderableManager::class.java
                val JLONG = java.lang.Long.TYPE
                val JINT = java.lang.Integer.TYPE
                val nGet = rmClass.getDeclaredMethod(
                    "nGetMaterialInstanceAt", JLONG, JINT, JINT,
                ).apply { isAccessible = true }
                val nSet = rmClass.getDeclaredMethod(
                    "nSetMaterialInstanceAt", JLONG, JINT, JINT, JLONG,
                ).apply { isAccessible = true }
                val nGetInst = rmClass.getDeclaredMethod(
                    "nGetInstance", JLONG, JINT,
                ).apply { isAccessible = true }
                val rmField = rmClass.getDeclaredField("mNativeObject").apply { isAccessible = true }
                val miField = MaterialInstance::class.java.getDeclaredField("mNativeObject")
                    .apply { isAccessible = true }
                RawInstanceApi(nGet, nSet, nGetInst, rmField, miField)
            } catch (_: Throwable) {
                null
            }
        }
    }

    companion object {
        // 自动旋转：浏览器 controls.autoRotateSpeed=1.2（60fps 下 50s/圈 ≈ 0.12°/帧）
        private const val AUTO_ROTATE_DELTA_DEGREES = 0.12f
        private val JUNK_NAME = Regex("^(group\\d+|rootnode|node\\d*|mesh\\d*|object\\d*)$", RegexOption.IGNORE_CASE)
        fun isMeaningfulPartName(name: String): Boolean {
            if (name.isBlank()) return false
            if (JUNK_NAME.matches(name)) return false
            if (name.all { it.isDigit() }) return false
            return true
        }
    }

    /**
     * 剖面：零件级裁剪。
     * @param axis 0=X 1=Y 2=Z；@param position ∈ [-1,1]，切面位置（相对模型半径）
     * 完全位于切面负侧的零件隐藏，与切面相交的零件半透明，并显示蓝色半透明切面。
     */
    fun setSection(enabled: Boolean, axis: Int = 1, position: Float = 0f) {
        sectionEnabled = enabled
        sectionAxis = axis.coerceIn(0, 2)
        sectionPos = position.coerceIn(-1f, 1f)
        applyAppearance()
    }

    /** 按当前隔离/剖面状态刷新每个零件的材质与可见性 */
    private fun applyAppearance() {
        val eng = engine ?: return
        val rm = eng.renderableManager
        val scn = scene ?: return
        if (asset == null) return
        // ghost 原生指针（0 = ghost 不可用：材质未加载成功，或反射通道不可用）
        val ghostRaw = ghostInstanceRaw()
        val isolating = isolatedName != null || isolatedEntity != 0
        // 选中零件的红色高亮指针（运行时编译失败为 0 → 选中零件保持原材质）
        val highlightRaw = if (isolating) highlightInstanceRaw() else 0L
        // 诊断计数：隔离时选中/ghost 匹配分布，用于核对 inPart 判定是否生效
        var dbgMatched = 0
        var dbgGhosted = 0
        for (i in partEntities.indices) {
            val entity = partEntities[i]
            var hidden = false
            var ghost = false
            if (sectionEnabled) {
                // RM API 必须传 RM instance（TM/RM instance 空间互不相同，不能混用）
                val rmInst = rm.getInstance(entity)
                if (rmInst == 0) continue
                val box = rm.getAxisAlignedBoundingBox(rmInst, Box())
                val c = box.center[sectionAxis]
                val h = box.halfExtent[sectionAxis]
                val p = sectionPos * modelRadius
                if (c + h < p) hidden = true
            }
            if (!hidden && isolating) {
                val inPart = (isolatedName != null && partNames[i] == isolatedName) ||
                    (isolatedEntity != 0 && entity == isolatedEntity)
                if (inPart) {
                    dbgMatched++
                } else {
                    if (ghostRaw != 0L) { ghost = true; dbgGhosted++ } else hidden = true // 降级：隐藏代替半透明
                }
            }
            if (hidden) {
                if (hiddenBySection.add(entity)) scn.removeEntity(entity)
            } else {
                if (hiddenBySection.remove(entity)) scn.addEntity(entity)
                // 隔离时：选中零件 → 红色高亮（用户指定），其余 → 原色透明 ghost
                // （按零件主材质色取缓存实例；取不到回退共享 identity ghost）
                val targetRaw = when {
                    !isolating -> 0L
                    ghost -> {
                        val name = partNames[i]
                        val perColorRaw = if (name != null) ghostRawForColor(partColorByNode[name]) else 0L
                        if (perColorRaw != 0L) perColorRaw else ghostRaw
                    }
                    else -> highlightRaw
                }
                setGhost(rm, entity, targetRaw)
            }
        }
        if (isolating) {
            android.util.Log.i(
                "FilamentModelRenderer",
                "隔离匹配: name=$isolatedName entity=$isolatedEntity ghostRaw=$ghostRaw hlRaw=$highlightRaw " +
                    "matched=$dbgMatched ghosted=$dbgGhosted total=${partEntities.size} " +
                    "命中实体在列表=${partEntities.contains(isolatedEntity)}",
            )
        }
        updateSectionPlane()
    }

    private fun ghostInstance(): MaterialInstance? {
        val mat = ghostMaterial ?: return null
        // 简化材质无自定义参数，直接复用单例（剖面指示平面用，走 Builder.material 安全路径）
        return ghostInstances.getOrPut("simple") { mat.createInstance() }
    }

    /** ghost 实例的原生指针；0 表示 ghost 不可用（未加载/反射失败），调用方自动降级 */
    private fun ghostInstanceRaw(): Long {
        val api = rawApi ?: return 0L
        // 运行时编译的 PartGhost（透明度 0.15，对齐浏览器）优先；不可用回退 assets ghost.filamat
        val runtimeMat = runtimeGhostMaterial
        if (runtimeMat != null) {
            val runtimeInst = runtimeGhostInstance
                ?: runtimeMat.createInstance().also { runtimeGhostInstance = it }
            return try { api.instanceNative(runtimeInst) } catch (_: Throwable) { 0L }
        }
        val inst = ghostInstance() ?: return 0L
        return try {
            api.instanceNative(inst)
        } catch (_: Throwable) {
            0L
        }
    }

    /**
     * 「原色透明」ghost：按零件主材质色取（或编译并缓存）透明实例的原生指针。
     * 用户指定（2026-10-01）：隔离时其余零件保持各自原色 + 半透明，而非统一 ghost 色。
     * 材质/实例按颜色 key 缓存（同色零件共享同一编译产物、跨模型复用；
     * 本机 MACHINE-ARM-01 十个零件主材质全是 arm-shell 铜 → 只编译 1 个）。
     * 编译或反射失败返回 0，调用方回退共享 identity ghost，再不行走隐藏降级。
     */
    private fun ghostRawForColor(color: FloatArray?): Long {
        if (color == null || color.size < 3) return 0L
        val api = rawApi ?: return 0L
        // 颜色 key：glTF baseColorFactor 原值即 linear（勿再转换，见 extractPartColors 注释），
        // 千分位量化防浮点抖动
        val key = "${(color[0] * 1000).toInt()}_${(color[1] * 1000).toInt()}_${(color[2] * 1000).toInt()}"
        val mat = ghostMaterialByColor[key]
            ?: compileRuntimeMaterial(
                "PartGhost_$key",
                String.format(
                    java.util.Locale.US,
                    "void material(inout MaterialInputs material) {\n" +
                        "    prepareMaterial(material);\n" +
                        "    material.baseColor = vec4(%.4f, %.4f, %.4f, 0.22);\n" +
                        "    material.roughness = 0.38;\n" +
                        "    material.metallic = 0.0;\n" +
                        "}\n",
                    color[0], color[1], color[2],
                ),
                transparent = true,
                unlit = false,
            )?.also { ghostMaterialByColor[key] = it }
            ?: return 0L
        val inst = ghostInstanceByColor[key]
            ?: try {
                mat.createInstance()
            } catch (_: Throwable) {
                null
            }?.also { ghostInstanceByColor[key] = it }
            ?: return 0L
        return try { api.instanceNative(inst) } catch (_: Throwable) { 0L }
    }

    /**
     * ghostRaw != 0：把该零件「有材质实例」的 primitive 换成 ghost；
     * ghostRaw == 0：恢复原始实例。原始指针为 0 的空槽位永远跳过——
     * 那些槽位本来就不渲染，且任何经 Java 包装器的读写都可能触发原生空指针崩溃。
     */
    private fun setGhost(rm: RenderableManager, entity: Int, ghostRaw: Long) {
        val api = rawApi ?: return
        val originals = originalInstances[entity] ?: return
        for (pi in originals.indices) {
            val orig = originals[pi]
            if (orig == 0L) continue
            val target = if (ghostRaw != 0L) ghostRaw else orig
            runCatching { api.setInstanceRaw(rm, entity, pi, target) }
        }
    }

    private fun updateSectionPlane() {
        val eng = engine ?: return
        if (!sectionEnabled || released || asset == null) {
            destroySectionPlane()
            return
        }
        if (sectionPlaneEntity == 0 || sectionPlaneAxis != sectionAxis) {
            destroySectionPlane()
            createSectionPlane(eng)
        }
        val inst = eng.transformManager.getInstance(sectionPlaneEntity)
        if (inst != 0) {
            val m = FloatArray(16) { if (it % 5 == 0) 1f else 0f }
            m[12 + sectionAxis] = sectionPos * modelRadius
            eng.transformManager.setTransform(inst, m)
        }
    }

    /** 创建蓝色半透明切面指示（与轴向垂直的大 quad） */
    private fun createSectionPlane(eng: Engine) {
        val em = entityManager ?: return
        val scn = scene ?: return
        val planeMat = ghostInstance() ?: return
        val s = modelRadius * 2.2f
        val verts = when (sectionAxis) {
            0 -> floatArrayOf(0f, -s, -s, 0f, -s, s, 0f, s, s, 0f, s, -s) // YZ 平面
            2 -> floatArrayOf(-s, -s, 0f, s, -s, 0f, s, s, 0f, -s, s, 0f) // XY 平面
            else -> floatArrayOf(-s, 0f, -s, s, 0f, -s, s, 0f, s, -s, 0f, s) // XZ 平面
        }
        val vb = VertexBuffer.Builder().vertexCount(4).bufferCount(1)
            .attribute(VertexBuffer.VertexAttribute.POSITION, 0, VertexBuffer.AttributeType.FLOAT3, 0, 12)
            .build(eng)
        vb.setBufferAt(eng, 0, FloatBuffer.wrap(verts))
        val ib = IndexBuffer.Builder().indexCount(6)
            .bufferType(IndexBuffer.Builder.IndexType.USHORT).build(eng)
        ib.setBuffer(eng, ShortBuffer.wrap(shortArrayOf(0, 1, 2, 0, 2, 3)))
        val e = em.create()
        RenderableManager.Builder(1)
            .boundingBox(Box().apply { setCenter(0f, 0f, 0f); setHalfExtent(s, s, s) })
            .geometry(0, RenderableManager.PrimitiveType.TRIANGLES, vb, ib)
            .material(0, planeMat)
            .culling(false)
            .castShadows(false)
            .receiveShadows(false)
            .build(eng, e)
        scn.addEntity(e)
        sectionPlaneEntity = e
        sectionPlaneVb = vb
        sectionPlaneIb = ib
        sectionPlaneAxis = sectionAxis
    }

    private fun destroySectionPlane() {
        val eng = engine ?: return
        if (sectionPlaneEntity != 0) {
            scene?.removeEntity(sectionPlaneEntity)
            eng.renderableManager.destroy(sectionPlaneEntity)
            entityManager?.destroy(sectionPlaneEntity)
            sectionPlaneEntity = 0
        }
        sectionPlaneVb?.let { eng.destroyVertexBuffer(it); sectionPlaneVb = null }
        sectionPlaneIb?.let { eng.destroyIndexBuffer(it); sectionPlaneIb = null }
        sectionPlaneAxis = -1
    }

    /** 新模型加载时重置隔离/剖面状态（旧实体已销毁） */
    private fun resetAppearanceState() {
        originalInstances.clear()
        hiddenBySection.clear()
        isolatedName = null
        isolatedEntity = 0
        sectionEnabled = false
        destroySectionPlane()
    }

    /** 零件原始局部变换快照（爆炸归零时还原 → 装配态与 GLB 原样一致，层级不破坏） */
    private var originalTransforms = mutableMapOf<Int, FloatArray>()

    /** 收集每个可渲染零件的世界中心（模型空间），爆炸时沿中心向外散开 */
    private fun collectPartCenters(asset: FilamentAsset) {
        partCenters.clear()
        partEntities.clear()
        partNames.clear()
        originalTransforms.clear()
        val eng = engine ?: return
        val tm = eng.transformManager
        val renderableManager = eng.renderableManager
        for (entity in asset.entities) {
            if (!renderableManager.hasComponent(entity)) continue
            val tmInstance = tm.getInstance(entity)
            // 注意：TM 缺失的 renderable 也必须收集进 partEntities！
            // 实测牛油果测试机的主零件（entity=10）无 TM 组件，被旧逻辑过滤后
            // picking 命中它时 inPart 双分支全失败 → 所有零件误走 ghost → 全白。
            val m = FloatArray(16)
            if (tmInstance != 0) {
                // 原局部变换快照：爆炸归零时还原，装配态 = GLB 原样（浏览器紧凑效果）
                tm.getTransform(tmInstance, m)
                originalTransforms[entity] = m.copyOf()
                // 世界中心（root 已做中心校正 → 模型中心≈原点），爆炸方向基于它
                val w = FloatArray(16)
                tm.getWorldTransform(tmInstance, w)
                partCenters.add(floatArrayOf(w[12], w[13], w[14]))
            } else {
                partCenters.add(floatArrayOf(0f, 0f, 0f))
            }
            partEntities.add(entity)
            partNames.add(try { asset.getName(entity) } catch (_: Exception) { null })
            // 加载期一次性捕获原始材质实例（裸指针，0=空槽位）。
            // 隔离/恢复只回放这里捕获到的指针，运行期不再经 getMaterialInstanceAt() 回读——
            // 该 API 对空槽位不做判空，new MaterialInstance(0) 构造即原生崩溃。
            // 注意：RM 的所有 native 调用都要用 RM instance（getInstance 转换），
            // 不能把 Entity 或 TransformManager instance 混传进来。
            val api = rawApi
            val rmInstance = renderableManager.getInstance(entity)
            if (rmInstance == 0) continue
            val primitiveCount = renderableManager.getPrimitiveCount(rmInstance)
            originalInstances[entity] = if (api != null) {
                LongArray(primitiveCount) { pi ->
                    try {
                        api.getInstanceRaw(renderableManager, entity, pi)
                    } catch (_: Throwable) {
                        0L
                    }
                }
            } else {
                LongArray(primitiveCount) // 反射不可用：全部标记为空槽位，隔离走隐藏降级
            }
        }
        android.util.Log.i(
            "FilamentModelRenderer",
            "零件收集: ${partEntities.size}个 [${partEntities.joinToString(",")}] names=${partNames.joinToString("/")}",
        )
    }

    /** 应用爆炸位移；爆炸系数为 0 时还原 GLB 原始局部变换（紧凑装配态） */
    private fun applyPartTransforms() {
        val eng = engine ?: return
        val tm = eng.transformManager
        // 爆炸距离：按模型半径缩放，保证大小模型都合适
        val explodeDistance = explosionFactor * modelRadius * 1.5f
        for (i in partEntities.indices) {
            val entity = partEntities[i]
            val instance = tm.getInstance(entity)
            if (instance == 0) continue
            // 从原始局部变换出发（不累积、不破坏层级关系）：
            // 归零 = GLB 原样；爆炸 = 原变换 + 沿世界中心方向的位移
            val original = originalTransforms[entity] ?: continue
            val m = original.copyOf()
            if (explodeDistance != 0f) {
                val c = partCenters[i]
                // 方向：从模型中心（原点）指向零件世界中心
                val len = sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2])
                val (dx, dy, dz) = if (len > 1e-6f) {
                    Triple(c[0] / len, c[1] / len, c[2] / len)
                } else {
                    // 中心处的零件沿 Y 轴散开
                    Triple(0f, 1f, 0f)
                }
                m[12] += dx * explodeDistance
                m[13] += dy * explodeDistance
                m[14] += dz * explodeDistance
            }
            tm.setTransform(instance, m)
        }
    }

    fun release() {
        if (released) return
        released = true
        detachSurface()
        destroySectionPlane()
        destroyGrid()
        runtimeHighlightInstance?.let { engine?.destroyMaterialInstance(it) }
        runtimeHighlightInstance = null
        runtimeGhostInstance?.let { engine?.destroyMaterialInstance(it) }
        runtimeGhostInstance = null
        runtimeHighlightMaterial?.let { engine?.destroyMaterial(it) }
        runtimeHighlightMaterial = null
        runtimeGhostMaterial?.let { engine?.destroyMaterial(it) }
        runtimeGhostMaterial = null
        runtimeGridMaterial?.let { engine?.destroyMaterial(it) }
        runtimeGridMaterial = null
        runtimeMatBuilderReady = false
        // 按色缓存的「原色透明」ghost：先实例后材质（实例由材质创建）
        ghostInstanceByColor.values.forEach { engine?.destroyMaterialInstance(it) }
        ghostInstanceByColor.clear()
        ghostMaterialByColor.values.forEach { engine?.destroyMaterial(it) }
        ghostMaterialByColor.clear()
        ghostInstances.values.forEach { engine?.destroyMaterialInstance(it) }
        ghostInstances.clear()
        ghostMaterial?.let { engine?.destroyMaterial(it) }
        ghostMaterial = null
        originalInstances.clear()
        hiddenBySection.clear()
        asset?.let { current ->
            scene?.removeEntities(current.entities)
            assetLoader?.destroyAsset(current)
        }
        asset = null
        resourceLoader?.destroy()
        assetLoader?.destroy()
        view?.let { engine?.destroyView(it) }
        scene?.let { engine?.destroyScene(it) }
        renderer?.let { engine?.destroyRenderer(it) }
        cameraComponent?.let {
            engine?.destroyCameraComponent(it.getEntity())
            entityManager?.destroy(it.getEntity())
        }
        skybox?.let { engine?.destroySkybox(it) }
        skybox = null
        fillLightEntity?.let { entity -> engine?.lightManager?.destroy(entity); entityManager?.destroy(entity) }
        hemiLightEntity?.let { entity -> engine?.lightManager?.destroy(entity); entityManager?.destroy(entity) }
        keyLightEntity?.let { entity -> engine?.lightManager?.destroy(entity); entityManager?.destroy(entity) }
        fillLightEntity = null
        hemiLightEntity = null
        keyLightEntity = null
        materialProvider?.destroy()
        engine?.destroy()
    }

    internal fun initializationFailureMessage(): String? = initializationError?.message

    /** Root-cause chain for diagnostics, e.g. "UnsatisfiedLinkError(dlopen failed...) <- ...". */
    internal fun initializationFailureChain(): String? =
        initializationError?.let { root ->
            generateSequence(root) { it.cause }.take(5)
                .joinToString(" <- ") { "${it.javaClass.simpleName}(${it.message ?: "-"})" }
        }

    private fun checkAvailable() {
        if (initializationError != null) {
            throw FilamentUnavailableException(initializationError)
        }
    }

    private fun applyCamera() {
        val yaw = Math.toRadians(camera.yawDegrees.toDouble())
        val pitch = Math.toRadians(camera.pitchDegrees.toDouble())
        val distance = camera.distance.toDouble()
        val cosPitch = cos(pitch)
        val centerX = modelCenter[0].toDouble()
        val centerY = modelCenter[1].toDouble()
        val centerZ = modelCenter[2].toDouble()
        cameraComponent?.lookAt(
            centerX + distance * cosPitch * sin(yaw) + camera.panX,
            centerY + distance * sin(pitch) + camera.panY,
            centerZ + distance * cosPitch * cos(yaw),
            centerX + camera.panX.toDouble(),
            centerY + camera.panY.toDouble(),
            centerZ,
            0.0,
            1.0,
            0.0,
        )
        val (near, far) = RenderMath.clipPlanes(camera.distance, modelRadius)
        cameraComponent?.setProjection(
            45.0,
            viewportWidth.toDouble() / viewportHeight,
            near,
            far,
            Camera.Fov.VERTICAL,
        )
    }
}
// 注意：FilamentUnavailableException 放在同包 RendererExceptions.kt（main sourceSet），
// 与渲染器解耦，错误归因与单测可直接引用。
