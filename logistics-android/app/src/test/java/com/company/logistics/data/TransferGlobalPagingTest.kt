package com.company.logistics.data

import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.flow.emptyFlow
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Public global API/Repository behavior against synthetic HTTP on loopback only. */
class TransferGlobalPagingTest {
    @Test fun normalFiveThousandAndOneHundredOneRowsCompleteInFiftyTwoRequests() = runBlocking {
        withServer({ request -> Reply(body = page(request.page, 5_101)) }) { server, api ->
            val result = LogisticsRepository(api, NoOpDao()).transferRequests().getOrThrow()
            assertEquals(5_101, result.items.size)
            assertEquals((1..5_101).map { "r-$it" }, result.items.map { it.id })
            assertEquals(52, server.requests.size)
        }
    }

    @Test fun repeatedPageNumberFailsInsteadOfReturningPartialList() = runBlocking {
        withServer({ Reply(body = page(1, 201)) }) { _, api ->
            assertTrue(runCatching { api.listTransferRequests() }.isFailure)
        }
    }

    @Test fun repeatedIdsWithAdvancingPagesFailInsteadOfLooping() = runBlocking {
        withServer({ request ->
            Reply(body = page(request.page, 201, ids = (1..100).toList()))
        }) { _, api -> assertTrue(runCatching { api.listTransferRequests() }.isFailure) }
    }

    @Test fun changedCountInvalidatesWholeGlobalRead() = runBlocking {
        withServer({ request -> Reply(body = page(request.page, if (request.page == 1) 101 else 102)) }) { _, api ->
            assertTrue(LogisticsRepository(api, NoOpDao()).transferRequests().isFailure)
        }
    }

    @Test fun shortPageWithClaimedRemainingPagesAndNoTotalFails() = runBlocking {
        withServer({ Reply(body = JSONObject(page(1, 1)).apply {
            remove("total"); put("totalPages", 2)
        }.toString()) }) { _, api -> assertTrue(runCatching { api.listTransferRequests() }.isFailure) }
    }

    @Test fun changingTotalPagesFailsEvenWhenTotalIsStable() = runBlocking {
        withServer({ request -> Reply(body = JSONObject(page(request.page, 201)).apply {
            put("totalPages", if (request.page == 2) 4 else 3)
        }.toString()) }) { _, api -> assertTrue(runCatching { api.listTransferRequests() }.isFailure) }
    }

    @Test fun malformedMetadataCannotBeSilentlyIgnored() = runBlocking {
        withServer({ Reply(body = JSONObject(page(1, 1)).put("total", "invalid").toString()) }) { _, api ->
            assertTrue(runCatching { api.listTransferRequests() }.isFailure)
        }
    }

    @Test fun failedLaterPageDoesNotReturnEarlierRows() = runBlocking {
        withServer({ request ->
            if (request.page == 1) Reply(body = page(1, 101))
            else Reply(500, """{"error":{"code":"UNAVAILABLE","message":"synthetic","retryable":true}}""")
        }) { _, api -> assertTrue(LogisticsRepository(api, NoOpDao()).transferRequests().isFailure) }
    }

    @Test fun legacyShortResponseRemainsSupported() = runBlocking {
        withServer({ Reply(body = legacy(1)) }) { server, api ->
            assertEquals(listOf("r-1"), api.listTransferRequests().items.map { it.id })
            assertEquals(1, server.requests.size)
        }
    }

    @Test fun legacyFullResponseWithoutPageMetadataCannotProveCompleteness() = runBlocking {
        withServer({ Reply(body = legacy(100)) }) { _, api ->
            assertTrue(runCatching { api.listTransferRequests() }.isFailure)
        }
    }

    @Test fun statusIsEncodedOnEveryPageAndReturnedWithServerTimeAndOrder() = runBlocking {
        val status = "PENDING &+/中文"
        withServer({ request -> Reply(body = JSONObject(page(request.page, 101)).apply {
            put("serverTime", "time-${request.page}")
        }.toString()) }) { server, api ->
            val result = api.listTransferRequests(status)
            assertEquals(status, result.statusFilter)
            assertEquals("time-2", result.serverTime)
            assertEquals((1..101).map { "r-$it" }, result.items.map { it.id })
            assertTrue(server.requests.all { it.values("status") == listOf(status) })
            assertTrue(server.requests.all { it.values("documentNo").isEmpty() && it.values("pageSize") == listOf("100") })
        }
    }

    @Test fun switchedIdentityCancelsGlobalReadAndNeverPublishesEarlierRows() = runBlocking {
        lateinit var activeApi: MaterialFlowApi
        withServer({ request ->
            if (request.page == 2) activeApi.restoreSession("other", "other-refresh", "other-device")
            Reply(body = page(request.page, 201))
        }) { server, api ->
            activeApi = api
            val error = runCatching { LogisticsRepository(api, NoOpDao()).transferRequests() }.exceptionOrNull()
            assertTrue(error is CancellationException)
            assertEquals(2, server.requests.size)
            assertTrue(server.requests.all { it.authorization == "Bearer synthetic" })
            assertEquals("other", api.accessToken)
        }
    }

