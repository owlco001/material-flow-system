package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.Assert.*
import org.junit.Test
import kotlin.coroutines.Continuation
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

/** Deliberately uncancellable fake responses prove generation checks, independently of job cancellation. */
@OptIn(ExperimentalCoroutinesApi::class)
class DeviceDetailLifecycleRegressionTest {
    @Test fun lateDetailSuccessCannotSelectPreviousMachineOrStartItsAggregate() = fixture { vm, repo ->
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        vm.openDeviceDetail("B")
        old.complete(Result.success(device("A")))
        assertEquals("B", vm.state.value.deviceDetail?.deviceId)
        assertFalse(repo.taskRequests.any { it.first == "A" })
    }

    @Test fun lateDetailFailureCannotReplaceCurrentMachineError() = fixture { vm, repo ->
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        vm.openDeviceDetail("B")
        old.complete(Result.failure(IllegalStateException("stale A")))
        assertEquals("B", vm.state.value.deviceDetail?.deviceId)
        assertNull(vm.state.value.error)
        assertFalse(vm.state.value.loading)
    }

    @Test fun lateAggregateCannotMixPreviousMachineIntoCurrentMachine() = fixture { vm, repo ->
        val old = Pending<LaborSummaryPage>()
        repo.laborOverride = { machine, page -> if (machine == "A") old.await() else Result.success(laborPage(machine, page, 1)) }
        vm.openDeviceDetail("A")
        vm.openDeviceDetail("B")
        old.complete(Result.success(laborPage("A", 1, 1)))
        assertEquals("B", vm.state.value.deviceDetail?.deviceId)
        assertTrue(vm.state.value.deviceAssemblyTasks.all { it.deviceId == "B" })
        assertTrue(vm.state.value.deviceLabor!!.items.all { it.deviceId == "B" })
    }

    @Test fun logoutRejectsOldDetailEvenIfAnotherLoginIsAlreadyComplete() = fixture { vm, repo ->
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        vm.logout()
        vm.login("new-user", "password", "device", false)
        old.complete(Result.success(device("A")))
        assertNull(vm.state.value.deviceDetail)
        assertTrue(vm.state.value.deviceAssemblyTasks.isEmpty())
        assertNotEquals(Screen.DEVICE_DETAIL, vm.state.value.screen)
    }

    @Test fun logoutRejectsOldAggregate() = fixture { vm, repo ->
        val old = Pending<LaborSummaryPage>()
        repo.laborOverride = { _, _ -> old.await() }
        vm.openDeviceDetail("A")
        vm.logout()
        old.complete(Result.success(laborPage("A", 1, 1)))
        assertFalse(vm.state.value.loggedIn)
        assertNull(vm.state.value.deviceLabor)
        assertTrue(vm.state.value.deviceAssemblyTasks.isEmpty())
    }

    @Test fun previewBoundaryRejectsPendingDetailAndClearsPreviousMachine() = fixture { vm, repo ->
        vm.openDeviceDetail("B")
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        assertTrue(vm.enterRolePreview(WorkspaceViewRole.OPERATOR).isSuccess)
        assertTrue(vm.exitRolePreview().isSuccess)
        old.complete(Result.success(device("A")))
        assertNull(vm.state.value.deviceDetail)
        assertNull(vm.state.value.deviceLabor)
    }

    @Test fun navigationAwayCannotBeUndoneByPendingDetail() = fixture { vm, repo ->
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        vm.navigate(Screen.SCANNER)
        old.complete(Result.success(device("A")))
        assertEquals(Screen.SCANNER, vm.state.value.screen)
        assertFalse(vm.state.value.loading)
        assertNull(vm.state.value.deviceDetail)
    }

    @Test fun tasksAndLaborIncludeTheTwentyFirstAndFortyFirstGroup() {
        for (count in listOf(0, 20, 21, 41)) fixture(count) { vm, repo ->
            vm.openDeviceDetail("A")
            assertEquals("task count=$count", count, vm.state.value.deviceAssemblyTasks.size)
            assertEquals("minutes count=$count", count * 10, vm.state.value.deviceLabor!!.items.sumOf { it.totalLaborMinutes ?: 0 })
            val pages = (1..maxOf(1, (count + 19) / 20)).toList()
            assertEquals(pages, repo.taskRequests.filter { it.first == "A" }.map { it.second })
            assertEquals(pages, repo.laborRequests.filter { it.first == "A" }.map { it.second })
        }
    }

    @Test fun refreshingSameMachineRejectsPreviousDetailGeneration() = fixture { vm, repo ->
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        repo.details.remove("A")
        vm.openDeviceDetail("A")
        old.complete(Result.success(device("A").copy(deviceName = "obsolete")))
        assertEquals("A", vm.state.value.deviceDetail?.deviceName)
        assertEquals(1, repo.taskRequests.size)
    }

