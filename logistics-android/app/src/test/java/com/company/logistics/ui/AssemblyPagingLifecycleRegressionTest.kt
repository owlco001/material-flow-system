package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.AssemblyTaskPage
import com.company.logistics.model.AssemblyTaskStatus
import com.company.logistics.model.LaborSummaryItem
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Correct invariants for assembly paging and labor request ownership. */
class AssemblyPagingLifecycleRegressionTest {
    @Test
    fun serverTotalsArePreservedForZeroTwentyTwentyOneAndFortyOneRows() {
        listOf(0 to 0, 20 to 1, 21 to 2, 41 to 3).forEach { (total, pages) ->
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
            val repository = PagingLaborRepository(total = total, totalPages = pages)
            val viewModel = LogisticsViewModel(repository, scope)
            viewModel.login("assembler", "password", "device", remember = false)

            assertEquals(total, viewModel.state.value.assemblyTaskTotal)
            assertEquals(pages, viewModel.state.value.assemblyTaskTotalPages)
            assertEquals(if (total == 0) 0 else minOf(total, 20), viewModel.state.value.assemblyTasks.size)
            scope.cancel()
        }
    }

    @Test
    fun nextAndPreviousUseServerPageMetadataAndDoNotSkipLastPage() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = PagingLaborRepository(total = 41, totalPages = 3)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)

        viewModel.loadNextWorkspacePage()
        viewModel.loadNextWorkspacePage()
        viewModel.loadNextWorkspacePage()
        viewModel.loadPreviousWorkspacePage()

        assertEquals(listOf(1, 2, 3, 2), repository.pageRequests)
        assertEquals(2, viewModel.state.value.assemblyTaskPage)
        assertEquals(41, viewModel.state.value.assemblyTaskTotal)
        assertEquals(3, viewModel.state.value.assemblyTaskTotalPages)
        scope.cancel()
    }

    @Test
    fun serverDeviceFilterRemainsScopedToReturnedPageAndKeepsServerTotal() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = PagingLaborRepository(total = 21, totalPages = 2)
        repository.pageItems = listOf(task("wanted", "machine-1", "M-1"))
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)

        assertEquals(listOf("wanted"), LogisticsViewModel.filterAssemblyTasksByDevice(repository.pageItems, "M-1").map { it.id })
        assertEquals(listOf("wanted"), viewModel.state.value.assemblyTasks.map { it.id })
        assertEquals(21, viewModel.state.value.assemblyTaskTotal)
        assertEquals(2, viewModel.state.value.assemblyTaskTotalPages)
        scope.cancel()
    }

    @Test
    fun refreshCancelsPreviousLaborChildrenAndLogoutRejectsOldSuccessAndFailure() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = PagingLaborRepository(total = 20, totalPages = 1)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)
        val first = repository.laborRequests.first()

        viewModel.refreshWorkspace()
        assertTrue(first.cancelled)
        val staleSuccess = repository.laborRequests.first()
        val staleFailure = repository.laborRequests.last()
        viewModel.logout()
        staleSuccess.result.complete(Result.success(laborPage("task-1", 37)))
        staleFailure.result.complete(Result.failure(IllegalStateException("stale failure")))

        assertTrue(viewModel.state.value.assemblyLaborSummary.isEmpty())
        assertEquals(null, viewModel.state.value.assemblyLaborError)
        assertTrue(viewModel.state.value.loggedIn.not())
        scope.cancel()
    }

    @Test
    fun laborChildrenAreBoundedBySmallConcurrencyLimit() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = PagingLaborRepository(total = 20, totalPages = 1)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)

        assertTrue("labor request concurrency must be bounded", repository.maxConcurrent <= 4)
        scope.cancel()
    }

    @Test
    fun manualLaborRefreshCancelsPreviousDetailAndLogoutDiscardsIt() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = PagingLaborRepository(total = 0, totalPages = 0)
        val viewModel = LogisticsViewModel(repository, scope)
        try {
            viewModel.login("assembler", "password", "device", remember = false)
            viewModel.refreshAssemblyLabor(task("detail-A", "machine-A", "M-A"))
            val older = repository.laborRequests.single()
            viewModel.refreshAssemblyLabor(task("detail-B", "machine-B", "M-B"))
            assertTrue(older.cancelled)
            val current = repository.laborRequests.last()
            viewModel.logout()
            assertTrue(current.cancelled)
            older.result.complete(Result.success(laborPage("detail-A", 37)))
            current.result.complete(Result.failure(IllegalStateException("stale detail error")))
            assertTrue(viewModel.state.value.assemblyLaborSummary.isEmpty())
            assertEquals(null, viewModel.state.value.assemblyLaborError)
        } finally { scope.cancel() }
    }

    private class PagingLaborRepository(
        private val total: Int,
        private val totalPages: Int,
    ) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val pageRequests = mutableListOf<Int>()
        val laborRequests = mutableListOf<LaborRequest>()
        var pageItems: List<AssemblyTask> = (1..minOf(total, 20)).map { task("task-$it", "machine-$it", "M-$it") }
        var lastDeviceId: String? = null
        private var concurrent = 0
        var maxConcurrent = 0

        override suspend fun login(username: String, password: String, deviceId: String, remember: Boolean) =
            com.company.logistics.model.LoginResult("access", null, null, false, User("u-1", username, "Test", UserRole.ASSEMBLER))

        override suspend fun assemblyTaskPage(page: Int, pageSize: Int, deviceNo: String?, deviceId: String?): Result<AssemblyTaskPage> {
            pageRequests += page
            lastDeviceId = deviceId
            val items = if (page == 1) pageItems else emptyList()
            return Result.success(AssemblyTaskPage(items, page, pageSize, total, totalPages, "server"))
        }

        override suspend fun assemblyTaskLaborSummary(taskId: String, assemblerId: String?): Result<LaborSummaryPage> {
            val request = LaborRequest(taskId)
            laborRequests += request
            concurrent++
            maxConcurrent = maxOf(maxConcurrent, concurrent)
            try {
                return request.result.await()
            } finally {
                concurrent--
                request.cancelled = !request.result.isCompleted
            }
        }

        override suspend fun workspaceSummary(): Result<WorkspaceSummary> = Result.success(WorkspaceSummary(role = UserRole.ASSEMBLER))
        override suspend fun workspaceMaterialItems(status: String?, orderNo: String?, page: Int, pageSize: Int): Result<WorkspaceMaterialItemsPage> =
            Result.success(WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null))
    }

    private class LaborRequest(val taskId: String) {
        val result = CompletableDeferred<Result<LaborSummaryPage>>()
        val failure = CompletableDeferred<Throwable>()
        var cancelled: Boolean = false
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
        private fun task(id: String, deviceId: String, deviceNo: String) = AssemblyTask(
            id = id, orderNo = "WO-$id", deviceId = deviceId, deviceNo = deviceNo,
            materialSummary = "material", status = AssemblyTaskStatus.WAITING_MATERIAL,
            progressStage = 0, taskVersion = 1, currentLaborRecordId = null,
            currentLaborStartedAt = null, accumulatedLaborMinutes = 0,
            assignedAssemblerId = null, assignedAssemblerName = null, members = emptyList(),
        )

        private fun laborPage(taskId: String, minutes: Int) = LaborSummaryPage(
            listOf(LaborSummaryItem(taskId, "order", "device", "device", "user", "assembler", minutes, 0, minutes))
        )
    }
}
