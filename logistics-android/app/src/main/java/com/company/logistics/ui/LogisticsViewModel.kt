package com.company.logistics.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.SubmitResult
import com.company.logistics.data.SyncReport
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.safeMessage
import com.company.logistics.model.AuditLog
import com.company.logistics.model.AssemblyAction
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.HandoverActionPolicy
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.MachineProgress
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.OrderDetail
import com.company.logistics.model.RoleWorkspaceSummary
import com.company.logistics.model.RoleWorkspaceSummaryFactory
import com.company.logistics.model.ScanResult
import com.company.logistics.model.ScanType
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.model.ServerWorkspaceSummaryFactory
import com.company.logistics.model.AdminRolePreviewController
import com.company.logistics.model.InMemoryAdminRolePreviewController
import com.company.logistics.model.WorkspaceQueryContext
import com.company.logistics.model.WorkspaceViewRole
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestAction
import com.company.logistics.model.TransferRequestActionPolicy
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.async
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * 页面路由 —— 按角色动态生成，无权限页面不渲染。
 */
enum class Screen(val title: String) {
    LOGIN("登录"),
    WORKSPACE("工作台"),
    SCANNER("扫码作业"),
    MATERIAL_DETAIL("物料详情"),
    ORDER_DETAIL("订单物料状态"),
    LOCATION_BIND("库位绑定"),
    SUBMIT("提交流转"),
    QUEUE("离线暂存"),
    APPROVAL("待审批"),
    AUDIT("审计记录"),
    INVENTORY("库存查询"),
    PROFILE("我的"),
    ENDPOINT_CONFIG("服务端配置")
}

/**
 * 会话状态机。
 *
 * Restoring 是冷启动期间的显式状态，避免在持久化会话尚未读取时短暂显示登录页。
 * Authenticated 只携带服务端登录返回的用户摘要；Unauthenticated 是唯一登录入口。
 */
sealed interface AuthState {
    data object Restoring : AuthState
    data class Authenticated(val user: User, val mustChangePassword: Boolean = false) : AuthState
    data object Unauthenticated : AuthState
}

/** 底部导航项 —— 图标用具名语义符号，避免引入图标库依赖 */
enum class NavTab(val label: String, val symbol: String, val screen: Screen) {
    WORKSPACE("工作台", "⌂", Screen.WORKSPACE),
    SCAN("扫码", "⊞", Screen.SCANNER),
    ORDER("订单", "☰", Screen.ORDER_DETAIL),
    INVENTORY("库存", "▤", Screen.INVENTORY),
    QUEUE("记录", "↻", Screen.QUEUE),
    APPROVAL("审批", "✓", Screen.APPROVAL),
    PROFILE("我的", "◎", Screen.PROFILE)
}

/** 工作台两个独立接口的可观测加载状态。 */
enum class WorkspaceLoadState {
    IDLE,
    LOADING,
    CONTENT,
    EMPTY,
    ERROR
}

/**
 * 全局 UI 状态。
 */
data class LogisticsUiState(
    val authState: AuthState = AuthState.Restoring,

    val screen: Screen = Screen.LOGIN,
    val navTabs: List<NavTab> = emptyList(),

    val loading: Boolean = false,
    val message: String? = null,
    val error: String? = null,

    // 扫码结果
    val lastScan: ScanResult? = null,
    val materialInventory: MaterialInventory? = null,
    val orderStatus: OrderMaterialStatus? = null,
    val orderDetail: OrderDetail? = null,
    val workspaceSummary: RoleWorkspaceSummary = RoleWorkspaceSummary.empty(UserRole.OPERATOR),
    val previewRole: WorkspaceViewRole? = null,
    val workspaceSummaryState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val workspaceSummaryError: String? = null,
    val workspaceSummaryUnavailable: Boolean = false,
    val workspaceItems: List<WorkspaceMaterialItem> = emptyList(),
    val workspaceItemsState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val workspaceItemsError: String? = null,
    val workspaceItemsUnavailable: Boolean = false,
    val workspacePage: Int = 1,
    val workspacePageSize: Int = WORKSPACE_PAGE_SIZE,
    val workspaceTotal: Int = 0,
    val workspaceTotalPages: Int = 0,
    val workspaceServerTime: String? = null,
    val assemblyTasks: List<AssemblyTask> = emptyList(),
    val assemblyTaskState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val assemblyTaskError: String? = null,
    val assemblyTaskUnavailable: Boolean = false,
    val assemblyTaskPage: Int = 1,
    val assemblyTaskPageSize: Int = WORKSPACE_PAGE_SIZE,
    val assemblyTaskTotal: Int = 0,
    val assemblyTaskTotalPages: Int = 0,
    val assemblySubmittingTaskId: String? = null,
    val assemblySubmittingAction: AssemblyAction? = null,
    val assemblyPendingOperationKey: String? = null,
    val assemblyClientOperationId: String? = null,
    val assemblyActiveLabor: Map<String, LaborRecord> = emptyMap(),
    val temporaryTransfer: LaborRecord? = null,
    val lastCompletedTemporaryTransfer: LaborRecord? = null,
    val temporaryTransferSourceTaskId: String? = null,
    val temporaryTransferSubmitting: Boolean = false,
    val temporaryTransferPendingOperationKey: String? = null,
    val temporaryTransferClientOperationId: String? = null,
    val workshopProgressSummary: WorkshopProgressSummary? = null,
    val workshopSummaryState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val workshopSummaryError: String? = null,
    val workshopSummaryUnavailable: Boolean = false,
    val workshopMachineProgress: List<MachineProgress> = emptyList(),
    val workshopMachineState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val workshopMachineError: String? = null,
    val workshopMachineUnavailable: Boolean = false,
    val workshopMachinePage: Int = 1,
    val workshopMachinePageSize: Int = WORKSPACE_PAGE_SIZE,
    val workshopMachineHasNext: Boolean = false,
    val workspaceTimelineItemId: String? = null,
    val workspaceTimeline: HandoverTimeline? = null,
    val workspaceTimelineState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val workspaceTimelineError: String? = null,
    val transferRequests: List<TransferRequest> = emptyList(),
    val transferRequestState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val transferRequestError: String? = null,
    val transferRequestFilter: String? = null,
    val transferRequestServerTime: String? = null,
    val selectedTransferRequestId: String? = null,
    val transferRequestDetail: TransferRequest? = null,
    val transferRequestDetailState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val transferRequestDetailError: String? = null,
    val auditLogs: List<AuditLog> = emptyList(),
    val auditState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    val auditError: String? = null,
    val auditPage: Int = 1,
    val auditPageSize: Int = AUDIT_PAGE_SIZE,
    val auditHasNext: Boolean = false,
    val auditServerTime: String? = null,
    val handoverSubmittingId: String? = null,
    /** 同一业务 payload 的网络重试复用同一个幂等键，避免服务端已成功但响应丢失时重复建单。 */
    val handoverPendingOperationKey: String? = null,
    val handoverClientOperationId: String? = null,
    val handoverRequestId: String? = null,
    val transferDecisionSubmittingId: String? = null,
    val transferDecisionPendingOperationKey: String? = null,
    val transferDecisionClientOperationId: String? = null,
    val transferDecisionRequestId: String? = null,

    // 离线
    val offlineQueue: List<OfflineOperation> = emptyList(),
    val pendingCount: Int = 0,
    val syncing: Boolean = false,

    // 提交表单
    val formQuantity: Int = 0,
    val formLocation: String? = null
) {
    val loggedIn: Boolean get() = authState is AuthState.Authenticated
    val currentUser: String
        get() = (authState as? AuthState.Authenticated)?.user?.displayName.orEmpty()
    val currentUserId: String?
        get() = (authState as? AuthState.Authenticated)?.user?.id
    val role: UserRole
        get() = (authState as? AuthState.Authenticated)?.user?.role ?: UserRole.OPERATOR
    val workspaceRole: UserRole
        get() = previewRole?.toUserRole() ?: role
    val preview: Boolean
        get() = previewRole != null
    val mustChangePassword: Boolean
        get() = (authState as? AuthState.Authenticated)?.mustChangePassword ?: false

    val workspaceLoading: Boolean
        get() = workspaceSummaryState == WorkspaceLoadState.LOADING ||
            workspaceItemsState == WorkspaceLoadState.LOADING

    val transferRequestLoading: Boolean
        get() = transferRequestState == WorkspaceLoadState.LOADING

    val auditLoading: Boolean
        get() = auditState == WorkspaceLoadState.LOADING

    val assemblyLoading: Boolean
        get() = assemblyTaskState == WorkspaceLoadState.LOADING

    val workshopLoading: Boolean
        get() = workshopSummaryState == WorkspaceLoadState.LOADING ||
            workshopMachineState == WorkspaceLoadState.LOADING

    companion object {
        const val WORKSPACE_PAGE_SIZE = 20
        const val AUDIT_PAGE_SIZE = 50
    }
}

/**
 * 主 ViewModel —— 承载会话、路由、扫码流程与离线队列。
 */