    @Test fun callerCancellationIsRethrownByRepository() = runBlocking {
        val arrived = CompletableDeferred<Unit>()
        val release = CountDownLatch(1)
        try {
            withServer({
                arrived.complete(Unit)
                check(release.await(5, TimeUnit.SECONDS))
                Reply(body = page(1, 0))
            }) { _, api ->
                val read = async(Dispatchers.IO) { LogisticsRepository(api, NoOpDao()).transferRequests() }
                withTimeout(5_000) { arrived.await() }
                read.cancel()
                release.countDown()
                assertTrue(runCatching { withTimeout(5_000) { read.await() } }.exceptionOrNull() is CancellationException)
            }
        } finally { release.countDown() }
    }

    private data class Captured(val target: String, val authorization: String?) {
        fun values(key: String): List<String> = target.substringAfter('?', "").split('&').mapNotNull {
            val pieces = it.split('=', limit = 2)
            if (pieces[0] != key) null else URLDecoder.decode(pieces.getOrElse(1) { "" }, "UTF-8")
        }
        val page: Int get() = values("page").single().toInt()
    }
    private data class Reply(val code: Int = 200, val body: String)

    private class Server(private val handler: (Captured) -> Reply) : AutoCloseable {
        private val listener = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        val url = "http://127.0.0.1:${listener.localPort}"
        val requests = CopyOnWriteArrayList<Captured>()
        private val failures = CopyOnWriteArrayList<Throwable>()
        @Volatile private var activeSocket: Socket? = null
        private val worker = Thread {
            while (!listener.isClosed) {
                val socket = try { listener.accept() } catch (error: Exception) {
                    if (!listener.isClosed) failures += error
                    break
                }
                activeSocket = socket
                try {
                    socket.use { connection ->
                        connection.soTimeout = 5_000
                        val input = connection.getInputStream().bufferedReader()
                        val target = input.readLine().split(' ')[1]
                        var authorization: String? = null
                        while (true) {
                            val header = input.readLine()
                            if (header.isNullOrEmpty()) break
                            if (header.startsWith("Authorization:", ignoreCase = true)) authorization = header.substringAfter(':').trim()
                        }
                        val request = Captured(target, authorization)
                        requests += request
                        val reply = handler(request)
                        val bytes = reply.body.toByteArray(Charsets.UTF_8)
                        connection.getOutputStream().apply {
                            write("HTTP/1.1 ${reply.code} Response\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ${bytes.size}\r\n\r\n".toByteArray(Charsets.US_ASCII))
                            write(bytes); flush()
                        }
                    }
                } catch (error: Throwable) { failures += error }
                finally { activeSocket = null }
            }
        }.apply { isDaemon = true; start() }
        override fun close() {
            listener.close()
            worker.join(1_000)
            if (worker.isAlive) { activeSocket?.close(); worker.join(5_000) }
            assertFalse("loopback worker did not exit", worker.isAlive)
            assertTrue("loopback failures: $failures", failures.isEmpty())
        }
    }

    private suspend fun withServer(handler: (Captured) -> Reply, block: suspend (Server, MaterialFlowApi) -> Unit) {
        val previous = ApiConfig.baseUrl
        Server(handler).use { server ->
            try {
                ApiConfig.baseUrl = server.url
                block(server, MaterialFlowApi().also { it.restoreSession("synthetic", "refresh", "device") })
            } finally { ApiConfig.baseUrl = previous }
        }
    }

    private class NoOpDao : OfflineOperationDao {
        override fun observeAll() = emptyFlow<List<OfflineOperationEntity>>()
        override suspend fun pending(): List<OfflineOperationEntity> = emptyList()
        override fun observePendingCount() = emptyFlow<Int>()
        override fun observeAttentionCount() = emptyFlow<Int>()
        override suspend fun insert(operation: OfflineOperationEntity) = 1L
        override suspend fun updateStatus(id: String, status: String, error: String?) = Unit
        override suspend fun markSynced(id: String, status: String, serverTime: String?) = Unit
        override suspend fun clearSynced() = Unit
        override suspend fun findById(id: String): OfflineOperationEntity? = null
        override suspend fun resetStuckSyncing() = 0
        override suspend fun resetToPending(id: String) = Unit
        override suspend fun deleteById(id: String) = Unit
    }

    private companion object {
        fun legacy(size: Int): String = JSONObject(page(1, size)).apply {
            remove("page"); remove("pageSize"); remove("total")
        }.toString()

        fun page(number: Int, total: Int, ids: List<Int> =
            ((number - 1) * 100 + 1..minOf(number * 100, total)).toList()): String = JSONObject().apply {
            put("page", number); put("pageSize", 100); put("total", total)
            put("items", JSONArray(ids.map { id -> JSONObject().apply {
                put("id", "r-$id"); put("documentNo", "WO"); put("status", "PENDING"); put("type", "INBOUND")
            } }))
        }.toString()
    }
}
