package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.HandoverActionResult
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LogisticsViewModelHandoverTest {

    @Test
    fun operatorCanConfirmOnlyAnAssignedPendingServerHandover() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeHandoverRepository(
            role = UserRole.OPERATOR,
            item = pendingItem(receiverUserId = "u-operator"),
        )
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("operator", "password", "device", remember = false)

        viewModel.decideHandover(repository.item, HandoverAction.CONFIRM)

        assertEquals(1, repository.decideCalls)
        assertEquals(HandoverAction.CONFIRM, repository.lastAction)
        assertEquals(WorkspaceLoadState.CONTENT, viewModel.state.value.workspaceTimelineState)
        scope.cancel()
    }

    @Test
    fun materialCannotConfirmAndForbiddenActionNeverReachesRepository() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeHandoverRepository(
            role = UserRole.MATERIAL,
            item = pendingItem(receiverUserId = "u-operator"),
        )
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("material", "password", "device", remember = false)

        viewModel.decideHandover(repository.item, HandoverAction.CONFIRM)

        assertEquals(0, repository.decideCalls)
        assertTrue(viewModel.state.value.error!!.contains("不允许"))
        scope.cancel()
    }

    @Test
    fun networkRetryReusesClientOperationIdForTheSameActionPayload() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeHandoverRepository(
            role = UserRole.OPERATOR,
            item = pendingItem(receiverUserId = "u-operator"),
            decideResult = Result.failure(RuntimeException("network")),
        )
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("operator", "password", "device", remember = false)

        viewModel.decideHandover(repository.item, HandoverAction.CONFIRM)
        viewModel.decideHandover(repository.item, HandoverAction.CONFIRM)

        assertEquals(2, repository.operationIds.size)
        assertEquals(repository.operationIds[0], repository.operationIds[1])
        assertEquals(repository.requestIds[0], repository.requestIds[1])
        assertTrue(repository.requestIds[0] != null)
        assertTrue(viewModel.state.value.error!!.contains("网络不可用"))
        scope.cancel()
    }

    private class FakeHandoverRepository(
        private val role: UserRole,
        val item: WorkspaceMaterialItem,
        private val decideResult: Result<HandoverActionResult> = Result.success(
            HandoverActionResult(item.lastHandoverId.orEmpty(), "CONFIRMED")
        ),
    ) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        var decideCalls: Int = 0
        var timelineCalls: Int = 0
        var lastAction: HandoverAction? = null
        val operationIds = mutableListOf<String>()
        val requestIds = mutableListOf<String?>()

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
            user = User("u-${role.name.lowercase()}", username, role.label, role),
        )

        override suspend fun workspaceSummary(): Result<WorkspaceSummary> =
            Result.success(WorkspaceSummary(role = role))

        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
        ): Result<WorkspaceMaterialItemsPage> = Result.success(
            WorkspaceMaterialItemsPage(listOf(item), page, pageSize, 1, 1, "server", "trace")
        )

        override suspend fun decideHandover(
            handoverId: String,
            action: HandoverAction,
            clientOperationId: String,
            reason: String?,
            requestId: String?,
        ): Result<HandoverActionResult> {
            decideCalls++
            lastAction = action
            operationIds += clientOperationId
            requestIds += requestId
            return decideResult
        }

        override suspend fun handoverTimeline(
            workItemId: String,
            page: Int,
            pageSize: Int,
        ): Result<HandoverTimeline> {
            timelineCalls++
            return Result.success(
                HandoverTimeline(
                    handoverId = item.lastHandoverId.orEmpty(),
                    workItemId = workItemId,
                    status = "CONFIRMED",
                    workspaceStatus = "AT_STATION",
                    items = listOf(
                        com.company.logistics.model.HandoverTimelineEvent(
                            id = "event-1",
                            eventType = "HANDOVER_CONFIRMED",
                            actorUserId = "u-operator",
                            actorRole = "OPERATOR",
                            requestId = "request-1",
                            clientOperationId = "operation-1",
                            serverTime = "server",
                            result = "SUCCESS",
                        )
                    ),
                    serverTime = "server",
                )
            )
        }
    }

    private fun pendingItem(receiverUserId: String) = WorkspaceMaterialItem(
        id = "wi-1",
        requirementId = "req-1",
        orderNo = "PO-1",
        productName = "产品",
        orderStatus = "RELEASED",
        deviceId = "device-1",
        deviceType = "BUFFER",
        deviceNo = "D-01",
        materialId = "mat-1",
        materialCode = "MAT-001",
        materialName = "物料",
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
        updatedAt = "server",
        assignedUserId = receiverUserId,
        assignedUserName = "操作员",
        currentOwnerUserId = receiverUserId,
        currentOwnerName = "操作员",
        responsibilitySummary = null,
        lastHandoverId = "ho-1",
        lastHandoverStatus = "PENDING",
        lastHandover = com.company.logistics.model.WorkspaceLastHandover(
            id = "ho-1",
            status = "PENDING",
            quantity = 1,
            fromLocation = "A-01",
            deviceId = "device-1",
            transferRequestId = "tr-1",
            senderUserId = "u-material",
            senderName = "物料员",
            receiverUserId = receiverUserId,
            receiverName = "操作员",
            initiatedAt = "server",
            confirmedBy = null,
            confirmedAt = null,
            remark = null,
        ),
        handoverSummary = null,
        transferRequestId = "tr-1",
        transferStatus = "EXECUTED",
    )

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
