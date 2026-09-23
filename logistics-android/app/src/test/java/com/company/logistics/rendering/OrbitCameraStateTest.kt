package com.company.logistics.rendering

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OrbitCameraStateTest {
    @Test
    fun rotateClampsPitchAndWrapsYaw() {
        val camera = OrbitCameraState()

        camera.rotate(deltaYaw = 450f, deltaPitch = 1000f)

        assertEquals(90f, camera.yawDegrees, 0.001f)
        assertEquals(89f, camera.pitchDegrees, 0.001f)
    }

    @Test
    fun zoomClampsDistanceToConfiguredBounds() {
        val camera = OrbitCameraState(minDistance = 2f, maxDistance = 8f, initialDistance = 4f)

        camera.zoom(scale = 0.01f)
        assertEquals(8f, camera.distance, 0.001f)

        camera.zoom(scale = 100f)
        assertEquals(2f, camera.distance, 0.001f)
    }

    @Test
    fun panClampsEachAxisToConfiguredBounds() {
        val camera = OrbitCameraState(maxPan = 3f)

        camera.pan(deltaX = 10f, deltaY = -10f)

        assertEquals(3f, camera.panX, 0.001f)
        assertEquals(-3f, camera.panY, 0.001f)
    }

    @Test
    fun resetRestoresInitialCamera() {
        val camera = OrbitCameraState(initialYawDegrees = 25f, initialPitchDegrees = -10f, initialDistance = 6f)
        camera.rotate(40f, 20f)
        camera.zoom(0.5f)
        camera.pan(2f, -1f)

        camera.reset()

        assertEquals(25f, camera.yawDegrees, 0.001f)
        assertEquals(-10f, camera.pitchDegrees, 0.001f)
        assertEquals(6f, camera.distance, 0.001f)
        assertEquals(0f, camera.panX, 0.001f)
        assertEquals(0f, camera.panY, 0.001f)
    }

    @Test
    fun adapterDelegatesGesturesAndExposesStateWithoutRenderingDependency() {
        val adapter: ModelRendererAdapter = OrbitCameraModelRendererAdapter()

        adapter.onRotate(deltaX = 12f, deltaY = -8f)
        adapter.onScale(scaleFactor = 0.5f)
        adapter.onPan(deltaX = 1f, deltaY = 2f)

        assertEquals(12f, adapter.camera.yawDegrees, 0.001f)
        assertEquals(-8f, adapter.camera.pitchDegrees, 0.001f)
        assertTrue(adapter.camera.distance > 1f)
        assertEquals(1f, adapter.camera.panX, 0.001f)
        assertEquals(2f, adapter.camera.panY, 0.001f)

        adapter.resetCamera()
        assertEquals(0f, adapter.camera.yawDegrees, 0.001f)
        assertEquals(0f, adapter.camera.panX, 0.001f)
    }
}
