package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.AssemblyTaskPage
import com.company.logistics.model.AssemblyTaskStatus
import com.company.logistics.model.OrderMaterialItem
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.MaterialStatusCode
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.LaborType
import com.company.logistics.model.MachineProgress
import com.company.logistics.model.MachineProgressPage
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.WorkspaceViewRole
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LogisticsViewModelAssemblyTest {
    @Test
    fun assemblyDeviceFilterMatchesDeviceNumberOrIdOnly() {
        val tasks = listOf(
            AssemblyTask("a", "o", "id-1", "NO-1", "m", AssemblyTaskStatus.WAITING_MATERIAL, 0, 1, null, null, 0, null, null),
            AssemblyTask("b", "o", "id-2", "NO-2", "m", AssemblyTaskStatus.COMPLETED, 3, 1, null, null, 0, null, null),
        )
        assertEquals(listOf("a"), LogisticsViewModel.filterAssemblyTasksByDevice(tasks, " NO-1 ").map { it.id })
        assertEquals(listOf("b"), LogisticsViewModel.filterAssemblyTasksByDevice(tasks, "id-2").map { it.id })
        assertTrue(LogisticsViewModel.filterAssemblyTasksByDevice(tasks, "missing").isEmpty())
    }

    @Test
    fun orderMaterialsAreFilteredByMachineAndOrder() {
        val status = OrderMaterialStatus("PO-1", "PRODUCTION_ORDER", listOf(
            OrderMaterialItem("d1", null, "D-1", "m1", "M-1", "螺栓", null, 2, 1, 0, MaterialStatusCode.OUT_OF_STOCK, "缺货", ""),
            OrderMaterialItem("d2", null, "D-2", "m2", "M-2", "螺母", null, 1, 1, 1, MaterialStatusCode.IN_STOCK, "在库", ""),
        ), null)
        assertEquals(listOf("M-1"), LogisticsViewModel.filterOrderMaterialsForDevice(status, "PO-1", "D-1").map { it.materialCode })
        assertTrue(LogisticsViewModel.filterOrderMaterialsForDevice(status, "PO-2", "D-1").isEmpty())
    }

    @Test
    fun assemblerLifecyclePassesExpectedVersionAndStableOperationId() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ASSEMBLER)
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("assembler", "password", "device", remember = false)
        var task = viewModel.state.value.assemblyTasks.single()

        viewModel.acceptAssemblyMaterial(task)
        task = viewModel.state.value.assemblyTasks.single()
        viewModel.startAssemblyWork(task)
        task = viewModel.state.value.assemblyTasks.single()
        viewModel.submitAssemblyProgress(task, 1)
        task = viewModel.state.value.assemblyTasks.single()
        viewModel.submitAssemblyProgress(task, 2)
        task = viewModel.state.value.assemblyTasks.single()
        viewModel.submitAssemblyProgress(task, 3)
        task = viewModel.state.value.assemblyTasks.single()
        viewModel.completeAssemblyWork(task)

        assertEquals(listOf("accept", "start", "progress:1", "progress:2", "progress:3", "complete"), repository.actions)
        assertEquals(listOf(2, 3, 4, 5, 6), repository.expectedVersions)
        assertEquals(repository.operationIds.distinct().size, repository.operationIds.size)
        assertEquals(AssemblyTaskStatus.COMPLETED, viewModel.state.value.assemblyTasks.single().status)
        scope.cancel()
    }

    @Test
    fun adminAssemblerPreviewBlocksEveryAssemblyWrite() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ADMIN)
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("admin", "password", "device", remember = false)
        viewModel.enterRolePreview(WorkspaceViewRole.ASSEMBLER)
        val task = viewModel.state.value.assemblyTasks.single()
        viewModel.acceptAssemblyMaterial(task)
        viewModel.startTemporaryTransfer(task.id, "预览备注")

        assertTrue(viewModel.state.value.error!!.contains("只读"))
        assertTrue(repository.actions.isEmpty())
        scope.cancel()
    }

    @Test
    fun temporaryTransferKeepsItsOwnRecordAndDoesNotReuseAssemblyLabor() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ASSEMBLER)
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("assembler", "password", "device", remember = false)
        viewModel.startTemporaryTransfer(taskId = null, remark = "处理其他工作")
        assertEquals("tt-1", viewModel.state.value.temporaryTransfer?.temporaryTransferId)
        assertTrue(viewModel.state.value.assemblyActiveLabor.isEmpty())

        viewModel.completeTemporaryTransfer("已完成")

        assertEquals("tt-1", viewModel.state.value.lastCompletedTemporaryTransfer?.temporaryTransferId)
        assertEquals("已完成", viewModel.state.value.lastCompletedTemporaryTransfer?.remark)
        assertEquals(listOf("temporary-start", "temporary-complete"), repository.actions)
        scope.cancel()
    }

    @Test
    fun supervisorLoadsServerSummaryAndMachineProgressSeparately() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.WORKSHOP_SUPERVISOR)
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("supervisor", "password", "device", remember = false)

        assertEquals(80, viewModel.state.value.workshopProgressSummary?.overallProgressPercent)
        assertEquals("M-01", viewModel.state.value.workshopMachineProgress.single().deviceNo)
        assertEquals(1, repository.workshopSummaryCalls)
        assertEquals(1, repository.workshopMachineCalls)
        scope.cancel()
    }

    private class FakeAssemblyRepository(
        private val loginRole: UserRole,
    ) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        private var task = AssemblyTask(
            id = "task-1",
            orderNo = "WO-1",
            deviceId = "machine-1",
            deviceNo = "M-01",
            materialSummary = "物料",
            status = AssemblyTaskStatus.WAITING_MATERIAL,
            progressStage = 0,
            taskVersion = 1,
            currentLaborRecordId = null,
            currentLaborStartedAt = null,
            accumulatedLaborMinutes = 0,
            assignedAssemblerId = "assembler-1",
            assignedAssemblerName = "装配工",
        )
        val actions = mutableListOf<String>()
        val expectedVersions = mutableListOf<Int>()
        val operationIds = mutableListOf<String>()
        var workshopSummaryCalls = 0
        var workshopMachineCalls = 0

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
            user = User("user-1", username, "测试用户", loginRole),
        )

        override suspend fun assemblyTaskPage(page: Int, pageSize: Int): Result<AssemblyTaskPage> = Result.success(
            AssemblyTaskPage(listOf(task), page, pageSize, 1, 1, "server-time")
        )

        override suspend fun acceptAssemblyMaterial(taskId: String, clientOperationId: String): Result<AssemblyTask> {
            actions += "accept"
            operationIds += clientOperationId
            task = task.copy(status = AssemblyTaskStatus.MATERIAL_ACCEPTED, taskVersion = 2)
            return Result.success(task)
        }

        override suspend fun startAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): Result<LaborRecord> {
            actions += "start"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            task = task.copy(status = AssemblyTaskStatus.IN_PROGRESS, taskVersion = 3)
            return Result.success(
                LaborRecord(
                    id = "lr-1",
                    taskId = taskId,
                    type = LaborType.ASSEMBLY,
                    startedAt = "server-start",
                    endedAt = null,
                    durationMinutes = null,
                    remark = null,
                    laborRecordId = "lr-1",
                    status = "ACTIVE",
                    taskVersion = 3,
                )
            )
        }

        override suspend fun submitAssemblyProgress(taskId: String, stage: Int, expectedVersion: Int, clientOperationId: String): Result<AssemblyTask> {
            actions += "progress:$stage"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            task = task.copy(progressStage = stage, taskVersion = expectedVersion + 1)
            return Result.success(task)
        }

        override suspend fun completeAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): Result<AssemblyTask> {
            actions += "complete"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            task = task.copy(status = AssemblyTaskStatus.COMPLETED, taskVersion = expectedVersion + 1)
            return Result.success(task)
        }

        override suspend fun startTemporaryTransfer(taskId: String?, remark: String, clientOperationId: String): Result<LaborRecord> {
            actions += "temporary-start"
            return Result.success(
                LaborRecord(
                    id = "lr-tt-1",
                    taskId = null,
                    type = LaborType.TEMPORARY_TRANSFER,
                    startedAt = "transfer-start",
                    endedAt = null,
                    durationMinutes = null,
                    remark = remark,
                    temporaryTransferId = "tt-1",
                    status = "ACTIVE",
                )
            )
        }

        override suspend fun completeTemporaryTransfer(transferId: String, remark: String, clientOperationId: String): Result<LaborRecord> {
            actions += "temporary-complete"
            return Result.success(
                LaborRecord(
                    id = "tt-1",
                    taskId = null,
                    type = LaborType.TEMPORARY_TRANSFER,
                    startedAt = "",
                    endedAt = "transfer-end",
                    durationMinutes = 4,
                    remark = remark,
                    temporaryTransferId = transferId,
                    status = "COMPLETED",
                )
            )
        }

        override suspend fun workshopProgressSummary(from: String?, to: String?): Result<WorkshopProgressSummary> {
            workshopSummaryCalls++
            return Result.success(
                WorkshopProgressSummary(
                    workshopId = "workshop-1",
                    totalTasks = 5,
                    completedTasks = 4,
                    overallProgressPercent = 80,
                    totalLaborMinutes = 60,
                    assemblyLaborMinutes = 50,
                    temporaryTransferLaborMinutes = 10,
                    machines = emptyList(),
                    generatedAt = "server-time",
                )
            )
        }

        override suspend fun workshopMachineProgress(page: Int, pageSize: Int): Result<MachineProgressPage> {
            workshopMachineCalls++
            return Result.success(
                MachineProgressPage(
                    items = listOf(MachineProgress("machine-1", "M-01", 5, 4, 80, 50)),
                    page = page,
                    pageSize = pageSize,
                )
            )
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
    }
}
