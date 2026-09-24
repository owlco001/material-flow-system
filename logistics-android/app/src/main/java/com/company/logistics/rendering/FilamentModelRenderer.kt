package com.company.logistics.rendering

import android.view.Choreographer
import android.view.Surface
import com.google.android.filament.Camera
import com.google.android.filament.Engine
import com.google.android.filament.Filament
import com.google.android.filament.Renderer
import com.google.android.filament.Scene
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
import java.io.File
import java.nio.channels.FileChannel
import java.nio.file.StandardOpenOption
import kotlin.math.cos
import kotlin.math.sin

/** Real Filament GLB renderer; all Filament objects are owned and released here. */
class FilamentModelRenderer(
    private val filamentInitializer: () -> Unit = { Filament.init() },
    private val gltfioInitializer: () -> Unit = { Gltfio.init() },
    private val engineFactory: () -> Engine = { Engine.create() },
) : ModelRendererAdapter, Choreographer.FrameCallback {
    override val camera = OrbitCameraState()

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
            assetLoader = createdAssetLoader
            resourceLoader = createdResourceLoader
            view?.scene = createdScene
            view?.camera = createdCamera
            view?.isPostProcessingEnabled = false
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
        cameraComponent?.setProjection(
            45.0,
            viewportWidth.toDouble() / viewportHeight,
            0.1,
            100.0,
            Camera.Fov.VERTICAL,
        )
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
            camera.fit(if (radius > 0f) radius else 1f)
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
        cameraComponent?.lookAt(
            distance * cosPitch * sin(yaw) + camera.panX,
            distance * sin(pitch) + camera.panY,
            distance * cosPitch * cos(yaw),
            camera.panX.toDouble(),
            camera.panY.toDouble(),
            0.0,
            0.0,
            0.0,
            1.0,
        )
    }
}

class FilamentUnavailableException(cause: Throwable?) :
    IllegalStateException("FILAMENT_UNAVAILABLE", cause)
