package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.*
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.*
import org.junit.Test

class LogisticsViewModelStocktakeRaceTest {
    private class Fixture {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = FakeRepository()
        val vm = LogisticsViewModel(repo, scope)
        init { vm.login("admin", "password", "device", remember = false) }
        fun close() = scope.cancel()
    }

    @Test
    fun refreshDiscardsAnOlderAppendResponse() {
        val f = Fixture()
        try {
            f.vm.refreshStocktakes()
            f.repo.requests[0].result.complete(Result.success(page("old-first", 1)))
            f.vm.loadMoreStocktakes()
            f.vm.refreshStocktakes()
            f.repo.requests[2].result.complete(Result.success(page("new-first", 1)))
            f.repo.requests[1].result.complete(Result.success(page("old-second", 2)))
            assertEquals(listOf("new-first"), f.vm.state.value.stocktakes.map { it.id })
            assertEquals(1, f.vm.state.value.stocktakePage)
            assertFalse(f.vm.state.value.stocktakeLoadingMore)
        } finally { f.close() }
    }

    @Test
    fun olderRefreshAndItsErrorCannotReplaceNewerResults() {
        val f = Fixture()
        try {
            f.vm.refreshStocktakes()
            f.vm.refreshStocktakes()
            f.repo.requests[1].result.complete(Result.success(page("latest", 1)))
            f.repo.requests[0].result.complete(Result.failure(IllegalStateException("old network failure")))
            assertEquals(listOf("latest"), f.vm.state.value.stocktakes.map { it.id })
            assertEquals(WorkspaceLoadState.CONTENT, f.vm.state.value.stocktakeState)
            assertNull(f.vm.state.value.stocktakeError)
        } finally { f.close() }
    }

    @Test
    fun staleAppendFailureCannotClearNewerAppendLoadingState() {
        val f = Fixture()
        try {
            f.vm.refreshStocktakes()
            f.repo.requests[0].result.complete(Result.success(page("first", 1)))
            f.vm.loadMoreStocktakes()
            f.vm.refreshStocktakes()
            f.repo.requests[2].result.complete(Result.success(page("new-first", 1)))
            f.vm.loadMoreStocktakes()
            f.repo.requests[1].result.complete(Result.failure(IllegalStateException("old failure")))
            assertTrue(f.vm.state.value.stocktakeLoadingMore)
            assertNull(f.vm.state.value.stocktakeError)
            f.repo.requests[3].result.complete(Result.success(page("new-second", 2)))
            assertEquals(listOf("new-first", "new-second"), f.vm.state.value.stocktakes.map { it.id })
        } finally { f.close() }
    }

    @Test
    fun loadMoreDuringRefreshAndRepeatedClickDoNotIssueDuplicateRequests() {
        val f = Fixture()
        try {
            f.vm.refreshStocktakes()
            f.repo.requests[0].result.complete(Result.success(page("first", 1)))
            f.vm.refreshStocktakes()
            f.vm.loadMoreStocktakes()
            assertEquals(2, f.repo.requests.size)
            f.repo.requests[1].result.complete(Result.success(page("new-first", 1)))
            f.vm.loadMoreStocktakes()
            f.vm.loadMoreStocktakes()
            assertEquals(3, f.repo.requests.size)
        } finally { f.close() }
    }

    @Test
    fun responseFromBeforeLogoutCannotRepopulateStocktakes() {
        val f = Fixture()
        try {
            f.vm.refreshStocktakes()
            f.vm.logout()
            f.repo.requests[0].result.complete(Result.success(page("old-user", 1)))
            assertFalse(f.vm.state.value.loggedIn)
            assertTrue(f.vm.state.value.stocktakes.isEmpty())
            assertEquals(WorkspaceLoadState.IDLE, f.vm.state.value.stocktakeState)
        } finally { f.close() }
    }

    @Test
    fun currentAppendFailureKeepsPageAndAllowsRetry() {
        val f = Fixture()
        try {
            f.vm.refreshStocktakes()
            f.repo.requests[0].result.complete(Result.success(page("first", 1)))
            f.vm.loadMoreStocktakes()
            f.repo.requests[1].result.complete(Result.failure(IllegalStateException("network")))
            assertEquals(listOf("first"), f.vm.state.value.stocktakes.map { it.id })
            assertEquals(1, f.vm.state.value.stocktakePage)
            assertFalse(f.vm.state.value.stocktakeLoadingMore)
            f.vm.loadMoreStocktakes()
            assertEquals(2, f.repo.requests[2].page)
            f.repo.requests[2].result.complete(Result.success(page("second", 2)))
            assertEquals(listOf("first", "second"), f.vm.state.value.stocktakes.map { it.id })
            assertNull(f.vm.state.value.stocktakeError)
        } finally { f.close() }
    }

    private data class PendingPage(val page: Int, val result: CompletableDeferred<Result<StocktakePage>>)
    private class FakeRepository : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val requests = mutableListOf<PendingPage>()
        override suspend fun login(username: String, password: String, deviceId: String, remember: Boolean) =
            LoginResult("test-access", null, null, false, User("stock-user", username, "测试", UserRole.ADMIN))
        override suspend fun workspaceSummary() = Result.success(WorkspaceSummary())
        override suspend fun workspaceMaterialItems(status: String?, orderNo: String?, page: Int, pageSize: Int) =
            Result.success(WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null))
        override suspend fun stocktakes(page: Int, pageSize: Int): Result<StocktakePage> {
            val deferred = CompletableDeferred<Result<StocktakePage>>()
            requests += PendingPage(page, deferred)
            return deferred.await()
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

    companion object {
        private fun page(id: String, number: Int) = StocktakePage(
            listOf(StocktakeRecord(id, "material", null, 10, 9, -1, "PENDING_CONFIRM", null)), 100, number,
        )
    }
}
