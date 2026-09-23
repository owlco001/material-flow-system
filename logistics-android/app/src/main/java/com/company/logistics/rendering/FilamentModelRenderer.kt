package com.company.logistics.rendering

import android.view.Choreographer
import android.view.Surface
import com.google.android.filament.Camera
import com.google.android.filament.Engine
import com.google.android.filament.Renderer
import com.google.android.filament.Scene
import com.google.android.filament.SwapChain
import com.google.android.filament.View
import com.google.android.filament.Viewport
import com.google.android.filament.gltfio.AssetLoader
import com.google.android.filament.gltfio.FilamentAsset
import com.google.android.filament.gltfio.ResourceLoader
import com.google.android.filament.gltfio.UbershaderProvider
import com.google.android.filament.EntityManager
import java.io.File
import java.nio.channels.FileChannel
import java.nio.file.StandardOpenOption
import kotlin.math.cos
import kotlin.math.sin

/** Real Filament GLB renderer; all Filament objects are owned and released here. */
class FilamentModelRenderer : ModelRendererAdapter, Choreographer.FrameCallback {
    override val camera = OrbitCameraState()

    private val choreographer = Choreographer.getInstance()
    private val engine = Engine.create()
    private val entityManager = EntityManager.get()
    private val materialProvider = UbershaderProvider(engine)
    private val renderer: Renderer = engine.createRenderer()
    private val scene: Scene = engine.createScene()
    private val view: View = engine.createView()
    private val cameraComponent: Camera = engine.createCamera(entityManager.create())
    private val assetLoader = AssetLoader(engine, materialProvider, entityManager)
    private val resourceLoader = ResourceLoader(engine)
    private var swapChain: SwapChain? = null
    private var asset: FilamentAsset? = null
    private var released = false
    private var viewportWidth = 1
    private var viewportHeight = 1

    init {
        view.scene = scene
        view.camera = cameraComponent
        view.isPostProcessingEnabled = false
        applyCamera()
    }

    fun attach(surface: Surface) {
        check(!released) { "renderer has been released" }
        swapChain?.let(engine::destroySwapChain)
        swapChain = engine.createSwapChain(surface)
        choreographer.removeFrameCallback(this)
        choreographer.postFrameCallback(this)
    }

    fun onViewportChanged(width: Int, height: Int) {
        viewportWidth = width.coerceAtLeast(1)
        viewportHeight = height.coerceAtLeast(1)
        view.viewport = Viewport(0, 0, viewportWidth, viewportHeight)
        cameraComponent.setProjection(
            45.0,
            viewportWidth.toDouble() / viewportHeight,
            0.1,
            100.0,
            Camera.Fov.VERTICAL,
        )
    }

    fun loadGlb(file: File): Result<Unit> = runCatching {
        check(!released) { "renderer has been released" }
        require(file.isFile) { "GLB file does not exist: ${file.path}" }
        require(file.length() >= 20L) { "GLB file is truncated" }
        val loaded = FileChannel.open(file.toPath(), StandardOpenOption.READ).use { channel ->
            val buffer = channel.map(FileChannel.MapMode.READ_ONLY, 0, channel.size())
            assetLoader.createAsset(buffer)
                ?: error("Filament rejected GLB: ${file.name}")
        }
        asset?.let(assetLoader::destroyAsset)
        scene.removeEntities(asset?.entities ?: intArrayOf())
        asset = loaded
        resourceLoader.loadResources(loaded)
        loaded.releaseSourceData()
        scene.addEntities(loaded.entities)
        applyCamera()
    }.onFailure {
        asset?.let(assetLoader::destroyAsset)
        asset = null
    }

    override fun doFrame(frameTimeNanos: Long) {
        val chain = swapChain
        if (!released && chain != null && renderer.beginFrame(chain, frameTimeNanos)) {
            renderer.render(view)
            renderer.endFrame()
            choreographer.postFrameCallback(this)
        }
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
        choreographer.removeFrameCallback(this)
        asset?.let(assetLoader::destroyAsset)
        asset = null
        swapChain?.let(engine::destroySwapChain)
        swapChain = null
        resourceLoader.destroy()
        assetLoader.destroy()
        engine.destroyView(view)
        engine.destroyScene(scene)
        engine.destroyRenderer(renderer)
        engine.destroyCameraComponent(cameraComponent.getEntity())
        entityManager.destroy(cameraComponent.getEntity())
        materialProvider.destroy()
        engine.destroy()
    }

    private fun applyCamera() {
        val yaw = Math.toRadians(camera.yawDegrees.toDouble())
        val pitch = Math.toRadians(camera.pitchDegrees.toDouble())
        val distance = camera.distance.toDouble()
        val cosPitch = cos(pitch)
        cameraComponent.lookAt(
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