    @Test fun switchingMachineCancelsCooperativePendingRequest() = fixture { vm, repo ->
        val pending = CompletableDeferred<Result<DeviceDetail>>()
        var cancelled = false
        repo.details["A"] = {
            try { pending.await() } finally { cancelled = !currentCoroutineContext().isActive }
        }
        vm.openDeviceDetail("A")
        vm.openDeviceDetail("B")
        assertTrue(cancelled)
        assertEquals("B", vm.state.value.deviceDetail?.deviceId)
    }

    @Test fun missingLaborTotalContinuesUntilShortPage() = fixture(21) { vm, repo ->
        repo.laborOverride = { machine, page -> Result.success(laborPage(machine, page, 21).copy(total = null)) }
        vm.openDeviceDetail("A")
        assertEquals(210, vm.state.value.deviceLabor!!.items.sumOf { it.totalLaborMinutes ?: 0 })
        assertEquals(listOf(1, 2), repo.laborRequests.map { it.second })
    }

    @Test fun failedSecondLaborPageDoesNotPublishFirstPageAsTotal() = fixture(21) { vm, repo ->
        repo.laborOverride = { machine, page ->
            if (page == 1) Result.success(laborPage(machine, page, 21)) else Result.failure(IllegalStateException("offline"))
        }
        vm.openDeviceDetail("A")
        assertNull(vm.state.value.deviceLabor)
        assertEquals(21, vm.state.value.deviceAssemblyTasks.size)
        assertTrue(vm.state.value.deviceAggregateError?.contains("工时") == true)
        assertNull(vm.state.value.error)
    }

    @Test fun repeatedTaskPageReportsIncompleteInsteadOfPublishingPartialPersonnel() = fixture(21) { vm, repo ->
        repo.taskOverride = { machine, _ -> Result.success(AssemblyTaskPage((0 until 20).map { task(machine, it) }, 1, 20, 21, 2)) }
        vm.openDeviceDetail("A")
        assertTrue(vm.state.value.deviceAssemblyTasks.isEmpty())
        assertTrue(vm.state.value.deviceAggregateError?.contains("装配任务") == true)
        assertEquals(210, vm.state.value.deviceLabor!!.items.sumOf { it.totalLaborMinutes ?: 0 })
        assertEquals(2, repo.taskRequests.size)
    }

    @Test fun emptyLaborPageClaimingMoreReportsIncomplete() = fixture { vm, repo ->
        repo.laborOverride = { _, page -> Result.success(LaborSummaryPage(emptyList(), page = page, total = 21)) }
        vm.openDeviceDetail("A")
        assertNull(vm.state.value.deviceLabor)
        assertTrue(vm.state.value.deviceAggregateError?.contains("工时") == true)
        assertEquals(1, repo.laborRequests.size)
    }

    @Test fun lateAggregateFailureCannotSetWarningOnNewMachine() = fixture { vm, repo ->
        val old = Pending<LaborSummaryPage>()
        repo.laborOverride = { machine, page -> if (machine == "A") old.await() else Result.success(laborPage(machine, page, 1)) }
        vm.openDeviceDetail("A")
        vm.openDeviceDetail("B")
        old.complete(Result.failure(IllegalStateException("stale error")))
        assertEquals("B", vm.state.value.deviceDetail?.deviceId)
        assertNull(vm.state.value.deviceAggregateError)
    }

    @Test fun previewBoundaryClearsCompletedAggregateAndCancelsOldAggregate() = fixture { vm, repo ->
        vm.openDeviceDetail("B")
        assertNotNull(vm.state.value.deviceLabor)
        val old = Pending<LaborSummaryPage>()
        repo.laborOverride = { _, _ -> old.await() }
        vm.openDeviceDetail("A")
        assertTrue(vm.enterRolePreview(WorkspaceViewRole.OPERATOR).isSuccess)
        old.complete(Result.success(laborPage("A", 1, 1)))
        assertNull(vm.state.value.deviceDetail)
        assertNull(vm.state.value.deviceLabor)
        assertTrue(vm.state.value.deviceAssemblyTasks.isEmpty())
        assertFalse(vm.state.value.loading)
    }

    @Test fun openingOrderDirectlyInvalidatesPendingDeviceEvenFromOrderScreen() = fixture { vm, repo ->
        vm.openOrderDetail("ORDER-1")
        val old = Pending<DeviceDetail>()
        repo.details["A"] = { old.await() }
        vm.openDeviceDetail("A")
        vm.openOrderDetail("ORDER-2")
        old.complete(Result.success(device("A")))
        assertEquals(Screen.ORDER_DETAIL, vm.state.value.screen)
        assertNull(vm.state.value.deviceDetail)
        assertFalse(repo.taskRequests.any { it.first == "A" })
    }

    @Test fun changedTaskCountDuringOffsetPagingReportsIncomplete() = fixture(21) { vm, repo ->
        repo.taskOverride = { machine, page ->
            Result.success(if (page == 1) AssemblyTaskPage((0 until 20).map { task(machine, it) }, 1, 20, 21, 2)
            else AssemblyTaskPage(emptyList(), 2, 20, 20, 1))
        }
        vm.openDeviceDetail("A")
        assertTrue(vm.state.value.deviceAssemblyTasks.isEmpty())
        assertTrue(vm.state.value.deviceAggregateError?.contains("装配任务") == true)
    }

