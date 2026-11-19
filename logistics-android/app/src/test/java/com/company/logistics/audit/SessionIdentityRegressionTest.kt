package com.company.logistics.audit

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Session isolation invariants exercised through MaterialFlowApi's public surface and a loopback server.
 * No production credentials or non-loopback services are used.
 */
class SessionIdentityRegressionTest {
    @Test
    fun olderLoginFinishingLastCannotReplaceTheNewerLoginSession() = runBlocking {
        val oldLoginArrived = CountDownLatch(1)
        val releaseOldLogin = CountDownLatch(1)
        withServer({ request, _ ->
            assertEquals("/api/v1/auth/login", request.path)
            if (request.body.contains("\"employeeNo\":\"A\"")) {
                oldLoginArrived.countDown()
                check(releaseOldLogin.await(5, TimeUnit.SECONDS))
                respond(200, loginJson("access-A", "refresh-A", "A"))
            } else {
                respond(200, loginJson("access-B", "refresh-B", "B"))
            }
        }) { server ->
            val api = MaterialFlowApi()
            val olderLogin = async(Dispatchers.IO) { api.login("A", "synthetic", "device-A", "test") }
            await(oldLoginArrived)

            val newer = api.login("B", "synthetic", "device-B", "test")
            assertEquals("B", newer.user.id)
            assertEquals("access-B", api.accessToken)
            assertEquals("refresh-B", api.refreshToken)

            releaseOldLogin.countDown()
            assertEquals("A", withTimeout(5_000) { olderLogin.await() }.user.id)
            assertEquals("access-B", api.accessToken)
            assertEquals("refresh-B", api.refreshToken)
            assertEquals("device-B", api.deviceId)
            assertEquals(2, server.requests.size)
        }
    }

    @Test
    fun writeStartedAsOldIdentityDoesNotRefreshOrReplayWithNewIdentity() = runBlocking {
        val oldWriteArrived = CountDownLatch(1)
        val releaseOldWrite = CountDownLatch(1)
        withServer({ request, _ ->
            assertEquals("/api/v1/stocktakes", request.path)
            oldWriteArrived.countDown()
            check(releaseOldWrite.await(5, TimeUnit.SECONDS))
            respond(401, UNAUTHORIZED)
        }) { server ->
            val api = MaterialFlowApi().also { it.restoreSession("access-A", "refresh-A", "device-A") }
            val operationId = UUID.randomUUID().toString()
            val oldWrite = async(Dispatchers.IO) {
                runCatching { api.createStocktake(operationId, "SYNTHETIC-MATERIAL", null, 7) }
            }
            await(oldWriteArrived)
            api.restoreSession("access-B", "refresh-B", "device-B")
            releaseOldWrite.countDown()

            assertTrue("the stale operation must not succeed under another identity", withTimeout(5_000) { oldWrite.await() }.exceptionOrNull() != null)
            assertEquals("access-B", api.accessToken)
            assertEquals("refresh-B", api.refreshToken)
            assertEquals("device-B", api.deviceId)
            assertEquals("no refresh or replay may be issued for the old operation", 1, server.requests.size)
            assertFalse(server.requests.any { it.path == "/api/v1/auth/refresh" })
        }
    }

    @Test
    fun inFlightOldRefresh200CannotOverwriteNewSessionOrReplayOldWrite() = runBlocking {
        assertOldRefreshCannotMutateNewIdentity(refreshStatus = 200)
    }

    @Test
    fun inFlightOldRefresh4xxCannotClearNewSession() = runBlocking {
        assertOldRefreshCannotMutateNewIdentity(refreshStatus = 401)
    }

