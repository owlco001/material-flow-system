package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.ApiException
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

/** Regression coverage for same-VM order-cache identity and material-status generations. */
class MultiOrderSessionPagingRegressionTest {
    @Test
    fun logoutAndLowerPrivilegeLoginCannotRestorePreviousAccountsOrderCache() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin-A"] = UserRole.ADMIN
            repo.roleByUser["operator-B"] = UserRole.OPERATOR
            vm.login("admin-A", "password", "device", remember = false)
            vm.openOrderDetail("SECRET-A")
            assertEquals("SECRET-A", vm.state.value.orderDetail?.orderNo)
            val beforeLogoutRequests = repo.detailRequests.count { it == "SECRET-A" }

            vm.logout()
            vm.login("operator-B", "password", "device", remember = false)
            vm.openOrderDetail("VISIBLE-B")
            assertEquals("VISIBLE-B", vm.state.value.orderDetail?.orderNo)
            assertFalse(vm.state.value.multiOrderSnapshot.entries.any { it.orderNo == "SECRET-A" })

            vm.selectOrder("SECRET-A")
            assertEquals("A lower-privilege identity must not receive A's cached content", null,
                vm.state.value.orderDetail?.materials?.singleOrNull()?.name)
            assertEquals(beforeLogoutRequests + 1, repo.detailRequests.count { it == "SECRET-A" })
            assertTrue(vm.state.value.error?.contains("无权") == true)
        } finally { scope.cancel() }
    }

    @Test
    fun orderCacheIsClearedWhenAdminChangesRolePreviewScope() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin"] = UserRole.ADMIN
            vm.login("admin", "password", "device", remember = false)
            vm.openOrderDetail("ADMIN-ONLY")
            assertEquals("ADMIN-ONLY", vm.state.value.orderDetail?.orderNo)

            assertTrue(vm.enterRolePreview(WorkspaceViewRole.OPERATOR).isSuccess)
            assertTrue(vm.state.value.multiOrderSnapshot.entries.isEmpty())
            assertNull(vm.state.value.orderDetail)
            assertNull(vm.state.value.orderStatus)

            assertTrue(vm.exitRolePreview().isSuccess)
            assertTrue(vm.state.value.multiOrderSnapshot.entries.isEmpty())
            assertNull(vm.state.value.orderDetail)
        } finally { scope.cancel() }
    }

    @Test
    fun selectingSameOrderDuringItsRefreshDoesNotStartDuplicateDetailRequests() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin"] = UserRole.ADMIN
            val pending = CompletableDeferred<Result<OrderDetail>>()
            repo.detailOverride["A"] = pending
            vm.login("admin", "password", "device", remember = false)
            vm.openOrderDetail("A")
            assertEquals(1, repo.detailRequests.count { it == "A" })
            vm.selectOrder("A")
            assertEquals("a tab switch should reuse the in-flight per-order request", 1, repo.detailRequests.count { it == "A" })
            pending.complete(Result.success(detail("A", "fresh-A")))
            assertEquals("A", vm.state.value.orderDetail?.orderNo)
            assertEquals("A", vm.state.value.orderStatus?.documentNo)
        } finally { scope.cancel() }
    }

    @Test
    fun lateRefreshOfPreviouslySelectedOrderCannotReplaceCurrentOrderProjection() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin"] = UserRole.ADMIN
            val oldA = CompletableDeferred<Result<OrderDetail>>()
            repo.detailOverride["A"] = oldA
            vm.login("admin", "password", "device", remember = false)
            vm.openOrderDetail("A")
            vm.openOrderDetail("B")
            assertEquals("B", vm.state.value.orderDetail?.orderNo)

            oldA.complete(Result.success(detail("A", "old-A")))
            assertEquals("B", vm.state.value.multiOrderSnapshot.selectedOrderNo)
            assertEquals("B", vm.state.value.orderDetail?.orderNo)
            assertEquals("old-A", vm.state.value.multiOrderSnapshot.entries.single { it.orderNo == "A" }.state.content?.productName)
        } finally { scope.cancel() }
    }

    @Test
    fun lateFailureOfPreviouslySelectedOrderCannotReplaceCurrentOrderErrorOrProjection() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin"] = UserRole.ADMIN
            val oldA = CompletableDeferred<Result<OrderDetail>>()
            repo.detailOverride["A"] = oldA
            vm.login("admin", "password", "device", remember = false)
            vm.openOrderDetail("A")
            vm.openOrderDetail("B")
            oldA.complete(Result.failure(IllegalStateException("stale A failure")))
            assertEquals("B", vm.state.value.multiOrderSnapshot.selectedOrderNo)
            assertEquals("B", vm.state.value.orderDetail?.orderNo)
            assertNull(vm.state.value.error)
            assertTrue(vm.state.value.multiOrderSnapshot.entries.single { it.orderNo == "A" }.state.error?.isNotBlank() == true)
        } finally { scope.cancel() }
    }

    @Test
    fun oldAppendCannotMergeIntoSameOrderAfterRefreshStarts() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin"] = UserRole.ADMIN
            vm.login("admin", "password", "device", remember = false)
            repo.materialOverrides += CompletableDeferred(Result.success(page("A", page=1, hasMore=true, summary=200, materials=listOf(material("initial")))))
            vm.openOrderDetail("A")
            val stalePage2 = CompletableDeferred<Result<OrderMaterialStatus>>()
            repo.materialOverrides += stalePage2
            vm.loadMoreOrderMaterials()
            assertEquals(2, repo.materialPages.last())

            val freshPage1 = page("A", page=1, hasMore=true, summary=100, materials=listOf(material("fresh-page-1")))
            repo.materialOverrides += CompletableDeferred(Result.success(freshPage1))
            vm.refreshOrderDetail()
            assertEquals(1, repo.materialPages.last())
            assertEquals(setOf("fresh-page-1"), vm.state.value.orderStatus?.items?.map { it.materialId }?.toSet())

            stalePage2.complete(Result.success(page("A", page=2, hasMore=true, summary=999, materials=listOf(material("stale-page-2")))))
            val current = vm.state.value.orderStatus!!
            assertEquals(setOf("fresh-page-1"), current.items.map { it.materialId }.toSet())
            assertEquals(1, current.page)
            assertEquals(100, current.summaryRequired)
            assertTrue(current.hasMore)
            assertEquals(listOf(1, 2, 1), repo.materialPages)
        } finally { scope.cancel() }
    }

    @Test
    fun selectingCachedOrderRestoresItsAuthoritativeMaterialPaginationAndProjection() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repo = OrderRepository()
        val vm = LogisticsViewModel(repo, scope)
        try {
            repo.roleByUser["admin"] = UserRole.ADMIN
            vm.login("admin", "password", "device", remember = false)
            val firstA = page("A", page=1, hasMore=true, summary=450, materials=listOf(material("A1", workflow="OUTBOUND_PENDING")))
            repo.materialOverrides += CompletableDeferred(Result.success(firstA))
            vm.openOrderDetail("A")
            val secondA = page("A", page=2, hasMore=true, summary=450,
                materials=firstA.items + material("A2", workflow="PICKED_UP"))
            repo.materialOverrides += CompletableDeferred(Result.success(secondA))
            vm.loadMoreOrderMaterials()

            vm.openOrderDetail("B")
            val aRequests = repo.materialOrders.zip(repo.materialPages).count { (order, _) -> order == "A" }
            vm.selectOrder("A")
            val restored = vm.state.value.orderStatus!!
            assertEquals(setOf("A1", "A2"), restored.items.map { it.materialId }.toSet())
            assertEquals(2, restored.page)
            assertTrue(restored.hasMore)
            assertEquals(450, restored.summaryRequired)
            assertEquals("PICKED_UP", restored.items.single { it.materialId == "A2" }.effectiveStatusCode)
            vm.loadMoreOrderMaterials()
            assertEquals(aRequests + 1, repo.materialOrders.zip(repo.materialPages).count { (order, _) -> order == "A" })
            assertEquals(3, repo.materialPages.last())
        } finally { scope.cancel() }
    }

    private class OrderRepository : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val roleByUser = mutableMapOf<String, UserRole>()
        val detailRequests = mutableListOf<String>()
        val detailOverride = mutableMapOf<String, CompletableDeferred<Result<OrderDetail>>>()
        val materialOrders = mutableListOf<String>()
        val materialPages = mutableListOf<Int>()
        val materialOverrides = ArrayDeque<CompletableDeferred<Result<OrderMaterialStatus>>>()
        private var currentUser: String? = null

        override suspend fun login(username: String, password: String, deviceId: String, remember: Boolean) =
            LoginResult("access-$username", "refresh-$username", null, false,
                User(username, username, username, roleByUser.getValue(username))).also { currentUser = username }

        override suspend fun workspaceSummary() = Result.success(WorkspaceSummary())
        override suspend fun workspaceMaterialItems(status: String?, orderNo: String?, page: Int, pageSize: Int) =
            Result.success(WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null))

        override suspend fun orderDetail(orderNo: String, page: Int, pageSize: Int, materialsPage: Int, materialsPageSize: Int): Result<OrderDetail> {
            detailRequests += orderNo
            if (orderNo == "SECRET-A" && currentUser != "admin-A") return Result.failure(ApiException(403, "FORBIDDEN", "forbidden"))
            return detailOverride[orderNo]?.await() ?: Result.success(detail(orderNo, "detail-$orderNo"))
        }

        override suspend fun orderMaterialStatus(documentNo: String, page: Int, pageSize: Int): Result<OrderMaterialStatus> {
            materialOrders += documentNo
            materialPages += page
            if (materialOverrides.isNotEmpty()) return materialOverrides.removeFirst().await()
            return Result.success(page(documentNo, page=page, hasMore=false, summary=100,
                materials=listOf(material("$documentNo-$page"))))
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
        private fun detail(order: String, marker: String) = OrderDetail(
            order, order, marker, "OPEN", listOf(material(marker)), emptyList(),
            OrderDetailLaborSummary(0,0,0), emptyList(), 1, 20, 1,
        )
        private fun material(id: String, workflow: String = "IN_STOCK") = OrderMaterialItem(
            "device", "TYPE", "DEVICE", id, id, id, null, 1, 1, 1,
            MaterialStatusCode.IN_STOCK, "在库", colorToken = "synthetic-color", workflowStatusCode = workflow,
        )
        private fun page(order: String, page: Int, hasMore: Boolean, summary: Int, materials: List<OrderMaterialItem>) =
            OrderMaterialStatus(order, "PRODUCTION_ORDER", materials, "server", total=450, page=page, pageSize=200,
                hasMore=hasMore, summaryRequired=summary, summaryInStock=summary, shortageLineCount=0)
    }
}