    @Test fun changedLaborCountDuringOffsetPagingReportsIncomplete() = fixture(21) { vm, repo ->
        repo.laborOverride = { machine, page -> Result.success(
            if (page == 1) laborPage(machine, 1, 21) else LaborSummaryPage(emptyList(), page = 2, total = 20)) }
        vm.openDeviceDetail("A")
        assertNull(vm.state.value.deviceLabor)
        assertTrue(vm.state.value.deviceAggregateError?.contains("工时") == true)
    }

    @Test fun backgroundOrderRefreshDoesNotCancelCurrentMachineAggregate() = fixture { vm, repo ->
        vm.openOrderDetail("ORDER")
        val pending = Pending<LaborSummaryPage>()
        repo.laborOverride = { _, _ -> pending.await() }
        vm.openDeviceDetail("A")
        vm.refreshOrderDetail()
        pending.complete(Result.success(laborPage("A", 1, 1)))
        assertEquals(Screen.DEVICE_DETAIL, vm.state.value.screen)
        assertEquals(10, vm.state.value.deviceLabor!!.items.sumOf { it.totalLaborMinutes ?: 0 })
        assertNull(vm.state.value.error)
    }

    private fun fixture(count: Int = 1, test: (LogisticsViewModel, DeviceRepository) -> Unit) {
        val dispatcher = UnconfinedTestDispatcher()
        Dispatchers.setMain(dispatcher)
        val scope = CoroutineScope(SupervisorJob() + dispatcher)
        try {
            val repo = DeviceRepository(count)
            val vm = LogisticsViewModel(repo, scope)
            vm.login("admin", "password", "device", false)
            test(vm, repo)
        } finally { scope.cancel(); Dispatchers.resetMain() }
    }

    private class Pending<T> {
        private var continuation: Continuation<Result<T>>? = null
        suspend fun await(): Result<T> = suspendCoroutine { continuation = it }
        fun complete(result: Result<T>) = checkNotNull(continuation).resume(result)
    }

    private class DeviceRepository(val count: Int) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val details = mutableMapOf<String, suspend () -> Result<DeviceDetail>>()
        val taskRequests = mutableListOf<Pair<String, Int>>()
        val laborRequests = mutableListOf<Pair<String, Int>>()
        var taskOverride: (suspend (String, Int) -> Result<AssemblyTaskPage>)? = null
        var laborOverride: (suspend (String, Int) -> Result<LaborSummaryPage>)? = null
        override suspend fun login(username: String, password: String, deviceId: String, remember: Boolean) =
            LoginResult("synthetic", null, null, false, User(username, username, username, UserRole.ADMIN))
        override suspend fun workspaceSummary() = Result.success(WorkspaceSummary())
        override suspend fun workspaceMaterialItems(status: String?, orderNo: String?, page: Int, pageSize: Int) =
            Result.success(WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null))
        override suspend fun deviceDetail(deviceId: String) = details[deviceId]?.invoke() ?: Result.success(device(deviceId))
        override suspend fun assemblyTaskPage(page: Int, pageSize: Int, deviceNo: String?, deviceId: String?): Result<AssemblyTaskPage> {
            val machine = deviceId ?: "unknown"
            taskRequests += machine to page
            return taskOverride?.invoke(machine, page) ?: Result.success(AssemblyTaskPage(
                ((page - 1) * 20 until minOf(page * 20, count)).map { task(machine, it) }, page, 20, count, (count + 19) / 20))
        }
        override suspend fun workshopLaborSummary(page: Int, pageSize: Int, deviceId: String?, orderNo: String?, assemblerId: String?): Result<LaborSummaryPage> {
            val machine = deviceId ?: "unknown"
            laborRequests += machine to page
            return laborOverride?.invoke(machine, page) ?: Result.success(laborPage(machine, page, count))
        }
        override suspend fun orderDetail(orderNo: String, page: Int, pageSize: Int, materialsPage: Int, materialsPageSize: Int): Result<OrderDetail> =
            Result.failure(IllegalStateException("synthetic order response"))
        override suspend fun transferRequests(status: String?) = Result.success(TransferRequestPage(emptyList()))
        override suspend fun deviceOrderMaterials(documentNo: String, deviceId: String, deviceNo: String) = Result.success(emptyList<OrderMaterialItem>())
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
        private fun device(id: String) = DeviceDetail(id, id, id, null, null, status = "ACTIVE")
        private fun task(id: String, index: Int) = AssemblyTask("$id-$index", "ORDER", id, id,
            status = AssemblyTaskStatus.WAITING_MATERIAL, progressStage = 0, taskVersion = 1,
            assignedAssemblerId = "worker-$index", assignedAssemblerName = "worker-$index")
        private fun laborPage(id: String, page: Int, count: Int) = LaborSummaryPage(
            ((page - 1) * 20 until minOf(page * 20, count)).map { LaborSummaryItem("$id-$it", "ORDER", id, id, "worker-$it", "worker-$it", 10, 0, 10) },
            page = page, total = count)
    }
}
