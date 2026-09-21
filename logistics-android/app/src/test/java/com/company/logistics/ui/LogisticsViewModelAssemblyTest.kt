package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.data.remote.ApiException
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.AssemblyTaskPage
import com.company.logistics.model.AssemblyTaskStatus
import com.company.logistics.model.AssemblyAssignmentResponse
import com.company.logistics.model.AssemblyMember
import com.company.logistics.model.OrderMaterialItem
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.MaterialStatusCode
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.LaborType
import com.company.logistics.model.LaborSummaryItem
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.MachineProgress
import com.company.logistics.model.MachineProgressPage
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
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
    fun assemblyStageActionsPassVersionAndValidateReworkReason() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ASSEMBLER)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)
        val task = repository.fixtureTask
        viewModel.startAssemblyStage(task, 1)
        viewModel.completeAssemblyStage(task, 2)
        viewModel.reworkAssemblyStage(task, 3, "  尺寸不符  ")
        assertEquals(listOf("stage-start:1", "stage-complete:2", "stage-rework:3:尺寸不符"), repository.actions)
        assertEquals(listOf(1, 1, 1), repository.expectedVersions)
        assertEquals(3, repository.operationIds.distinct().size)
        viewModel.reworkAssemblyStage(task, 1, " ")
        assertTrue(viewModel.state.value.error!!.contains("1~500"))
        scope.cancel()
    }

    @Test
    fun adminAssemblerPreviewBlocksEveryAssemblyWrite() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ADMIN)
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("admin", "password", "device", remember = false)
        viewModel.enterRolePreview(WorkspaceViewRole.ASSEMBLER)
        val task = repository.fixtureTask
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
    fun temporaryTransferBindsTaskAndDeviceAndRejectsMissingContext() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ASSEMBLER)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)

        viewModel.startTemporaryTransfer("task-1", "绑定机台")
        assertEquals("task-1", repository.temporaryTaskId)
        assertEquals("machine-1", repository.temporaryDeviceId)

        viewModel.startTemporaryTransfer("missing", "不存在任务")
        assertTrue(viewModel.state.value.error!!.contains("任务不存在"))
        assertEquals(1, repository.temporaryStartCalls)

        repository.fixtureTask = repository.fixtureTask.copy(deviceId = "")
        viewModel.refreshWorkspace()
        viewModel.startTemporaryTransfer("task-1", "缺少机台")
        assertTrue(viewModel.state.value.error!!.contains("未绑定机台"))
        assertEquals(1, repository.temporaryStartCalls)
        scope.cancel()
    }

    @Test
    fun temporaryTransferFailureClearsSubmittingAndExposesError() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ASSEMBLER)
        repository.temporaryFailure = ApiException(409, "CONFLICT", "conflict")
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("assembler", "password", "device", remember = false)
        viewModel.startTemporaryTransfer("task-1", "失败")
        assertEquals(false, viewModel.state.value.temporaryTransferSubmitting)
        assertTrue(viewModel.state.value.error!!.contains("状态已变化"))
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

    @Test
    fun supervisorLaborSummaryUsesServerMinutesAndDeviceFilter() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.WORKSHOP_SUPERVISOR)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("supervisor", "password", "device", remember = false)
        viewModel.refreshWorkshopLabor("machine-1")
        assertEquals("machine-1", repository.laborDeviceId)
        assertEquals(17, viewModel.state.value.workshopLaborSummary?.items?.single()?.totalLaborMinutes)
        assertEquals(WorkspaceLoadState.CONTENT, viewModel.state.value.workshopLaborState)
        scope.cancel()
    }
    @Test
    fun adminAndSupervisorCanAssignAssemblyMembersAndUpdateState() {
        listOf(UserRole.ADMIN, UserRole.WORKSHOP_SUPERVISOR).forEach { role ->
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
            val repository = FakeAssemblyRepository(role)
            val viewModel = LogisticsViewModel(repository, scope)
            viewModel.login("user", "password", "device", remember = false)
            val task = repository.fixtureTask
            viewModel.assignAssemblyMembers(task, " a-1, a-2 ")
            assertEquals(listOf("a-1", "a-2"), repository.assignedIds)
            assertEquals(listOf("a-1", "a-2"), repository.fixtureTask.members.map { it.assemblerId })
            assertEquals(false, viewModel.state.value.loading)
            scope.cancel()
        }
    }

    @Test
    fun previewAndAssemblerCannotAssignOrRemoveAssemblyMembers() {
        listOf(UserRole.ADMIN to true, UserRole.ASSEMBLER to false).forEach { (role, preview) ->
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
            val repository = FakeAssemblyRepository(role)
            val viewModel = LogisticsViewModel(repository, scope)
            viewModel.login("user", "password", "device", remember = false)
            if (preview) viewModel.enterRolePreview(WorkspaceViewRole.ASSEMBLER)
            val task = repository.fixtureTask
            viewModel.assignAssemblyMembers(task, "a-1")
            viewModel.removeAssemblyMember(task, "a-1")
            assertTrue(viewModel.state.value.error!!.contains(if (preview) "只读" else "无权"))
            assertTrue(repository.actions.isEmpty())
            scope.cancel()
        }
    }

    @Test
    fun assignmentRejectsEmptyAndMoreThanTwentyIdsBeforeRepositoryCall() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ADMIN)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        val task = repository.fixtureTask
        viewModel.assignAssemblyMembers(task, (1..21).joinToString(",") { "a-$it" })
        assertTrue(viewModel.state.value.error != null)
        viewModel.assignAssemblyMembers(task, " , ")
        assertTrue(viewModel.state.value.error != null)
        assertTrue(repository.actions.isEmpty())
        scope.cancel()
    }

    @Test
    fun removingAssemblyMemberUpdatesTaskAndReportsConflict() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ADMIN)
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        val task = repository.fixtureTask
        viewModel.removeAssemblyMember(task, "a-1")
        assertTrue(repository.fixtureTask.members.none { it.assemblerId == "a-1" })
        assertEquals("装配成员移除成功", viewModel.state.value.message)
        val failureScope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val failureRepository = FakeAssemblyRepository(UserRole.ADMIN)
        failureRepository.removeFailure = ApiException(409, "CONFLICT", "conflict")
        val failureViewModel = LogisticsViewModel(failureRepository, failureScope)
        failureViewModel.login("admin", "password", "device", remember = false)
        failureViewModel.removeAssemblyMember(failureRepository.fixtureTask, "a-2")
        assertTrue(failureViewModel.state.value.error != null)
        assertEquals(false, failureViewModel.state.value.loading)
        failureScope.cancel()
        assertEquals(false, viewModel.state.value.loading)
        scope.cancel()
    }

    @Test
    fun assignmentFailureClearsLoadingAndExposesRetryMessage() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeAssemblyRepository(UserRole.ADMIN)
        repository.assignFailure = ApiException(503, "UNAVAILABLE", "unavailable")
        val viewModel = LogisticsViewModel(repository, scope)
        viewModel.login("admin", "password", "device", remember = false)
        viewModel.assignAssemblyMembers(repository.fixtureTask, "a-1")
        assertTrue(viewModel.state.value.error!!.contains("重试"))
        assertEquals(false, viewModel.state.value.loading)
        scope.cancel()
    }

    private class FakeAssemblyRepository(
        private val loginRole: UserRole,
    ) : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        var fixtureTask = AssemblyTask(
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
            members = listOf(AssemblyMember("a-1", "ASSEMBLER"), AssemblyMember("a-2", "ASSEMBLER")),
        )
        val actions = mutableListOf<String>()
        val assignedIds = mutableListOf<String>()
        val expectedVersions = mutableListOf<Int>()
        val operationIds = mutableListOf<String>()
        var assignFailure: Throwable? = null
        var removeFailure: Throwable? = null
        var workshopSummaryCalls = 0
        var workshopMachineCalls = 0
        var laborDeviceId: String? = null
        var temporaryTaskId: String? = null
        var temporaryDeviceId: String? = null
        var temporaryStartCalls = 0
        var temporaryFailure: Throwable? = null

        override suspend fun assignAssemblyMembers(taskId: String, assemblerIds: List<String>, clientOperationId: String): Result<AssemblyAssignmentResponse> {
            actions += "assign"
            assignedIds += assemblerIds
            assignFailure?.let { return Result.failure(it) }
            fixtureTask = fixtureTask.copy(members = assemblerIds.map { AssemblyMember(it, "ASSEMBLER") })
            return Result.success(AssemblyAssignmentResponse(taskId, fixtureTask.members))
        }

        override suspend fun removeAssemblyMember(taskId: String, assemblerId: String, clientOperationId: String): Result<AssemblyAssignmentResponse> {
            actions += "remove"
            removeFailure?.let { return Result.failure(it) }
            fixtureTask = fixtureTask.copy(members = fixtureTask.members.filterNot { it.assemblerId == assemblerId })
            return Result.success(AssemblyAssignmentResponse(taskId, fixtureTask.members))
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
            user = User("user-1", username, "测试用户", loginRole),
        )

        override suspend fun assemblyTaskPage(page: Int, pageSize: Int): Result<AssemblyTaskPage> = Result.success(
            AssemblyTaskPage(listOf(fixtureTask), page, pageSize, 1, 1, "server-time")
        )

        override suspend fun acceptAssemblyMaterial(taskId: String, clientOperationId: String): Result<AssemblyTask> {
            actions += "accept"
            operationIds += clientOperationId
            fixtureTask = fixtureTask.copy(status = AssemblyTaskStatus.MATERIAL_ACCEPTED, taskVersion = 2)
            return Result.success(fixtureTask)
        }

        override suspend fun startAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): Result<LaborRecord> {
            actions += "start"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            fixtureTask = fixtureTask.copy(status = AssemblyTaskStatus.IN_PROGRESS, taskVersion = 3)
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
            fixtureTask = fixtureTask.copy(progressStage = stage, taskVersion = expectedVersion + 1)
            return Result.success(fixtureTask)
        }

        override suspend fun completeAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): Result<AssemblyTask> {
            actions += "complete"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            fixtureTask = fixtureTask.copy(status = AssemblyTaskStatus.COMPLETED, taskVersion = expectedVersion + 1)
            return Result.success(fixtureTask)
        }

        override suspend fun startAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String): Result<com.company.logistics.model.AssemblyStageOperationResult> {
            actions += "stage-start:$stageNo"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            return Result.success(stageResult(stageNo, com.company.logistics.model.AssemblyStageStatus.IN_PROGRESS, expectedVersion + 1))
        }

        override suspend fun completeAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String): Result<com.company.logistics.model.AssemblyStageOperationResult> {
            actions += "stage-complete:$stageNo"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            return Result.success(stageResult(stageNo, com.company.logistics.model.AssemblyStageStatus.COMPLETED, expectedVersion + 1))
        }

        override suspend fun reworkAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, reason: String, clientOperationId: String): Result<com.company.logistics.model.AssemblyStageOperationResult> {
            actions += "stage-rework:$stageNo:$reason"
            expectedVersions += expectedVersion
            operationIds += clientOperationId
            return Result.success(stageResult(stageNo, com.company.logistics.model.AssemblyStageStatus.REWORK_REQUIRED, expectedVersion + 1, reason))
        }

        private fun stageResult(stageNo: Int, status: com.company.logistics.model.AssemblyStageStatus, version: Int, reason: String? = null) =
            com.company.logistics.model.AssemblyStageOperationResult(fixtureTask.id, stageNo, status, version, null, if (status == com.company.logistics.model.AssemblyStageStatus.COMPLETED) "done" else null, reason, "now", "trace")

        override suspend fun startTemporaryTransfer(taskId: String?, remark: String, clientOperationId: String, deviceId: String?): Result<LaborRecord> {
            actions += "temporary-start"
            temporaryStartCalls++
            temporaryTaskId = taskId
            temporaryDeviceId = deviceId
            temporaryFailure?.let { return Result.failure(it) }
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

        override suspend fun workshopLaborSummary(page: Int, pageSize: Int, deviceId: String?, orderNo: String?, assemblerId: String?): Result<LaborSummaryPage> {
            laborDeviceId = deviceId
            return Result.success(LaborSummaryPage(listOf(LaborSummaryItem("task-1", "WO-1", deviceId, "M-01", "u-1", "装配工", 11, 6, 17))))
        }

        // 必须打桩：否则 login() 后的工作台加载会回落到真实 MaterialFlowApi 网络调用，
        // 失败时触发 expireSession() 把 authState 异步重置为 Unauthenticated，
        // 导致依赖 ADMIN 角色的断言随机失败（跨测试类混跑时约 50%）。
        override suspend fun workspaceSummary(): Result<WorkspaceSummary> =
            Result.success(WorkspaceSummary(role = loginRole))

        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
        ): Result<WorkspaceMaterialItemsPage> = Result.success(
            WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, "server", "trace")
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
    }
}
