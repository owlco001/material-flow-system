package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import org.junit.Assert.*
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class DeviceAggregateConcurrencyTest {
    @Test fun tasksPublishWhileLaborIsStillPending() = fixture { vm, repo ->
        repo.holdLabor = true
        vm.openDeviceDetail("A")
        assertTrue("labor must already be in flight", "labor:A" in repo.active)
        assertEquals(listOf("A-task"), vm.state.value.deviceAssemblyTasks.map { it.id })
        assertNull(vm.state.value.deviceLabor)
        repo.release.complete(Unit)
        assertNotNull(vm.state.value.deviceLabor)
    }

    @Test fun taskFailurePublishesWhileLaborKeepsRunning() = fixture { vm, repo ->
        repo.failTasks = true
        repo.holdLabor = true
        vm.openDeviceDetail("A")
        assertTrue("labor must keep running after task failure", "labor:A" in repo.active)
        assertTrue(vm.state.value.deviceAggregateError?.contains("装配任务") == true)
        assertNull(vm.state.value.error)
        repo.release.complete(Unit)
        assertNotNull(vm.state.value.deviceLabor)
        assertTrue(vm.state.value.deviceAggregateError?.contains("装配任务") == true)
    }

    @Test fun navigatingAwayCancelsBothInflightChildren() = fixture { vm, repo ->
        repo.holdTasks = true
        repo.holdLabor = true
        vm.openDeviceDetail("A")
        assertEquals(setOf("task:A", "labor:A"), repo.active)
        vm.navigate(Screen.SCANNER)
        assertTrue(repo.active.isEmpty())
        assertEquals(setOf("task:A", "labor:A"), repo.cancelled)
        assertEquals(Screen.SCANNER, vm.state.value.screen)
    }

    @Test fun reopeningPartiallyLoadedMachineRestartsCancelledAggregate() = fixture { vm, repo ->
        repo.holdLabor = true
        vm.openDeviceDetail("A")
        assertEquals(WorkspaceLoadState.CONTENT, vm.state.value.deviceAggregateSections[DeviceAggregateSection.TASKS])
        assertEquals(WorkspaceLoadState.LOADING, vm.state.value.deviceAggregateSections[DeviceAggregateSection.LABOR])
        assertTrue("labor:A" in repo.active)

        vm.navigate(Screen.SCANNER)
        assertTrue("labor:A" in repo.cancelled)
        assertTrue(repo.active.isEmpty())
        vm.navigate(Screen.DEVICE_DETAIL)

        assertEquals(Screen.DEVICE_DETAIL, vm.state.value.screen)
        assertEquals("A", vm.state.value.deviceDetail?.deviceId)
        assertTrue("reopening must restart the cancelled labor request", "labor:A" in repo.active)
        repo.release.complete(Unit)
        assertEquals(WorkspaceLoadState.CONTENT, vm.state.value.deviceAggregateSections[DeviceAggregateSection.LABOR])
        assertEquals("A", vm.state.value.deviceLabor?.items?.single()?.deviceId)
    }

    @Test fun successiveMachinesShareFourPermitsEvenWhenOldRequestsIgnoreCancellation() = fixture { vm, repo ->
        repo.holdTasks = true
        repo.holdLabor = true
        repo.ignoreCancellation = true
        repeat(7) { vm.openDeviceDetail("M$it") }
        assertTrue("multiple sections should have entered", repo.peak > 1)
        assertTrue("old requests must count against the shared limit", repo.peak <= 4)
        assertFalse(repo.release.isCompleted)
        repo.release.complete(Unit)
        assertEquals("M6", vm.state.value.deviceDetail?.deviceId)
        assertEquals(listOf("M6-task"), vm.state.value.deviceAssemblyTasks.map { it.id })
        assertEquals("M6", vm.state.value.deviceLabor?.items?.single()?.deviceId)
        assertNull(vm.state.value.deviceAggregateError)
    }

    @Test fun machineWithoutOrdersDoesNotReadTransfers() = fixture { vm, repo ->
        vm.openDeviceDetail("A")
        assertTrue(vm.state.value.deviceDetail!!.orders.isEmpty())
        assertEquals(0, repo.globalTransferCalls)
        assertEquals(0, repo.scopedTransferCalls)
        assertTrue(vm.state.value.deviceTransferRequests.isEmpty())
    }

    @Test fun fourSectionsTrackLoadingAndUseExactOrderScope() = fixture { vm, repo ->
        repo.orders = listOf("ORDER-B", "ORDER-A", "ORDER-B")
        repo.holdTasks = true
        repo.holdLabor = true
        repo.holdMaterials = true
        repo.holdTransfers = true
        vm.openDeviceDetail("A")
        assertEquals(setOf("task:A", "labor:A", "material:ORDER-B", "transfer"), repo.active)
        assertEquals(DeviceAggregateSection.entries.associateWith { WorkspaceLoadState.LOADING },
            vm.state.value.deviceAggregateSections)
        assertEquals(listOf(setOf("ORDER-B", "ORDER-A")), repo.scopedTransferOrders)
        assertEquals(0, repo.globalTransferCalls)

        repo.release.complete(Unit)
        assertEquals(listOf("ORDER-B", "ORDER-A"), repo.materialOrders)
        assertEquals(mapOf(
            DeviceAggregateSection.TASKS to WorkspaceLoadState.CONTENT,
            DeviceAggregateSection.MATERIALS to WorkspaceLoadState.EMPTY,
            DeviceAggregateSection.TRANSFERS to WorkspaceLoadState.EMPTY,
            DeviceAggregateSection.LABOR to WorkspaceLoadState.CONTENT,
        ), vm.state.value.deviceAggregateSections)
    }

    private fun fixture(test: (LogisticsViewModel, DeviceRepository) -> Unit) {
        val scope = CoroutineScope(SupervisorJob() + UnconfinedTestDispatcher())
        val repo = DeviceRepository()
        try {
            val vm = LogisticsViewModel(repo, scope)
            vm.login("admin", "password", "device", false)
            test(vm, repo)
        } finally {
            scope.cancel()
            repo.release.complete(Unit)
        }
    }

    private class DeviceRepository : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val release = CompletableDeferred<Unit>()
        val active = mutableSetOf<String>()
        val cancelled = mutableSetOf<String>()
        var peak = 0
        var holdTasks = false
        var holdLabor = false
        var holdMaterials = false
        var holdTransfers = false
        var orders = emptyList<String>()
        val materialOrders = mutableListOf<String>()
        val scopedTransferOrders = mutableListOf<Set<String>>()
        var failTasks = false
        var ignoreCancellation = false
        var globalTransferCalls = 0
        var scopedTransferCalls = 0

        private suspend fun gate(key: String, held: Boolean) {
            if (!held) return
            check(active.add(key))
            peak = maxOf(peak, active.size)
            try {
                if (ignoreCancellation) withContext(NonCancellable) { release.await() } else release.await()
            } finally {
                if (!currentCoroutineContext().isActive) cancelled += key
                active -= key
            }
        }

        override suspend fun login(username: String, password: String, deviceId: String, remember: Boolean) =
            LoginResult("synthetic", null, null, false, User(username, username, username, UserRole.ADMIN))
        override suspend fun workspaceSummary() = Result.success(WorkspaceSummary())
        override suspend fun workspaceMaterialItems(status: String?, orderNo: String?, page: Int, pageSize: Int) =
            Result.success(WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null))
        override suspend fun deviceDetail(deviceId: String) =
            Result.success(DeviceDetail(deviceId, deviceId, deviceId, null, null, status = "ACTIVE",
                orders = orders.map { DeviceOrder(it, null, null) }))
        override suspend fun deviceOrderMaterials(documentNo: String, deviceId: String, deviceNo: String): Result<List<OrderMaterialItem>> {
            materialOrders += documentNo
            gate("material:$documentNo", holdMaterials)
            return Result.success(emptyList())
        }
        override suspend fun assemblyTaskPage(page: Int, pageSize: Int, deviceNo: String?, deviceId: String?): Result<AssemblyTaskPage> {
            val id = checkNotNull(deviceId)
            gate("task:$id", holdTasks)
            if (failTasks) throw IllegalStateException("synthetic task error")
            val task = AssemblyTask("$id-task", "ORDER", id, id,
                status = AssemblyTaskStatus.WAITING_MATERIAL, progressStage = 0, taskVersion = 1)
            return Result.success(AssemblyTaskPage(listOf(task), page, pageSize, 1, 1))
        }
        override suspend fun workshopLaborSummary(page: Int, pageSize: Int, deviceId: String?, orderNo: String?, assemblerId: String?): Result<LaborSummaryPage> {
            val id = checkNotNull(deviceId)
            gate("labor:$id", holdLabor)
            return Result.success(LaborSummaryPage(
                listOf(LaborSummaryItem("$id-task", "ORDER", id, id, "worker", "worker", 10, 0, 10)),
                page = page, total = 1))
        }
        override suspend fun transferRequests(status: String?): Result<TransferRequestPage> {
            globalTransferCalls++
            return Result.success(TransferRequestPage(emptyList()))
        }
        override suspend fun transferRequestsForOrders(orderNos: Set<String>): Result<TransferRequestPage> {
            scopedTransferCalls++
            scopedTransferOrders += orderNos
            gate("transfer", holdTransfers)
            return Result.success(TransferRequestPage(emptyList()))
        }
    }

    private class NoOpDao : OfflineOperationDao {
        override fun observeAll() = emptyFlow<List<OfflineOperationEntity>>()
        override suspend fun pending(): List<OfflineOperationEntity> = emptyList()
        override fun observePendingCount() = emptyFlow<Int>()
        override fun observeAttentionCount() = emptyFlow<Int>()
        override suspend fun insert(operation: OfflineOperationEntity) = 1L
        override suspend fun updateStatus(id: String, status: String, error: String?) = Unit
        override suspend fun markSynced(id: String, status: String, serverTime: String?) = Unit
        override suspend fun clearSynced() = Unit
        override suspend fun findById(id: String): OfflineOperationEntity? = null
        override suspend fun resetStuckSyncing() = 0
        override suspend fun resetToPending(id: String) = Unit
        override suspend fun deleteById(id: String) = Unit
    }
}
