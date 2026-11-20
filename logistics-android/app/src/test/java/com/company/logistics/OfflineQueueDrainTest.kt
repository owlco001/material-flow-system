package com.company.logistics.data

import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.data.SyncReport
import com.company.logistics.model.OfflineOpType
import com.company.logistics.model.SyncStatus
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.UUID
import java.util.concurrent.ConcurrentLinkedQueue

class OfflineQueueDrainTest {
    @Test
    fun workerKeepsPendingRetryScheduledWhenCurrentPassHasNoDueRows() {
        val report = SyncReport(success = 0, failed = 0, conflict = 0, retrying = 0)
        assertTrue(shouldRetryForPendingQueue(report, hasPending = true))
    }

    @Test
    fun pendingRetryIsNotSuppressedByConflictOutcome() {
        val report = SyncReport(success = 0, failed = 0, conflict = 1, retrying = 1)
        assertTrue(shouldRetryForPendingQueue(report, hasPending = true))
    }

    @Test
    fun emptyQueueAfterSuccessfulDrainDoesNotRetry() {
        val report = SyncReport(success = 21, failed = 0, conflict = 0, retrying = 0)
        assertTrue(!shouldRetryForPendingQueue(report, hasPending = false))
    }

    @Test
    fun drainsMoreThanOneBatchExactlyOnce() = runBlocking {
        val dao = RecordingDao()
        val operations = (1..47).map(::operation)
        operations.forEach { dao.insert(it) }
        val api = MaterialFlowApi().also { it.updateToken("session-token") }
        val repo = LogisticsRepository(api, dao)
        val server = LocalApiServer(expectedRequests = operations.size)
        val oldBaseUrl = ApiConfig.baseUrl
        val report = try {
            ApiConfig.baseUrl = server.baseUrl
            repo.syncPending()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            server.close()
        }

        assertEquals("多批次积压应在一次同步内排空", operations.size, report.success)
        assertEquals(0, report.failed)
        assertEquals(0, report.retrying)
        assertEquals(operations.size, server.requests.size)
        assertEquals("每条操作只尝试一次", operations.size, server.requests.map { it.idempotencyKey }.toSet().size)
        assertTrue(dao.rows.all { it.status == SyncStatus.SYNCED.name })
    }

    @Test
    fun overlappingDrainsDoNotReplayTheSameRowsTwice() = runBlocking {
        val dao = RecordingDao()
        val operations = (1..21).map(::operation)
        operations.forEach { dao.insert(it) }
        val api = MaterialFlowApi().also { it.updateToken("session-token") }
        val repo = LogisticsRepository(api, dao)
        val server = LocalApiServer(expectedRequests = operations.size)
        val oldBaseUrl = ApiConfig.baseUrl
        val reports = try {
            ApiConfig.baseUrl = server.baseUrl
            listOf(
                async { repo.syncPending() },
                async { repo.syncPending() },
            ).awaitAll()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            server.close()
        }

        assertEquals(operations.size, reports.sumOf { it.success })
        assertEquals(operations.size, server.requests.size)
        assertEquals(operations.size, server.requests.map { it.idempotencyKey }.toSet().size)
        assertTrue(dao.rows.all { it.status == SyncStatus.SYNCED.name })
    }

    @Test
    fun deferredOldestTwentyDoNotStarveLaterDueOperation() = runBlocking {
        val dao = RecordingDao()
        val deferred = (1..20).map(::operation).map { it.copy(nextRetryAt = System.currentTimeMillis() + 60_000) }
        deferred.forEach { dao.insert(it) }
        val due = operation(21)
        dao.insert(due)
        val api = MaterialFlowApi().also { it.updateToken("session-token") }
        val repo = LogisticsRepository(api, dao)
        val server = LocalApiServer(expectedRequests = 1)
        val oldBaseUrl = ApiConfig.baseUrl
        val report = try {
            ApiConfig.baseUrl = server.baseUrl
            repo.syncPending()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            server.close()
        }

        assertEquals("到期记录不应被较早创建但退避中的项目饿死", 1, report.success)
        assertEquals(listOf(due.id), server.requests.map { it.idempotencyKey })
        assertTrue(deferred.all { dao.findById(it.id)?.status == SyncStatus.PENDING.name })
        assertEquals(SyncStatus.SYNCED.name, dao.findById(due.id)?.status)
    }