    @Test
    fun concurrentRefreshesForOneIdentityShareOneRefreshAndReplayWithRotatedToken() = runBlocking {
        val firstRequestsArrived = CountDownLatch(2)
        val refreshArrived = CountDownLatch(1)
        val refreshCount = AtomicInteger()
        withServer({ request, _ ->
            when (request.path) {
                "/api/v1/auth/refresh" -> {
                    refreshCount.incrementAndGet()
                    refreshArrived.countDown()
                    respond(200, """{"accessToken":"access-A2","refreshToken":"refresh-A2"}""")
                }
                "/api/v1/stocktakes" -> {
                    if (request.authorization == "Bearer access-A") {
                        firstRequestsArrived.countDown()
                        check(firstRequestsArrived.await(5, TimeUnit.SECONDS))
                        respond(401, UNAUTHORIZED)
                    } else {
                        assertEquals("Bearer access-A2", request.authorization)
                        respond(200, """{"stocktakeId":"synthetic-created"}""")
                    }
                }
                else -> error("Unexpected loopback request: ${request.path}")
            }
        }) { server ->
            val api = MaterialFlowApi().also { it.restoreSession("access-A", "refresh-A", "device-A") }
            val writes = (1..2).map {
                async(Dispatchers.IO) {
                    api.createStocktake(UUID.randomUUID().toString(), "SYNTHETIC-MATERIAL-$it", null, it)
                }
            }

            await(refreshArrived)
            assertEquals(listOf("synthetic-created", "synthetic-created"), withTimeout(8_000) { writes.map { it.await() } })
            assertEquals(1, refreshCount.get())
            assertEquals(1, server.requests.count { it.path == "/api/v1/auth/refresh" })
            assertEquals(4, server.requests.count { it.path == "/api/v1/stocktakes" })
            assertEquals("access-A2", api.accessToken)
            assertEquals("refresh-A2", api.refreshToken)
            assertTrue(server.requests.filter { it.path == "/api/v1/stocktakes" }.takeLast(2)
                .all { it.authorization == "Bearer access-A2" })
        }
    }

    private suspend fun assertOldRefreshCannotMutateNewIdentity(refreshStatus: Int) {
        val firstWriteArrived = CountDownLatch(1)
        val releaseUnauthorized = CountDownLatch(1)
        val refreshArrived = CountDownLatch(1)
        val releaseRefresh = CountDownLatch(1)
        withServer({ request, _ ->
            when (request.path) {
                "/api/v1/stocktakes" -> {
                    firstWriteArrived.countDown()
                    check(releaseUnauthorized.await(5, TimeUnit.SECONDS))
                    respond(401, UNAUTHORIZED)
                }
                "/api/v1/auth/refresh" -> {
                    refreshArrived.countDown()
                    check(releaseRefresh.await(5, TimeUnit.SECONDS))
                    if (refreshStatus == 200) {
                        assertTrue(request.body.contains("refresh-A"))
                        respond(200, """{"accessToken":"access-A2","refreshToken":"refresh-A2"}""")
                    } else {
                        respond(401, UNAUTHORIZED)
                    }
                }
                else -> error("Unexpected loopback request: ${request.path}")
            }
        }) { server ->
            coroutineScope {
                val api = MaterialFlowApi().also { it.restoreSession("access-A", "refresh-A", "device-A") }
                val write = async(Dispatchers.IO) {
                    runCatching { api.createStocktake(UUID.randomUUID().toString(), "SYNTHETIC-MATERIAL", null, 1) }
                }
                await(firstWriteArrived)
                releaseUnauthorized.countDown()
                await(refreshArrived)

                api.restoreSession("access-B", "refresh-B", "device-B")
                releaseRefresh.countDown()

                assertTrue("old call should fail rather than be replayed as B", withTimeout(5_000) { write.await() }.isFailure)
                assertEquals("access-B", api.accessToken)
                assertEquals("refresh-B", api.refreshToken)
                assertEquals("device-B", api.deviceId)
                assertEquals(1, server.requests.count { it.path == "/api/v1/stocktakes" })
            }
        }
    }

