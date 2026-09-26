package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LogisticsViewModelWorkspaceTest {

    @Test
    fun paginationGuardsNeverRequestOutsideServerPages() {
        assertEquals(20, LogisticsUiState.WORKSPACE_PAGE_SIZE)
        assertEquals(setOf(20, 50), LogisticsViewModel.WORKSPACE_PAGE_SIZES)
        assertTrue(LogisticsViewModel.canLoadNextWorkspacePage(1, 2))
        assertFalse(LogisticsViewModel.canLoadNextWorkspacePage(2, 2))
        assertFalse(LogisticsViewModel.canLoadNextWorkspacePage(0, 2))
        assertTrue(LogisticsViewModel.canLoadPreviousWorkspacePage(2))
        assertFalse(LogisticsViewModel.canLoadPreviousWorkspacePage(1))
    }

    @Test
    fun loginLoadsServerWorkspaceAndKeepsEmptyPageExplicit() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeWorkspaceRepository(
            summaryResult = Result.success(WorkspaceSummary(pickedUpCount = 0)),
            itemsResult = Result.success(
                WorkspaceMaterialItemsPage(
                    items = emptyList(),
                    page = 1,
                    pageSize = 20,
                    total = 0,
                    totalPages = 0,
                    serverTime = "server-time",
                    traceId = "trace-items"
                )
            )
        )
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("operator", "password", "device", remember = false)

        assertEquals(WorkspaceLoadState.CONTENT, viewModel.state.value.workspaceSummaryState)
        assertEquals(WorkspaceLoadState.EMPTY, viewModel.state.value.workspaceItemsState)
        assertEquals(0, viewModel.state.value.workspaceTotal)
        assertEquals(0, viewModel.state.value.workspaceSummary.metric(com.company.logistics.model.WorkspaceMetricKey.CLAIMED).count)
        assertFalse(viewModel.state.value.workspaceSummary.metric(com.company.logistics.model.WorkspaceMetricKey.OUTBOUND_PENDING).available)
        scope.cancel()
    }

    @Test
    fun unavailableWorkspaceEndpointIsShownAsUnavailable() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeWorkspaceRepository(
            summaryResult = Result.failure(ApiException(404, "NOT_FOUND", "not found")),
            itemsResult = Result.failure(ApiException(404, "NOT_FOUND", "not found"))
        )
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("operator", "password", "device", remember = false)

        assertEquals(WorkspaceLoadState.ERROR, viewModel.state.value.workspaceSummaryState)
        assertEquals(WorkspaceLoadState.ERROR, viewModel.state.value.workspaceItemsState)
        assertTrue(viewModel.state.value.workspaceSummaryUnavailable)
        assertTrue(viewModel.state.value.workspaceItemsUnavailable)
        assertTrue(viewModel.state.value.workspaceSummaryError!!.contains("不可用"))
        scope.cancel()
    }

    @Test
    fun pageSizeCanSwitchOnlyBetweenTwentyAndFifty() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeWorkspaceRepository(
            summaryResult = Result.success(WorkspaceSummary()),
            itemsResult = Result.success(
                WorkspaceMaterialItemsPage(emptyList(), 1, 50, 0, 0, null, null)
            )
        )
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("operator", "password", "device", remember = false)

        viewModel.setWorkspacePageSize(50)

        assertEquals(50, viewModel.state.value.workspacePageSize)
        assertTrue(viewModel.state.value.workspaceItemsState == WorkspaceLoadState.EMPTY)
        scope.cancel()
    }

    private class FakeWorkspaceRepository(
        private val summaryResult: Result<WorkspaceSummary>,
        private val itemsResult: Result<WorkspaceMaterialItemsPage>
    ) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        override suspend fun login(
            username: String,
            password: String,
            deviceId: String,
            remember: Boolean
        ) = com.company.logistics.model.LoginResult(
            accessToken = "access",
            refreshToken = null,
            expiresAt = null,
            mustChangePassword = false,
            user = com.company.logistics.model.User(
                id = "u-1",
                username = username,
                displayName = "操作员",
                role = com.company.logistics.model.UserRole.OPERATOR
            )
        )

        override suspend fun workspaceSummary(): Result<WorkspaceSummary> = summaryResult

        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int
        ): Result<WorkspaceMaterialItemsPage> {
            assertTrue(pageSize == 20 || pageSize == 50)
            return itemsResult.map { it.copy(pageSize = pageSize) }
        }
    }

    private class NoOpDao : OfflineOperationDao {
        override fun observeAll(): Flow<List<OfflineOperationEntity>> = emptyFlow()
        override suspend fun pending(): List<OfflineOperationEntity> = emptyList()
        override fun observePendingCount(): Flow<Int> = emptyFlow()
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
