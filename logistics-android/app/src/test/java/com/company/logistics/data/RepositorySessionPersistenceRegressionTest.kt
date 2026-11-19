package com.company.logistics.data

import android.content.SharedPreferences
import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test
import java.lang.reflect.Proxy
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow

/** Real repository + SessionStore methods; preferences are in-memory, not Android crypto/disk. */
class RepositorySessionPersistenceRegressionTest {
    @Test
    fun olderLoginCannotPersistItsUserOrTokensAfterNewerLoginCommits() = runBlocking {
        val server = LoginServer()
        val oldUrl = ApiConfig.baseUrl
        try {
            ApiConfig.baseUrl = server.url
            val api = MaterialFlowApi()
            val store = memoryStore()
            val repository = LogisticsRepository(api, NoOpDao(), store)
            val older = async(Dispatchers.IO) { runCatching { repository.login("A", "synthetic", "device-A", true) } }
            withContext(Dispatchers.IO) { assertTrue(server.aArrived.await(10, TimeUnit.SECONDS)) }
            val newer = repository.login("B", "synthetic", "device-B", true)
            assertEquals("B", newer.user.id)
            assertEquals("B", store.userSummary()?.id)
            assertEquals("access-B", store.accessToken())
            server.releaseA.countDown()
            withTimeout(10_000) { older.await() }
            assertEquals("B", store.userSummary()?.id)
            assertEquals("access-B", store.accessToken())
            assertEquals("refresh-B", store.refreshToken())
            assertEquals("device-B", store.deviceId())
            assertEquals("access-B", api.accessToken)
        } finally {
            server.close()
            ApiConfig.baseUrl = oldUrl
        }
    }

    private fun memoryStore(): SessionStore {
        val values = ConcurrentHashMap<String, Any>()
        lateinit var editor: SharedPreferences.Editor
        editor = Proxy.newProxyInstance(SharedPreferences.Editor::class.java.classLoader,
            arrayOf(SharedPreferences.Editor::class.java)) { _, method, args ->
            when (method.name) {
                "putString", "putBoolean", "putInt", "putLong", "putFloat", "putStringSet" -> {
                    val key = args!![0] as String
                    val value = args[1]
                    if (value == null) values.remove(key) else values[key] = value
                    editor
                }
                "remove" -> { values.remove(args!![0] as String); editor }
                "clear" -> { values.clear(); editor }
                "apply" -> null
                "commit" -> true
                else -> error("Unexpected Editor call: ${method.name}")
            }
        } as SharedPreferences.Editor
        val preferences = Proxy.newProxyInstance(SharedPreferences::class.java.classLoader,
            arrayOf(SharedPreferences::class.java)) { _, method, args ->
            when (method.name) {
                "edit" -> editor
                "getString", "getBoolean", "getInt", "getLong", "getFloat", "getStringSet" -> values[args!![0] as String] ?: args[1]
                "contains" -> values.containsKey(args!![0] as String)
                "getAll" -> HashMap(values)
                else -> error("Unexpected SharedPreferences call: ${method.name}")
            }
        } as SharedPreferences
        return SessionStore::class.java.getDeclaredConstructor(SharedPreferences::class.java, java.lang.Boolean.TYPE)
            .apply { isAccessible = true }.newInstance(preferences, true)
    }

    private class LoginServer : AutoCloseable {
        private val socket = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        val url = "http://127.0.0.1:${socket.localPort}"
        val aArrived = CountDownLatch(1)
        val releaseA = CountDownLatch(1)
        private val children = CopyOnWriteArrayList<Thread>()
        private val acceptor = Thread {
            repeat(2) {
                val client = try { socket.soTimeout = 10_000; socket.accept() } catch (_: Exception) { return@Thread }
                val worker = Thread {
                    client.use { connection ->
                        connection.soTimeout = 10_000
                        val body = readBody(connection)
                        val id = if (body.contains("\"employeeNo\":\"A\"")) "A" else "B"
                        if (id == "A") {
                            aArrived.countDown()
                            check(releaseA.await(10, TimeUnit.SECONDS))
                        }
                        val bytes = ("{\"accessToken\":\"access-$id\",\"refreshToken\":\"refresh-$id\"," +
                            "\"mustChangePassword\":false,\"user\":{\"id\":\"$id\",\"username\":\"$id\",\"displayName\":\"Synthetic $id\",\"role\":\"ADMIN\"}}")
                            .toByteArray(Charsets.UTF_8)
                        connection.getOutputStream().apply {
                            write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ${bytes.size}\r\n\r\n".toByteArray())
                            write(bytes); flush()
                        }
                    }
                }.apply { isDaemon = true }
                children += worker
                worker.start()
            }
        }.apply { isDaemon = true; start() }

        private fun readBody(connection: Socket): String {
            val input = connection.getInputStream().buffered()
            fun line(): String {
                val output = java.io.ByteArrayOutputStream()
                while (true) {
                    val value = input.read()
                    if (value < 0 || value == 10) break
                    if (value != 13) output.write(value)
                }
                return output.toString("UTF-8")
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
                check(count > 0)
                offset += count
            }
            return bytes.toString(Charsets.UTF_8)
        }

        override fun close() {
            releaseA.countDown()
            socket.close()
            acceptor.join(1_000)
            children.forEach { it.join(1_000) }
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
}
