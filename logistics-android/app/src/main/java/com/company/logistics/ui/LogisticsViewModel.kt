package com.company.logistics.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.SubmitResult
import com.company.logistics.data.SyncReport
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.RoleWorkspaceSummary
import com.company.logistics.model.RoleWorkspaceSummaryFactory
import com.company.logistics.model.ScanResult
import com.company.logistics.model.ScanType
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.model.ServerWorkspaceSummaryFactory
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.async
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

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
    val workspaceSummary: RoleWorkspaceSummary = RoleWorkspaceSummary.empty(UserRole.OPERATOR),
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
    val role: UserRole
        get() = (authState as? AuthState.Authenticated)?.user?.role ?: UserRole.OPERATOR
    val mustChangePassword: Boolean
        get() = (authState as? AuthState.Authenticated)?.mustChangePassword ?: false

    val workspaceLoading: Boolean
        get() = workspaceSummaryState == WorkspaceLoadState.LOADING ||
            workspaceItemsState == WorkspaceLoadState.LOADING

    companion object {
        const val WORKSPACE_PAGE_SIZE = 20
    }
}

/**
 * 主 ViewModel —— 承载会话、路由、扫码流程与离线队列。
 */
class LogisticsViewModel(
    private val repo: LogisticsRepository,
    private val injectedScope: CoroutineScope? = null
) : ViewModel() {

    private val operationScope: CoroutineScope
        get() = injectedScope ?: viewModelScope

    private val _state = MutableStateFlow(LogisticsUiState())
    val state: StateFlow<LogisticsUiState> = _state.asStateFlow()

    init {
        repo.onSessionExpired = {
            _state.value = LogisticsUiState(
                authState = AuthState.Unauthenticated,
                error = "登录已失效，请重新登录"
            )
        }
        // restoreSession 会读取加密摘要并把 token 对恢复到 API。放进协程后，UI 能明确经历
        // Restoring，而不是在 ViewModel 构造的同一帧里错误地落到登录页。
        operationScope.launch {
            repo.restoreSession()
            val persisted = repo.persistedUser()
            _state.update {
                if (persisted == null) {
                    it.copy(authState = AuthState.Unauthenticated)
                } else {
                    val user = persisted.toUser()
                    it.copy(
                        authState = AuthState.Authenticated(user, persisted.mustChangePassword),
                        navTabs = tabsFor(user.role),
                        screen = defaultScreenFor(user.role),
                        workspaceSummary = RoleWorkspaceSummaryFactory.from(user.role, null),
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
        operationScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            runCatching { repo.login(username, password, deviceId, remember) }
                .onSuccess { result ->
                    _state.update {
                        it.copy(
                            authState = AuthState.Authenticated(result.user, result.mustChangePassword),
                            navTabs = tabsFor(result.user.role),
                            screen = defaultScreenFor(result.user.role),
                            workspaceSummary = RoleWorkspaceSummaryFactory.from(result.user.role, null),
                            loading = false,
                            message = "登录成功"
                        )
                    }
                    loadWorkspacePage(resetToFirstPage = true)
                }
                .onFailure { e ->
                    _state.update { it.copy(loading = false, error = e.message ?: "登录失败") }
                }
        }
    }

    fun logout() {
        repo.logout()
        _state.value = LogisticsUiState(authState = AuthState.Unauthenticated)
    }

    // ==================== 路由 ====================

    fun navigate(screen: Screen) = _state.update {
        if (!canNavigate(it.role, screen)) {
            it.copy(error = "当前角色无权访问该页面")
        } else {
            it.copy(screen = screen, error = null)
        }
    }

    fun consumeMessage() = _state.update { it.copy(message = null, error = null) }

    // ==================== 工作台 ====================

    /** 刷新服务端摘要与第一页工作项；服务端分页是唯一数据来源。 */
    fun refreshWorkspace() {
        loadWorkspacePage(resetToFirstPage = true)
    }

    /** 切换服务端分页大小；仅支持内存约束规定的 20/50。 */
    fun setWorkspacePageSize(pageSize: Int) {
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
        if (canLoadNextWorkspacePage(current.workspacePage, current.workspaceTotalPages) &&
            !current.workspaceLoading
        ) {
            loadWorkspacePage(
                resetToFirstPage = false,
                page = current.workspacePage + 1,
                pageSize = current.workspacePageSize,
            )
        }
    }

    fun loadPreviousWorkspacePage() {
        val current = _state.value
        if (canLoadPreviousWorkspacePage(current.workspacePage) && !current.workspaceLoading) {
            loadWorkspacePage(
                resetToFirstPage = false,
                page = current.workspacePage - 1,
                pageSize = current.workspacePageSize,
            )
        }
    }

    private fun loadWorkspacePage(
        resetToFirstPage: Boolean,
        page: Int = 1,
        pageSize: Int = _state.value.workspacePageSize,
    ) {
        if (!_state.value.loggedIn) return
        operationScope.launch {
            val requestedPage = if (resetToFirstPage) 1 else page
            _state.update {
                it.copy(
                    workspaceSummary = if (resetToFirstPage) RoleWorkspaceSummary.empty(it.role) else it.workspaceSummary,
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
                val summary = if (resetToFirstPage) async { repo.workspaceSummary() } else null
                val items = async {
                    repo.workspaceMaterialItems(
                        page = requestedPage,
                        pageSize = pageSize
                    )
                }

                val summaryResult = summary?.await()
                val itemsResult = items.await()

                if (summaryResult != null) {
                    summaryResult
                    .onSuccess { serverSummary ->
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

    private fun workspaceErrorMessage(error: Throwable): String = when {
        isWorkspaceUnavailable(error) -> "服务端未提供工作台接口，当前工作台不可用"
        error.message.isNullOrBlank() -> "工作台加载失败，请稍后重试"
        else -> error.message.orEmpty()
    }

    private fun isWorkspaceUnavailable(error: Throwable): Boolean {
        val apiError = error as? com.company.logistics.data.remote.ApiException
        return apiError?.statusCode == 404 || apiError?.statusCode == 405 ||
            apiError?.code in setOf("NOT_FOUND", "WORKSPACE_NOT_SUPPORTED")
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
                                it.copy(loading = false, message = "流转单 ${scan.normalizedValue}（详情页待接入）")
                            }
                        }
                        ScanType.UNKNOWN -> _state.update {
                            it.copy(loading = false, error = "无法识别该条码")
                        }
                    }
                }
                .onFailure { e ->
                    _state.update { it.copy(loading = false, error = e.message ?: "扫码解析失败") }
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
                _state.update { it.copy(loading = false, error = e.message ?: "料号查询失败") }
            }
    }

    private suspend fun loadOrder(orderNo: String) {
        repo.orderMaterialStatus(orderNo)
            .onSuccess { status ->
                _state.update {
                    it.copy(
                        orderStatus = status,
                        screen = Screen.ORDER_DETAIL,
                        loading = false,
                        message = "订单状态已更新"
                    )
                }
            }
            .onFailure { e ->
                _state.update { it.copy(loading = false, error = e.message ?: "订单查询失败") }
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

private fun com.company.logistics.data.SessionStore.UserSummary.toUser(): User = User(
    id = id,
    username = username,
    displayName = displayName,
    role = role
)
