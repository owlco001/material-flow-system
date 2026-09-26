package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.ApprovalDecisionResult
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.AuditLog
import com.company.logistics.model.AuditLogPage
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestPage
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

class LogisticsViewModelApprovalAuditTest {

    @Test
    fun approvalListDetailAndActionRefreshUseServerRecords() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val request = transferRequest("PENDING_APPROVAL")
        val repository = FakeApprovalRepository(listOf(request))
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)

        viewModel.refreshTransferRequests()
        assertEquals(WorkspaceLoadState.CONTENT, viewModel.state.value.transferRequestState)
        assertEquals("tr-1", viewModel.state.value.transferRequests.single().id)

        viewModel.selectTransferRequest("tr-1")
        assertEquals(WorkspaceLoadState.CONTENT, viewModel.state.value.transferRequestDetailState)
        assertEquals("tr-1", viewModel.state.value.transferRequestDetail?.id)

        viewModel.approveTransferRequest("tr-1", approve = true)

        assertEquals(1, repository.approveCalls)
        assertTrue(repository.transferRequestListCalls >= 2)
        assertTrue(repository.transferRequestDetailCalls >= 2)
        assertEquals(null, viewModel.state.value.transferDecisionSubmittingId)
        scope.cancel()
    }

    @Test
    fun repeatedClickWhileActionIsInFlightSubmitsOnlyOnce() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeApprovalRepository(listOf(transferRequest("PENDING_APPROVAL")))
        val result = CompletableDeferred<Result<ApprovalDecisionResult>>()
        repository.approveDeferred = result
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        viewModel.refreshTransferRequests()

        viewModel.approveTransferRequest("tr-1", approve = true)
        viewModel.approveTransferRequest("tr-1", approve = true)

        assertEquals(1, repository.approveCalls)
        assertTrue(viewModel.state.value.transferDecisionSubmittingId == "tr-1")
        result.complete(Result.success(ApprovalDecisionResult("tr-1", com.company.logistics.model.ApprovalStatus.APPROVED)))
        assertEquals(null, viewModel.state.value.transferDecisionSubmittingId)
        scope.cancel()
    }

    @Test
    fun retryAfterNetworkFailureReusesTheSameActionIdempotencyIdentity() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeApprovalRepository(listOf(transferRequest("PENDING_APPROVAL")))
        repository.approveResult = Result.failure(RuntimeException("network"))
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        viewModel.refreshTransferRequests()

        viewModel.approveTransferRequest("tr-1", approve = true)
        viewModel.approveTransferRequest("tr-1", approve = true)

        assertEquals(2, repository.approveCalls)
        assertEquals(repository.approveOperationIds[0], repository.approveOperationIds[1])
        assertEquals(repository.approveRequestIds[0], repository.approveRequestIds[1])
        scope.cancel()
    }

    @Test
    fun serverErrorsMapToSafeMessagesAndConflictRefreshesTheList() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeApprovalRepository(listOf(transferRequest("PENDING_APPROVAL")))
        repository.approveResult = Result.failure(ApiException(403, "FORBIDDEN", "server detail"))
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        viewModel.refreshTransferRequests()
        viewModel.approveTransferRequest("tr-1", approve = true)
        assertTrue(viewModel.state.value.error!!.contains("无权"))

        repository.approveResult = Result.failure(ApiException(409, "TRANSFER_STATE_CONFLICT", "server detail"))
        viewModel.approveTransferRequest("tr-1", approve = true)
        assertTrue(viewModel.state.value.error!!.contains("状态已变化"))
        assertTrue(repository.transferRequestListCalls >= 2)
        scope.cancel()
    }

    @Test
    fun auditPagerUsesServerPageAndRejectsUnauthorizedRead() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeApprovalRepository(listOf(transferRequest("PENDING_APPROVAL")))
        repository.auditResult = { page, pageSize ->
            Result.success(
                AuditLogPage(
                    items = if (page == 1) listOf(auditLog(1L)) else listOf(auditLog(2L)),
                    page = page,
                    pageSize = pageSize,
                    hasNext = page == 1,
                    serverTime = "server-$page",
                )
            )
        }
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        viewModel.refreshAuditLogs()
        assertEquals(1, viewModel.state.value.auditPage)
        assertEquals(WorkspaceLoadState.CONTENT, viewModel.state.value.auditState)

        viewModel.loadNextAuditPage()
        assertEquals(2, viewModel.state.value.auditPage)
        assertEquals(2L, viewModel.state.value.auditLogs.single().id)
        assertEquals(LogisticsUiState.AUDIT_PAGE_SIZE, repository.auditPageSizes.last())

        repository.auditResult = { _, _ ->
            Result.failure(ApiException(401, "UNAUTHORIZED", "server detail"))
        }
        viewModel.refreshAuditLogs()
        assertTrue(viewModel.state.value.auditError!!.contains("登录已失效"))
        scope.cancel()
    }

    private fun transferRequest(status: String) = TransferRequest(
        id = "tr-1",
        clientOperationId = "operation-1",
        type = "OUTBOUND",
        documentNo = "PO-1",
        status = status,
        items = emptyList(),
        createdBy = "u-creator",
        createdAt = "server",
    )

    private fun auditLog(id: Long) = AuditLog(
        id = id,
        operatorId = "u-admin",
        role = "ADMIN",
        action = "EXECUTE",
        resourceType = "TRANSFER_REQUEST",
        resourceId = "tr-1",
        requestId = "request-$id",
        deviceId = "device",
        occurredAt = "server",
        result = "SUCCESS",
    )

    private class FakeApprovalRepository(
        private val requests: List<TransferRequest>,
    ) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        var transferRequestListCalls = 0
        var transferRequestDetailCalls = 0
        var approveCalls = 0
        val approveOperationIds = mutableListOf<String>()
        val approveRequestIds = mutableListOf<String>()
        val auditPageSizes = mutableListOf<Int>()
        var approveResult: Result<ApprovalDecisionResult> = Result.success(
            ApprovalDecisionResult("tr-1", com.company.logistics.model.ApprovalStatus.APPROVED)
        )
        var approveDeferred: CompletableDeferred<Result<ApprovalDecisionResult>>? = null
        var auditResult: (suspend (Int, Int) -> Result<AuditLogPage>) = { page, pageSize ->
            Result.success(AuditLogPage(emptyList(), page, pageSize, false))
        }

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
            user = User("u-admin", username, "管理员", UserRole.ADMIN),
        )

        override suspend fun workspaceSummary(): Result<WorkspaceSummary> = Result.success(WorkspaceSummary())

        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
        ): Result<WorkspaceMaterialItemsPage> = Result.success(
            WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null)
        )

        override suspend fun transferRequests(status: String?): Result<TransferRequestPage> {
            transferRequestListCalls++
            return Result.success(TransferRequestPage(requests, statusFilter = status, serverTime = "server"))
        }

        override suspend fun transferRequestDetail(requestId: String): Result<TransferRequest> {
            transferRequestDetailCalls++
            return Result.success(requests.single { it.id == requestId })
        }

        override suspend fun approveTransferRequest(
            transferRequestId: String,
            approve: Boolean,
            comment: String,
            clientOperationId: String,
            requestId: String,
        ): Result<ApprovalDecisionResult> {
            approveCalls++
            approveOperationIds += clientOperationId
            approveRequestIds += requestId
            return approveDeferred?.await() ?: approveResult
        }

        override suspend fun auditLogs(page: Int, pageSize: Int): Result<AuditLogPage> {
            auditPageSizes += pageSize
            return auditResult(page, pageSize)
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
