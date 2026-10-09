package com.company.logistics.ui

import androidx.compose.foundation.Image
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.company.logistics.data.EndpointStore
import com.company.logistics.data.remote.AppUpdateChecker
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.ui.components.LogisticsIcons
import com.company.logistics.ui.components.OfflineBanner
import com.company.logistics.ui.screens.ApprovalScreen
import com.company.logistics.ui.screens.AuditScreen
import com.company.logistics.ui.screens.ChangePasswordScreen
import com.company.logistics.ui.screens.UserManagementScreen
import com.company.logistics.ui.screens.EndpointConfigScreen
import com.company.logistics.ui.screens.InventoryScreen
import com.company.logistics.ui.screens.LoginScreen
import com.company.logistics.ui.screens.MaterialDetailScreen
import com.company.logistics.ui.screens.OrderDetailScreen
import com.company.logistics.ui.screens.ProfileScreen
import com.company.logistics.ui.screens.QueueScreen
import com.company.logistics.ui.screens.ScannerScreen
import com.company.logistics.ui.screens.WorkspaceScreen
import com.company.logistics.ui.screens.BomImportScreen
import com.company.logistics.ui.screens.ProductionManagementScreen
import com.company.logistics.ui.screens.DeviceDetailScreen
import com.company.logistics.ui.screens.FlowDetailScreen
import com.company.logistics.ui.screens.FlowHandoverScreen
import com.company.logistics.ui.screens.MyExceptionsScreen
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
    onOpenDebug3d: (() -> Unit)? = null,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    var showUnauthenticatedEndpointConfig by rememberSaveable { mutableStateOf(false) }
    var showAdminActivation by rememberSaveable { mutableStateOf(false) }
    // 网络连通性：离线横幅常驻的依据（断网但队列为空时也要提示离线）
    val isOnline by rememberIsOnline()

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
                if (showAdminActivation) {
                    com.company.logistics.ui.screens.AdminActivationScreen(viewModel.repository, { showAdminActivation = false }, { showAdminActivation = false })
                } else if (showUnauthenticatedEndpointConfig) {
                    EndpointConfigScreen(
                        store = endpointStore,
                        repository = viewModel.repository,
                        onEndpointChanged = onEndpointChanged,
                        onBack = { showUnauthenticatedEndpointConfig = false },
                        onOpenActivation = { showAdminActivation = true },
                        onLogout = { viewModel.logout() },
                    )
                } else {
                    LoginScreen(
                        loading = state.loading,
                        errorMessage = state.error,
                        deviceId = remember { DeviceId.value },
                        endpointConfigured = endpointStore.isConfigured,
                        onOpenEndpointConfig = { showUnauthenticatedEndpointConfig = true },
                        onLogin = { u, p, d, remember -> viewModel.login(u, p, d, remember) }
                    )
                }
                return@LogisticsTheme
            }

            is AuthState.Authenticated -> Unit
        }

        if (state.mustChangePassword) {
            ChangePasswordScreen(state.loading, state.error) { old, next, confirm -> viewModel.changePassword(old, next, confirm) }
            return@LogisticsTheme
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
                    ),
                    actions = {
                        // 顶部快捷入口已移除（2026-10-01 用户指令）：3D测试 / 订单/机台 / BOM 导入。
                        // onOpenDebug3d 参数保留（MainActivity 仍在传入），仅不再展示入口。
                        if (state.role == com.company.logistics.model.UserRole.ADMIN && !state.preview && state.screen == Screen.WORKSPACE) {
                            androidx.compose.material3.TextButton(onClick = { viewModel.navigate(Screen.USER_MANAGEMENT) }) { Text("用户管理") }
                        }
                    }
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
                // 离线状态条常驻：断网时始终显示（即使队列为空），有待同步时显示数量
                if (!isOnline || state.pendingCount > 0 || state.attentionCount > 0) {
                    OfflineBanner(
                        pendingCount = state.pendingCount,
                        syncing = state.syncing,
                        offline = !isOnline,
                        attentionCount = state.attentionCount,
                        onTap = { viewModel.navigate(Screen.QUEUE) }
                    )
                }

                when (state.screen) {
                    Screen.WORKSPACE -> WorkspaceRoute(
                        viewModel = viewModel,
                        state = state,
                        endpointStore = endpointStore,
                    )

                    Screen.SCANNER -> ScannerScreen(
                        viewModel = scannerViewModel,
                        role = state.role,
                        readOnly = state.preview,
                        pendingCount = state.pendingCount,
                        syncing = state.syncing,
                        onResolved = { viewModel.onScanned(it.normalizedValue) },
                        onOpenQueue = { viewModel.navigate(Screen.QUEUE) },
                        onBack = { viewModel.navigate(Screen.WORKSPACE) },
                        lastDevice = state.deviceDetail,
                        onReopenDevice = { viewModel.navigate(Screen.DEVICE_DETAIL) },
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
                                onBindLocation = { viewModel.navigate(Screen.LOCATION_BIND) },
                                drawings = {
                                    com.company.logistics.ui.screens.DrawingsCard(
                                        api = viewModel.repository.apiHandle,
                                        target = com.company.logistics.drawing.DrawingTarget.Material(inv.material.code),
                                    )
                                    androidx.compose.foundation.layout.Spacer(androidx.compose.ui.Modifier.height(com.company.logistics.ui.theme.Spacing.md))
                                },
                            )
                        }
                    }

                    Screen.ORDER_DETAIL -> OrderDetailScreen(
                        status = state.orderStatus,
                        detail = state.orderDetail,
                        loading = state.loading,
                        error = state.error,
                        onBack = { viewModel.navigate(Screen.SCANNER) },
                        onRefresh = { viewModel.refreshOrderDetail() },
                        multiOrder = state.multiOrderSnapshot,
                        onSelectOrder = { viewModel.selectOrder(it) },
                        onOpenDevice = { viewModel.openDeviceDetail(it) },
                        recentOrders = state.recentOrders,
                        recentOrdersError = state.recentOrdersError,
                        onRetryRecent = { viewModel.loadRecentOrders(force = true) },
                    )

                    Screen.QUEUE -> QueueScreen(
                        queue = state.offlineQueue,
                        syncing = state.syncing,
                        readOnly = state.preview,
                        onSync = { viewModel.syncNow() },
                        onClearSynced = { viewModel.clearSynced() },
                        onRetry = { viewModel.retryQueuedOperation(it) },
                        onDiscard = { viewModel.discardQueuedOperation(it) }
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

                    Screen.FLOW_DETAIL -> FlowDetailScreen(
                        role = state.role,
                        detail = state.transferRequestDetail,
                        detailState = state.transferRequestDetailState,
                        detailError = state.transferRequestDetailError,
                        handoverSubmitting = state.handoverSubmittingId != null,
                        handoverRecords = state.transferHandoverRecords,
                        handoverRecordsState = state.transferHandoverRecordsState,
                        onBack = {
                            viewModel.closeTransferRequestDetail()
                            viewModel.navigate(Screen.WORKSPACE)
                        },
                        onRetry = { viewModel.retryTransferRequestDetail() },
                        onConfirmHandover = { viewModel.confirmHandoverById(it) },
                        onStartHandover = { viewModel.navigate(Screen.FLOW_HANDOVER) },
                        onLoadHandoverRecords = {
                            state.selectedTransferRequestId?.let { viewModel.loadTransferHandoverRecords(it) }
                        },
                    )

                    Screen.FLOW_HANDOVER -> FlowHandoverScreen(
                        detail = state.transferRequestDetail,
                        detailState = state.transferRequestDetailState,
                        detailError = state.transferRequestDetailError,
                        submitting = state.transferHandoverSubmitting,
                        submitError = state.transferHandoverError,
                        onBack = { viewModel.navigate(Screen.FLOW_DETAIL) },
                        onRetry = { viewModel.retryTransferRequestDetail() },
                        onConfirm = {
                            viewModel.submitTransferHandover(it)
                        },
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

                    Screen.MY_EXCEPTIONS -> MyExceptionsScreen(
                        records = state.myExceptions,
                        state = state.myExceptionsState,
                        error = state.myExceptionsError,
                        onRefresh = { viewModel.refreshMyExceptions() },
                        onBack = { viewModel.navigate(Screen.WORKSPACE) },
                    )

                    Screen.BOM_IMPORT -> BomImportScreen(
                        state = state,
                        onPick = { name, bytes, model -> viewModel.previewBomImport(name, bytes, model) },
                        onReadError = viewModel::bomFileReadFailed,
                        onCommit = { viewModel.commitBomImport(publish = true) },
                    )

                    Screen.PRODUCTION_MANAGEMENT -> ProductionManagementScreen(state.role, state.productionWriteLoading, state.productionWriteError, viewModel::createProductionOrder, viewModel::createDevice, viewModel::assignDevice) { viewModel.navigate(Screen.WORKSPACE) }

                    Screen.DEVICE_DETAIL -> DeviceDetailScreen(
                        state.deviceDetail,
                        state.loading,
                        state.error,
                        state.deviceAssemblyTasks,
                        state.deviceMaterials,
                        state.deviceTransferRequests,
                        state.deviceLabor,
                        onBack = { viewModel.navigate(Screen.SCANNER) },
                        onSubmitMaterialRequest = { material, orderNo, qty, remark ->
                            viewModel.submitDeviceMaterialRequest(material, orderNo, qty, remark)
                        },
                        onSubmitMaterialRequestBatch = { items, orderNo, remark ->
                            viewModel.submitDeviceMaterialRequestBatch(items, orderNo, remark)
                        },
                        onSubmitException = { deviceId, materialId, materialCode, orderNo, type, bookQty, actualQty, desc ->
                            viewModel.submitDeviceException(deviceId, materialId, materialCode, orderNo, type, bookQty, actualQty, desc)
                        },
                        role = state.role,
                        drawings = { deviceId ->
                            com.company.logistics.ui.screens.DrawingsCard(
                                api = viewModel.repository.apiHandle,
                                target = com.company.logistics.drawing.DrawingTarget.Device(deviceId),
                            )
                            androidx.compose.foundation.layout.Spacer(androidx.compose.ui.Modifier.height(com.company.logistics.ui.theme.Spacing.md))
                        },
                    )

                    Screen.INVENTORY -> InventoryScreen(
                        onGoScan = { viewModel.navigate(Screen.SCANNER) }
                    )

                    Screen.PROFILE -> {
                        val _ctx = androidx.compose.ui.platform.LocalContext.current
                        val _updateChecker = remember(endpointStore.effectiveUrl) {
                            AppUpdateChecker(
                                context = _ctx,
                                api = viewModel.repository.apiHandle,
                                baseUrl = { endpointStore.effectiveUrl },
                            )
                        }
                        ProfileScreen(
                        appUpdateChecker = _updateChecker,
                        userName = state.currentUser,
                        role = state.role,
                        queue = state.offlineQueue,
                        endpointUrl = endpointStore.effectiveUrl,
                        endpointConfigured = endpointStore.isConfigured,
                        onOpenQueue = { viewModel.navigate(Screen.QUEUE) },
                        onOpenEndpointConfig = { viewModel.navigate(Screen.ENDPOINT_CONFIG) },
                        onOpenMyExceptions = { viewModel.navigate(Screen.MY_EXCEPTIONS) },
                        myTransferRequests = state.myTransferRequests,
                        myTransferRequestsLoading = state.myTransferRequestsLoading,
                        myTransferRequestsError = state.myTransferRequestsError,
                        onRetryMyTransfers = { viewModel.loadMyTransferRequests() },
                        onLogout = { viewModel.logout() }
                    )
                    }

                    Screen.ENDPOINT_CONFIG -> EndpointConfigScreen(
                        store = endpointStore,
                        onEndpointChanged = onEndpointChanged,
                        onBack = { viewModel.navigate(Screen.PROFILE) },
                        onLogout = { viewModel.logout() },
                    )

                    Screen.USER_MANAGEMENT -> {

                        LaunchedEffect(Unit) { viewModel.loadManagedUsers() }
                        UserManagementScreen(
                            users = state.managedUsers,
                            loading = state.managedUsersLoading,
                            error = state.managedUsersError,
                            onRefresh = { viewModel.loadManagedUsers() },
                            onBack = { viewModel.navigate(Screen.WORKSPACE) },
                            deletingUserId = state.managedUserDeletingId,
                            success = state.managedUsersSuccess,
                            onDelete = { viewModel.deleteManagedUser(it) },
                        ) { no, name, role, pw, manager -> viewModel.addManagedUser(no, name, role, pw, manager) }
                    }

                    Screen.LOCATION_BIND, Screen.SUBMIT, Screen.LOGIN, Screen.CHANGE_PASSWORD ->
                        com.company.logistics.ui.components.PlaceholderScreen(
                            title = state.screen.title,
                            description = "该流程界面接入中"
                        )
                }
            }
        }
    }
}

/**
 * 持续监听网络连通性。
 *
 * 用 produceState + NetworkCallback，只在网络变化时更新一个 Boolean，
 * 禁止在组合体内轮询 activeNetworkInfo（可能 ANR）。
 */
@Composable
private fun rememberIsOnline(): androidx.compose.runtime.State<Boolean> {
    val context = androidx.compose.ui.platform.LocalContext.current
    return androidx.compose.runtime.produceState(initialValue = true) {
        val cm = context.getSystemService(android.content.Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        val callback = object : android.net.ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: android.net.Network) { value = true }
            override fun onLost(network: android.net.Network) { value = false }
        }
        cm.registerNetworkCallback(android.net.NetworkRequest.Builder().build(), callback)
        value = cm.activeNetwork != null
        awaitDispose { cm.unregisterNetworkCallback(callback) }
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
                        val tabIcon = LogisticsIcons.fromSymbol(tab.symbol)
                        if (tabIcon != null) {
                            Image(
                                imageVector = tabIcon,
                                contentDescription = null,
                                colorFilter = ColorFilter.tint(color),
                                modifier = Modifier.size(19.dp)
                            )
                        } else {
                            Text(
                                tab.symbol,
                                fontSize = 19.sp,
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                color = color
                            )
                        }
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
