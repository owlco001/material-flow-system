package com.company.logistics.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import com.company.logistics.data.EndpointStore
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.ui.screens.WorkspaceScreen

/**
 * 工作区路由：把传给 [WorkspaceScreen] 的 20+ 个回调收敛到稳定 holder。
 *
 * 背景：原来 LogisticsApp 每次重组都在 when 分支里重建全部 lambda，
 * 参数稍有变化就导致 WorkspaceScreen 整体重组。ViewModel 在组合期间是稳定实例，
 * 这里用 remember(viewModel) 缓存一次，后续重组复用同一批回调实例。
 *
 * 注意：holder 只捕获 viewModel；endpointStore.isConfigured 是纯 getter，
 * 每次重组重新读取即可，不影响回调稳定性。
 */
@Composable
fun WorkspaceRoute(
    viewModel: LogisticsViewModel,
    state: LogisticsUiState,
    endpointStore: EndpointStore,
) {
    val callbacks = remember(viewModel) { WorkspaceCallbacks(viewModel) }
    WorkspaceScreen(
        role = state.workspaceRole,
        authenticatedRole = state.role,
        previewRole = state.previewRole,
        summary = state.workspaceSummary,
        items = state.workspaceItems,
        summaryState = state.workspaceSummaryState,
        summaryError = state.workspaceSummaryError,
        summaryUnavailable = state.workspaceSummaryUnavailable,
        itemsState = state.workspaceItemsState,
        itemsError = state.workspaceItemsError,
        itemsUnavailable = state.workspaceItemsUnavailable,
        page = state.workspacePage,
        pageSize = state.workspacePageSize,
        total = state.workspaceTotal,
        totalPages = state.workspaceTotalPages,
        serverTime = state.workspaceServerTime,
        onRefresh = callbacks.onRefresh,
        onPageSizeChange = callbacks.onPageSizeChange,
        onNextPage = callbacks.onNextPage,
        onPreviousPage = callbacks.onPreviousPage,
        onOpenApproval = callbacks.onOpenApproval,
        onOpenAudit = callbacks.onOpenAudit,
        currentUserId = state.currentUserId,
        timelineItemId = state.workspaceTimelineItemId,
        timeline = state.workspaceTimeline,
        timelineState = state.workspaceTimelineState,
        timelineError = state.workspaceTimelineError,
        handoverSubmittingId = state.handoverSubmittingId,
        onOpenTimeline = callbacks.onOpenTimeline,
        onRetryTimeline = callbacks.onRetryTimeline,
        onHandoverAction = callbacks.onHandoverAction,
        onCreateHandover = callbacks.onCreateHandover,
        onEnterPreview = callbacks.onEnterPreview,
        onExitPreview = callbacks.onExitPreview,
        onOpenEndpointConfig = callbacks.onOpenEndpointConfig,
        endpointConfigured = endpointStore.isConfigured,
        assemblyTasks = state.assemblyTasks,
        assemblyTaskState = state.assemblyTaskState,
        assemblyTaskError = state.assemblyTaskError,
        assemblyTaskUnavailable = state.assemblyTaskUnavailable,
        assemblyTaskPage = state.assemblyTaskPage,
        assemblyTaskPageSize = state.assemblyTaskPageSize,
        assemblyTaskTotal = state.assemblyTaskTotal,
        assemblyTaskTotalPages = state.assemblyTaskTotalPages,
        assemblyDeviceFilter = state.assemblyDeviceFilter,
        assemblyMaterialStatus = state.assemblyMaterialStatus,
        assemblyMaterialState = state.assemblyMaterialState,
        assemblyMaterialError = state.assemblyMaterialError,
        assemblySubmittingTaskId = state.assemblySubmittingTaskId,
        assemblySubmittingAction = state.assemblySubmittingAction,
        assemblyActiveLabor = state.assemblyActiveLabor,
        assemblyLaborSummary = state.assemblyLaborSummary,
        temporaryTransfer = state.temporaryTransfer,
        lastCompletedTemporaryTransfer = state.lastCompletedTemporaryTransfer,
        temporaryTransferSourceTaskId = state.temporaryTransferSourceTaskId,
        temporaryTransferSubmitting = state.temporaryTransferSubmitting,
        workshopProgressSummary = state.workshopProgressSummary,
        workshopSummaryState = state.workshopSummaryState,
        workshopSummaryError = state.workshopSummaryError,
        workshopSummaryUnavailable = state.workshopSummaryUnavailable,
        workshopMachineProgress = state.workshopMachineProgress,
        workshopMachineState = state.workshopMachineState,
        workshopMachineError = state.workshopMachineError,
        workshopMachineUnavailable = state.workshopMachineUnavailable,
        workshopMachinePage = state.workshopMachinePage,
        workshopMachinePageSize = state.workshopMachinePageSize,
        workshopMachineHasNext = state.workshopMachineHasNext,
        workshopLaborSummary = state.workshopLaborSummary,
        workshopLaborState = state.workshopLaborState,
        workshopLaborError = state.workshopLaborError,
        workshopLaborDeviceFilter = state.workshopLaborDeviceFilter,
        onRefreshWorkshopLabor = callbacks.onRefreshWorkshopLabor,
        onAcceptAssemblyMaterial = callbacks.onAcceptAssemblyMaterial,
        onStartAssemblyWork = callbacks.onStartAssemblyWork,
        onSubmitAssemblyProgress = callbacks.onSubmitAssemblyProgress,
        onCompleteAssemblyWork = callbacks.onCompleteAssemblyWork,
        onStartAssemblyStage = callbacks.onStartAssemblyStage,
        onCompleteAssemblyStage = callbacks.onCompleteAssemblyStage,
        onReworkAssemblyStage = callbacks.onReworkAssemblyStage,
        onStartTemporaryTransfer = callbacks.onStartTemporaryTransfer,
        onCompleteTemporaryTransfer = callbacks.onCompleteTemporaryTransfer,
        onScanAssemblyDevice = callbacks.onScanAssemblyDevice,
        onAssignAssemblyMembers = callbacks.onAssignAssemblyMembers,
        onRemoveAssemblyMember = callbacks.onRemoveAssemblyMember,
        onSubmitException = callbacks.onSubmitException,
        exceptionSubmitting = state.exceptionSubmitting,
    )
}

