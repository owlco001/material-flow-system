package com.company.logistics.data

import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.OfflineOpType
import com.company.logistics.model.SyncStatus
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cancellation invariants for a claimed offline operation.
 * The fake is stateful and models a durable claim; no Room implementation is substituted here.
 */
class OfflineCancellationRegressionTest {
    @Test
    fun cancellingAfterClaimReturnsOnlyThisRoundClaimToPendingAndPreservesRetryMetadata() = runBlocking {
        val dao = ClaimDao(target = operation("target", SyncStatus.PENDING.name, retryCount = 2, nextRetryAt = 42L))
        val repository = LogisticsRepository(MaterialFlowApi(), dao)
        val drain = launch { repository.syncPending() }

        withTimeout(5_000) { dao.claimCommitted.await() }
        drain.cancelAndJoin()

        val target = dao.findById("target") ?: error("target row missing")
        assertEquals(SyncStatus.PENDING.name, target.status)
        assertEquals(2, target.retryCount)
        assertEquals(42L, target.nextRetryAt)
        assertEquals(1, dao.conditionalResetChangedRows)
        assertFalse("cancellation must not reset unrelated active claims", dao.findById("other")?.status == SyncStatus.PENDING.name)
        assertEquals(SyncStatus.SYNCING.name, dao.findById("other")?.status)
    }

    @Test
    fun cancellationDoesNotResetClaimThatChangedStateBeforeCleanup() = runBlocking {
        val dao = ClaimDao(target = operation("target", SyncStatus.PENDING.name), mutateBeforeCancellation = true)
        val repository = LogisticsRepository(MaterialFlowApi(), dao)
        val drain = launch { repository.syncPending() }

        withTimeout(5_000) { dao.claimCommitted.await() }
        drain.cancelAndJoin()

        assertEquals("external completion wins over cancellation cleanup", SyncStatus.SYNCED.name, dao.findById("target")?.status)
        assertEquals("an unrelated active claim is never touched", SyncStatus.SYNCING.name, dao.findById("other")?.status)
        assertEquals(0, dao.conditionalResetChangedRows)
    }

    private class ClaimDao(
        target: OfflineOperationEntity,
        private val mutateBeforeCancellation: Boolean = false,
    ) : OfflineOperationDao {
        private val rows = linkedMapOf(
            target.id to target,
            "other" to operation("other", SyncStatus.SYNCING.name),
        )
        val claimCommitted = CompletableDeferred<Unit>()
        var conditionalResetChangedRows = 0
            private set
        private val holdClaim = CompletableDeferred<Unit>()

        override fun observeAll(): Flow<List<OfflineOperationEntity>> = emptyFlow()
        override suspend fun pending(): List<OfflineOperationEntity> = rows.values.filter { it.status == SyncStatus.PENDING.name }
        override fun observePendingCount(): Flow<Int> = emptyFlow()
        override fun observeAttentionCount(): Flow<Int> = emptyFlow()
        override suspend fun insert(operation: OfflineOperationEntity): Long {
            rows[operation.id] = operation
            return 1L
        }
        override suspend fun updateStatus(id: String, status: String, error: String?) {
            rows[id] = rows.getValue(id).copy(status = status, errorMessage = error)
            if (status == SyncStatus.SYNCING.name && id == "target") {
                claimCommitted.complete(Unit)
                if (mutateBeforeCancellation) {
                    rows[id] = rows.getValue(id).copy(status = SyncStatus.SYNCED.name)
                }
                try {
                    holdClaim.await()
                } catch (cancelled: CancellationException) {
                    throw cancelled
                }
            }
        }
        override suspend fun markSynced(id: String, status: String, serverTime: String?) {
            rows[id] = rows.getValue(id).copy(status = status, serverTime = serverTime, errorMessage = null)
        }
        override suspend fun clearSynced() {
            rows.entries.removeIf { it.value.status == SyncStatus.SYNCED.name }
        }
        override suspend fun findById(id: String): OfflineOperationEntity? = rows[id]
        override suspend fun resetStuckSyncing(): Int = 0
        override suspend fun resetToPending(id: String) {
            // Existing public API is unconditional manual retry. This fake intentionally mirrors it;
            // cancellation cleanup must check this round's claim before invoking it.
            conditionalResetChangedRows++
            rows[id] = rows.getValue(id).copy(status = SyncStatus.PENDING.name, errorMessage = null)
        }
        override suspend fun deleteById(id: String) {
            rows.remove(id)
        }
    }

    private companion object {
        fun operation(id: String, status: String, retryCount: Int = 0, nextRetryAt: Long? = null) =
            OfflineOperationEntity(
                id = id,
                clientOperationId = "client-$id",
                materialCode = "MAT-001",
                operationType = OfflineOpType.STOCKTAKE.name,
                status = status,
                createdAt = 1L,
                quantity = 1,
                targetLocation = "A-01",
                retryCount = retryCount,
                nextRetryAt = nextRetryAt,
            )
    }
}
