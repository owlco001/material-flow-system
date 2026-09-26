package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.HandoverActionResult
import com.company.logistics.model.RoleSummary
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceViewRole
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

class LogisticsViewModelAdminRolePreviewTest {

    @Test
    fun switchingRoleKeepsAuthRoleAndUsesReadOnlyProjection() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakePreviewRepository()
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("admin", "password", "device", remember = false)
        viewModel.enterRolePreview(WorkspaceViewRole.MATERIAL)

        assertEquals(UserRole.ADMIN, viewModel.state.value.role)
        assertEquals(WorkspaceViewRole.MATERIAL, viewModel.state.value.previewRole)
        assertEquals(UserRole.MATERIAL, viewModel.state.value.workspaceRole)
        assertTrue(repository.previewSummaryRoles.contains(WorkspaceViewRole.MATERIAL))
        assertTrue(repository.previewItemRoles.contains(WorkspaceViewRole.MATERIAL))

        viewModel.exitRolePreview()

        assertEquals(UserRole.ADMIN, viewModel.state.value.role)
        assertEquals(null, viewModel.state.value.previewRole)
        assertEquals(UserRole.ADMIN, viewModel.state.value.workspaceRole)
        scope.cancel()
    }

    @Test
    fun switchingRoleCancelsOldWorkspaceLoadBeforeShowingNewRole() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakePreviewRepository()
        repository.blockRole = WorkspaceViewRole.OPERATOR
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("admin", "password", "device", remember = false)
        viewModel.enterRolePreview(WorkspaceViewRole.OPERATOR)
        viewModel.enterRolePreview(WorkspaceViewRole.WAREHOUSE_ADMIN)

        assertEquals(WorkspaceViewRole.WAREHOUSE_ADMIN, viewModel.state.value.previewRole)
        assertTrue(repository.previewItemRoles.contains(WorkspaceViewRole.WAREHOUSE_ADMIN))
        assertTrue(repository.blockedLoadCancelled)
        scope.cancel()
    }

    @Test
    fun previewWriteActionsAreStoppedBeforeRepository() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakePreviewRepository()
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        viewModel.enterRolePreview(WorkspaceViewRole.MATERIAL)

        viewModel.createHandover(repository.item, 1, "A-01")
        viewModel.approveTransferRequest("request-1", approve = true)

        assertEquals(0, repository.createHandoverCalls)
        assertEquals(0, repository.approveCalls)
        assertTrue(viewModel.state.value.error!!.contains("只读"))
        scope.cancel()
    }

    private class FakePreviewRepository : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val previewSummaryRoles = mutableListOf<WorkspaceViewRole?>()
        val previewItemRoles = mutableListOf<WorkspaceViewRole?>()
        var createHandoverCalls = 0
        var approveCalls = 0
        var blockRole: WorkspaceViewRole? = null
        var blockedLoadCancelled = false
        val blockedLoad = CompletableDeferred<Result<RoleSummary>>()

        override suspend fun login(
            username: String,
            password: String,
            deviceId: String,
            remember: Boolean,
        ) = com.company.logistics.model.LoginResult(
            accessToken = "access",
            refreshToken = null,
            expiresAt = null,
            mustChangePassword = false,
            user = User("admin-1", username, "管理员", UserRole.ADMIN),
        )

        override suspend fun workspaceSummary(): Result<WorkspaceSummary> = Result.success(WorkspaceSummary())

        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
        ): Result<WorkspaceMaterialItemsPage> = Result.success(emptyPage(page, pageSize))

        override suspend fun loadRoleSummary(
            viewRole: WorkspaceViewRole?,
            requestId: String,
        ): Result<RoleSummary> {
            previewSummaryRoles += viewRole
            if (viewRole == blockRole) {
                return try {
                    blockedLoad.await()
                } catch (cancelled: kotlinx.coroutines.CancellationException) {
                    blockedLoadCancelled = true
                    throw cancelled
                }
            }
            return Result.success(
                WorkspaceSummary(
                    role = viewRole?.toUserRole() ?: UserRole.ADMIN,
                    preview = viewRole != null,
                    authenticatedRole = UserRole.ADMIN,
                )
            )
        }

        override suspend fun listWorkItems(
            viewRole: WorkspaceViewRole?,
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
            requestId: String,
        ): Result<WorkspaceMaterialItemsPage> {
            previewItemRoles += viewRole
            return Result.success(emptyPage(page, pageSize))
        }

        override suspend fun createHandover(
            clientOperationId: String,
            workItemId: String,
            transferRequestId: String,
            quantity: Int,
            fromLocation: String,
            deviceId: String?,
            receiverUserId: String?,
            remark: String?,
        ): Result<HandoverActionResult> {
            createHandoverCalls++
            return Result.success(HandoverActionResult("handover-1", "PENDING"))
        }

        override suspend fun approveTransferRequest(
            transferRequestId: String,
            approve: Boolean,
            comment: String,
            clientOperationId: String,
            requestId: String,
        ): Result<com.company.logistics.data.remote.ApprovalDecisionResult> {
            approveCalls++
            return Result.failure(UnsupportedOperationException())
        }

        fun emptyPage(page: Int, pageSize: Int) = WorkspaceMaterialItemsPage(
            items = emptyList(),
            page = page,
            pageSize = pageSize,
            total = 0,
            totalPages = 0,
            serverTime = null,
            traceId = null,
        )

        val item = WorkspaceMaterialItem(
            id = "item-1",
            requirementId = null,
            orderNo = "PO-1",
            productName = null,
            orderStatus = null,
            deviceId = null,
            deviceType = null,
            deviceNo = null,
            materialId = "material-1",
            materialCode = "MAT-1",
            materialName = "测试物料",
            specification = null,
            unit = "件",
            requiredQuantity = 1,
            arrivedQuantity = 1,
            inStockQuantity = 0,
            issuedQuantity = 1,
            pickedQuantity = 0,
            statusCode = "PENDING",
            statusLabel = "待交接",
            colorToken = "STATUS-YELLOW",
            statusDomain = "HANDOVER",
            updatedAt = null,
            assignedUserId = "operator-1",
            assignedUserName = "操作员",
            currentOwnerUserId = "operator-1",
            currentOwnerName = "操作员",
            responsibilitySummary = null,
            lastHandoverId = "handover-1",
            lastHandoverStatus = "PENDING",
            lastHandover = null,
            handoverSummary = null,
            transferRequestId = "request-1",
            transferStatus = "EXECUTED",
        )
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
