package com.company.logistics.rendering

import android.view.Choreographer
import android.view.Surface
import com.google.android.filament.Camera
import com.google.android.filament.Engine
import com.google.android.filament.Filament
import com.google.android.filament.Renderer
import com.google.android.filament.Scene
import com.google.android.filament.Skybox
import com.google.android.filament.SwapChain
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
import java.io.File
import java.nio.channels.FileChannel
import java.nio.file.StandardOpenOption
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** Real Filament GLB renderer; all Filament objects are owned and released here. */
class FilamentModelRenderer(
    private val filamentInitializer: () -> Unit = { Filament.init() },
    private val gltfioInitializer: () -> Unit = { Gltfio.init() },
    private val engineFactory: () -> Engine = { Engine.create() },
    backgroundArgb: Int = 0xFFFFFBFE.toInt(),
) : ModelRendererAdapter, Choreographer.FrameCallback {
    // Distance bounds scale with real assemblies: the Gearbox sample sits ~160 units from
    // the origin with a 15.6 radius and needs a ~94 unit viewing distance.
    override val camera = OrbitCameraState(minDistance = 0.1f, maxDistance = 2000f)
    private var modelCenter = floatArrayOf(0f, 0f, 0f)
    private var modelRadius = 1f
    // 爆炸图：每个零件的原始局部中心（模型空间），用于计算爆炸方向
    private var partCenters = mutableListOf<FloatArray>()
    private var partEntities = mutableListOf<Int>()
    private var explosionFactor = 0f
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
    private var skybox: Skybox? = null
    private var assetLoader: AssetLoader? = null
    private var resourceLoader: ResourceLoader? = null
    private var initializationError: Throwable? = null
    private var swapChain: SwapChain? = null
    private var asset: FilamentAsset? = null
    private var released = false
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
        var createdSkybox: Skybox? = null
        var createdAssetLoader: AssetLoader? = null
        var createdResourceLoader: ResourceLoader? = null
        try {
            filamentInitializer()
            gltfioInitializer()
            createdEngine = engineFactory()
            createdEntityManager = EntityManager.get()
            createdMaterialProvider = UbershaderProvider(createdEngine)
            createdRenderer = createdEngine.createRenderer()
            createdScene = createdEngine.createScene()
            createdView = createdEngine.createView()
            createdCamera = createdEngine.createCamera(createdEntityManager.create())
            createdKeyLight = createdEntityManager.create().also { entity ->
                LightManager.Builder(LightManager.Type.DIRECTIONAL)
                    .intensity(60_000f)
                    .color(1.0f, 0.98f, 0.95f)
                    .direction(-0.6f, -1.0f, -0.8f)
                    .build(createdEngine, entity)
                createdScene.addEntity(entity)
            }
            createdFillLight = createdEntityManager.create().also { entity ->
                LightManager.Builder(LightManager.Type.DIRECTIONAL)
                    .intensity(20_000f)
                    .color(0.85f, 0.92f, 1.0f)
                    .direction(0.8f, -0.3f, 0.7f)
                    .build(createdEngine, entity)
                createdScene.addEntity(entity)
            }
            createdSkybox = Skybox.Builder()
                .color(backgroundR, backgroundG, backgroundB, 1.0f)
                .build(createdEngine)
            createdScene.skybox = createdSkybox
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
            createdKeyLight?.let { entity -> createdEngine?.lightManager?.destroy(entity); createdEntityManager?.destroy(entity) }
            createdMaterialProvider?.destroy()
            createdEngine?.destroy()
        }
    }

    fun attach(surface: Surface): Result<Unit> = runCatching {
        check(!released) { "renderer has been released" }
        checkAvailable()
        val activeEngine = engine ?: error("Filament engine is unavailable")
        swapChain?.let(activeEngine::destroySwapChain)
        swapChain = activeEngine.createSwapChain(surface)
        choreographer.removeFrameCallback(this)
        choreographer.postFrameCallback(this)
        frameCallbackPosted = true
    }

    fun detachSurface() {
        if (frameCallbackPosted) choreographer.removeFrameCallback(this)
        frameCallbackPosted = false
        swapChain?.let { chain -> engine?.destroySwapChain(chain) }
        swapChain = null
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
            asset = candidate
            activeScene.addEntities(candidate!!.entities)
            val radius = candidate!!.boundingBox.halfExtent.maxOrNull() ?: 1f
            // 中心校正：把包围盒中心移到原点，旋转围绕真中心，不会"飞"
            val bboxCenter = candidate!!.boundingBox.center
            modelCenter = floatArrayOf(0f, 0f, 0f)
            modelRadius = if (radius > 0f) radius else 1f
            // 收集零件中心（用于爆炸图）
            collectPartCenters(candidate!!, bboxCenter)
            // 应用中心校正 + 当前爆炸系数
            applyPartTransforms()
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

    /** 点选零件：返回零件名称（GLB节点名），无命中返回null */
    fun pickPart(x: Float, y: Float, onResult: (String?) -> Unit) {
        val vw = view ?: run { onResult(null); return }
        val handler = android.os.Handler(android.os.Looper.getMainLooper())
        vw.pick(x.toInt(), viewportHeight - y.toInt(), handler) { result: View.PickingQueryResult ->
            val name = if (result.entity != 0) {
                try { asset?.getName(result.entity) } catch (_: Exception) { null }
            } else null
            onResult(name?.takeIf { it.isNotBlank() } ?: "零件 #${result.entity}")
        }
    }

    /** 收集每个可渲染零件的世界中心（模型空间），爆炸时沿中心向外散开 */
    private fun collectPartCenters(asset: FilamentAsset, bboxCenter: FloatArray) {
        partCenters.clear()
        partEntities.clear()
        val eng = engine ?: return
        val tm = eng.transformManager
        val renderableManager = eng.renderableManager
        for (entity in asset.entities) {
            if (!renderableManager.hasComponent(entity)) continue
            val instance = tm.getInstance(entity)
            if (instance == 0) continue
            // 取该零件的世界变换，提取平移部分作为近似中心
            val m = FloatArray(16)
            tm.getTransform(instance, m)
            // 中心校正：减去包围盒中心，移到以原点为中心的空间
            partCenters.add(floatArrayOf(m[12] - bboxCenter[0], m[13] - bboxCenter[1], m[14] - bboxCenter[2]))
            partEntities.add(entity)
        }
    }

    /** 应用中心校正 + 爆炸位移 */
    private fun applyPartTransforms() {
        val eng = engine ?: return
        val tm = eng.transformManager
        // 爆炸距离：按模型半径缩放，保证大小模型都合适
        val explodeDistance = explosionFactor * modelRadius * 1.5f
        for (i in partEntities.indices) {
            val entity = partEntities[i]
            val instance = tm.getInstance(entity)
            if (instance == 0) continue
            val c = partCenters[i]
            // 方向：从原点（模型中心）指向零件中心
            val len = sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2])
            val (dx, dy, dz) = if (len > 1e-6f) {
                Triple(c[0] / len, c[1] / len, c[2] / len)
            } else {
                // 中心处的零件沿 Y 轴散开
                Triple(0f, 1f, 0f)
            }
            // 最终位移 = 中心校正(-bboxCenter) + 爆炸位移
            // 注意 partCenters 已做过中心校正，这里只需加爆炸位移
            // 但原始变换的平移部分需要保留，所以重新计算
            val m = FloatArray(16)
            tm.getTransform(instance, m)
            // 爆炸：在当前变换基础上叠加位移
            // 为避免累积，先恢复到收集时的状态再叠加
            // 简化：直接设置平移 = 原始平移 - bboxCenter + 爆炸位移
            // 原始平移 = partCenters[i] + bboxCenter
            m[12] = partCenters[i][0] + dx * explodeDistance
            m[13] = partCenters[i][1] + dy * explodeDistance
            m[14] = partCenters[i][2] + dz * explodeDistance
            tm.setTransform(instance, m)
        }
    }

    fun release() {
        if (released) return
        released = true
        detachSurface()
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
        fillLightEntity?.let { entity -> engine?.lightManager?.destroy(entity); entityManager?.destroy(entity) }
        keyLightEntity?.let { entity -> engine?.lightManager?.destroy(entity); entityManager?.destroy(entity) }
        fillLightEntity = null
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
