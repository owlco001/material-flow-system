package com.company.logistics.data

import android.content.Context
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.company.logistics.model.OfflineOpType
import com.company.logistics.model.SyncStatus
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OfflineOperationRoomTest {
    private lateinit var database: LogisticsDatabase
    private lateinit var dao: OfflineOperationDao

    @Before
    fun setUp() {
        val context: Context = InstrumentationRegistry.getInstrumentation().targetContext
        database = Room.inMemoryDatabaseBuilder(context, LogisticsDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        dao = database.offlineOperationDao()
    }

    @After
    fun tearDown() {
        database.close()
    }

    @Test
    fun duplicateIdWithIgnoreKeepsOneRow() = runBlocking {
        val first = operation(id = "duplicate", createdAt = 10L, quantity = 1)
        val second = operation(id = "duplicate", createdAt = 20L, quantity = 2)

        assertEquals(1L, dao.insert(first))
        assertEquals(-1L, dao.insert(second))

        assertEquals(first, dao.findById("duplicate"))
    }

    @Test
    fun pendingReturnsPendingAndFailedInAscendingOrderWithLimit() = runBlocking {
        (1L..21L).forEach { createdAt ->
            dao.insert(
                operation(
                    id = "pending-$createdAt",
                    createdAt = createdAt,
                    status = if (createdAt % 2L == 0L) SyncStatus.FAILED.name else SyncStatus.PENDING.name,
                ),
            )
        }
        dao.insert(operation(id = "synced", createdAt = 0L, status = SyncStatus.SYNCED.name))
        dao.insert(operation(id = "conflict", createdAt = -1L, status = SyncStatus.CONFLICT.name))

        val pending = dao.pending()

        assertEquals(20, pending.size)
        assertEquals((1L..20L).toList(), pending.map { it.createdAt })
        assertTrue(pending.any { it.status == SyncStatus.PENDING.name })
        assertTrue(pending.any { it.status == SyncStatus.FAILED.name })
        assertFalse(pending.any { it.status == SyncStatus.SYNCED.name })
        assertFalse(pending.any { it.status == SyncStatus.CONFLICT.name })
    }

    @Test
    fun statusTransitionsControlRetryQueueAndMarkSyncedClearsSyncMetadata() = runBlocking {
        dao.insert(
            operation(
                id = "transition",
                status = SyncStatus.PENDING.name,
                serverTime = "old-server-time",
                errorMessage = "old-error",
            ),
        )

        dao.updateStatus("transition", SyncStatus.CONFLICT.name, "inventory changed")
        assertTrue(dao.pending().isEmpty())
        assertEquals(SyncStatus.CONFLICT.name, dao.findById("transition")?.status)

        dao.updateStatus("transition", SyncStatus.FAILED.name, "temporary failure")
        assertTrue(dao.pending().any { it.id == "transition" })
        assertEquals("temporary failure", dao.findById("transition")?.errorMessage)

        dao.markSynced("transition", SyncStatus.SYNCED.name, "2026-09-18T00:00:00Z")
        val synced = dao.findById("transition")
        assertEquals(SyncStatus.SYNCED.name, synced?.status)
        assertEquals("2026-09-18T00:00:00Z", synced?.serverTime)
        assertNull(synced?.errorMessage)
        assertTrue(dao.pending().isEmpty())
    }

    private fun operation(
        id: String,
        createdAt: Long = 1L,
        status: String = SyncStatus.PENDING.name,
        quantity: Int = 1,
        serverTime: String? = null,
        errorMessage: String? = null,
    ) = OfflineOperationEntity(
        id = id,
        clientOperationId = "client-$id",
        materialCode = "MAT-001",
        operationType = OfflineOpType.INBOUND.name,
        status = status,
        createdAt = createdAt,
        quantity = quantity,
        targetLocation = "A-01",
        materialId = "material-1",
        expectedInventoryVersion = 3,
        serverTime = serverTime,
        errorMessage = errorMessage,
    )
}
