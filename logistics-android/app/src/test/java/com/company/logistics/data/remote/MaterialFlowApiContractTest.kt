package com.company.logistics.data.remote

import kotlinx.coroutines.runBlocking
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID
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
            MaterialFlowApi().createException("WO-1", "", "mat-1", "OTHER", 1, 0)
        }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
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
    fun scanMachineTypeIsPreserved() {
        assertEquals(com.company.logistics.model.ScanType.MACHINE, ApiParser.parseScanResolve("""{"type":"MACHINE","normalizedValue":"M-01","resourceId":"d1"}""").type)
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

    private data class CapturedRequest(
        val requestLine: String,
        val headers: Map<String, String>,
        val body: String,
    )

    private suspend fun captureOneRequest(
        response: String,
        call: suspend (Int) -> Unit,
    ): CapturedRequest {
        ServerSocket(0).use { server ->
            var captured: CapturedRequest? = null
            val worker = Thread {
                server.accept().use { socket ->
                    captured = readRequest(socket)
                    val bytes = response.toByteArray(Charsets.UTF_8)
                    val output = socket.getOutputStream()
                    output.bufferedWriter().apply {
                        write("HTTP/1.1 200 OK\r\n")
                        write("Content-Type: application/json\r\n")
                        write("Content-Length: ${bytes.size}\r\n")
                        write("Connection: close\r\n\r\n")
                        flush()
                    }
                    output.write(bytes)
                    output.flush()
                }
            }
            worker.start()
            call(server.localPort)
            worker.join(3_000)
            return captured ?: error("HTTP request was not captured")
        }
    }

    private fun readRequest(socket: Socket): CapturedRequest {
        val input = socket.getInputStream().bufferedReader()
        val requestLine = input.readLine()
        val headers = buildMap {
            while (true) {
                val line = input.readLine()
                if (line.isNullOrEmpty()) break
                val separator = line.indexOf(':')
                if (separator > 0) put(line.substring(0, separator), line.substring(separator + 1).trim())
            }
        }
        val length = headers["Content-Length"]?.toIntOrNull() ?: 0
        val body = CharArray(length)
        var offset = 0
        while (offset < length) {
            val read = input.read(body, offset, length - offset)
            if (read < 0) break
            offset += read
        }
        return CapturedRequest(requestLine.orEmpty(), headers, String(body, 0, offset))
    }

}
