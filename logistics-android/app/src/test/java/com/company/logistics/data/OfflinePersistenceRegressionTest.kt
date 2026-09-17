package com.company.logistics.data


import com.company.logistics.model.Inventory
import com.company.logistics.model.LocationStock
import com.company.logistics.model.Material

import com.company.logistics.model.OfflineOpType
import com.company.logistics.model.SyncStatus
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.charset.StandardCharsets
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import java.util.Base64

class OfflinePersistenceRegressionTest {
    @Test
    fun duplicateClientOperationIdIsIgnoredByQueue() = runBlocking {
        val dao = InMemoryOfflineDao()

        val operationId = "00000000-0000-0000-0000-000000000001"
        dao.insert(entity(operationId).copy(remark = "first"))
        dao.insert(entity(operationId).copy(remark = "second"))

        assertEquals(1, dao.rows.size)
        assertEquals(operationId, dao.rows.single().clientOperationId)
        assertEquals("first", dao.rows.single().remark)
    }

    @Test
    fun encryptedPayloadRoundTripsAfterStorage() {
        val cipher = TestPayloadCipher()
        val payload = "{\"materialId\":\"mat-1\",\"quantity\":2,\"remark\":\"check\"}"

        val stored = cipher.encrypt(payload)

        assertTrue(stored.startsWith("v1:"))
        assertTrue(stored != payload)
        assertEquals(payload, cipher.decrypt(stored))
    }

    @Test
    fun reInstantiatingRepositoryReadsTheSamePersistedQueueState() = runBlocking {
        val dao = InMemoryOfflineDao()
        val operationId = "00000000-0000-0000-0000-000000000002"
        dao.insert(entity(operationId).copy(quantity = 3, targetLocation = "B-02", remark = "persist"))

        val restored = dao.findById(operationId)

        assertEquals(operationId, restored?.clientOperationId)
        assertEquals(SyncStatus.PENDING.name, restored?.status)
        assertEquals("persist", restored?.remark)
    }

    @Test
    fun failedMissingPayloadIsNotRetryableAndConflictIsRetained() = runBlocking {
        val dao = InMemoryOfflineDao()
        val failedId = "failed"
        val conflictId = "conflict"
        dao.insert(entity(failedId, materialId = null))
        dao.insert(entity(conflictId, materialId = "mat-1", status = SyncStatus.CONFLICT.name))

        dao.updateStatus(failedId, SyncStatus.FAILED.name, "缺少物料 ID，无法重放")
        assertEquals(SyncStatus.FAILED.name, dao.findById(failedId)?.status)
        assertEquals(SyncStatus.CONFLICT.name, dao.findById(conflictId)?.status)
        assertEquals(1, dao.pending().size)
        assertEquals(failedId, dao.pending().single().id)
    }

    @Test
    fun conflictTransitionIsTerminalAndFailedRowsRemainRetryable() = runBlocking {
        val dao = InMemoryOfflineDao()
        dao.insert(entity("transition"))

        dao.updateStatus("transition", SyncStatus.CONFLICT.name, "version changed")
        assertEquals(SyncStatus.CONFLICT.name, dao.findById("transition")?.status)
        assertTrue(dao.pending().isEmpty())

        dao.updateStatus("transition", SyncStatus.FAILED.name, "temporary")
        assertEquals(1, dao.pending().size)
        assertEquals(SyncStatus.FAILED.name, dao.pending().single().status)
    }

    @Test
    fun workerRetriesOnlyBeforeConfiguredAttemptBoundary() {
        assertEquals(5, OfflineSyncWorker.MAX_RETRIES)
    }

    @Test
    fun syncedRowsAreExcludedFromRetryAndStatusTransitionsClearError() = runBlocking {
        val dao = InMemoryOfflineDao()
        val id = "synced"
        dao.insert(entity(id, status = SyncStatus.FAILED.name, errorMessage = "temporary"))
        dao.updateStatus(id, SyncStatus.SYNCING.name)
        dao.markSynced(id, SyncStatus.SYNCED.name, "2026-09-18T00:00:00Z")

        assertEquals(SyncStatus.SYNCED.name, dao.findById(id)?.status)
        assertEquals("2026-09-18T00:00:00Z", dao.findById(id)?.serverTime)
        assertEquals(null, dao.findById(id)?.errorMessage)
        assertTrue(dao.pending().isEmpty())
    }



    private fun entity(
        id: String,
        materialId: String? = "mat-1",
        status: String = SyncStatus.PENDING.name,
        errorMessage: String? = null,
    ) = OfflineOperationEntity(
        id = id,
        clientOperationId = id,
        materialCode = "MAT-001",
        operationType = OfflineOpType.INBOUND.name,
        status = status,
        createdAt = 1L,
        quantity = 1,
        targetLocation = "A-01",
        materialId = materialId,
        expectedInventoryVersion = 3,
        errorMessage = errorMessage,
    )

    private class InMemoryOfflineDao : OfflineOperationDao {
        private val state = MutableStateFlow<List<OfflineOperationEntity>>(emptyList())
        val rows: List<OfflineOperationEntity> get() = state.value

        override fun observeAll(): Flow<List<OfflineOperationEntity>> = state
        override suspend fun pending() = state.value.filter {
            it.status == SyncStatus.PENDING.name || it.status == SyncStatus.FAILED.name
        }.sortedBy { it.createdAt }.take(20)
        override fun observePendingCount() = flowOf(pendingCount())
        override suspend fun insert(operation: OfflineOperationEntity): Long {
            if (state.value.any { it.id == operation.id }) return -1L
            state.value = state.value + operation
            return 1L
        }
        override suspend fun updateStatus(id: String, status: String, error: String?) {
            state.value = state.value.map { if (it.id == id) it.copy(status = status, errorMessage = error) else it }
        }
        override suspend fun markSynced(id: String, status: String, serverTime: String?) {
            state.value = state.value.map {
                if (it.id == id) it.copy(status = status, serverTime = serverTime, errorMessage = null) else it
            }
        }
        override suspend fun clearSynced() { state.value = state.value.filter { it.status != SyncStatus.SYNCED.name } }
        override suspend fun findById(id: String) = state.value.firstOrNull { it.id == id }
        private fun pendingCount() = state.value.count {
            it.status == SyncStatus.PENDING.name || it.status == SyncStatus.FAILED.name || it.status == SyncStatus.SYNCING.name
        }
    }


    private class TestPayloadCipher : OfflinePayloadCipher {
        private val key = SecretKeySpec(ByteArray(16) { (it + 1).toByte() }, "AES")
        override fun encrypt(plainText: String): String {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key)
            return "v1:" + Base64.getEncoder().encodeToString(cipher.iv) + ":" +
                Base64.getEncoder().encodeToString(cipher.doFinal(plainText.toByteArray(StandardCharsets.UTF_8)))
        }
        override fun decrypt(storedValue: String): String {
            val parts = storedValue.removePrefix("v1:").split(":", limit = 2)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, Base64.getDecoder().decode(parts[0])))
            return String(cipher.doFinal(Base64.getDecoder().decode(parts[1])), StandardCharsets.UTF_8)
        }
    }
}
