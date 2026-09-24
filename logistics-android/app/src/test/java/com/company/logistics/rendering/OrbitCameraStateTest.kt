package com.company.logistics.rendering

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OrbitCameraStateTest {
    @Test
    fun fitFramesModelRadiusResetsPanAndClampsToBounds() {
        val camera = OrbitCameraState()
        camera.pan(2f, 3f)

        camera.fit(radius = 2f)
        assertEquals(5.55f, camera.distance, 0.01f)
        assertEquals(0f, camera.panX, 0.001f)
        assertEquals(0f, camera.panY, 0.001f)

        camera.fit(radius = 100f)
        assertEquals(20f, camera.distance, 0.001f)

        camera.fit(radius = 0.01f)
        assertEquals(1f, camera.distance, 0.001f)
    }

    @Test
    fun fitAccountsForNarrowPortraitAspect() {
        val camera = OrbitCameraState()
        camera.fit(radius = 2f, aspect = 1f)
        val squareAspectDistance = camera.distance
        camera.fit(radius = 2f, aspect = 0.46f)
        val portraitDistance = camera.distance
        assert(portraitDistance > squareAspectDistance) { "portrait fit must back the camera up" }
        // halfFovX = atan(tan(22.5deg) * 0.46); distance = radius / tan(halfFovX) * 1.15
        assertEquals(12.07f, portraitDistance, 0.05f)
    }
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
