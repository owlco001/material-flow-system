package com.company.logistics.ui.screens

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Debug3dPanelTest {
    @Test
    fun uploadDefaultsAreValid() {
        assertTrue(Debug3dUploadPolicy.DEFAULT_MODEL_CODE == "GearboxAssy")
        assertTrue(Debug3dUploadPolicy.DEFAULT_MODEL_NAME == "Gearbox Assembly")
        assertTrue(Debug3dUploadPolicy.validate(Debug3dUploadPolicy.DEFAULT_MODEL_CODE, Debug3dUploadPolicy.DEFAULT_MODEL_NAME) == null)
    }

    @Test
    fun uploadValidationRejectsEmptyAndInvalidValues() {
        assertTrue(Debug3dUploadPolicy.validate("", "Assembly")?.contains("modelCode") == true)
        assertTrue(Debug3dUploadPolicy.validate("Gearbox Assy", "Assembly")?.contains("modelCode") == true)
        assertTrue(Debug3dUploadPolicy.validate("GearboxAssy", " ") == "modelName 不能为空")
    }

    @Test
    fun entryIsVisibleOnlyForDebugBuilds() {
        assertTrue(Debug3dEntryPolicy.isVisible(true))
        assertFalse(Debug3dEntryPolicy.isVisible(false))
    }

    @Test
    fun uploadSuccessCanTriggerPublishedModelLoad() {
        val coordinator = Debug3dModelLoadCoordinator()
        var requestedCode: String? = null

        assertTrue(coordinator.request("GearboxAssy") { requestedCode = it })
        assertTrue(requestedCode == "GearboxAssy")
    }

    @Test
    fun blankOrInFlightPublishedModelRequestsAreSuppressed() {
        val coordinator = Debug3dModelLoadCoordinator()
        var requests = 0

        assertFalse(coordinator.request("") { requests++ })
        assertTrue(coordinator.request("GearboxAssy") { requests++ })
        assertFalse(coordinator.request("GearboxAssy") { requests++ })
        assertTrue(requests == 1)
        coordinator.complete()
        assertTrue(coordinator.request("GearboxAssy") { requests++ })
        assertTrue(requests == 2)
    }

    @Test
    fun debugPanelUsesCameraStateAdapterForInteraction() {
        val adapter = com.company.logistics.rendering.OrbitCameraModelRendererAdapter()
        adapter.onRotate(15f, -5f)
        adapter.onScale(1.25f)
        adapter.onPan(0.5f, -0.25f)

        assertTrue(adapter.camera.yawDegrees == 15f)
        assertTrue(adapter.camera.pitchDegrees == -5f)
        assertTrue(adapter.camera.distance < 5f)
        assertTrue(adapter.camera.panX == 0.5f)
    }
}