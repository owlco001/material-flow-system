package com.company.logistics.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.company.logistics.data.EndpointStore
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.ui.components.OfflineBanner
import com.company.logistics.ui.screens.ApprovalScreen
import com.company.logistics.ui.screens.AuditScreen
import com.company.logistics.ui.screens.EndpointConfigScreen
import com.company.logistics.ui.screens.InventoryScreen
import com.company.logistics.ui.screens.LoginScreen
import com.company.logistics.ui.screens.MaterialDetailScreen
import com.company.logistics.ui.screens.OrderDetailScreen
import com.company.logistics.ui.screens.ProfileScreen
import com.company.logistics.ui.screens.QueueScreen
import com.company.logistics.ui.screens.ScannerScreen
import com.company.logistics.ui.screens.WorkspaceScreen
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsTypography
import com.company.logistics.ui.theme.Spacing
import java.util.UUID

/**
 * 应用根组件 —— 会话、路由、导航骨架。
 *
 * 权限落地原则：
 *  - 底部导航按角色动态生成（[LogisticsViewModel.tabsFor]）；
 *  - 无权限页面不渲染，而非渲染后置灰；
 *  - 审批等敏感操作的权限校验同时在服务端执行（客户端仅做体验层收敛）。
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun LogisticsApp(
    viewModel: LogisticsViewModel,
    scannerViewModel: ScannerViewModel,
    endpointStore: EndpointStore,
    onEndpointChanged: (String) -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }

    // 已认证页面的成功 / 错误统一走 Snackbar；登录页保留错误文案，避免
    // LaunchedEffect 在展示后立刻清掉登录失败或 refresh 失效提示。
    LaunchedEffect(state.message, state.error) {
        val text = state.message
            ?: state.error.takeIf { state.authState is AuthState.Authenticated }
        if (text != null) {
            snackbar.showSnackbar(text)
            viewModel.consumeMessage()
        }
    }

    LogisticsTheme {
        when (state.authState) {
            AuthState.Restoring -> {
                RestoringSessionScreen()
                return@LogisticsTheme
            }

            AuthState.Unauthenticated -> {
                LoginScreen(
                    loading = state.loading,
                    errorMessage = state.error,
                    deviceId = remember { DeviceId.value },
                    onLogin = { u, p, d, remember -> viewModel.login(u, p, d, remember) }
                )
                return@LogisticsTheme
            }

            is AuthState.Authenticated -> Unit
        }

        Scaffold(
            containerColor = LogisticsTheme.colors.pageBackground,
            snackbarHost = { SnackbarHost(snackbar) },
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text(
                                state.screen.title,
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold,
                                color = LogisticsTheme.colors.textPrimary
                            )
                            Text(
                                "${state.currentUser} · ${state.role.label}" +
                                    if (state.preview) " · 测试预览，只读" else "",
                                fontSize = 11.sp,
                                color = LogisticsTheme.colors.textTertiary
                            )
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = LogisticsTheme.colors.pageBackground
                    )
                )
            },
            bottomBar = {
                if (state.navTabs.isNotEmpty()) {
                    BottomNavBar(
                        tabs = state.navTabs,
                        current = state.screen,
                        pendingCount = state.pendingCount,
                        onSelect = { viewModel.navigate(it.screen) }
                    )
                }
            }
        ) { padding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
            ) {
                // 离线状态条常驻（有待同步时才显示）
                if (state.pendingCount > 0) {
                    OfflineBanner(
                        pendingCount = state.pendingCount,
                        syncing = state.syncing,
                        onTap = { viewModel.navigate(Screen.QUEUE) }
                    )
                }

                when (state.screen) {
                    Screen.WORKSPACE -> WorkspaceScreen(
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
                        onRefresh = { viewModel.refreshWorkspace() },
                        onPageSizeChange = { viewModel.setWorkspacePageSize(it) },
                        onNextPage = { viewModel.loadNextWorkspacePage() },
                        onPreviousPage = { viewModel.loadPreviousWorkspacePage() },
                        onOpenApproval = { viewModel.navigate(Screen.APPROVAL) },
                        onOpenAudit = { viewModel.navigate(Screen.AUDIT) },
                        currentUserId = state.currentUserId,
                        timelineItemId = state.workspaceTimelineItemId,
                        timeline = state.workspaceTimeline,
                        timelineState = state.workspaceTimelineState,
                        timelineError = state.workspaceTimelineError,
                        handoverSubmittingId = state.handoverSubmittingId,
                        onOpenTimeline = { viewModel.openHandoverTimeline(it) },
                        onRetryTimeline = { viewModel.retryHandoverTimeline() },
                        onHandoverAction = { item, action, reason ->
                            viewModel.decideHandover(item, action, reason)
                        },
                        onCreateHandover = { item, quantity, fromLocation, remark ->
                            viewModel.createHandover(item, quantity, fromLocation, remark)
                        },
                        onEnterPreview = { viewModel.enterRolePreview(it) },
                        onExitPreview = { viewModel.exitRolePreview() },
                        onOpenEndpointConfig = { viewModel.navigate(Screen.ENDPOINT_CONFIG) },
                        assemblyTasks = state.assemblyTasks,
                        assemblyTaskState = state.assemblyTaskState,
                        assemblyTaskError = state.assemblyTaskError,
                        assemblyTaskUnavailable = state.assemblyTaskUnavailable,
                        assemblyTaskPage = state.assemblyTaskPage,
                        assemblyTaskPageSize = state.assemblyTaskPageSize,
                        assemblyTaskTotal = state.assemblyTaskTotal,
                        assemblyTaskTotalPages = state.assemblyTaskTotalPages,
                        assemblySubmittingTaskId = state.assemblySubmittingTaskId,
                        assemblySubmittingAction = state.assemblySubmittingAction,
                        assemblyActiveLabor = state.assemblyActiveLabor,
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
                        onAcceptAssemblyMaterial = { viewModel.acceptAssemblyMaterial(it) },
                        onStartAssemblyWork = { viewModel.startAssemblyWork(it) },
                        onSubmitAssemblyProgress = { task, stage -> viewModel.submitAssemblyProgress(task, stage) },
                        onCompleteAssemblyWork = { viewModel.completeAssemblyWork(it) },
                        onStartTemporaryTransfer = { taskId, remark -> viewModel.startTemporaryTransfer(taskId, remark) },
                        onCompleteTemporaryTransfer = { remark -> viewModel.completeTemporaryTransfer(remark) },
                        onSubmitException = { item, type, actual, description -> viewModel.submitException(item, type, actual, description) },
                    )

                    Screen.SCANNER -> ScannerScreen(
                        viewModel = scannerViewModel,
                        role = state.role,
                        readOnly = state.preview,
                        pendingCount = state.pendingCount,
                        syncing = state.syncing,
                        onResolved = { viewModel.onScanned(it.normalizedValue) },
                        onOpenQueue = { viewModel.navigate(Screen.QUEUE) }
                    )

                    Screen.MATERIAL_DETAIL -> {
                        val inv = state.materialInventory
                        if (inv == null) {
                            viewModel.navigate(Screen.SCANNER)
                        } else {
                            MaterialDetailScreen(
                                inventory = inv,
                                loading = state.loading,
                                readOnly = state.preview,
                                onBack = { viewModel.navigate(Screen.SCANNER) },
                                onInbound = { viewModel.submitInbound() },
                                onBindLocation = { viewModel.navigate(Screen.LOCATION_BIND) }
                            )
                        }
                    }

                    Screen.ORDER_DETAIL -> OrderDetailScreen(
                        status = state.orderStatus,
                        detail = state.orderDetail,
                        loading = state.loading,
                        onRefresh = { viewModel.refreshOrderDetail() },
                        multiOrder = state.multiOrderSnapshot,
                        onSelectOrder = { viewModel.selectOrder(it) },
                    )

                    Screen.QUEUE -> QueueScreen(
                        queue = state.offlineQueue,
                        syncing = state.syncing,
                        readOnly = state.preview,
                        onSync = { viewModel.syncNow() },
                        onClearSynced = { viewModel.clearSynced() }
                    )

                    Screen.APPROVAL -> ApprovalScreen(
                        role = state.role,
                        readOnly = state.preview,
                        requests = state.transferRequests,
                        requestState = state.transferRequestState,
                        requestError = state.transferRequestError,
                        requestFilter = state.transferRequestFilter,
                        serverTime = state.transferRequestServerTime,
                        selectedRequestId = state.selectedTransferRequestId,
                        detail = state.transferRequestDetail,
                        detailState = state.transferRequestDetailState,
                        detailError = state.transferRequestDetailError,
                        submittingRequestId = state.transferDecisionSubmittingId,
                        onRefresh = { viewModel.refreshTransferRequests() },
                        onRetry = { viewModel.refreshTransferRequests() },
                        onFilter = { viewModel.refreshTransferRequests(it) },
                        onSelectRequest = { viewModel.selectTransferRequest(it) },
                        onCloseDetail = { viewModel.closeTransferRequestDetail() },
                        onRetryDetail = { viewModel.retryTransferRequestDetail() },
                        onApprove = { viewModel.approveTransferRequest(it, true) },
                        onReject = { id, reason -> viewModel.approveTransferRequest(id, false, reason) },
                        onExecute = { viewModel.executeTransferRequest(it) },
                    )

                    Screen.AUDIT -> AuditScreen(
                        role = state.role,
                        logs = state.auditLogs,
                        state = state.auditState,
                        error = state.auditError,
                        page = state.auditPage,
                        pageSize = state.auditPageSize,
                        hasNext = state.auditHasNext,
                        serverTime = state.auditServerTime,
                        onRefresh = { viewModel.refreshAuditLogs() },
                        onRetry = { viewModel.retryAuditLogs() },
                        onNextPage = { viewModel.loadNextAuditPage() },
                        onPreviousPage = { viewModel.loadPreviousAuditPage() },
                        onBack = { viewModel.navigate(Screen.WORKSPACE) },
                    )

                    Screen.INVENTORY -> InventoryScreen(
                        onGoScan = { viewModel.navigate(Screen.SCANNER) }
                    )

                    Screen.PROFILE -> ProfileScreen(
                        userName = state.currentUser,
                        role = state.role,
                        queue = state.offlineQueue,
                        endpointUrl = endpointStore.effectiveUrl,
                        endpointConfigured = !endpointStore.usingBuildDefault,
                        onOpenQueue = { viewModel.navigate(Screen.QUEUE) },
                        onOpenEndpointConfig = { viewModel.navigate(Screen.ENDPOINT_CONFIG) },
                        onLogout = { viewModel.logout() }
                    )

                    Screen.ENDPOINT_CONFIG -> EndpointConfigScreen(
                        store = endpointStore,
                        onEndpointChanged = onEndpointChanged,
                        onBack = { viewModel.navigate(Screen.PROFILE) }
                    )

                    Screen.LOCATION_BIND, Screen.SUBMIT, Screen.LOGIN ->
                        ScanPlaceholder(
                            title = state.screen.title,
                            onBack = { viewModel.navigate(Screen.MATERIAL_DETAIL) }
                        )
                }
            }
        }
    }
}

/** 冷启动读取加密会话期间的稳定页面，不让用户误以为需要重新登录。 */
@Composable
private fun RestoringSessionScreen() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(LogisticsTheme.colors.pageBackground),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "正在恢复会话…",
            fontSize = 18.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary,
        )
        Spacer(Modifier.height(Spacing.sm))
        Text(
            text = "正在读取本机安全存储中的登录状态",
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
    }
}

