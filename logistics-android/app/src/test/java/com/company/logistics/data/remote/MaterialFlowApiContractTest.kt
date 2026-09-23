package com.company.logistics.data.remote

import kotlinx.coroutines.runBlocking
import java.io.ByteArrayOutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID
import kotlin.system.measureTimeMillis
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Test

class MaterialFlowApiContractTest {

    @Test
    fun parsesExceptionSubmissionStatusAndDifference() {
        val result = ApiParser.parseExceptionSubmission("""{"exceptionId":"ex-1","status":"PENDING","difference":-1,"serverTime":"now"}""")
        assertEquals("ex-1", result.exceptionId)
        assertEquals("PENDING", result.status)
        assertEquals(-1, result.difference)
        assertEquals("now", result.serverTime)
    }

    @Test
    fun exceptionRequiresAllServerAssociationFieldsBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().createException(UUID.randomUUID().toString(), "WO-1", "", "mat-1", "OTHER", 1, 0)
        }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun transferRejectsMissingOrInvalidInventoryVersionBeforeNetworkAccess() = runBlocking {
        listOf(null, 0).forEach { version ->
            val error = runCatching {
                MaterialFlowApi().createTransferRequest(
                    clientOperationId = UUID.randomUUID().toString(), type = "INBOUND", documentNo = null,
                    items = listOf(TransferItem("mat-1", 1, expectedInventoryVersion = version)),
                )
            }.exceptionOrNull()
            assertTrue(error is IllegalArgumentException)
            assertTrue(error?.message?.contains("expectedInventoryVersion") == true)
        }
    }

    @Test
    fun exceptionUsesClientOperationIdInBodyAndIdempotencyHeader() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureOneRequest("""{"exceptionId":"ex-1","status":"PENDING","difference":0,"serverTime":"now"}""") { port ->
            val previous = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    api.createException(operationId, "WO-1", "device-1", "mat-1", "OTHER", 1, 0)
                }
            } finally { ApiConfig.baseUrl = previous }
        }
        assertEquals(operationId, captured.headers["Idempotency-Key"])
        assertTrue(captured.body.contains("\"clientOperationId\":\"$operationId\""))
    }

    @Test
    fun deleteUserUsesDeletePathAndStableIdempotencyHeaders() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureOneRequest("""{"userId":"u-1","status":"DELETED","serverTime":"now"}""") { port ->
            val previousBaseUrl = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    val result = api.deleteUser("u-1", operationId)
                    assertEquals("u-1", result.userId)
                    assertEquals("DELETED", result.status)
                }
            } finally {
                ApiConfig.baseUrl = previousBaseUrl
            }
        }

        assertEquals("DELETE /api/v1/admin/users/u-1 HTTP/1.1", captured.requestLine)
        assertEquals("Bearer access-token", captured.headers["Authorization"])
        assertEquals(operationId, captured.headers["Idempotency-Key"])
        UUID.fromString(captured.headers["X-Request-Id"] ?: error("X-Request-Id missing"))
        assertTrue(captured.body.contains("\"clientOperationId\":\"$operationId\""))
    }

    @Test
    fun exceptionRejectsInvalidClientOperationIdBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().createException("not-a-uuid", "WO-1", "device-1", "mat-1", "OTHER", 1, 0)
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
        assertTrue(error?.message?.contains("clientOperationId") == true)
    }

    @Test
    fun loginUsesEmployeeNoWireField() = runBlocking {
        val captured = captureOneRequest("""{"accessToken":"a","user":{"id":"u","username":"E-1","displayName":"E","role":"OPERATOR"}}""") { port ->
            val old = ApiConfig.baseUrl
            try { ApiConfig.baseUrl = "http://127.0.0.1:$port"; MaterialFlowApi().login("E-1", "secret", "pda", "0.3.0") }
            finally { ApiConfig.baseUrl = old }
        }
        assertTrue(captured.body.contains("\"employeeNo\":\"E-1\""))
        assertTrue(!captured.body.contains("\"username\""))
    }

    @Test
    fun scanContractTypesParseAndLegacyTypesAreUnknown() {
        val expected = mapOf(
            "PRODUCTION_ORDER" to com.company.logistics.model.ScanType.PRODUCTION_ORDER,
            "FLOW_NO" to com.company.logistics.model.ScanType.FLOW_NO,
            "MATERIAL_CODE" to com.company.logistics.model.ScanType.MATERIAL_CODE,
            "LOCATION_CODE" to com.company.logistics.model.ScanType.LOCATION_CODE,
        )
        expected.forEach { (wireType, scanType) ->
            assertEquals(scanType, ApiParser.parseScanResolve("""{"type":"$wireType","normalizedValue":"value"}""").type)
        }
        listOf("MACHINE", "ORDER_NO", "LOGISTICS_NO", "FUTURE_TYPE").forEach { wireType ->
            assertEquals(
                com.company.logistics.model.ScanType.UNKNOWN,
                ApiParser.parseScanResolve("""{"type":"$wireType","normalizedValue":"legacy"}""").type,
            )
        }
    }

    @Test
    fun productionOrderScanPreservesServerResourceId() {
        val result = ApiParser.parseScanResolve(
            """{"type":"PRODUCTION_ORDER","normalizedValue":"PO-1","resourceId":"order-1"}"""
        )
        assertEquals(com.company.logistics.model.ScanType.PRODUCTION_ORDER, result.type)
        assertEquals("PO-1", result.normalizedValue)
        assertEquals("order-1", result.resourceId)
    }

    @Test
    fun timelineRejectsPageSizeAboveMemoryBoundBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().handoverTimeline("work-item", pageSize = 50)
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun createHandoverRejectsNonPositiveQuantityBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().createHandover(
                clientOperationId = "operation",
                workItemId = "work-item",
                transferRequestId = "transfer",
                quantity = 0,
                fromLocation = "A-01",
                deviceId = null,
                receiverUserId = null,
            )
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun assemblyTaskPageRejectsNonContractPageSizeBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().assemblyTaskPage(page = 1, pageSize = 50)
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun temporaryTransferRequiresRemarkBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().startTemporaryTransfer(
                taskId = "task-1",
                remark = "",
                clientOperationId = "operation",
            )
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun temporaryTransferStartSendsTaskDeviceAndIdempotencyContract() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureOneRequest(
            """{"id":"tt-1","taskId":"task-1","deviceId":"device-1","laborRecordId":"lr-1","status":"ACTIVE","startedAt":"now"}"""
        ) { port ->
            val previous = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    api.startTemporaryTransfer("task-1", "device-1", "move", operationId)
                }
            } finally { ApiConfig.baseUrl = previous }
        }
        assertEquals("POST /api/v1/assembly/temporary-transfers/start HTTP/1.1", captured.requestLine)
        assertEquals(operationId, captured.headers["Idempotency-Key"])
        assertTrue(captured.body.contains("\"taskId\":\"task-1\""))
        assertTrue(captured.body.contains("\"deviceId\":\"device-1\""))
        assertTrue(captured.body.contains("\"clientOperationId\":\"$operationId\""))
    }

    @Test
    fun machineProgressSupportsOptionalDeviceQueryAndCompleteSnapshotParsing() = runBlocking {
        val transfer = ApiParser.parseTemporaryTransfer(
            """{"labor_record_id":"lr-1","task_id":"task-1","order_no":"WO-1","device_id":"d-1","device_no":"D-1","duration_minutes":12,"ended_at":"done"}"""
        )
        assertEquals("WO-1", transfer.orderNo)
        assertEquals("d-1", transfer.deviceId)
        assertEquals("D-1", transfer.deviceNo)
        assertEquals(12, transfer.durationMinutes)
        assertEquals("done", transfer.endedAt)

        val captured = captureOneRequest("""{"items":[]}""") { port ->
            val previous = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    api.workshopMachineProgress(deviceId = "device 1")
                }
            } finally { ApiConfig.baseUrl = previous }
        }
        assertEquals("GET /api/v1/workshop/machine-progress?page=1&pageSize=20&deviceId=device+1 HTTP/1.1", captured.requestLine)
    }

    @Test
    fun workshopSummaryDoesNotAcceptUndeclaredDateQueryParameters() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().workshopSummary(from = "2026-09-14T00:00:00Z")
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun assemblyStartUsesBackendPathBodyAndStableIdempotencyHeaders() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureOneRequest(
            response = """
                {"id":"lr-1","laborRecordId":"lr-1","type":"ASSEMBLY","status":"ACTIVE","startedAt":"server"}
            """.trimIndent(),
        ) { port ->
            val previousBaseUrl = ApiConfig.baseUrl
            val previousConnectTimeout = ApiConfig.connectTimeoutMs
            val previousReadTimeout = ApiConfig.readTimeoutMs
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                ApiConfig.connectTimeoutMs = 3_000
                ApiConfig.readTimeoutMs = 3_000
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    api.startAssemblyWork("task-1", expectedVersion = 7, clientOperationId = operationId)
                }
            } finally {
                ApiConfig.baseUrl = previousBaseUrl
                ApiConfig.connectTimeoutMs = previousConnectTimeout
                ApiConfig.readTimeoutMs = previousReadTimeout
            }
        }

        assertEquals("POST /api/v1/assembly/tasks/task-1/start HTTP/1.1", captured.requestLine)
        assertEquals("Bearer access-token", captured.headers["Authorization"])
        assertEquals(operationId, captured.headers["Idempotency-Key"])
        UUID.fromString(captured.headers["X-Request-Id"] ?: error("X-Request-Id missing"))
        assertTrue(captured.body.contains("\"clientOperationId\":\"$operationId\""))
        assertTrue(captured.body.contains("\"expectedVersion\":7"))
    }

    @Test
    fun bomCommitUsesStableIdempotencyAndWireFields() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureOneRequest("""{"bomVersionId":"b1","modelCode":"M-1","versionNo":2,"status":"PUBLISHED","itemCount":1}""") { port ->
            val old = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api -> api.updateToken("token"); api.commitBomImport("preview-1", operationId, true) }
            } finally { ApiConfig.baseUrl = old }
        }
        assertEquals("POST /api/v1/boms/import/commit HTTP/1.1", captured.requestLine)
        assertEquals(operationId, captured.headers["Idempotency-Key"])
        assertTrue(captured.body.contains("\"previewId\":\"preview-1\""))
        assertTrue(captured.body.contains("\"publish\":true"))
        assertTrue(captured.body.contains("\"clientOperationId\":\"$operationId\""))
    }

    @Test
    fun orderCreateUsesStableIdempotencyAndRequiredFields() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureOneRequest("""{"orderNo":"WO-1","orderId":"o1","status":"CREATED"}""") { port ->
            val old = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api -> api.updateToken("token"); api.createProductionOrder(operationId, "WO-1", "产品", 3, "2099-12-31", org.json.JSONArray()) }
            } finally { ApiConfig.baseUrl = old }
        }
        assertEquals("POST /api/v1/production-orders HTTP/1.1", captured.requestLine)
        assertEquals(operationId, captured.headers["Idempotency-Key"])
        assertTrue(captured.body.contains("\"plannedQuantity\":3"))
        assertTrue(captured.body.contains("\"plannedDeliveryDate\":\"2099-12-31\""))
    }

    @Test
    fun deviceCreateAndAssignmentUseExpectedRoutesAndVersions() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val device = captureOneRequest("""{"deviceId":"d1","deviceNo":"D-1","status":"ACTIVE"}""") { port ->
            val old = ApiConfig.baseUrl
            try { ApiConfig.baseUrl = "http://127.0.0.1:$port"; MaterialFlowApi().also { api -> api.updateToken("token"); api.createDevice(operationId, "D-1", "机台", "一车间", "M-1") } } finally { ApiConfig.baseUrl = old }
        }
        assertEquals("POST /api/v1/devices HTTP/1.1", device.requestLine)
        assertEquals(operationId, device.headers["Idempotency-Key"])
        assertTrue(device.body.contains("\"workshop\":\"一车间\""))

        val binding = captureOneRequest("""{"taskId":"t1","orderNo":"WO-1","modelCode":"M-1","deviceId":"d1","expectedVersion":2}""") { port ->
            val old = ApiConfig.baseUrl
            try { ApiConfig.baseUrl = "http://127.0.0.1:$port"; MaterialFlowApi().also { api -> api.updateToken("token"); api.assignDevice("WO-1", "M-1", operationId, "d1", 2) } } finally { ApiConfig.baseUrl = old }
        }
        assertEquals("POST /api/v1/production-orders/WO-1/models/M-1/assign-device HTTP/1.1", binding.requestLine)
        assertEquals(operationId, binding.headers["Idempotency-Key"])
        assertTrue(binding.body.contains("\"expectedVersion\":2"))
    }

    @Test
    fun realUnauthorizedWritePreservesStatusAuthorizationAndPath() = runBlocking {
        val captured = captureOneRequest(
            response = """{"code":"TOKEN_EXPIRED","message":"expired"}""",
            status = 401,
        ) { port ->
            val old = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("expired-token")
                    val error = runCatching {
                        api.createProductionOrder("11111111-1111-1111-1111-111111111111", "WO-1", "产品", 1, "2099-12-31", org.json.JSONArray())
                    }.exceptionOrNull()
                    assertTrue(error is ApiException)
                    assertEquals(401, (error as ApiException).statusCode)
                    assertTrue(error.isUnauthorized)
                }
            } finally { ApiConfig.baseUrl = old }
        }
        assertEquals("POST /api/v1/production-orders HTTP/1.1", captured.requestLine)
        assertEquals("Bearer expired-token", captured.headers["Authorization"])
    }

    @Test
    fun realConflictBindingPreservesStatusAndConflictFlag() = runBlocking {
        val captured = captureOneRequest(
            response = """{"code":"CONFLICT","message":"version conflict"}""",
            status = 409,
        ) { port ->
            val old = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("token")
                    val error = runCatching {
                        api.assignDevice("WO-1", "M-1", "11111111-1111-1111-1111-111111111111", "d1", 2)
                    }.exceptionOrNull()
                    assertTrue(error is ApiException)
                    assertEquals(409, (error as ApiException).statusCode)
                    assertTrue(error.isConflict)
                }
            } finally { ApiConfig.baseUrl = old }
        }
        assertEquals("POST /api/v1/production-orders/WO-1/models/M-1/assign-device HTTP/1.1", captured.requestLine)
    }

    @Test
    fun repeatedOrderCreateKeepsOperationIdHeadersAndBodyStable() = runBlocking {
        val operationId = "11111111-1111-1111-1111-111111111111"
        val captured = captureRequests(2, """{"orderNo":"WO-1","orderId":"o1","status":"CREATED"}""") { port ->
            val old = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("token")
                    repeat(2) { api.createProductionOrder(operationId, "WO-1", "产品", 1, "2099-12-31", org.json.JSONArray()) }
                }
            } finally { ApiConfig.baseUrl = old }
        }
        assertEquals(2, captured.size)
        captured.forEach { request ->
            assertEquals(operationId, request.headers["Idempotency-Key"])
            assertTrue(request.body.contains("\"clientOperationId\":\"$operationId\""))
            UUID.fromString(request.headers["X-Request-Id"] ?: error("X-Request-Id missing"))
        }
    }

    @Test
    fun readTimeoutUsesConfiguredTimeoutAndFinishesPromptly() = runBlocking {
        val oldBaseUrl = ApiConfig.baseUrl
        val oldReadTimeout = ApiConfig.readTimeoutMs
        ServerSocket(0).use { server ->
            val worker = Thread {
                server.accept().use { socket ->
                    readRequest(socket)
                    Thread.sleep(1_000)
                }
            }.apply { isDaemon = true }
            worker.start()
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:${server.localPort}"
                ApiConfig.readTimeoutMs = 150
                val elapsed = measureTimeMillis {
                    val error = runCatching {
                        MaterialFlowApi().also { it.updateToken("token") }
                            .createDevice("11111111-1111-1111-1111-111111111111", "D-1", "机台", "车间", null)
                    }.exceptionOrNull()
                    assertTrue(error != null)
                }
                assertTrue("read timeout took ${elapsed}ms", elapsed < 2_000)
            } finally {
                ApiConfig.baseUrl = oldBaseUrl
                ApiConfig.readTimeoutMs = oldReadTimeout
            }
        }
    }

    @Test
    fun apiErrorsPreserveUnauthorizedConflictAndNetworkFailure() = runBlocking {
        val unauthorized = ApiParser.parseError(401, "{\"code\":\"TOKEN_EXPIRED\",\"message\":\"expired\"}")
        assertEquals(401, unauthorized.statusCode)
        assertEquals("TOKEN_EXPIRED", unauthorized.code)
        val conflict = ApiParser.parseError(409, "{\"code\":\"CONFLICT\",\"message\":\"version conflict\",\"retryable\":false}")
        assertEquals(409, conflict.statusCode)
        assertTrue(!conflict.retryable)
        listOf(403, 404, 413, 415).forEach { status ->
            assertEquals(status, ApiParser.parseError(status, null).statusCode)
        }
        val old = ApiConfig.baseUrl
        try {
            ApiConfig.baseUrl = "http://127.0.0.1:1"
            assertTrue(runCatching { MaterialFlowApi().also { it.updateToken("token") }.createDevice("11111111-1111-1111-1111-111111111111", "D-1", "机台", "一车间", null) }.isFailure)
        } finally { ApiConfig.baseUrl = old }
    }

    @Test
    fun modelDownloadStreamsAuthorizedBytesAndReportsProgress() = runBlocking {
        val meta = AssemblyModelMeta("id", "M-1", "Model", 3, "glb", 9, "a".repeat(64), "/content")
        val output = ByteArrayOutputStream()
        var progress = 0L
        val captured = captureOneRequest("glb-bytes") { port ->
            val previous = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    val received = api.downloadAssemblyModelContent(meta, output) { progress = it }
                    assertEquals(9, received)
                }
            } finally { ApiConfig.baseUrl = previous }
        }
        assertEquals("GET /api/v1/assembly-models/M-1/versions/3/content HTTP/1.1", captured.requestLine)
        assertEquals("Bearer access-token", captured.headers["Authorization"])
        assertEquals("glb-bytes", output.toString(Charsets.UTF_8.name()))
        assertEquals(9, progress)
    }

    private data class CapturedRequest(
        val requestLine: String,
        val headers: Map<String, String>,
        val body: String,
    )

    private suspend fun captureOneRequest(
        response: String,
        status: Int = 200,
        call: suspend (Int) -> Unit,
    ): CapturedRequest = captureRequests(1, response, status, call).single()

    private suspend fun captureRequests(
        count: Int,
        response: String,
        status: Int = 200,
        call: suspend (Int) -> Unit,
    ): List<CapturedRequest> {
        ServerSocket(0).use { server ->
            val captured = mutableListOf<CapturedRequest>()
            val worker = Thread {
                repeat(count) {
                    server.accept().use { socket ->
                        captured += readRequest(socket)
                        val bytes = response.toByteArray(Charsets.UTF_8)
                        val output = socket.getOutputStream()
                        output.bufferedWriter().apply {
                            write("HTTP/1.1 $status ${if (status == 200) "OK" else "ERROR"}\r\n")
                            write("Content-Type: application/json\r\n")
                            write("Content-Length: ${bytes.size}\r\n")
                            write("Connection: close\r\n\r\n")
                            flush()
                        }
                        output.write(bytes)
                        output.flush()
                    }
                }
            }
            worker.start()
            call(server.localPort)
            worker.join(3_000)
            return captured.toList().also { requests ->
                check(requests.size == count) { "Expected $count HTTP requests, captured ${requests.size}" }
            }
        }
    }

    @Test
    fun setupStatusParsesContractFields() {
        val status = ApiParser.parseSetupStatus("""{"initialized":false,"adminUsername":"owlco","mustChangePassword":true,"serverTime":"now"}""")
        assertEquals(false, status.initialized)
        assertEquals("owlco", status.adminUsername)
        assertTrue(status.mustChangePassword)
    }

    @Test
    fun initializeAdminRejectsShortOrMismatchedPasswordsBeforeNetworkAccess() = runBlocking {
        assertTrue(runCatching { MaterialFlowApi().initializeAdmin("short", "short", UUID.randomUUID().toString()) }.exceptionOrNull() is IllegalArgumentException)
        assertTrue(runCatching { MaterialFlowApi().initializeAdmin("long-enough", "different", UUID.randomUUID().toString()) }.exceptionOrNull() is IllegalArgumentException)
    }

    @Test
    fun bomPreviewRejectsBlankModelCodeBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().previewBomImport("bom.csv", "a,b\n".toByteArray(), " ")
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
        assertTrue(error?.message?.contains("modelCode") == true)
    }

    @Test
    fun bomPreviewMultipartUsesRealCrLfAndAcceptsExactly10MiB() = runBlocking {
        val fileBytes = ByteArray(MaterialFlowApi.MAX_BOM_FILE_BYTES) { 'x'.code.toByte() }
        val captured = captureOneRequest(
            """{"previewId":"p-1","modelCode":"M-1","totalRows":1,"validRows":1,"invalidRows":0,"canCommit":true,"errors":[]}"""
        ) { port ->
            val previous = ApiConfig.baseUrl
            try {
                ApiConfig.baseUrl = "http://127.0.0.1:$port"
                MaterialFlowApi().also { api ->
                    api.updateToken("access-token")
                    api.previewBomImport("bom.csv", fileBytes, "M-1")
                }
            } finally { ApiConfig.baseUrl = previous }
        }

        assertTrue(captured.body.contains("\\r\\n") == false)
        assertTrue(captured.body.contains("\r\nContent-Disposition: form-data; name=\"modelCode\"\r\n\r\nM-1\r\n"))
        assertTrue(captured.body.contains("\r\nContent-Disposition: form-data; name=\"file\"; filename=\"bom.csv\"\r\n"))
        assertTrue(captured.body.endsWith("\r\n"))
    }

    private fun readRequest(socket: Socket): CapturedRequest {
        val connection = socket.getInputStream()
        val input = java.io.BufferedInputStream(connection)
        fun readLine(): String {
            val bytes = java.io.ByteArrayOutputStream()
            while (true) { val b = input.read(); if (b < 0 || b == '\n'.code) break; if (b != '\r'.code) bytes.write(b) }
            return bytes.toString(Charsets.UTF_8.name())
        }
        val requestLine = readLine()
        val headers = buildMap {
            while (true) {
                val line = readLine()
                if (line.isEmpty()) break
                val separator = line.indexOf(':')
                if (separator > 0) put(line.substring(0, separator), line.substring(separator + 1).trim())
            }
        }
        val length = headers["Content-Length"]?.toIntOrNull() ?: 0
        val body = ByteArray(length)
        var offset = 0
        while (offset < length) { val read = input.read(body, offset, length - offset); if (read < 0) break; offset += read }
        return CapturedRequest(requestLine, headers, String(body, 0, offset, Charsets.UTF_8))
    }

}
