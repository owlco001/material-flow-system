package com.company.logistics.data

import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.Flow
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

/** Real HTTP client, synthetic loopback responses, no Android storage or production services. */
class DeviceTransferScopeTest {
    @Test fun emptyOrdersDoNotIssueHttp() = runBlocking {
        withServer({ _, _ -> Reply(body = page(1, 0, emptyList())) }) { server, api ->
            val result = LogisticsRepository(api, NoOpDao()).transferRequestsForOrders(emptySet()).getOrThrow()
            assertTrue(result.items.isEmpty())
            assertEquals(0, server.requests.size)
        }
    }

    @Test fun repeatedEncodedDocumentFiltersAreSentOnEveryPageAndOldServerRowsAreFiltered() = runBlocking {
        val orders = linkedSetOf("WO &+/中文", "WO-2")
        withServer({ request, _ ->
            val number = request.values("page").single().toInt()
            val items = if (number == 1) (1..100).map { "r-$it" to "OTHER" }
                else listOf("target" to orders.first())
            Reply(body = page(number, 101, items))
        }) { server, api ->
            val result = api.listTransferRequestsForOrders(orders)
            assertEquals(listOf("target"), result.items.map { it.id })
            assertEquals(2, server.requests.size)
            server.requests.forEach { request ->
                assertEquals(orders.toSet(), request.values("documentNo").toSet())
                assertEquals(listOf("100"), request.values("pageSize"))
            }
        }
    }

    @Test fun moreThanFiveThousandRowsAreNeverSilentlyTruncated() = runBlocking {
        withServer({ request, _ ->
            val number = request.values("page").single().toInt()
            val start = (number - 1) * 100 + 1
            Reply(body = page(number, 5_101, (start..minOf(start + 99, 5_101)).map { "r-$it" to "WO" }))
        }) { server, api ->
            val result = api.listTransferRequestsForOrders(setOf("WO"))
            assertEquals(5_101, result.items.size)
            assertEquals(5_101, result.items.map { it.id }.toSet().size)
            assertEquals(52, server.requests.size)
        }
    }

    @Test fun repeatedPageCannotBecomeSuccessfulPartialList() = runBlocking {
        withServer({ _, _ -> Reply(body = page(1, 201, (1..100).map { "r-$it" to "WO" })) }) { _, api ->
            assertTrue(runCatching { api.listTransferRequestsForOrders(setOf("WO")) }.isFailure)
        }
    }

    @Test fun countChangeCannotBecomeSuccessfulPartialList() = runBlocking {
        withServer({ request, _ ->
            val number = request.values("page").single().toInt()
            Reply(body = page(number, if (number == 1) 101 else 102,
                if (number == 1) (1..100).map { "r-$it" to "WO" } else listOf("r-101" to "WO")))
        }) { _, api ->
            assertTrue(runCatching { api.listTransferRequestsForOrders(setOf("WO")) }.isFailure)
        }
    }

    @Test fun failureOnLaterPageDoesNotReturnEarlierRows() = runBlocking {
        withServer({ request, _ ->
            val number = request.values("page").single().toInt()
            if (number == 1) Reply(body = page(1, 101, (1..100).map { "r-$it" to "WO" }))
            else Reply(500, """{"error":{"code":"UNAVAILABLE","message":"synthetic","retryable":true}}""")
        }) { _, api ->
            assertTrue(LogisticsRepository(api, NoOpDao()).transferRequestsForOrders(setOf("WO")).isFailure)
        }
    }

    @Test fun twentyOneOrdersUseStableTwentyOrderChunks() = runBlocking {
        val orders = (1..21).map { "WO-${it.toString().padStart(2, '0')}" }.reversed().toSet()
        withServer({ request, _ ->
            val scope = request.values("documentNo")
            Reply(body = page(1, scope.size, scope.map { "id-$it" to it }))
        }) { server, api ->
            val result = LogisticsRepository(api, NoOpDao()).transferRequestsForOrders(orders).getOrThrow()
            assertEquals(orders, result.items.mapNotNull { it.documentNo }.toSet())
            assertEquals(listOf(20, 1), server.requests.map { it.values("documentNo").size })
            assertEquals(orders.sorted(), server.requests.flatMap { it.values("documentNo") })
        }
    }

    @Test fun orderScopeIsTrimmedAndDeduplicatedBeforeQueryAndDefensiveFiltering() = runBlocking {
        withServer({ _, _ -> Reply(body = page(1, 1, listOf("target" to "WO"))) }) { server, api ->
            val result = LogisticsRepository(api, NoOpDao())
                .transferRequestsForOrders(linkedSetOf(" WO ", "\tWO")).getOrThrow()
            assertEquals(listOf("target"), result.items.map { it.id })
            assertEquals(listOf("WO"), server.requests.single().values("documentNo"))
        }
    }

    @Test fun shortPageClaimingMoreTotalPagesCannotBeDeclaredCompleteWithoutTotal() = runBlocking {
        withServer({ _, _ ->
            Reply(body = JSONObject(page(1, 0, listOf("target" to "WO"))).apply {
                remove("total")
                put("totalPages", 2)
            }.toString())
        }) { _, api ->
            assertTrue("a short page contradicts the remaining-page metadata",
                runCatching { api.listTransferRequestsForOrders(setOf("WO")) }.isFailure)
        }
    }

    @Test fun totalPagesChangingDuringReadCannotReturnSuccessfulList() = runBlocking {
        withServer({ request, _ ->
            val number = request.values("page").single().toInt()
            Reply(body = JSONObject(page(number, 201,
                ((number - 1) * 100 + 1..minOf(number * 100, 201)).map { "r-$it" to "WO" })).apply {
                put("totalPages", if (number == 2) 4 else 3)
            }.toString())
        }) { _, api ->
            assertTrue("page-count changes invalidate the whole read",
                runCatching { api.listTransferRequestsForOrders(setOf("WO")) }.isFailure)
        }
    }