/**
 * 工作区回调的稳定 holder：只在 viewModel 实例变化时重建。
 * 所有 lambda 仅捕获 viewModel，不捕获 state，避免 state 变化导致回调失效。
 */
private class WorkspaceCallbacks(private val vm: LogisticsViewModel) {
    val onRefresh: () -> Unit = { vm.refreshWorkspace() }
    val onPageSizeChange: (Int) -> Unit = { vm.setWorkspacePageSize(it) }
    val onNextPage: () -> Unit = { vm.loadNextWorkspacePage() }
    val onPreviousPage: () -> Unit = { vm.loadPreviousWorkspacePage() }
    val onOpenApproval: () -> Unit = { vm.navigate(Screen.APPROVAL) }
    val onOpenAudit: () -> Unit = { vm.navigate(Screen.AUDIT) }
    val onOpenTimeline: (WorkspaceMaterialItem) -> Unit = { vm.openHandoverTimeline(it) }
    val onRetryTimeline: () -> Unit = { vm.retryHandoverTimeline() }
    val onHandoverAction: (WorkspaceMaterialItem, HandoverAction, String?) -> Unit =
        { item, action, reason -> vm.decideHandover(item, action, reason) }
    val onCreateHandover: (WorkspaceMaterialItem, Int, String, String?) -> Unit =
        { item, quantity, fromLocation, remark -> vm.createHandover(item, quantity, fromLocation, remark) }
    val onEnterPreview: (com.company.logistics.model.WorkspaceViewRole) -> Unit = { vm.enterRolePreview(it) }
    val onExitPreview: () -> Unit = { vm.exitRolePreview() }
    val onOpenEndpointConfig: () -> Unit = { vm.navigate(Screen.ENDPOINT_CONFIG) }
    val onRefreshWorkshopLabor: (String?) -> Unit = { vm.refreshWorkshopLabor(it) }
    val onAcceptAssemblyMaterial: (AssemblyTask) -> Unit = { vm.acceptAssemblyMaterial(it) }
    val onStartAssemblyWork: (AssemblyTask) -> Unit = { vm.startAssemblyWork(it) }
    val onSubmitAssemblyProgress: (AssemblyTask, Int) -> Unit =
        { task, stage -> vm.submitAssemblyProgress(task, stage) }
    val onCompleteAssemblyWork: (AssemblyTask) -> Unit = { vm.completeAssemblyWork(it) }
    val onStartAssemblyStage: (AssemblyTask, Int) -> Unit =
        { task, stage -> vm.startAssemblyStage(task, stage) }
    val onCompleteAssemblyStage: (AssemblyTask, Int) -> Unit =
        { task, stage -> vm.completeAssemblyStage(task, stage) }
    val onReworkAssemblyStage: (AssemblyTask, Int, String) -> Unit =
        { task, stage, reason -> vm.reworkAssemblyStage(task, stage, reason) }
    val onStartTemporaryTransfer: (String?, String) -> Unit =
        { taskId, remark -> vm.startTemporaryTransfer(taskId, remark) }
    val onCompleteTemporaryTransfer: (String) -> Unit = { vm.completeTemporaryTransfer(it) }
    val onScanAssemblyDevice: () -> Unit = { vm.navigate(Screen.SCANNER) }
    val onAssignAssemblyMembers: (AssemblyTask, String) -> Unit =
        { task, ids -> vm.assignAssemblyMembers(task, ids) }
    val onRemoveAssemblyMember: (AssemblyTask, String) -> Unit =
        { task, assemblerId -> vm.removeAssemblyMember(task, assemblerId) }
    val onSubmitException: (WorkspaceMaterialItem, String, Int, String?) -> Unit =
        { item, type, actual, description -> vm.submitException(item, type, actual, description) }
}