    @Test
    fun multipartPreviewCannotReturnAnOldIdentityResult() = runBlocking {
        val arrived = CountDownLatch(1)
        val release = CountDownLatch(1)
        withServer({ _, _ ->
            arrived.countDown()
            check(release.await(5, TimeUnit.SECONDS))
            respond(200, """{"previewId":"old-A-preview","modelCode":"SYNTHETIC","totalRows":1,"validRows":1,"invalidRows":0,"canCommit":true}""")
        }) { _ ->
            coroutineScope {
                val api = MaterialFlowApi().also { it.restoreSession("access-A", "refresh-A", "device-A") }
                val old = async(Dispatchers.IO) { runCatching { api.previewBomImport("synthetic.csv", "code,quantity\\nM,1".toByteArray(), "SYNTHETIC") } }
                await(arrived)
                api.restoreSession("access-B", "refresh-B", "device-B")
                release.countDown()
                assertTrue(withTimeout(5_000) { old.await() }.isFailure)
                assertEquals("access-B", api.accessToken)
            }
        }
    }

    @Test
    fun authorizedByteFetchCannotReturnAnOldIdentityResult() = runBlocking {
        val arrived = CountDownLatch(1)
        val release = CountDownLatch(1)
        withServer({ _, _ ->
            arrived.countDown()
            check(release.await(5, TimeUnit.SECONDS))
            respond(200, "synthetic-old-A-bytes")
        }) { _ ->
            coroutineScope {
                val api = MaterialFlowApi().also { it.restoreSession("access-A", "refresh-A", "device-A") }
                val old = async(Dispatchers.IO) { runCatching { api.fetchAuthorizedBytes("/synthetic-content") } }
                await(arrived)
                api.restoreSession("access-B", "refresh-B", "device-B")
                release.countDown()
                assertTrue(withTimeout(5_000) { old.await() }.isFailure)
                assertEquals("access-B", api.accessToken)
            }
        }
    }

    @Test
    fun oldIdentityCancellationIsNotConvertedIntoANewOfflineOperation() = runBlocking {
        val arrived = CountDownLatch(1)
        val release = CountDownLatch(1)
        withServer({ _, _ ->
            arrived.countDown()
            check(release.await(5, TimeUnit.SECONDS))
            respond(401, UNAUTHORIZED)
        }) { _ ->
            coroutineScope {
                val api = MaterialFlowApi().also { it.restoreSession("access-A", "refresh-A", "device-A") }
                val dao = object : com.company.logistics.data.OfflineOperationDao by NoOpDao() {
                    var inserted = 0
                    override suspend fun insert(operation: com.company.logistics.data.OfflineOperationEntity): Long {
                        inserted++
                        return 1L
                    }
                }
                val repository = LogisticsRepository(api, dao)
                val old = async(Dispatchers.IO) { runCatching { repository.submitStocktakeQueued("SYNTHETIC", null, 1) } }
                await(arrived)
                api.restoreSession("access-B", "refresh-B", "device-B")
                release.countDown()
                val result = withTimeout(5_000) { old.await() }
                assertTrue(result.exceptionOrNull() is kotlinx.coroutines.CancellationException)
                assertEquals(0, dao.inserted)
            }
        }
    }

    private suspend fun await(latch: CountDownLatch) {
        assertTrue("timed out waiting for loopback request", withContext(Dispatchers.IO) {
            latch.await(5, TimeUnit.SECONDS)
        })
    }

    private suspend fun withServer(
        handler: (CapturedRequest, Int) -> Unit,
        block: suspend (LoopbackServer) -> Unit,
    ) {
        val oldBaseUrl = ApiConfig.baseUrl
        val oldConnectTimeout = ApiConfig.connectTimeoutMs
        val oldReadTimeout = ApiConfig.readTimeoutMs
        val server = LoopbackServer(handler)
        try {
            ApiConfig.baseUrl = server.url
            ApiConfig.connectTimeoutMs = 5_000
            ApiConfig.readTimeoutMs = 10_000
            block(server)
        } finally {
            server.close()
            ApiConfig.baseUrl = oldBaseUrl
            ApiConfig.connectTimeoutMs = oldConnectTimeout
            ApiConfig.readTimeoutMs = oldReadTimeout
        }
    }