    @Test fun legacyShortResponseWithoutMetadataIsAcceptedAndFiltered() = runBlocking {
        withServer({ _, _ ->
            Reply(body = legacy(listOf("target" to "WO", "unrelated" to "OTHER")))
        }) { server, api ->
            assertEquals(listOf("target"), api.listTransferRequestsForOrders(setOf("WO")).items.map { it.id })
            assertEquals(1, server.requests.size)
        }
    }

    @Test fun legacyFullResponseWithoutMetadataFailsRatherThanGuessCompleteness() = runBlocking {
        withServer({ _, _ -> Reply(body = legacy((1..100).map { "r-$it" to "WO" })) }) { server, api ->
            assertTrue(runCatching { api.listTransferRequestsForOrders(setOf("WO")) }.isFailure)
            assertEquals(1, server.requests.size)
        }
    }

    @Test fun repeatedRowsWithAdvancingPageAreRejectedWithoutUnboundedLoop() = runBlocking {
        withServer({ request, _ ->
            val root = JSONObject(page(request.values("page").single().toInt(), 0,
                (1..100).map { "r-$it" to "WO" }))
            root.remove("total")
            Reply(body = root.toString())
        }) { server, api ->
            assertTrue(runCatching { api.listTransferRequestsForOrders(setOf("WO")) }.isFailure)
            assertEquals(2, server.requests.size)
        }
    }

    @Test fun identityChangeOnLaterPageIsRethrownAsCancellationNotPartialResult() = runBlocking {
        lateinit var activeApi: MaterialFlowApi
        withServer({ request, _ ->
            val number = request.values("page").single().toInt()
            if (number == 2) activeApi.restoreSession("other-access", "other-refresh", "other-device")
            Reply(body = page(number, 201,
                ((number - 1) * 100 + 1..minOf(number * 100, 201)).map { "r-$it" to "WO" }))
        }) { server, api ->
            activeApi = api
            val error = runCatching {
                LogisticsRepository(api, NoOpDao()).transferRequestsForOrders(setOf("WO"))
            }.exceptionOrNull()
            assertTrue("identity changes must propagate cancellation", error is CancellationException)
            assertEquals(2, server.requests.size)
            assertTrue(server.requests.all { it.authorization == "Bearer synthetic" })
            assertEquals("other-access", api.accessToken)
        }
    }

    @Test fun cancellingCallerPropagatesCancellation() = runBlocking {
        val arrived = CompletableDeferred<Unit>()
        val release = CountDownLatch(1)
        try {
            withServer({ _, _ ->
                arrived.complete(Unit)
                check(release.await(5, TimeUnit.SECONDS))
                Reply(body = page(1, 0, emptyList()))
            }) { _, api ->
                val call = async(Dispatchers.IO) {
                    LogisticsRepository(api, NoOpDao()).transferRequestsForOrders(setOf("WO"))
                }
                withTimeout(5_000) { arrived.await() }
                call.cancel()
                release.countDown()
                assertTrue(runCatching { withTimeout(5_000) { call.await() } }.exceptionOrNull() is CancellationException)
            }
        } finally { release.countDown() }
    }

    private data class Captured(val target: String, val authorization: String?) {
        fun values(key: String): List<String> = target.substringAfter('?', "").split('&').mapNotNull {
            val pieces = it.split('=', limit = 2)
            if (pieces[0] != key) null else URLDecoder.decode(pieces.getOrElse(1) { "" }, "UTF-8")
        }
    }
    private data class Reply(val code: Int = 200, val body: String)

    private class Server(private val handler: (Captured, Int) -> Reply) : AutoCloseable {
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
                        val reply = handler(request, requests.size)
                        val bytes = reply.body.toByteArray(Charsets.UTF_8)
                        connection.getOutputStream().apply {
                            write("HTTP/1.1 ${reply.code} Response\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ${bytes.size}\r\n\r\n".toByteArray(Charsets.US_ASCII))
                            write(bytes)
                            flush()
                        }
                    }
                } catch (error: Throwable) {
                    failures += error
                } finally { activeSocket = null }
            }
        }.apply { isDaemon = true; start() }
        override fun close() {
            listener.close()
            worker.join(1_000)
            if (worker.isAlive) {
                activeSocket?.close()
                worker.join(5_000)
            }
            assertFalse("loopback worker did not exit", worker.isAlive)
            assertTrue("loopback worker failures: $failures", failures.isEmpty())
        }
    }

    private suspend fun withServer(handler: (Captured, Int) -> Reply, block: suspend (Server, MaterialFlowApi) -> Unit) {
        val previous = ApiConfig.baseUrl
        Server(handler).use { server ->
            try {
                ApiConfig.baseUrl = server.url
                block(server, MaterialFlowApi().also { it.restoreSession("synthetic", "synthetic-refresh", "device") })
            } finally { ApiConfig.baseUrl = previous }
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

    private companion object {
        fun legacy(rows: List<Pair<String, String>>): String = JSONObject(page(1, rows.size, rows)).apply {
            remove("page"); remove("pageSize"); remove("total")
        }.toString()

        fun page(number: Int, total: Int, rows: List<Pair<String, String>>): String = JSONObject().apply {
            put("page", number); put("pageSize", 100); put("total", total)
            put("items", JSONArray(rows.map { (id, document) -> JSONObject().apply {
                put("id", id); put("documentNo", document); put("status", "PENDING"); put("type", "INBOUND")
            } }))
        }.toString()
    }
}