    @Test
    fun enqueuedDuringDrainIsLeftForNextPass() = runBlocking {
        val dao = RecordingDao()
        val initial = (1..21).map(::operation)
        initial.forEach { dao.insert(it) }
        val api = MaterialFlowApi().also { it.updateToken("session-token") }
        val repo = LogisticsRepository(api, dao)
        var drainCutoff = Long.MIN_VALUE
        dao.onPendingDue = { queriedAt -> if (drainCutoff == Long.MIN_VALUE) drainCutoff = queriedAt }
        val later = operation(999)
        dao.afterFirstSynced = { dao.insertImmediately(later.copy(createdAt = drainCutoff + 1)) }
        val firstServer = LocalApiServer(expectedRequests = initial.size)
        val oldBaseUrl = ApiConfig.baseUrl
        val firstReport = try {
            ApiConfig.baseUrl = firstServer.baseUrl
            repo.syncPending()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            firstServer.close()
        }

        assertEquals(initial.size, firstReport.success)
        assertEquals(initial.size, firstServer.requests.size)
        assertEquals(SyncStatus.PENDING.name, dao.findById(later.id)?.status)

        dao.updateCreatedAt(later.id, createdAt = 1L)
        val nextServer = LocalApiServer(expectedRequests = 1)
        val nextReport = try {
            ApiConfig.baseUrl = nextServer.baseUrl
            repo.syncPending()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            nextServer.close()
        }
        assertEquals(1, nextReport.success)
        assertEquals(listOf(later.id), nextServer.requests.map { it.idempotencyKey })
        assertEquals(SyncStatus.SYNCED.name, dao.findById(later.id)?.status)
    }

    @Test
    fun failedItemIsDeferredAndDoesNotBlockLaterDueBatches() = runBlocking {
        val dao = RecordingDao()
        val operations = (1..25).map(::operation)
        operations.forEach { dao.insert(it) }
        val failedId = operations.first().id
        val api = MaterialFlowApi().also { it.updateToken("session-token") }
        val repo = LogisticsRepository(api, dao)
        val server = LocalApiServer(expectedRequests = operations.size, statusFor = { request: CapturedRequest ->
            if (request.idempotencyKey == failedId) 503 else 200
        })
        val oldBaseUrl = ApiConfig.baseUrl
        val report = try {
            ApiConfig.baseUrl = server.baseUrl
            repo.syncPending()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            server.close()
        }

        assertEquals(24, report.success)
        assertEquals(1, report.retrying)
        assertEquals(0, report.failed)
        assertEquals(operations.size, server.requests.size)
        assertEquals(operations.size, server.requests.map { it.idempotencyKey }.toSet().size)
        val deferred = dao.findById(failedId)!!
        assertEquals(SyncStatus.PENDING.name, deferred.status)
        assertEquals(1, deferred.retryCount)
        assertTrue("首次退避应在 30 秒附近", (deferred.nextRetryAt ?: 0L) > System.currentTimeMillis())
        assertTrue(operations.drop(1).all { dao.findById(it.id)?.status == SyncStatus.SYNCED.name })
    }

