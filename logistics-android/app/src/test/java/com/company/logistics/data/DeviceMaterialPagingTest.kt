package com.company.logistics.data

import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList

class DeviceMaterialPagingTest {
    @Test
    fun repositorySendsMachineFilterOnEveryPageAndKeepsLaterRows() = runBlocking {
        val responses = listOf(response(1, true, "m1"), response(2, false, "m2"))
        withResponses(responses) { repo, requests ->
            val rows = repo.deviceOrderMaterials("ORDER", "master-device-id", "MC-2").getOrThrow()
            assertEquals(listOf("m1", "m2"), rows.map { it.materialId })
            assertEquals(listOf(1, 2), requests.map { it.getInt("page") })
            assertTrue(requests.all { it.getInt("pageSize") == 200 && it.getString("deviceNo") == "MC-2" })
        }
    }

    @Test
    fun repeatedOrWrongPageIsFailureInsteadOfSilentlyTruncatingOrLooping() = runBlocking {
        withResponses(listOf(response(1, true, "m1"), response(1, true, "m1"))) { repo, requests ->
            assertTrue(repo.deviceOrderMaterials("ORDER", "master-device-id", "MC-2").isFailure)
            assertEquals(2, requests.size)
        }
    }

    @Test
    fun emptyPageClaimingMoreIsFailure() = runBlocking {
        withResponses(listOf("""{"documentNo":"ORDER","items":[],"page":1,"hasMore":true}""")) { repo, requests ->
            assertTrue(repo.deviceOrderMaterials("ORDER", "master-device-id", "MC-2").isFailure)
            assertEquals(1, requests.size)
        }
    }

    @Test
    fun clientDefenseExcludesOtherMachineRowsIfOlderServerIgnoresFilter() = runBlocking {
        withResponses(listOf(response(1, false, "other", deviceNo = "MC-OTHER"))) { repo, _ ->
            assertTrue(repo.deviceOrderMaterials("ORDER", "master-device-id", "MC-2").getOrThrow().isEmpty())
        }
    }

    @Test
    fun historicalLongImportedDeviceNumberIsSentUntruncated() = runBlocking {
        val longNumber = "IMPORTED-" + "机台".repeat(40)
        withResponses(listOf(response(1, false, "target", longNumber))) { repo, requests ->
            val rows = repo.deviceOrderMaterials("ORDER", "master-device-id", longNumber).getOrThrow()
            assertEquals(listOf("target"), rows.map { it.materialId })
            assertEquals(longNumber, requests.single().getString("deviceNo"))
        }
    }

    @Test
    fun ignoredFilterStillFindsTargetInLaterPageOnOlderServer() = runBlocking {
        withResponses(listOf(response(1, true, "other", "MC-OTHER"), response(2, false, "target"))) { repo, requests ->
            assertEquals(listOf("target"), repo.deviceOrderMaterials("ORDER", "master-device-id", "MC-2").getOrThrow().map { it.materialId })
            assertEquals(listOf(1, 2), requests.map { it.getInt("page") })
        }
    }

    @Test
    fun prePagingServerResponseWithoutMetadataIsHandledAsComplete() = runBlocking {
        val legacy = JSONObject(response(1, false, "target"))
        for (key in listOf("page", "pageSize", "hasMore", "total")) legacy.remove(key)
        withResponses(listOf(legacy.toString())) { repo, requests ->
            assertEquals(listOf("target"), repo.deviceOrderMaterials("ORDER", "master-device-id", "MC-2").getOrThrow().map { it.materialId })
            assertEquals(1, requests.size)
        }
    }

    private suspend fun withResponses(responses: List<String>, block: suspend (LogisticsRepository, List<JSONObject>) -> Unit) {
        val server = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        val requests = CopyOnWriteArrayList<JSONObject>()
        val errors = CopyOnWriteArrayList<Throwable>()
        val worker = Thread {
            try {
                responses.forEach { body ->
                    server.soTimeout = 10_000
                    server.accept().use { socket ->
                        socket.soTimeout = 10_000
                        requests += JSONObject(readBody(socket))
                        val bytes = body.toByteArray(Charsets.UTF_8)
                        socket.getOutputStream().apply {
                            write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ${bytes.size}\r\n\r\n".toByteArray())
                            write(bytes); flush()
                        }
                    }
                }
            } catch (error: Throwable) { if (!server.isClosed) errors += error }
        }.apply { isDaemon = true; start() }
        val oldUrl = ApiConfig.baseUrl
        try {
            ApiConfig.baseUrl = "http://127.0.0.1:${server.localPort}"
            block(LogisticsRepository(MaterialFlowApi().also { it.updateToken("synthetic-token") }, EmptyDao()), requests)
            worker.join(1_000)
            assertTrue(errors.toString(), errors.isEmpty())
        } finally {
            ApiConfig.baseUrl = oldUrl
            server.close()
            worker.join(1_000)
        }
        assertFalse("loopback fixture must stop", worker.isAlive)
    }

    private fun readBody(socket: Socket): String {
        val input = socket.getInputStream().buffered()
        fun line(): String {
            val result = java.io.ByteArrayOutputStream()
            while (true) {
                val byte = input.read()
                if (byte == -1 || byte == 10) break
                if (byte != 13) result.write(byte)
            }
            return result.toString("UTF-8")
        }
        line()
        var length = 0
        while (true) {
            val header = line()
            if (header.isEmpty()) break
            if (header.startsWith("Content-Length:", true)) length = header.substringAfter(':').trim().toInt()
        }
        val bytes = ByteArray(length)
        var offset = 0
        while (offset < length) {
            val count = input.read(bytes, offset, length - offset)
            check(count > 0); offset += count
        }
        return bytes.toString(Charsets.UTF_8)
    }

    private fun response(page: Int, more: Boolean, id: String, deviceNo: String = "MC-2") =
        """{"documentNo":"ORDER","documentType":"PRODUCTION_ORDER","page":$page,"pageSize":200,"total":201,"hasMore":$more,
        "items":[{"deviceId":"order-device-id","deviceNo":"$deviceNo","materialId":"$id","materialCode":"$id","requiredQuantity":1,"arrivedQuantity":1,"inStockQuantity":1,"statusCode":"IN_STOCK"}]}"""

    private class EmptyDao : OfflineOperationDao {
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
}
