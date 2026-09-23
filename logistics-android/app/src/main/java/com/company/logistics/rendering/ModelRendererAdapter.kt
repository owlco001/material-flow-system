package com.company.logistics.rendering

/**
 * Isolates camera gestures from a rendering engine. A future Filament/GLB adapter can
 * implement the model lifecycle without changing this gesture/state contract.
 */
interface ModelRendererAdapter {
    val camera: OrbitCameraState

    fun onRotate(deltaX: Float, deltaY: Float)
    fun onScale(scaleFactor: Float)
    fun onPan(deltaX: Float, deltaY: Float)
    fun resetCamera()
}

class OrbitCameraModelRendererAdapter(
    override val camera: OrbitCameraState = OrbitCameraState(),
) : ModelRendererAdapter {
    override fun onRotate(deltaX: Float, deltaY: Float) {
        camera.rotate(deltaYaw = deltaX, deltaPitch = deltaY)
    }

    override fun onScale(scaleFactor: Float) {
        camera.zoom(scale = scaleFactor)
    }

    override fun onPan(deltaX: Float, deltaY: Float) {
        camera.pan(deltaX = deltaX, deltaY = deltaY)
    }

    override fun resetCamera() {
        camera.reset()
    }
}