    @Test
    fun sessionLogoutDuringDrainStopsStartingRemainingOperations() = runBlocking {
        val dao = RecordingDao()
        val operations = (1..25).map(::operation)
        operations.forEach { dao.insert(it) }
        val api = MaterialFlowApi().also { it.updateToken("session-token") }
        val repo = LogisticsRepository(api, dao)
        dao.afterFirstSynced = { repo.logout() }
        val server = LocalApiServer(expectedRequests = LogisticsRepository.MAX_CONCURRENT_OPERATIONS)
        val oldBaseUrl = ApiConfig.baseUrl
        val report = try {
            ApiConfig.baseUrl = server.baseUrl
            repo.syncPending()
        } finally {
            ApiConfig.baseUrl = oldBaseUrl
            server.close()
        }

        assertTrue("登出后最多完成登出前已开始的两个并发请求", report.success in 1..LogisticsRepository.MAX_CONCURRENT_OPERATIONS)
        assertTrue(server.requests.size in report.success..LogisticsRepository.MAX_CONCURRENT_OPERATIONS)
        assertEquals(report.success, dao.rows.count { it.status == SyncStatus.SYNCED.name })
        assertEquals(operations.size - report.success, dao.rows.count { it.retryCount == 0 && it.status == SyncStatus.PENDING.name })
        assertNull(api.accessToken)
    }

    private fun operation(number: Int): OfflineOperationEntity {
        val id = UUID(0L, number.toLong()).toString()
        return OfflineOperationEntity(
            id = id,
            clientOperationId = id,
            materialCode = "MAT-$number",
            operationType = OfflineOpType.STOCKTAKE.name,
            status = SyncStatus.PENDING.name,
            createdAt = number.toLong(),
            quantity = number,
        )
    }

    private data class CapturedRequest(val idempotencyKey: String)

