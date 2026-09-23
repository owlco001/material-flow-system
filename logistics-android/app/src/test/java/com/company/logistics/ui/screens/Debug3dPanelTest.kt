package com.company.logistics.ui.screens

import com.company.logistics.data.remote.ApiException
import org.junit.Assert.assertEquals
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
    fun failedUploadRetryReusesIdempotencyKeyUntilSuccess() {
        val operation = Debug3dUploadOperation()
        val first = operation.id()
        assertEquals(first, operation.id())
        operation.complete()
        assertFalse(first == operation.id())
    }

    @Test
    fun httpFailuresHaveRecoverableMessagesForUploadAndLoad() {
        val statuses = listOf(401, 403, 404, 409, 413, 415)
        statuses.forEach { status ->
            val error = ApiException(status, "TEST", "backend detail")
            assertTrue(Debug3dErrorPolicy.upload(error).startsWith("上传失败："))
            assertTrue(Debug3dErrorPolicy.load(error).startsWith("加载失败："))
            assertFalse(Debug3dErrorPolicy.upload(error).contains("backend detail"))
        }
        assertEquals("上传失败：网络失败，请检查连接后重试", Debug3dErrorPolicy.upload(IllegalStateException("offline")))
    }

    @Test
    fun publishedModelAbsenceHasAStableStatusDistinctFromFilamentFailure() {
        val noModel = Debug3dErrorPolicy.load(ApiException(404, "NOT_FOUND", "missing"))

        assertTrue(noModel.contains("NO_PUBLISHED_MODEL"))
        assertFalse(noModel.contains("FILAMENT_UNAVAILABLE"))
        assertTrue(com.company.logistics.rendering.FilamentUnavailableException(null).message == "FILAMENT_UNAVAILABLE")
    }

    @Test
    fun retryableServerErrorsTellUserToRetry() {
        val error = ApiException(503, "UNAVAILABLE", "temporary", retryable = true)
        assertTrue(Debug3dErrorPolicy.upload(error).contains("请重试"))
        assertTrue(Debug3dErrorPolicy.load(error).contains("请重试"))
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