/** 未完全接入的页面占位（保留路由，避免导航断链） */
@Composable
private fun ScanPlaceholder(title: String, onBack: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(Dimens.PagePadding),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            title,
            fontSize = 18.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary
        )
        Spacer(Modifier.height(Spacing.sm))
        Text(
            "该流程界面接入中，当前可通过详情页提交流转申请",
            fontSize = 14.sp,
            color = LogisticsTheme.colors.textSecondary
        )
        Spacer(Modifier.height(Spacing.xl))
        Text(
            "返回",
            modifier = Modifier.clickable { onBack() },
            fontSize = 15.sp,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary
        )
    }
}

/**
 * 底部导航栏 —— 按角色动态渲染。
 * 图标 + 文字双标注（现场强光/戴手套场景下纯图标不可识别）。
 */
@Composable
private fun BottomNavBar(
    tabs: List<NavTab>,
    current: Screen,
    pendingCount: Int,
    onSelect: (NavTab) -> Unit
) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 8.dp
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(Dimens.BottomBarHeight)
                .padding(horizontal = Spacing.sm),
            verticalAlignment = Alignment.CenterVertically
        ) {
            tabs.forEach { tab ->
                val selected = current == tab.screen
                val color = if (selected) MaterialTheme.colorScheme.primary
                else LogisticsTheme.colors.textTertiary

                Column(
                    modifier = Modifier
                        .weight(1f)
                        .height(Dimens.MinTouchTarget + 8.dp)
                        .clickable { onSelect(tab) },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center
                ) {
                    Box(contentAlignment = Alignment.TopEnd) {
                        Text(
                            tab.symbol,
                            fontSize = 19.sp,
                            fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                            color = color
                        )
                        // 待同步角标
                        if (tab == NavTab.QUEUE && pendingCount > 0) {
                            Box(
                                modifier = Modifier
                                    .size(14.dp)
                                    .background(LogisticsTheme.colors.warning, androidx.compose.foundation.shape.CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    if (pendingCount > 9) "9+" else "$pendingCount",
                                    fontSize = 8.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color.White
                                )
                            }
                        }
                    }
                    Spacer(Modifier.height(3.dp))
                    Text(
                        tab.label,
                        fontSize = 10.sp,
                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                        color = color
                    )
                }
            }
        }
    }
}

/**
 * 设备标识。
 * 契约 1 节审计字段包含 deviceId，登录与写操作均需上报。
 * 由 MainActivity 从加密会话存储注入，保证登录与审计使用同一稳定设备标识。
 */
object DeviceId {
    var value: String = "android-" + UUID.randomUUID().toString().take(16)
}