    private class LocalApiServer(
        private val expectedRequests: Int,
        private val statusFor: (CapturedRequest) -> Int = { 200 },
    ) : AutoCloseable {
        private val socket = ServerSocket(0)
        private val captured = ConcurrentLinkedQueue<CapturedRequest>()
        val baseUrl: String = "http://127.0.0.1:${socket.localPort}"
        val requests: List<CapturedRequest> get() = captured.toList()
        private val serverThread = Thread {
            repeat(expectedRequests) {
                val client = try {
                    socket.soTimeout = 4_000
                    socket.accept()
                } catch (_: SocketTimeoutException) {
                    return@Thread
                } catch (_: Exception) {
                    return@Thread
                }
                client.use { connection ->
                    val request = readRequest(connection)
                    captured.add(request)
                    respond(connection, statusFor(request))
                }
            }
        }.apply { isDaemon = true; start() }

        override fun close() {
            runCatching { socket.close() }
            serverThread.join(1_000)
        }

        private fun readRequest(connection: Socket): CapturedRequest {
            val input = BufferedInputStream(connection.getInputStream())
            fun line(): String {
                val bytes = ByteArrayOutputStream()
                while (true) {
                    val value = input.read()
                    if (value < 0 || value == '\n'.code) break
                    if (value != '\r'.code) bytes.write(value)
                }
                return bytes.toString(Charsets.UTF_8.name())
            }
            line()
            var contentLength = 0
            var idempotencyKey = ""
            while (true) {
                val header = line()
                if (header.isEmpty()) break
                if (header.startsWith("Content-Length:", ignoreCase = true)) {
                    contentLength = header.substringAfter(':').trim().toIntOrNull() ?: 0
                }
                if (header.startsWith("Idempotency-Key:", ignoreCase = true)) {
                    idempotencyKey = header.substringAfter(':').trim()
                }
            }
            repeat(contentLength) { input.read() }
            return CapturedRequest(idempotencyKey)
        }

        private fun respond(connection: Socket, status: Int) {
            val body = if (status == 200) {
                """{"stocktakeId":"stocktake-1"}"""
            } else {
                """{"code":"TEMPORARY","message":"temporary failure"}"""
            }.toByteArray(Charsets.UTF_8)
            val reason = if (status == 200) "OK" else "Service Unavailable"
            connection.getOutputStream().apply {
                write("HTTP/1.1 $status $reason\r\nContent-Type: application/json\r\n".toByteArray())
                write("Content-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray())
                write(body)
                flush()
            }
        }
    }

    private class RecordingDao : OfflineOperationDao {
        private val lock = Any()
        private val state = MutableStateFlow<List<OfflineOperationEntity>>(emptyList())
        private var firstSyncedCallbackFired = false
        var afterFirstSynced: (() -> Unit)? = null
        var onPendingDue: ((Long) -> Unit)? = null
        val rows: List<OfflineOperationEntity> get() = state.value

        fun insertImmediately(operation: OfflineOperationEntity) {
            synchronized(lock) {
                if (state.value.none { it.id == operation.id }) state.value = state.value + operation
            }
        }

        fun updateCreatedAt(id: String, createdAt: Long) {
            synchronized(lock) {
                state.value = state.value.map { if (it.id == id) it.copy(createdAt = createdAt) else it }
            }
        }

        override fun observeAll(): Flow<List<OfflineOperationEntity>> = state
        override suspend fun pending(): List<OfflineOperationEntity> = state.value
            .filter { it.status == SyncStatus.PENDING.name }
            .sortedWith(compareBy<OfflineOperationEntity> { it.createdAt }.thenBy { it.id })
            .take(20)

        override suspend fun pendingDue(nowMillis: Long, createdBeforeMillis: Long): List<OfflineOperationEntity> {
            onPendingDue?.invoke(nowMillis)
            return state.value.filter {
                it.status == SyncStatus.PENDING.name &&
                    (it.nextRetryAt == null || it.nextRetryAt <= nowMillis) &&
                    it.createdAt <= createdBeforeMillis
            }.sortedWith(compareBy<OfflineOperationEntity> { it.createdAt }.thenBy { it.id }).take(20)
        }

        override fun observePendingCount() = flowOf(state.value.count {
            it.status in setOf(SyncStatus.PENDING.name, SyncStatus.FAILED.name, SyncStatus.SYNCING.name)
        })
        override fun observeAttentionCount() = flowOf(state.value.count {
            it.status in setOf(SyncStatus.FAILED.name, SyncStatus.CONFLICT.name)
        })
        override suspend fun insert(operation: OfflineOperationEntity): Long = synchronized(lock) {
            if (state.value.any { it.id == operation.id }) return@synchronized -1L
            state.value = state.value + operation
            1L
        }
        override suspend fun updateStatus(id: String, status: String, error: String?) {
            synchronized(lock) {
                state.value = state.value.map { if (it.id == id) it.copy(status = status, errorMessage = error) else it }
            }
        }
        override suspend fun scheduleRetry(id: String, status: String, error: String?, retryCount: Int, nextRetryAt: Long?) {
            synchronized(lock) {
                state.value = state.value.map {
                    if (it.id == id) it.copy(status = status, errorMessage = error,
                        retryCount = retryCount, nextRetryAt = nextRetryAt) else it
                }
            }
        }
        override suspend fun markSynced(id: String, status: String, serverTime: String?) {
            val fireCallback = synchronized(lock) {
                state.value = state.value.map {
                    if (it.id == id) it.copy(status = status, serverTime = serverTime, errorMessage = null) else it
                }
                if (firstSyncedCallbackFired) false else {
                    firstSyncedCallbackFired = true
                    true
                }
            }
            if (fireCallback) afterFirstSynced?.invoke()
        }
        override suspend fun clearSynced() {
            synchronized(lock) { state.value = state.value.filter { it.status != SyncStatus.SYNCED.name } }
        }
        override suspend fun findById(id: String) = state.value.firstOrNull { it.id == id }
        override suspend fun resetStuckSyncing(): Int = synchronized(lock) {
            val count = state.value.count { it.status == SyncStatus.SYNCING.name }
            state.value = state.value.map {
                if (it.status == SyncStatus.SYNCING.name) it.copy(status = SyncStatus.PENDING.name) else it
            }
            count
        }
        override suspend fun resetToPending(id: String) {
            synchronized(lock) {
                state.value = state.value.map {
                    if (it.id == id) it.copy(status = SyncStatus.PENDING.name, errorMessage = null) else it
                }
            }
        }
        override suspend fun deleteById(id: String) {
            synchronized(lock) { state.value = state.value.filterNot { it.id == id } }
        }
    }
}