class LogisticsViewModel(
    private val repo: LogisticsRepository,
    private val injectedScope: CoroutineScope? = null,
    private val previewControllerFactory: (UserRole) -> AdminRolePreviewController =
        ::InMemoryAdminRolePreviewController,
) : ViewModel() {

    private val operationScope: CoroutineScope
        get() = injectedScope ?: viewModelScope

    private val _state = MutableStateFlow(LogisticsUiState())
    val state: StateFlow<LogisticsUiState> = _state.asStateFlow()

    // Generation guards prevent a late response from an older refresh replacing newer data.
    private var transferRequestLoadGeneration = 0L
    private var transferRequestDetailGeneration = 0L
    private var auditLoadGeneration = 0L
    private var workspaceLoadGeneration = 0L
    private var workspaceLoadJob: Job? = null
    private var previewController: AdminRolePreviewController? = null
    @Volatile
    private var sessionGeneration = 0L

    init {
        repo.onSessionExpired = {
            expireSession()
        }
        // restoreSession 会读取加密摘要并把 token 对恢复到 API。放进协程后，UI 能明确经历
        // Restoring，而不是在 ViewModel 构造的同一帧里错误地落到登录页。
        val restoreGeneration = sessionGeneration
        operationScope.launch {
            repo.restoreSession()
            val persisted = repo.persistedUser()
            if (restoreGeneration != sessionGeneration) return@launch
            _state.update {
                if (persisted == null) {
                    previewController = null
                    it.copy(authState = AuthState.Unauthenticated)
                } else {
                    val user = persisted.toUser()
                    previewController = previewControllerFactory(user.role)
                    it.copy(
                        authState = AuthState.Authenticated(user, persisted.mustChangePassword),
                        navTabs = tabsFor(user.role),
                        screen = defaultScreenFor(user.role),
                        workspaceSummary = RoleWorkspaceSummaryFactory.from(user.role, null),
                        previewRole = null,
                        workspaceSummaryState = WorkspaceLoadState.IDLE,
                        workspaceItemsState = WorkspaceLoadState.IDLE
                    )
                }
            }
            if (persisted != null) {
                loadWorkspacePage(resetToFirstPage = true)
            }
        }
        // 队列变化实时反映到 UI
        operationScope.launch {
            repo.observeQueue().collect { queue ->
                _state.update { it.copy(offlineQueue = queue) }
            }
        }
        operationScope.launch {
            repo.observePendingCount().collect { count ->
                _state.update { it.copy(pendingCount = count) }
            }
        }
    }

    // ==================== 登录 ====================

    fun login(username: String, password: String, deviceId: String, remember: Boolean = true) {
        if (username.isBlank() || password.isBlank()) {
            _state.update { it.copy(error = "请输入账号与密码") }
            return
        }
        val loginGeneration = ++sessionGeneration
        operationScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            resultOf { repo.login(username, password, deviceId, remember) }
                .onSuccess { result ->
                    if (loginGeneration != sessionGeneration) return@onSuccess
                    previewController = previewControllerFactory(result.user.role)
                    _state.update {
                        it.copy(
                            authState = AuthState.Authenticated(result.user, result.mustChangePassword),
                            navTabs = tabsFor(result.user.role),
                            screen = defaultScreenFor(result.user.role),
                            workspaceSummary = RoleWorkspaceSummaryFactory.from(result.user.role, null),
                            previewRole = null,
                            loading = false,
                            message = "登录成功"
                        )
                    }
                    loadWorkspacePage(resetToFirstPage = true)
                }
                .onFailure { e ->
                    if (loginGeneration != sessionGeneration) return@onFailure
                    val message = when (e) {
                        is ApiException -> if (e.isUnauthorized) {
                            "账号或密码错误，请检查后重试"
                        } else {
                            e.safeMessage("登录失败，请重试")
                        }
                        else -> "网络不可用，请检查连接后重试"
                    }
                    _state.update { it.copy(loading = false, error = message) }
                }
        }
    }

    fun logout() {
        sessionGeneration++
        cancelWorkspaceLoad()
        previewController = null
        repo.logout()
        _state.value = LogisticsUiState(authState = AuthState.Unauthenticated)
    }

    /**
     * 401 is terminal for the current access/refresh pair. Clear both memory and disk so
     * a later cold start cannot replay the same invalid refresh token.
     */
    private fun expireSession() {
        if (!_state.value.loggedIn) return
        sessionGeneration++
        cancelWorkspaceLoad()
        previewController = null
        repo.logout()
        _state.value = LogisticsUiState(
            authState = AuthState.Unauthenticated,
            error = "登录已失效，请重新登录",
        )
    }

    // ==================== 路由 ====================

    fun navigate(screen: Screen) = _state.update {
        if (!canNavigate(it.role, screen)) {
            it.copy(error = "当前角色无权访问该页面")
        } else {
            it.copy(screen = screen, error = null)
        }
    }.also {
        when (screen) {
            Screen.APPROVAL -> refreshTransferRequests()
            Screen.AUDIT -> refreshAuditLogs()
            else -> Unit
        }
    }

    fun consumeMessage() = _state.update { it.copy(message = null, error = null) }

    // ==================== 工作台 ====================

    /** Enters a read-only workspace projection while keeping AuthState.role as ADMIN. */
    fun enterRolePreview(role: WorkspaceViewRole): Result<Unit> {
        val current = _state.value
        if (!current.loggedIn || current.role != UserRole.ADMIN) {
            val failure = Result.failure<Unit>(IllegalStateException("只有 ADMIN 可以进入测试角色视图"))
            _state.update { it.copy(error = failure.exceptionOrNull()?.message) }
            return failure
        }
        val controller = previewController ?: previewControllerFactory(current.role).also {
            previewController = it
        }
        val result = controller.enterPreview(role)
        result.onSuccess {
            cancelWorkspaceLoad()
            _state.update {
                it.copy(
                    previewRole = role,
                    workspaceSummary = RoleWorkspaceSummary.empty(role.toUserRole()),
                    workspaceSummaryState = WorkspaceLoadState.IDLE,
                    workspaceSummaryError = null,
                    workspaceSummaryUnavailable = false,
                    workspaceItems = emptyList(),
                    workspaceItemsState = WorkspaceLoadState.IDLE,
                    workspaceItemsError = null,
                    workspaceItemsUnavailable = false,
                    workspacePage = 1,
                    workspaceTotal = 0,
                    workspaceTotalPages = 0,
                    workspaceServerTime = null,
                    assemblyTasks = emptyList(),
                    assemblyTaskState = WorkspaceLoadState.IDLE,
                    assemblyTaskError = null,
                    assemblyTaskUnavailable = false,
                    assemblyTaskPage = 1,
                    assemblyTaskTotal = 0,
                    assemblyTaskTotalPages = 0,
                    temporaryTransfer = null,
                    lastCompletedTemporaryTransfer = null,
                    temporaryTransferSourceTaskId = null,
                    workshopProgressSummary = null,
                    workshopSummaryState = WorkspaceLoadState.IDLE,
                    workshopSummaryError = null,
                    workshopSummaryUnavailable = false,
                    workshopMachineProgress = emptyList(),
                    workshopMachineState = WorkspaceLoadState.IDLE,
                    workshopMachineError = null,
                    workshopMachineUnavailable = false,
                    workshopMachinePage = 1,
                    workshopMachineHasNext = false,
                    message = "已切换到${role.label}测试视图",
                    error = null,
                )
            }
            loadWorkspacePage(resetToFirstPage = true)
        }.onFailure { error ->
            _state.update { it.copy(error = error.message ?: "无法进入测试角色视图") }
        }
        return result.map { Unit }
    }

    /** Leaves preview and reloads the real ADMIN workspace without logging out. */
    fun exitRolePreview(): Result<Unit> {
        val current = _state.value
        val result = previewController?.exitPreview()
            ?: Result.success(
                WorkspaceQueryContext(
                    authenticatedRole = current.role,
                    viewRole = WorkspaceViewRole.from(current.role),
                    preview = false,
                )
            )
        result.onSuccess {
            cancelWorkspaceLoad()
            _state.update {
                it.copy(
                    previewRole = null,
                    workspaceSummary = RoleWorkspaceSummary.empty(it.role),
                    workspaceSummaryState = WorkspaceLoadState.IDLE,
                    workspaceSummaryError = null,
                    workspaceSummaryUnavailable = false,
                    workspaceItems = emptyList(),
                    workspaceItemsState = WorkspaceLoadState.IDLE,
                    workspaceItemsError = null,
                    workspaceItemsUnavailable = false,
                    workspacePage = 1,
                    workspaceTotal = 0,
                    workspaceTotalPages = 0,
                    workspaceServerTime = null,
                    assemblyTasks = emptyList(),
                    assemblyTaskState = WorkspaceLoadState.IDLE,
                    assemblyTaskError = null,
                    assemblyTaskUnavailable = false,
                    assemblyTaskPage = 1,
                    assemblyTaskTotal = 0,
                    assemblyTaskTotalPages = 0,
                    temporaryTransfer = null,
                    lastCompletedTemporaryTransfer = null,
                    temporaryTransferSourceTaskId = null,
                    workshopProgressSummary = null,
                    workshopSummaryState = WorkspaceLoadState.IDLE,
                    workshopSummaryError = null,
                    workshopSummaryUnavailable = false,
                    workshopMachineProgress = emptyList(),
                    workshopMachineState = WorkspaceLoadState.IDLE,
                    workshopMachineError = null,
                    workshopMachineUnavailable = false,
                    workshopMachinePage = 1,
                    workshopMachineHasNext = false,
                    message = "已恢复管理员工作台",
                    error = null,
                )
            }
            if (_state.value.loggedIn) loadWorkspacePage(resetToFirstPage = true)
        }.onFailure { error ->
            _state.update { it.copy(error = error.message ?: "无法退出测试角色视图") }
        }
        return result.map { Unit }
    }

    /** 刷新服务端摘要与第一页工作项；服务端分页是唯一数据来源。 */
    fun refreshWorkspace() {
        loadWorkspacePage(resetToFirstPage = true)
    }

    /** 切换服务端分页大小；仅支持内存约束规定的 20/50。 */
    fun setWorkspacePageSize(pageSize: Int) {
        if (_state.value.workspaceRole in setOf(UserRole.ASSEMBLER, UserRole.WORKSHOP_SUPERVISOR) && pageSize != LogisticsUiState.WORKSPACE_PAGE_SIZE) {
            _state.update { it.copy(error = "装配与车间统计列表每页固定 20 条") }
            return
        }
        if (_state.value.preview && pageSize != LogisticsUiState.WORKSPACE_PAGE_SIZE) {
            _state.update { it.copy(error = "测试角色预览每页固定 20 条") }
            return
        }
        if (pageSize !in WORKSPACE_PAGE_SIZES) {
            _state.update { it.copy(error = "工作台每页数量只能是 20 或 50") }
            return
        }
        if (_state.value.loggedIn && _state.value.workspacePageSize != pageSize) {
            loadWorkspacePage(resetToFirstPage = true, pageSize = pageSize)
        }
    }

    fun loadNextWorkspacePage() {
        val current = _state.value
        when (current.workspaceRole) {
            UserRole.ASSEMBLER -> if (current.assemblyTaskPage < current.assemblyTaskTotalPages && !current.assemblyLoading) {
                loadWorkspacePage(resetToFirstPage = false, page = current.assemblyTaskPage + 1)
            }
            UserRole.WORKSHOP_SUPERVISOR -> if (current.workshopMachineHasNext && !current.workshopLoading) {
                loadWorkspacePage(resetToFirstPage = false, page = current.workshopMachinePage + 1)
            }
            else -> if (canLoadNextWorkspacePage(current.workspacePage, current.workspaceTotalPages) && !current.workspaceLoading) {
                loadWorkspacePage(
                    resetToFirstPage = false,
                    page = current.workspacePage + 1,
                    pageSize = current.workspacePageSize,
                )
            }
        }
    }

    fun loadPreviousWorkspacePage() {
        val current = _state.value
        when (current.workspaceRole) {
            UserRole.ASSEMBLER -> if (canLoadPreviousWorkspacePage(current.assemblyTaskPage) && !current.assemblyLoading) {
                loadWorkspacePage(resetToFirstPage = false, page = current.assemblyTaskPage - 1)
            }
            UserRole.WORKSHOP_SUPERVISOR -> if (canLoadPreviousWorkspacePage(current.workshopMachinePage) && !current.workshopLoading) {
                loadWorkspacePage(resetToFirstPage = false, page = current.workshopMachinePage - 1)
            }
            else -> if (canLoadPreviousWorkspacePage(current.workspacePage) && !current.workspaceLoading) {
                loadWorkspacePage(
                    resetToFirstPage = false,
                    page = current.workspacePage - 1,
                    pageSize = current.workspacePageSize,
                )
            }
        }
    }

    private fun loadWorkspacePage(
        resetToFirstPage: Boolean,
        page: Int = 1,
        pageSize: Int = _state.value.workspacePageSize,
    ) {
        when (_state.value.workspaceRole) {
            UserRole.ASSEMBLER -> loadAssemblyTaskPage(resetToFirstPage, page)
            UserRole.WORKSHOP_SUPERVISOR -> loadWorkshopDashboard(resetToFirstPage, page)
            else -> loadGenericWorkspacePage(resetToFirstPage, page, pageSize)
        }
    }

    private fun loadGenericWorkspacePage(
        resetToFirstPage: Boolean,
        page: Int = 1,
        pageSize: Int = _state.value.workspacePageSize,
    ) {
        if (!_state.value.loggedIn) return
        val currentSession = sessionGeneration
        val loadContext = currentWorkspaceContext()
        val generation = ++workspaceLoadGeneration
        workspaceLoadJob?.cancel()
        workspaceLoadJob = operationScope.launch {
            if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@launch
            val requestedPage = if (resetToFirstPage) 1 else page
            _state.update {
                it.copy(
                    workspaceSummary = if (resetToFirstPage) RoleWorkspaceSummary.empty(loadContext.viewRole.toUserRole()) else it.workspaceSummary,
                    workspaceSummaryState = if (resetToFirstPage) WorkspaceLoadState.LOADING else it.workspaceSummaryState,
                    workspaceSummaryError = if (resetToFirstPage) null else it.workspaceSummaryError,
                    workspaceSummaryUnavailable = if (resetToFirstPage) false else it.workspaceSummaryUnavailable,
                    workspaceItemsState = WorkspaceLoadState.LOADING,
                    workspaceItemsError = null,
                    workspaceItemsUnavailable = false,
                    workspacePage = requestedPage,
                    workspacePageSize = pageSize,
                    workspaceItems = if (resetToFirstPage) emptyList() else it.workspaceItems,
                    workspaceTotal = if (resetToFirstPage) 0 else it.workspaceTotal,
                    workspaceTotalPages = if (resetToFirstPage) 0 else it.workspaceTotalPages,
                )
            }

            kotlinx.coroutines.coroutineScope {
                val summary = if (resetToFirstPage) async {
                    if (loadContext.preview) {
                        repo.loadRoleSummary(
                            viewRole = loadContext.viewRole,
                            requestId = UUID.randomUUID().toString(),
                        )
                    } else {
                        repo.workspaceSummary()
                    }
                } else null
                val items = async {
                    if (loadContext.preview) {
                        repo.listWorkItems(
                            viewRole = loadContext.viewRole,
                            status = null,
                            orderNo = null,
                            page = requestedPage,
                            pageSize = LogisticsUiState.WORKSPACE_PAGE_SIZE,
                            requestId = UUID.randomUUID().toString(),
                        )
                    } else {
                        repo.workspaceMaterialItems(
                            page = requestedPage,
                            pageSize = pageSize
                        )
                    }
                }

                val summaryResult = summary?.await()
                val itemsResult = items.await()

                if (summaryResult != null) {
                    summaryResult
                    .onSuccess { serverSummary ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onSuccess
                        _state.update {
                            it.copy(
                                workspaceSummary = ServerWorkspaceSummaryFactory.from(serverSummary),
                                workspaceSummaryState = WorkspaceLoadState.CONTENT,
                                workspaceSummaryError = null,
                                workspaceSummaryUnavailable = false,
                                workspaceServerTime = serverSummary.serverTime
                                    ?: serverSummary.generatedAt
                            )
                        }
                    }
                    .onFailure { error ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onFailure
                        if (error is ApiException && error.isUnauthorized) {
                            expireSession()
                            return@onFailure
                        }
                        _state.update {
                            it.copy(
                                workspaceSummaryState = WorkspaceLoadState.ERROR,
                                workspaceSummaryError = workspaceErrorMessage(error),
                                workspaceSummaryUnavailable = isWorkspaceUnavailable(error)
                            )
                        }
                    }
                }

                itemsResult
                    .onSuccess { pageResult ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onSuccess
                        _state.update {
                            val currentSummary = it.workspaceSummary
                            val summaryWithTotal = currentSummary.copy(
                                metrics = currentSummary.metrics + (
                                    com.company.logistics.model.WorkspaceMetricKey.ALL to
                                        com.company.logistics.model.WorkspaceMetric.of(pageResult.total)
                                    )
                            )
                            it.copy(
                                workspaceSummary = summaryWithTotal,
                                workspaceItems = pageResult.items,
                                workspaceItemsState = if (pageResult.items.isEmpty()) {
                                    WorkspaceLoadState.EMPTY
                                } else {
                                    WorkspaceLoadState.CONTENT
                                },
                                workspaceItemsError = null,
                                workspaceItemsUnavailable = false,
                                workspacePage = pageResult.page,
                                workspacePageSize = pageResult.pageSize,
                                workspaceTotal = pageResult.total,
                                workspaceTotalPages = pageResult.totalPages,
                                workspaceServerTime = pageResult.serverTime
                                    ?: it.workspaceServerTime
                            )
                        }
                    }
                    .onFailure { error ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onFailure
                        if (error is ApiException && error.isUnauthorized) {
                            expireSession()
                            return@onFailure
                        }
                        _state.update {
                            it.copy(
                                workspaceItemsState = WorkspaceLoadState.ERROR,
                                workspaceItemsError = workspaceErrorMessage(error),
                                workspaceItemsUnavailable = isWorkspaceUnavailable(error)
                            )
                        }
                    }
            }
        }
    }

    private fun loadAssemblyTaskPage(
        resetToFirstPage: Boolean,
        page: Int = 1,
    ) {
        if (!_state.value.loggedIn) return
        val currentSession = sessionGeneration
        val loadContext = currentWorkspaceContext()
        val generation = ++workspaceLoadGeneration
        workspaceLoadJob?.cancel()
        workspaceLoadJob = operationScope.launch {
            if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@launch
            val requestedPage = if (resetToFirstPage) 1 else page
            _state.update {
                it.copy(
                    assemblyTaskState = WorkspaceLoadState.LOADING,
                    assemblyTaskError = null,
                    assemblyTaskUnavailable = false,
                    assemblyTaskPage = requestedPage,
                    assemblyTaskPageSize = LogisticsUiState.WORKSPACE_PAGE_SIZE,
                    assemblyTasks = if (resetToFirstPage) emptyList() else it.assemblyTasks,
                    assemblyTaskTotal = if (resetToFirstPage) 0 else it.assemblyTaskTotal,
                    assemblyTaskTotalPages = if (resetToFirstPage) 0 else it.assemblyTaskTotalPages,
                )
            }
            repo.assemblyTaskPage(requestedPage, LogisticsUiState.WORKSPACE_PAGE_SIZE)
                .onSuccess { result ->
                    if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onSuccess
                    _state.update {
                        it.copy(
                            assemblyTasks = result.items,
                            assemblyTaskState = if (result.items.isEmpty()) WorkspaceLoadState.EMPTY else WorkspaceLoadState.CONTENT,
                            assemblyTaskError = null,
                            assemblyTaskUnavailable = false,
                            assemblyTaskPage = result.page,
                            assemblyTaskPageSize = result.pageSize,
                            assemblyTaskTotal = result.total,
                            assemblyTaskTotalPages = result.totalPages,
                            workspaceServerTime = result.serverTime ?: it.workspaceServerTime,
                        )
                    }
                }
                .onFailure { error ->
                    if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onFailure
                    if (error is ApiException && error.isUnauthorized) {
                        expireSession()
                        return@onFailure
                    }
                    _state.update {
                        it.copy(
                            assemblyTaskState = WorkspaceLoadState.ERROR,
                            assemblyTaskError = assemblyErrorMessage(error),
                            assemblyTaskUnavailable = isWorkspaceUnavailable(error),
                        )
                    }
                }
        }
    }

    private fun loadWorkshopDashboard(
        resetToFirstPage: Boolean,
        page: Int = 1,
    ) {
        if (!_state.value.loggedIn) return
        val currentSession = sessionGeneration
        val loadContext = currentWorkspaceContext()
        val generation = ++workspaceLoadGeneration
        workspaceLoadJob?.cancel()
        workspaceLoadJob = operationScope.launch {
            if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@launch
            val requestedPage = if (resetToFirstPage) 1 else page
            _state.update {
                it.copy(
                    workshopProgressSummary = if (resetToFirstPage) null else it.workshopProgressSummary,
                    workshopSummaryState = if (resetToFirstPage) WorkspaceLoadState.LOADING else it.workshopSummaryState,
                    workshopSummaryError = if (resetToFirstPage) null else it.workshopSummaryError,
                    workshopSummaryUnavailable = if (resetToFirstPage) false else it.workshopSummaryUnavailable,
                    workshopMachineState = WorkspaceLoadState.LOADING,
                    workshopMachineError = null,
                    workshopMachineUnavailable = false,
                    workshopMachinePage = requestedPage,
                    workshopMachinePageSize = LogisticsUiState.WORKSPACE_PAGE_SIZE,
                    workshopMachineProgress = if (resetToFirstPage) emptyList() else it.workshopMachineProgress,
                )
            }

            kotlinx.coroutines.coroutineScope {
                val summary = if (resetToFirstPage) async { repo.workshopProgressSummary() } else null
                val machines = async {
                    repo.workshopMachineProgress(requestedPage, LogisticsUiState.WORKSPACE_PAGE_SIZE)
                }

                summary?.await()
                    ?.onSuccess { result ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onSuccess
                        _state.update {
                            it.copy(
                                workshopProgressSummary = result,
                                workshopSummaryState = WorkspaceLoadState.CONTENT,
                                workshopSummaryError = null,
                                workshopSummaryUnavailable = false,
                                workspaceServerTime = result.generatedAt,
                            )
                        }
                    }
                    ?.onFailure { error ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onFailure
                        if (error is ApiException && error.isUnauthorized) {
                            expireSession()
                            return@onFailure
                        }
                        _state.update {
                            it.copy(
                                workshopSummaryState = WorkspaceLoadState.ERROR,
                                workshopSummaryError = workshopErrorMessage(error),
                                workshopSummaryUnavailable = isWorkspaceUnavailable(error),
                            )
                        }
                    }

                machines.await()
                    .onSuccess { result ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onSuccess
                        _state.update {
                            it.copy(
                                workshopMachineProgress = result.items,
                                workshopMachineState = if (result.items.isEmpty()) WorkspaceLoadState.EMPTY else WorkspaceLoadState.CONTENT,
                                workshopMachineError = null,
                                workshopMachineUnavailable = false,
                                workshopMachinePage = result.page,
                                workshopMachinePageSize = result.pageSize,
                                workshopMachineHasNext = result.items.size >= result.pageSize,
                            )
                        }
                    }
                    .onFailure { error ->
                        if (!isWorkspaceLoadActive(currentSession, generation, loadContext)) return@onFailure
                        if (error is ApiException && error.isUnauthorized) {
                            expireSession()
                            return@onFailure
                        }
                        _state.update {
                            it.copy(
                                workshopMachineState = WorkspaceLoadState.ERROR,
                                workshopMachineError = workshopErrorMessage(error),
                                workshopMachineUnavailable = isWorkspaceUnavailable(error),
                            )
                        }
                    }
            }
        }
    }

    private fun workspaceErrorMessage(error: Throwable): String = when {
        isWorkspaceUnavailable(error) -> "服务端未提供工作台接口，当前工作台不可用"
        error is ApiException -> error.safeMessage("工作台加载失败，请稍后重试")
        else -> "网络不可用，请检查连接后重试"
    }

    private fun assemblyErrorMessage(error: Throwable): String = when {
        isWorkspaceUnavailable(error) -> "服务端未提供装配任务接口，当前任务不可用"
        error is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权查看装配任务"
            error.isConflict -> "装配任务状态已变化，请刷新后重试"
            else -> error.safeMessage("装配任务加载失败，请稍后重试")
        }
        else -> "网络不可用，请检查连接后重试"
    }

    private fun workshopErrorMessage(error: Throwable): String = when {
        isWorkspaceUnavailable(error) -> "服务端未提供车间统计接口，当前统计不可用"
        error is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权查看车间统计"
            else -> error.safeMessage("车间统计加载失败，请稍后重试")
        }
        else -> "网络不可用，请检查连接后重试"
    }

    private fun currentWorkspaceContext(): WorkspaceQueryContext {
        val current = _state.value
        return previewController?.currentContext()
            ?: WorkspaceQueryContext(
                authenticatedRole = current.role,
                viewRole = WorkspaceViewRole.from(current.role),
                preview = false,
            )
    }

    private fun isWorkspaceLoadActive(
        session: Long,
        generation: Long,
        context: WorkspaceQueryContext,
    ): Boolean = isSessionActive(session) &&
        generation == workspaceLoadGeneration &&
        currentWorkspaceContext() == context &&
        _state.value.preview == context.preview

    private fun cancelWorkspaceLoad() {
        workspaceLoadGeneration++
        workspaceLoadJob?.cancel()
        workspaceLoadJob = null
    }

    private fun isWorkspaceUnavailable(error: Throwable): Boolean {
        val apiError = error as? com.company.logistics.data.remote.ApiException
        return apiError?.statusCode == 404 || apiError?.statusCode == 405 ||
            apiError?.code in setOf("NOT_FOUND", "WORKSPACE_NOT_SUPPORTED")
    }

    // Assembly writes stay online because the server owns timestamps and task versions.
    fun acceptAssemblyMaterial(task: AssemblyTask) = submitAssemblyAction(task, AssemblyAction.ACCEPT_MATERIAL)

    fun startAssemblyWork(task: AssemblyTask) = submitAssemblyAction(task, AssemblyAction.START_WORK)

    fun submitAssemblyProgress(task: AssemblyTask, stage: Int) {
        if (stage !in 1..3) {
            _state.update { it.copy(error = "进度阶段必须为 1、2 或 3") }
            return
        }
        submitAssemblyAction(task, AssemblyAction.PROGRESS, stage)
    }

    fun completeAssemblyWork(task: AssemblyTask) = submitAssemblyAction(task, AssemblyAction.COMPLETE_WORK)

    private fun submitAssemblyAction(task: AssemblyTask, action: AssemblyAction, stage: Int? = null) {
        val current = _state.value
        if (current.preview) {
            _state.update { it.copy(error = "测试预览只读，不能执行装配操作") }
            return
        }
        if (current.role != UserRole.ASSEMBLER || current.assemblySubmittingTaskId != null) return
        val operationKey = listOf(action.name, task.id, task.taskVersion, stage ?: "").joinToString("|")
        val operationId = if (current.assemblyPendingOperationKey == operationKey) {
            current.assemblyClientOperationId ?: UUID.randomUUID().toString()
        } else UUID.randomUUID().toString()
        operationScope.launch {
            _state.update {
                it.copy(
                    assemblySubmittingTaskId = task.id,
                    assemblySubmittingAction = action,
                    assemblyPendingOperationKey = operationKey,
                    assemblyClientOperationId = operationId,
                    error = null,
                    message = null,
                )
            }
            val result = when (action) {
                AssemblyAction.ACCEPT_MATERIAL -> repo.acceptAssemblyMaterial(task.id, operationId)
                AssemblyAction.START_WORK -> repo.startAssemblyWork(task.id, task.taskVersion, operationId)
                AssemblyAction.PROGRESS -> repo.submitAssemblyProgress(task.id, stage ?: 0, task.taskVersion, operationId)
                AssemblyAction.COMPLETE_WORK -> repo.completeAssemblyWork(task.id, task.taskVersion, operationId)
            }
            result.onSuccess { response ->
                _state.update {
                    val refreshed = it.assemblyTasks.map { currentTask ->
                        when {
                            currentTask.id != task.id -> currentTask
                            response is AssemblyTask -> response
                            response is LaborRecord -> currentTask.copy(
                                status = com.company.logistics.model.AssemblyTaskStatus.IN_PROGRESS,
                                taskVersion = response.taskVersion ?: currentTask.taskVersion + 1,
                                currentLaborRecordId = response.laborRecordId ?: response.id,
                                currentLaborStartedAt = response.startedAt,
                            )
                            else -> currentTask
                        }
                    }
                    it.copy(
                        assemblyTasks = refreshed,
                        assemblySubmittingTaskId = null,
                        assemblySubmittingAction = null,
                        assemblyPendingOperationKey = null,
                        assemblyClientOperationId = null,
                        assemblyActiveLabor = if (response is LaborRecord && response.type == com.company.logistics.model.LaborType.ASSEMBLY) {
                            it.assemblyActiveLabor + (task.id to response)
                        } else if (action == AssemblyAction.COMPLETE_WORK) {
                            it.assemblyActiveLabor - task.id
                        } else it.assemblyActiveLabor,
                        message = "${action.label}成功",
                    )
                }
                if (action == AssemblyAction.COMPLETE_WORK || action == AssemblyAction.ACCEPT_MATERIAL) loadAssemblyTaskPage(true, 1)
                refreshOrderAfterMutation()
            }.onFailure { error ->
                if (error is ApiException && error.isUnauthorized) {
                    expireSession()
                    return@onFailure
                }
                _state.update {
                    it.copy(
                        assemblySubmittingTaskId = null,
                        assemblySubmittingAction = null,
                        assemblyPendingOperationKey = if (canRetryAssembly(error)) operationKey else null,
                        assemblyClientOperationId = if (canRetryAssembly(error)) operationId else null,
                        error = assemblyActionErrorMessage(error),
                    )
                }
                if (error is ApiException && error.isConflict) loadAssemblyTaskPage(true, 1)
            }
        }
    }

    fun startTemporaryTransfer(taskId: String?, remark: String) {
        val trimmed = remark.trim()
        val current = _state.value
        if (current.preview) {
            _state.update { it.copy(error = "测试预览只读，不能开始临时调拨") }
            return
        }
        if (current.role != UserRole.ASSEMBLER) return
        if (trimmed.length !in 1..500) {
            _state.update { it.copy(error = "临时调拨备注必填，长度为 1~500 字符") }
            return
        }
        if (current.temporaryTransferSubmitting) return
        val operationKey = listOf("START", taskId.orEmpty(), trimmed).joinToString("|")
        val operationId = if (current.temporaryTransferPendingOperationKey == operationKey) {
            current.temporaryTransferClientOperationId ?: UUID.randomUUID().toString()
        } else UUID.randomUUID().toString()
        operationScope.launch {
            _state.update {
                it.copy(
                    temporaryTransferSubmitting = true,
                    temporaryTransferPendingOperationKey = operationKey,
                    temporaryTransferClientOperationId = operationId,
                    error = null,
                )
            }
            repo.startTemporaryTransfer(taskId, trimmed, operationId)
                .onSuccess { result ->
                    _state.update {
                        it.copy(
                            temporaryTransfer = result,
                            lastCompletedTemporaryTransfer = null,
                            temporaryTransferSourceTaskId = taskId,
                            assemblyActiveLabor = if (taskId == null) it.assemblyActiveLabor else it.assemblyActiveLabor - taskId,
                            temporaryTransferSubmitting = false,
                            temporaryTransferPendingOperationKey = null,
                            temporaryTransferClientOperationId = null,
                            message = "临时调拨已开始",
                        )
                    }
                    loadAssemblyTaskPage(true, 1)
                    refreshOrderAfterMutation()
                }
                .onFailure { error ->
                    if (error is ApiException && error.isUnauthorized) {
                        expireSession()
                        return@onFailure
                    }
                    _state.update {
                        it.copy(
                            temporaryTransferSubmitting = false,
                            temporaryTransferPendingOperationKey = if (canRetryAssembly(error)) operationKey else null,
                            temporaryTransferClientOperationId = if (canRetryAssembly(error)) operationId else null,
                            error = temporaryTransferErrorMessage(error),
                        )
                    }
                }
        }
    }

    fun completeTemporaryTransfer(remark: String) {
        val current = _state.value
        val transfer = current.temporaryTransfer
        val transferId = transfer?.temporaryTransferId
        val trimmed = remark.trim()
        if (transferId.isNullOrBlank()) {
            _state.update { it.copy(error = "当前没有进行中的临时调拨") }
            return
        }
        if (trimmed.length !in 1..500) {
            _state.update { it.copy(error = "临时调拨备注必填，长度为 1~500 字符") }
            return
        }
        if (current.temporaryTransferSubmitting || current.preview) {
            if (current.preview) _state.update { it.copy(error = "测试预览只读，不能完成临时调拨") }
            return
        }
        val operationKey = listOf("COMPLETE", transferId, trimmed).joinToString("|")
        val operationId = if (current.temporaryTransferPendingOperationKey == operationKey) {
            current.temporaryTransferClientOperationId ?: UUID.randomUUID().toString()
        } else UUID.randomUUID().toString()
        operationScope.launch {
            _state.update {
                it.copy(
                    temporaryTransferSubmitting = true,
                    temporaryTransferPendingOperationKey = operationKey,
                    temporaryTransferClientOperationId = operationId,
                    error = null,
                )
            }
            repo.completeTemporaryTransfer(transferId, trimmed, operationId)
                .onSuccess { result ->
                    val completed = result.copy(
                        id = result.id.ifBlank { transfer.id },
                        taskId = result.taskId ?: transfer.taskId,
                        startedAt = result.startedAt.ifBlank { transfer.startedAt },
                        endedAt = result.endedAt ?: transfer.endedAt,
                        durationMinutes = result.durationMinutes ?: transfer.durationMinutes,
                        remark = result.remark ?: trimmed,
                        laborRecordId = result.laborRecordId ?: transfer.laborRecordId,
                        temporaryTransferId = result.temporaryTransferId ?: transfer.temporaryTransferId,
                        status = result.status ?: "COMPLETED",
                        serverTime = result.serverTime ?: transfer.serverTime,
                    )
                    _state.update {
                        it.copy(
                            temporaryTransfer = null,
                            lastCompletedTemporaryTransfer = completed,
                            temporaryTransferSubmitting = false,
                            temporaryTransferPendingOperationKey = null,
                            temporaryTransferClientOperationId = null,
                            message = "临时调拨已完成",
                        )
                    }
                    loadAssemblyTaskPage(true, 1)
                    refreshOrderAfterMutation()
                }
                .onFailure { error ->
                    if (error is ApiException && error.isUnauthorized) {
                        expireSession()
                        return@onFailure
                    }
                    _state.update {
                        it.copy(
                            temporaryTransferSubmitting = false,
                            temporaryTransferPendingOperationKey = if (canRetryAssembly(error)) operationKey else null,
                            temporaryTransferClientOperationId = if (canRetryAssembly(error)) operationId else null,
                            error = temporaryTransferErrorMessage(error),
                        )
                    }
                }
        }
    }

    private fun canRetryAssembly(error: Throwable): Boolean =
        error !is ApiException || error.retryable || error.isUnauthorized

    private fun assemblyActionErrorMessage(error: Throwable): String = when (error) {
        is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权操作该装配任务"
            error.isConflict -> when (error.contractCode()) {
                "TASK_VERSION_CONFLICT" -> "任务版本已变化，请刷新后重试"
                "PROGRESS_ORDER_CONFLICT" -> "进度必须按 1、2、3 顺序提交"
                else -> "装配任务状态已变化，请刷新后重试"
            }
            else -> error.safeMessage("装配操作未完成，请重试")
        }
        else -> "网络不可用，装配操作未完成，请重试"
    }

    private fun temporaryTransferErrorMessage(error: Throwable): String = when (error) {
        is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权执行临时调拨"
            error.isConflict -> "临时调拨状态已变化，请刷新后重试"
            else -> error.safeMessage("临时调拨未完成，请重试")
        }
        else -> "网络不可用，临时调拨未完成，请重试"
    }

    private fun ApiException.contractCode(): String =
        if (code == "HTTP_ERROR") message.orEmpty() else code

    // ==================== 审批与审计 ====================

    /** 刷新审批列表；筛选条件只作为服务端 query，不在客户端二次筛选。 */
    fun refreshTransferRequests(status: String? = _state.value.transferRequestFilter) {
        if (!_state.value.loggedIn || !_state.value.role.canApprove) return
        val filter = status?.trim()?.takeIf { it.isNotEmpty() }
        val generation = ++transferRequestLoadGeneration
        val currentSession = sessionGeneration
        operationScope.launch {
            if (!isSessionActive(currentSession)) return@launch
            _state.update {
                it.copy(
                    transferRequestState = WorkspaceLoadState.LOADING,
                    transferRequestError = null,
                    transferRequestFilter = filter,
                    transferRequests = emptyList(),
                )
            }
            repo.transferRequests(filter)
                .onSuccess { page ->
                    if (generation != transferRequestLoadGeneration || !isSessionActive(currentSession)) {
                        return@onSuccess
                    }
                    _state.update {
                        it.copy(
                            transferRequests = page.items,
                            transferRequestState = if (page.items.isEmpty()) {
                                WorkspaceLoadState.EMPTY
                            } else {
                                WorkspaceLoadState.CONTENT
                            },
                            transferRequestError = null,
                            transferRequestServerTime = page.serverTime,
                        )
                    }
                }
                .onFailure { error ->
                    if (generation != transferRequestLoadGeneration || !isSessionActive(currentSession)) {
                        return@onFailure
                    }
                    _state.update {
                        it.copy(
                            transferRequestState = WorkspaceLoadState.ERROR,
                            transferRequestError = transferRequestErrorMessage(error),
                            transferRequests = emptyList(),
                        )
                    }
                }
        }
    }

    fun selectTransferRequest(requestId: String) {
        if (!_state.value.loggedIn || !_state.value.role.canApprove || requestId.isBlank()) return
        val generation = ++transferRequestDetailGeneration
        val currentSession = sessionGeneration
        operationScope.launch {
            if (!isSessionActive(currentSession)) return@launch
            _state.update {
                it.copy(
                    selectedTransferRequestId = requestId,
                    transferRequestDetail = null,
                    transferRequestDetailState = WorkspaceLoadState.LOADING,
                    transferRequestDetailError = null,
                )
            }
            repo.transferRequestDetail(requestId)
                .onSuccess { detail ->
                    if (generation != transferRequestDetailGeneration || !isSessionActive(currentSession)) {
                        return@onSuccess
                    }
                    _state.update {
                        it.copy(
                            transferRequestDetail = detail,
                            transferRequestDetailState = WorkspaceLoadState.CONTENT,
                            transferRequestDetailError = null,
                        )
                    }
                }
                .onFailure { error ->
                    if (generation != transferRequestDetailGeneration || !isSessionActive(currentSession)) {
                        return@onFailure
                    }
                    _state.update {
                        it.copy(
                            transferRequestDetailState = WorkspaceLoadState.ERROR,
                            transferRequestDetailError = transferRequestErrorMessage(error),
                        )
                    }
                }
        }
    }

    fun closeTransferRequestDetail() {
        transferRequestDetailGeneration++
        _state.update {
            it.copy(
                selectedTransferRequestId = null,
                transferRequestDetail = null,
                transferRequestDetailState = WorkspaceLoadState.IDLE,
                transferRequestDetailError = null,
            )
        }
    }

    fun retryTransferRequestDetail() {
        _state.value.selectedTransferRequestId?.let(::selectTransferRequest)
    }

    /** 按列表中服务端最新状态再次校验，避免通过旧详情或绕过按钮越权提交。 */
    fun approveTransferRequest(transferRequestId: String, approve: Boolean, comment: String = "") {
        val current = _state.value
        if (current.preview) {
            _state.update { it.copy(error = "测试预览只读，不能审批或执行") }
            return
        }
        val request = current.transferRequests.firstOrNull { it.id == transferRequestId }
            ?: current.transferRequestDetail?.takeIf { it.id == transferRequestId }
        val action = if (approve) TransferRequestAction.APPROVE else TransferRequestAction.REJECT
        if (request == null || action !in TransferRequestActionPolicy.actionsFor(current.role, request)
        ) {
            _state.update { it.copy(error = "当前申请状态不允许该审批操作，请先刷新列表") }
            return
        }
        if (!approve && comment.trim().isBlank()) {
            _state.update { it.copy(error = "驳回申请必须填写原因") }
            return
        }
        submitTransferDecision(
            transferRequestId = transferRequestId,
            actionKey = "${action.name}:${comment.trim()}",
        ) { operationId, requestId ->
            repo.approveTransferRequest(
                transferRequestId, approve, comment.trim(), operationId, requestId
            )
        }
    }

    fun executeTransferRequest(transferRequestId: String) {
        val current = _state.value
        if (current.preview) {
            _state.update { it.copy(error = "测试预览只读，不能审批或执行") }
            return
        }
        val request = current.transferRequests.firstOrNull { it.id == transferRequestId }
            ?: current.transferRequestDetail?.takeIf { it.id == transferRequestId }
        if (request == null || TransferRequestAction.EXECUTE !in TransferRequestActionPolicy.actionsFor(
                current.role, request
            )
        ) {
            _state.update { it.copy(error = "当前申请状态不允许执行，请先刷新列表") }
            return
        }
        submitTransferDecision(transferRequestId, "EXECUTE") { operationId, requestId ->
            repo.executeTransferRequest(transferRequestId, operationId, requestId)
        }
    }

    private fun submitTransferDecision(
        transferRequestId: String,
        actionKey: String,
        call: suspend (String, String) -> Result<*>,
    ) {
        if (!_state.value.loggedIn || !_state.value.role.canApprove ||
            _state.value.transferDecisionSubmittingId != null
        ) return
        val operationKey = "$actionKey:$transferRequestId"
        val operationId = stableTransferOperationId(operationKey)
        val requestId = stableTransferRequestId(operationKey)
        val currentSession = sessionGeneration
        // 先同步占用提交槽，再启动协程；连续点击发生在同一帧时也只能产生一次请求。
        _state.update {
            it.copy(
                transferDecisionSubmittingId = transferRequestId,
                transferDecisionPendingOperationKey = operationKey,
                transferDecisionClientOperationId = operationId,
                transferDecisionRequestId = requestId,
                error = null,
                message = null,
            )
        }
        operationScope.launch {
            if (!isSessionActive(currentSession)) return@launch
            call(operationId, requestId).onSuccess {
                if (!isSessionActive(currentSession)) return@onSuccess
                _state.update {
                    it.copy(
                        transferDecisionSubmittingId = null,
                        transferDecisionPendingOperationKey = null,
                        transferDecisionClientOperationId = null,
                        transferDecisionRequestId = null,
                        message = "流转申请操作成功，列表已刷新",
                    )
                }
                refreshTransferRequests()
                refreshOrderAfterMutation()
                if (_state.value.selectedTransferRequestId == transferRequestId) {
                    selectTransferRequest(transferRequestId)
                }
            }.onFailure { error ->
                if (!isSessionActive(currentSession)) return@onFailure
                val canRetry = canRetryTransferDecision(error)
                _state.update {
                    it.copy(
                        transferDecisionSubmittingId = null,
                        transferDecisionPendingOperationKey = if (canRetry) operationKey else null,
                        transferDecisionClientOperationId = if (canRetry) operationId else null,
                        transferDecisionRequestId = if (canRetry) requestId else null,
                        error = transferRequestErrorMessage(error),
                    )
                }
                if (error is ApiException && error.isConflict) {
                    // 另一位审批人可能已改变状态；马上重新读取列表/详情，避免按钮继续基于旧事实。
                    refreshTransferRequests()
                    if (_state.value.selectedTransferRequestId == transferRequestId) {
                        selectTransferRequest(transferRequestId)
                    }
                }
            }
        }
    }

    private fun canRetryTransferDecision(error: Throwable): Boolean =
        error !is ApiException || error.retryable || error.isUnauthorized

    private fun transferRequestErrorMessage(error: Throwable): String = when (error) {
        is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权访问或操作流转申请"
            error.isConflict -> "申请状态已变化，请刷新列表后重试"
            error.statusCode == 404 -> "流转申请不存在，请刷新列表"
            error.retryable || error.statusCode >= 500 -> "服务端暂时不可用，请稍后重试"
            else -> "流转申请请求未完成，请稍后重试"
        }
        else -> "网络不可用，请检查连接后重试"
    }

    /** 管理员审计页只读取服务端分页；非管理员调用在 ViewModel 层也被拒绝。 */
    fun refreshAuditLogs(page: Int = 1) {
        if (!_state.value.loggedIn || !_state.value.role.canAdmin) return
        val requestedPage = page.coerceAtLeast(1)
        val generation = ++auditLoadGeneration
        val currentSession = sessionGeneration
        operationScope.launch {
            if (!isSessionActive(currentSession)) return@launch
            _state.update {
                it.copy(
                    auditState = WorkspaceLoadState.LOADING,
                    auditError = null,
                    auditPage = requestedPage,
                    auditLogs = emptyList(),
                )
            }
            repo.auditLogs(requestedPage, LogisticsUiState.AUDIT_PAGE_SIZE)
                .onSuccess { result ->
                    if (generation != auditLoadGeneration || !isSessionActive(currentSession)) {
                        return@onSuccess
                    }
                    _state.update {
                        it.copy(
                            auditLogs = result.items,
                            auditState = if (result.items.isEmpty()) {
                                WorkspaceLoadState.EMPTY
                            } else {
                                WorkspaceLoadState.CONTENT
                            },
                            auditError = null,
                            auditPage = result.page,
                            auditPageSize = result.pageSize,
                            auditHasNext = result.hasNext,
                            auditServerTime = result.serverTime,
                        )
                    }
                }
                .onFailure { error ->
                    if (generation != auditLoadGeneration || !isSessionActive(currentSession)) {
                        return@onFailure
                    }
                    _state.update {
                        it.copy(
                            auditState = WorkspaceLoadState.ERROR,
                            auditError = auditErrorMessage(error),
                            auditLogs = emptyList(),
                            auditHasNext = false,
                        )
                    }
                }
        }
    }

    fun loadNextAuditPage() {
        val current = _state.value
        if (current.auditHasNext && !current.auditLoading) refreshAuditLogs(current.auditPage + 1)
    }

    fun loadPreviousAuditPage() {
        val current = _state.value
        if (current.auditPage > 1 && !current.auditLoading) refreshAuditLogs(current.auditPage - 1)
    }

    fun retryAuditLogs() = refreshAuditLogs(_state.value.auditPage)

    private fun auditErrorMessage(error: Throwable): String = when (error) {
        is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权查看审计记录"
            error.statusCode == 404 -> "审计接口暂不可用"
            error.retryable || error.statusCode >= 500 -> "服务端暂时不可用，请稍后重试"
            else -> "审计记录加载失败，请稍后重试"
        }
        else -> "网络不可用，请检查连接后重试"
    }

    private fun isSessionActive(generation: Long): Boolean =
        generation == sessionGeneration && _state.value.loggedIn

    // ==================== 交接 ====================

    private fun stableTransferOperationId(operationKey: String): String {
        val current = _state.value
        return if (current.transferDecisionPendingOperationKey == operationKey) {
            current.transferDecisionClientOperationId ?: UUID.randomUUID().toString()
        } else UUID.randomUUID().toString()
    }

    private fun stableTransferRequestId(operationKey: String): String {
        val current = _state.value
        return if (current.transferDecisionPendingOperationKey == operationKey) {
            current.transferDecisionRequestId ?: UUID.randomUUID().toString()
        } else UUID.randomUUID().toString()
    }

    /** 打开当前工作项的一页交接时间线；客户端只保留这一条时间线。 */
    fun openHandoverTimeline(item: WorkspaceMaterialItem) {
        if (item.lastHandoverId.isNullOrBlank()) {
            _state.update { it.copy(error = "该工作项暂无交接记录") }
            return
        }
        val current = _state.value
        if (current.workspaceTimelineItemId == item.id &&
            current.workspaceTimelineState == WorkspaceLoadState.CONTENT
        ) {
            _state.update {
                it.copy(
                    workspaceTimelineItemId = null,
                    workspaceTimeline = null,
                    workspaceTimelineState = WorkspaceLoadState.IDLE,
                    workspaceTimelineError = null,
                )
            }
            return
        }
        loadHandoverTimeline(item.id)
    }

    fun retryHandoverTimeline() {
        _state.value.workspaceTimelineItemId?.let { loadHandoverTimeline(it) }
    }

    private fun loadHandoverTimeline(workItemId: String) {
        if (!_state.value.loggedIn) return
        operationScope.launch {
            _state.update {
                it.copy(
                    workspaceTimelineItemId = workItemId,
                    workspaceTimeline = null,
                    workspaceTimelineState = WorkspaceLoadState.LOADING,
                    workspaceTimelineError = null,
                )
            }
            repo.handoverTimeline(workItemId, page = 1, pageSize = 20)
                .onSuccess { timeline ->
                    _state.update {
                        it.copy(
                            workspaceTimeline = timeline,
                            workspaceTimelineState = if (timeline.items.isEmpty()) {
                                WorkspaceLoadState.EMPTY
                            } else {
                                WorkspaceLoadState.CONTENT
                            },
                            workspaceTimelineError = null,
                            workspaceServerTime = timeline.serverTime ?: it.workspaceServerTime,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            workspaceTimelineState = WorkspaceLoadState.ERROR,
                            workspaceTimelineError = handoverErrorMessage(error),
                        )
                    }
                }
        }
    }

    /** 物料员发起交接；网络失败不入队，用户可用同一页面重新提交。 */
    fun createHandover(
        item: WorkspaceMaterialItem,
        quantity: Int,
        fromLocation: String,
        remark: String? = null,
    ) {
        val current = _state.value
        if (current.preview) {
            _state.update { it.copy(error = "测试预览只读，不能发起交接") }
            return
        }
        if (!HandoverActionPolicy.canCreate(current.role, item)) {
            _state.update { it.copy(error = "当前角色或服务端状态不允许发起交接") }
            return
        }
        val transferRequestId = item.transferRequestId
        if (transferRequestId.isNullOrBlank()) {
            _state.update { it.copy(error = "服务端未返回有效出库单，不能发起交接") }
            return
        }
        if (quantity <= 0 || fromLocation.isBlank()) {
            _state.update { it.copy(error = "请填写正整数交接数量和出库库位") }
            return
        }
        if (current.handoverSubmittingId != null) return
        val operationKey = listOf(
            "CREATE",
            item.id,
            transferRequestId,
            quantity,
            fromLocation.trim(),
            remark.orEmpty().trim(),
        ).joinToString("|")
        val operationId = operationIdFor(operationKey)

        operationScope.launch {
            _state.update {
                it.copy(
                    handoverSubmittingId = item.id,
                    handoverPendingOperationKey = operationKey,
                    handoverClientOperationId = operationId,
                    handoverRequestId = null,
                    error = null,
                    message = null,
                )
            }
            repo.createHandover(
                clientOperationId = operationId,
                workItemId = item.id,
                transferRequestId = transferRequestId,
                quantity = quantity,
                fromLocation = fromLocation,
                deviceId = item.deviceId,
                receiverUserId = item.assignedUserId,
                remark = remark,
            ).onSuccess { result ->
                _state.update {
                    it.copy(
                        handoverSubmittingId = null,
                        handoverPendingOperationKey = null,
                        handoverClientOperationId = null,
                        handoverRequestId = null,
                        message = if (result.idempotent) "交接已提交（已使用原有幂等结果）" else "交接已发起",
                    )
                }
                refreshWorkspaceAfterHandover(item.id)
                refreshOrderAfterMutation()
            }.onFailure { error ->
                _state.update {
                    it.copy(
                        handoverSubmittingId = null,
                        handoverPendingOperationKey = if (canRetryHandover(error)) operationKey else null,
                        handoverClientOperationId = if (canRetryHandover(error)) operationId else null,
                        handoverRequestId = null,
                        error = handoverErrorMessage(error),
                    )
                }
            }
        }
    }

    /** 按服务端状态和角色策略再次校验，防止绕过 UI 按钮直接调用。 */
    fun decideHandover(item: WorkspaceMaterialItem, action: HandoverAction, reason: String? = null) {
        val current = _state.value
        if (current.preview) {
            _state.update { it.copy(error = "测试预览只读，不能确认、驳回或取消交接") }
            return
        }
        if (action !in HandoverActionPolicy.actionsFor(current.role, current.currentUserId, item)) {
            _state.update { it.copy(error = "当前角色或服务端状态不允许该交接操作") }
            return
        }
        if (action.requiresReason && reason.isNullOrBlank()) {
            _state.update { it.copy(error = "驳回或取消交接必须填写原因") }
            return
        }
        val handoverId = item.lastHandoverId
        if (handoverId.isNullOrBlank() || current.handoverSubmittingId != null) return
        val operationKey = listOf(
            action.name,
            handoverId,
            reason.orEmpty().trim(),
        ).joinToString("|")
        val operationId = operationIdFor(operationKey)
        val requestId = requestIdFor(operationKey)

        operationScope.launch {
            _state.update {
                it.copy(
                    handoverSubmittingId = item.id,
                    handoverPendingOperationKey = operationKey,
                    handoverClientOperationId = operationId,
                    handoverRequestId = requestId,
                    error = null,
                    message = null,
                )
            }
            repo.decideHandover(
                handoverId = handoverId,
                action = action,
                clientOperationId = operationId,
                reason = reason?.trim(),
                requestId = requestId,
            ).onSuccess { result ->
                _state.update {
                    it.copy(
                        handoverSubmittingId = null,
                        handoverPendingOperationKey = null,
                        handoverClientOperationId = null,
                        handoverRequestId = null,
                        message = if (result.idempotent) "交接操作已完成（已使用原有幂等结果）" else "交接${action.label}成功",
                    )
                }
                refreshWorkspaceAfterHandover(item.id)
                refreshOrderAfterMutation()
            }.onFailure { error ->
                _state.update {
                    it.copy(
                        handoverSubmittingId = null,
                        handoverPendingOperationKey = if (canRetryHandover(error)) operationKey else null,
                        handoverClientOperationId = if (canRetryHandover(error)) operationId else null,
                        handoverRequestId = if (canRetryHandover(error)) requestId else null,
                        error = handoverErrorMessage(error),
                    )
                }
            }
        }
    }

    private fun refreshWorkspaceAfterHandover(itemId: String) {
        loadWorkspacePage(resetToFirstPage = true)
        loadHandoverTimeline(itemId)
    }

    private fun operationIdFor(operationKey: String): String {
        val current = _state.value
        return if (current.handoverPendingOperationKey == operationKey) {
            current.handoverClientOperationId ?: UUID.randomUUID().toString()
        } else {
            UUID.randomUUID().toString()
        }
    }

    private fun requestIdFor(operationKey: String): String {
        val current = _state.value
        return if (current.handoverPendingOperationKey == operationKey) {
            current.handoverRequestId ?: UUID.randomUUID().toString()
        } else {
            UUID.randomUUID().toString()
        }
    }

    private fun canRetryHandover(error: Throwable): Boolean =
        error !is ApiException || error.retryable || error.isUnauthorized

    private fun handoverErrorMessage(error: Throwable): String = when (error) {
        is ApiException -> when {
            error.isUnauthorized -> "登录已失效，请重新登录"
            error.isForbidden -> "当前账号无权执行该交接操作"
            error.isConflict -> "交接状态已变化，请刷新工作台后重试"
            error.retryable || error.statusCode >= 500 -> "服务端暂时不可用，请稍后重试"
            else -> "交接操作未完成，请检查服务端状态"
        }
        else -> "网络不可用，交接操作未完成，请重试"
    }

    // ==================== 扫码 ====================

    /**
     * 处理扫码结果：先由服务端判定类型，再按类型路由。
     * 契约 3：不把解析规则硬编码到 UI；未命中时调用 /scan/resolve 由服务端最终判定。
     */
    fun onScanned(rawValue: String) {
        if (rawValue.isBlank()) return
        operationScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            repo.resolveScan(rawValue)
                .onSuccess { scan ->
                    _state.update { it.copy(lastScan = scan) }
                    if (!scan.type.isKnown) {
                        _state.update {
                            it.copy(loading = false, error = "无法识别该条码，请手动输入或重新扫码")
                        }
                        return@onSuccess
                    }
                    when (scan.type) {
                        ScanType.MATERIAL_CODE -> loadMaterial(scan.normalizedValue)
                        ScanType.PRODUCTION_ORDER ->
                            loadOrder(scan.normalizedValue)
                        ScanType.LOCATION_CODE -> {
                            _state.update {
                                it.copy(
                                    loading = false,
                                    formLocation = scan.normalizedValue,
                                    message = "已识别库位 ${scan.normalizedValue}"
                                )
                            }
                        }
                        ScanType.FLOW_NO -> {
                            _state.update {
                                it.copy(loading = false, message = "流转单 ${scan.normalizedValue} 暂未接入详情查询，请使用订单或物料码")
                            }
                        }
                        ScanType.MACHINE -> _state.update {
                            it.copy(loading = false, message = "已识别机台 ${scan.normalizedValue}，请从订单详情查看关联物料")
                        }
                        ScanType.UNKNOWN -> _state.update {
                            it.copy(loading = false, error = "无法识别该条码")
                        }
                    }
                }
                .onFailure { e ->
                    _state.update {
                        it.copy(
                            loading = false,
                            error = if (e is ApiException) {
                                e.safeMessage("扫码解析失败，请重试")
                            } else {
                                "网络不可用，请检查连接后重试"
                            },
                        )
                    }
                }
        }
    }

    private suspend fun loadMaterial(code: String) {
        repo.materialInventory(code)
            .onSuccess { inv ->
                _state.update {
                    it.copy(
                        materialInventory = inv,
                        formQuantity = inv.inventory.availableQuantity.coerceAtMost(1).coerceAtLeast(0),
                        formLocation = inv.primaryLocation,
                        screen = Screen.MATERIAL_DETAIL,
                        loading = false,
                        message = "料号查询成功"
                    )
                }
            }
            .onFailure { e ->
                _state.update {
                    it.copy(
                        loading = false,
                        error = if (e is ApiException) {
                            e.safeMessage("料号查询失败，请重试")
                        } else {
                            "网络不可用，请检查连接后重试"
                        },
                    )
                }
            }
    }

    private suspend fun loadOrder(orderNo: String) {
        repo.orderDetail(orderNo)
            .onSuccess { detail ->
                _state.update {
                    it.copy(
                        orderDetail = detail,
                        orderStatus = OrderMaterialStatus(detail.orderNo, "PRODUCTION_ORDER", detail.materials, null, detail.orderId, detail.productName, detail.orderStatus),
                        screen = Screen.ORDER_DETAIL,
                        loading = false,
                        message = "订单详情已更新"
                    )
                }
            }
            .onFailure { e ->
                _state.update {
                    it.copy(
                        loading = false,
                        error = if (e is ApiException) {
                            e.safeMessage("订单查询失败，请重试")
                        } else {
                            "网络不可用，请检查连接后重试"
                        },
                    )
                }
            }
    }

    /** Re-reads the same order after a successful mutation; failures never optimistically change order facts. */
    fun refreshOrderDetail() {
        val orderNo = _state.value.orderStatus?.documentNo ?: return
        operationScope.launch { loadOrder(orderNo) }
    }

    private fun refreshOrderAfterMutation() {
        if (_state.value.orderStatus != null) refreshOrderDetail()
    }

    /** 仅在服务端订单详情中已加载的机台物料上提报异常；不写入本地伪成功状态。 */
    fun submitException(item: WorkspaceMaterialItem, type: String, actualQuantity: Int, description: String?) {
        if (_state.value.preview) {
            _state.update { it.copy(error = "测试预览只读，不能提报异常") }
            return
        }
        val bookQuantity = item.requiredQuantity ?: 0
        operationScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            repo.createException(item.orderNo, item.deviceId.orEmpty(), item.materialId, type, bookQuantity, actualQuantity, description)
                .onSuccess { result ->
                    _state.update { it.copy(loading = false, message = "异常已提交，状态：${result.status}") }
                    refreshOrderAfterMutation()
                }
                .onFailure { e -> _state.update { it.copy(loading = false, error = if (e is ApiException) e.safeMessage("异常提报失败，请重试") else "网络不可用，异常未提交") } }
        }
    }

    // ==================== 表单 ====================

    fun setQuantity(value: Int) {
        val max = _state.value.materialInventory?.inventory?.availableQuantity ?: Int.MAX_VALUE
        _state.update { it.copy(formQuantity = value.coerceIn(0, max)) }
    }

    fun setLocation(code: String) = _state.update { it.copy(formLocation = code) }

    // ==================== 提交 ====================

    fun submitInbound() {
        if (_state.value.preview) {
            _state.update { it.copy(error = "测试预览只读，不能提交入库申请") }
            return
        }
        val inv = _state.value.materialInventory ?: return
        val qty = _state.value.formQuantity
        if (qty <= 0) {
            _state.update { it.copy(error = "流转数量必须大于 0") }
            return
        }
        operationScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            when (val r = repo.submitInbound(inv, qty, _state.value.formLocation, null)) {
                is SubmitResult.Success -> _state.update {
                    it.copy(loading = false, screen = Screen.QUEUE, message = "已提交，等待审批")
                }
                is SubmitResult.Queued -> _state.update {
                    it.copy(loading = false, screen = Screen.QUEUE, message = "网络不可用，已暂存本地队列")
                }
                is SubmitResult.Failure -> _state.update {
                    it.copy(loading = false, error = r.message)
                }
            }
        }
    }

    fun submitLocationBinding() {
        if (_state.value.preview) {
            _state.update { it.copy(error = "测试预览只读，不能绑定库位") }
            return
        }
        val inv = _state.value.materialInventory ?: return
        val loc = _state.value.formLocation
        if (loc.isNullOrBlank()) {
            _state.update { it.copy(error = "请先扫描库位码") }
            return
        }
        operationScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            when (val r = repo.submitLocationBinding(inv.material.code, loc, _state.value.formQuantity)) {
                is SubmitResult.Success -> _state.update {
                    it.copy(loading = false, screen = Screen.QUEUE, message = "库位绑定成功")
                }
                is SubmitResult.Queued -> _state.update {
                    it.copy(loading = false, screen = Screen.QUEUE, message = "网络不可用，已暂存本地队列")
                }
                is SubmitResult.Failure -> _state.update {
                    it.copy(loading = false, error = r.message)
                }
            }
        }
    }

    // ==================== 离线同步 ====================

    fun syncNow() {
        if (_state.value.preview) {
            _state.update { it.copy(error = "测试预览只读，不能同步离线写操作") }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(syncing = true) }
            val report: SyncReport = repo.syncPending()
            _state.update {
                it.copy(
                    syncing = false,
                    message = when {
                        report.success > 0 && report.hasIssue ->
                            "同步完成：成功 ${report.success}，失败 ${report.failed}，冲突 ${report.conflict}"
                        report.success > 0 -> "同步完成：成功 ${report.success} 条"
                        report.hasIssue -> "同步失败 ${report.failed} 条，冲突 ${report.conflict} 条"
                        else -> "没有待同步记录"
                    }
                )
            }
        }
    }

    fun clearSynced() {
        if (_state.value.preview) {
            _state.update { it.copy(error = "测试预览只读，不能清理离线写操作") }
            return
        }
        viewModelScope.launch {
            repo.clearSynced()
            _state.update { it.copy(message = "已清理同步完成的记录") }
        }
    }

    companion object {
        val WORKSPACE_PAGE_SIZES: Set<Int> = setOf(20, 50)

        fun canLoadNextWorkspacePage(page: Int, totalPages: Int): Boolean =
            page >= 1 && page < totalPages

        fun canLoadPreviousWorkspacePage(page: Int): Boolean = page > 1

        /** 按角色生成底部导航 —— 无权限入口不渲染 */
        fun tabsFor(role: UserRole): List<NavTab> = when (role) {
            UserRole.OPERATOR -> listOf(NavTab.WORKSPACE, NavTab.SCAN, NavTab.ORDER, NavTab.QUEUE, NavTab.PROFILE)
            UserRole.MATERIAL -> listOf(NavTab.WORKSPACE, NavTab.SCAN, NavTab.ORDER, NavTab.INVENTORY, NavTab.QUEUE, NavTab.PROFILE)
            UserRole.WAREHOUSE_ADMIN -> listOf(NavTab.WORKSPACE, NavTab.SCAN, NavTab.ORDER, NavTab.INVENTORY, NavTab.APPROVAL, NavTab.QUEUE)
            UserRole.ADMIN -> listOf(NavTab.WORKSPACE, NavTab.SCAN, NavTab.ORDER, NavTab.INVENTORY, NavTab.APPROVAL, NavTab.PROFILE)
            UserRole.WORKSHOP_SUPERVISOR, UserRole.ASSEMBLER -> listOf(NavTab.WORKSPACE, NavTab.PROFILE)
        }

        /** 角色的默认落地页 */
        fun defaultScreenFor(role: UserRole): Screen = Screen.WORKSPACE

        /** UI 路由收敛；服务端仍是最终鉴权来源。 */
        fun canNavigate(role: UserRole, screen: Screen): Boolean = when (screen) {
            Screen.APPROVAL -> role.canApprove
            Screen.AUDIT -> role.canAdmin
            Screen.LOGIN -> false
            else -> true
        }
    }
}

/** 与 ViewModel 生命周期一致：取消页面请求时不能把 CancellationException 当业务错误。 */
private suspend fun <T> resultOf(block: suspend () -> T): Result<T> = try {
    Result.success(block())
} catch (cancelled: CancellationException) {
    throw cancelled
} catch (error: Throwable) {
    Result.failure(error)
}

private fun com.company.logistics.data.SessionStore.UserSummary.toUser(): User = User(
    id = id,
    username = username,
    displayName = displayName,
    role = role
)