    private class NoOpDao : OfflineOperationDao {
        override fun observeAll(): Flow<List<OfflineOperationEntity>> = emptyFlow()
        override suspend fun pending(): List<OfflineOperationEntity> = emptyList()
        override fun observePendingCount(): Flow<Int> = emptyFlow()
        override fun observeAttentionCount(): Flow<Int> = emptyFlow()
        override suspend fun insert(operation: OfflineOperationEntity): Long = 1L
        override suspend fun updateStatus(id: String, status: String, error: String?) = Unit
        override suspend fun markSynced(id: String, status: String, serverTime: String?) = Unit
        override suspend fun clearSynced() = Unit
        override suspend fun findById(id: String): OfflineOperationEntity? = null
        override suspend fun resetStuckSyncing(): Int = 0
        override suspend fun resetToPending(id: String) = Unit
        override suspend fun deleteById(id: String) = Unit
    }

    private data class CapturedRequest(val path: String, val authorization: String?, val body: String)

    private class LoopbackServer(private val handler: (CapturedRequest, Int) -> Unit) : AutoCloseable {
        private val socket = ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"))
        val url: String = "http://127.0.0.1:${socket.localPort}"
        val requests = CopyOnWriteArrayList<CapturedRequest>()
        private val sequence = AtomicInteger()
        private val acceptThread = Thread {
            while (!socket.isClosed) {
                val client = try { socket.accept() } catch (_: Exception) { return@Thread }
                Thread({
                    client.use { connection ->
                        connection.soTimeout = 10_000
                        val request = readRequest(connection)
                        val ordinal = sequence.incrementAndGet()
                        requests += request
                        handler(request, ordinal)
                    }
                }, "session-regression-http-$sequence").apply { isDaemon = true; start() }
            }
        }.apply { isDaemon = true; start() }

        override fun close() {
            socket.close()
            acceptThread.join(1_000)
        }
    }

    companion object {
        private const val UNAUTHORIZED = """{"error":{"code":"UNAUTHORIZED","message":"synthetic expired","retryable":false}}"""

        private fun loginJson(access: String, refresh: String, user: String) =
            """{"accessToken":"$access","refreshToken":"$refresh","user":{"id":"$user","username":"$user","displayName":"$user","role":"ADMIN"}}"""

        private fun respond(code: Int, body: String) {
            val label = when (code) { 200 -> "OK"; 401 -> "Unauthorized"; else -> "Error" }
            val bytes = body.toByteArray(Charsets.UTF_8)
            val output = currentSocket.get()?.getOutputStream()
                ?: throw IllegalStateException("No loopback response socket")
            output.write("HTTP/1.1 $code $label\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ${bytes.size}\r\n\r\n".toByteArray(Charsets.US_ASCII))
            output.write(bytes)
            output.flush()
        }

        private val currentSocket = ThreadLocal<Socket>()

        private fun readRequest(client: Socket): CapturedRequest {
            currentSocket.set(client)
            val input = client.getInputStream().buffered()
            fun line(): String {
                val bytes = ByteArrayOutputStream()
                while (true) {
                    val value = input.read()
                    if (value < 0 || value == '\n'.code) break
                    if (value != '\r'.code) bytes.write(value)
                }
                return bytes.toString(Charsets.UTF_8.name())
            }
            val path = line().split(' ').getOrElse(1) { "" }
            var contentLength = 0
            var authorization: String? = null
            while (true) {
                val header = line()
                if (header.isEmpty()) break
                if (header.startsWith("Content-Length:", ignoreCase = true)) {
                    contentLength = header.substringAfter(':').trim().toIntOrNull() ?: 0
                }
                if (header.startsWith("Authorization:", ignoreCase = true)) {
                    authorization = header.substringAfter(':').trim()
                }
            }
            val bodyBytes = ByteArray(contentLength)
            var offset = 0
            while (offset < contentLength) {
                val read = input.read(bodyBytes, offset, contentLength - offset)
                check(read > 0) { "Unexpected EOF in loopback request body" }
                offset += read
            }
            return CapturedRequest(path, authorization, bodyBytes.toString(Charsets.UTF_8))
        }
    }
}
