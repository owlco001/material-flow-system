package com.company.logistics.data

import android.content.Context
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.data.remote.TransferItem
import com.company.logistics.data.remote.safeMessage
import com.company.logistics.model.LoginResult
import com.company.logistics.model.DeviceDetail
import com.company.logistics.model.AuditLogPage
import com.company.logistics.model.AssemblyTaskPage
import com.company.logistics.model.AssemblyAssignmentResponse
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.HandoverActionResult
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.MachineProgressPage
import com.company.logistics.model.OrderProgressPage
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.OfflineOpType
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.OrderDetail
import com.company.logistics.model.ScanResult
import com.company.logistics.model.SyncStatus
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
import com.company.logistics.model.WorkspaceViewRole
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.ExceptionSubmissionResult
import com.company.logistics.model.RoleWorkspaceRepository
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestPage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong
import org.json.JSONArray
import org.json.JSONObject

/**
 * 数据仓库 —— 串联网络层与本地离线队列。
 *
 * 离线优先策略：
 *  - 网络可用：直接提交，成功后不落本地队列；
 *  - 网络失败（连接异常 / 5xx 可重试）：写入本地队列，标记 PENDING，联网后重放；
 *  - 业务失败（4xx）：不重放（重放也不会成功），直接返回错误给用户。
 *
 * 幂等保证：离线记录保存 clientOperationId，重放时原样回传，
 *          由服务端幂等键去重，避免重复建单（契约第 5 节）。
 */
open class LogisticsRepository(
    private val api: MaterialFlowApi,
    private val dao: OfflineOperationDao,
    private val sessionStore: SessionStore? = null,
    /**
     * 可选的应用 Context：仅用于离线入队后调度 WorkManager 自动同步。
     * 为 null 时不调度（单测保持 LogisticsRepository(api, dao) 可编译、无 Android 依赖）。
     */
    private val appContext: Context? = null
) : RoleWorkspaceRepository {
    var onSessionExpired: (() -> Unit)? = null

    // Only a remembered login may write rotated tokens to disk.
    @Volatile
    private var persistSession = false

    @Volatile
    private var authenticatedRole: com.company.logistics.model.UserRole? = null

    /** Prevents overlapping UI/worker drains from replaying the same due batch concurrently. */
    private val syncPendingMutex = Mutex()

    /** Explicit session transitions only; access-token rotation during refresh is not a new identity. */
    private val sessionGeneration = AtomicLong(0)
    private val sessionStateLock = Any()
    private var ownedApiEpoch: Long? = api.currentIdentityEpoch()

    /**
     * 契约 API 句柄。
     *
     * 暴露给摄像头扫码链路（[com.company.logistics.data.CameraXScannerRepository]）复用同一份
     * 会话与 baseUrl 配置 —— 避免出现两个 ApiConfig 实例导致 token / 环境不一致。
     */
    val apiHandle: MaterialFlowApi get() = api

    init {
        // 网络层每次轮转令牌都同步落盘 / 擦除磁盘副本。
        // 不接这个回调的话，刷新后的新令牌只留在内存：
        // 下次冷启动会拿已被消费的旧令牌去刷新，被服务端判为重放并吊销整族会话。
        api.onTokensChangedForIdentity = { sourceEpoch, resultEpoch, access, refresh ->
            synchronized(sessionStateLock) {
                if (ownedApiEpoch == sourceEpoch && api.isCurrentIdentityEpoch(resultEpoch)) {
                    if (access == null && refresh == null) {
                        ownedApiEpoch = resultEpoch
                        sessionGeneration.incrementAndGet()
                        persistSession = false
                        authenticatedRole = null
                        sessionStore?.clear()
                        // The app callback is synchronous and logout is reentrant on this monitor.
                        // Keep delivery atomic with explicit login/logout, not just the prefs write.
                        onSessionExpired?.invoke()
                    } else if (api.tokensForIdentity(resultEpoch) == (access to refresh)) {
                        ownedApiEpoch = resultEpoch
                        if (persistSession) sessionStore?.updateTokens(access, refresh)
                    }
                }
            }
        }
    }

    /** 会话是否已启用加密存储（false 表示走了降级路径，UI 可提示风险） */
    val sessionEncrypted: Boolean get() = sessionStore?.encrypted ?: false

    // ==================== 会话 ====================

    open suspend fun login(username: String, password: String, deviceId: String, remember: Boolean = true): LoginResult {
        val (ticket, identity) = synchronized(sessionStateLock) {
            val generation = sessionGeneration.incrementAndGet()
            ownedApiEpoch = null
            persistSession = false
            authenticatedRole = null
            sessionStore?.clear()
            generation to api.beginLoginIdentity()
        }
        val result = api.loginForIdentity(identity, username, password, deviceId, CLIENT_VERSION)
        synchronized(sessionStateLock) {
            if (sessionGeneration.get() != ticket || !api.isCurrentIdentityEpoch(identity)) {
                throw CancellationException("Login belongs to an older session")
            }
            authenticatedRole = result.user.role
            ownedApiEpoch = identity
            val canPersistSession = remember && sessionStore?.encrypted == true
            persistSession = canPersistSession
            if (canPersistSession) {
                sessionStore?.save(result.accessToken, result.refreshToken, deviceId,
                    SessionStore.UserSummary(result.user.id, result.user.username, result.user.displayName,
                        result.user.role, result.mustChangePassword))
            } else {
                sessionStore?.clear()
            }
        }
        return result
    }

    fun logout(): Unit = synchronized(sessionStateLock) {
        sessionGeneration.incrementAndGet()
        ownedApiEpoch = null
        persistSession = false
        authenticatedRole = null
        api.clearIdentityForRepository()
        sessionStore?.clear()
    }

    open suspend fun changePassword(oldPassword: String, newPassword: String): Result<Unit> = resultOf {
        api.changePassword(oldPassword, newPassword)
        Unit
    }

    open suspend fun setupStatus(): Result<com.company.logistics.data.remote.SetupStatusDto> = resultOf { api.setupStatus() }

    open suspend fun initializeAdmin(password: String, confirmPassword: String): Result<com.company.logistics.data.remote.InitializeAdminResponseDto> = resultOf {
        api.initializeAdmin(password, confirmPassword, UUID.randomUUID().toString())
    }

    open suspend fun listUsers(): Result<List<com.company.logistics.model.ManagedUser>> = resultOf { api.listUsers() }

    open suspend fun addUser(employeeNo: String, displayName: String, role: String, password: String, managerId: String?): Result<Unit> = resultOf {
        api.addUser(employeeNo, displayName, role, password, managerId)
        Unit
    }

    open suspend fun deleteUser(userId: String, clientOperationId: String): Result<com.company.logistics.data.remote.DeleteUserResult> = resultOf {
        api.deleteUser(userId, clientOperationId)
    }

    /** 登出并通知服务端吊销该设备令牌（网络失败也保证本地已清） */
    suspend fun logoutRemote() {
        val (ticket, identity) = synchronized(sessionStateLock) {
            val generation = sessionGeneration.incrementAndGet()
            ownedApiEpoch = null
            persistSession = false
            authenticatedRole = null
            sessionStore?.clear()
            generation to api.currentIdentityEpoch()
        }
        try {
            api.logout(identity)
        } finally {
            synchronized(sessionStateLock) {
                if (sessionGeneration.get() == ticket) {
                    api.clearIdentityForRepository()
                    sessionStore?.clear()
                }
            }
        }
    }

    /**
     * 冷启动时恢复会话。
     *
     * 只有完整摘要和 token 对才恢复 UI 登录态；access token 可能已过期，
     * 首个业务请求会触发 401 → 自动刷新，无需在此处预判时效。
     */
    fun restoreSession(): Boolean = synchronized(sessionStateLock) {
        sessionGeneration.incrementAndGet()
        ownedApiEpoch = null
        persistSession = false
        authenticatedRole = null
        val store = sessionStore ?: return@synchronized false
        if (!store.encrypted) {
            store.clear()
            api.clearIdentityForRepository()
            return@synchronized false
        }
        val access = store.accessToken()
        val refresh = store.refreshToken()
        val summary = store.userSummary()
        if (access == null || refresh == null || summary == null) {
            store.clear()
            api.clearIdentityForRepository()
            return@synchronized false
        }
        ownedApiEpoch = api.restoreIdentityForRepository(access, refresh, store.deviceId() ?: "unknown")
        persistSession = true
        authenticatedRole = summary.role
        true
    }

    fun persistedUser(): SessionStore.UserSummary? = synchronized(sessionStateLock) {
        val epoch = ownedApiEpoch
        if (persistSession && epoch != null && api.isCurrentIdentityEpoch(epoch)
            && api.accessToken != null && api.refreshToken != null) sessionStore?.userSummary() else null
    }

    /** 刷新成功后由网络层回调落盘，此处无需再手动调用 */
    @Deprecated("由 api.onTokensRotated 回调自动完成", ReplaceWith(""))
    fun persistTokens(): Unit = synchronized(sessionStateLock) {
        val tokens = ownedApiEpoch?.let(api::tokensForIdentity)
        if (persistSession && tokens != null) sessionStore?.updateTokens(tokens.first, tokens.second)
    }

    val isLoggedIn: Boolean get() = api.accessToken != null || api.refreshToken != null

    // ==================== 扫码 ====================

    suspend fun resolveScan(rawValue: String): Result<ScanResult> = resultOf {
        api.resolveScan(rawValue)
    }

    open suspend fun materialInventory(code: String): Result<MaterialInventory> = resultOf {
        api.materialInventory(code)
    }

    open suspend fun orderMaterialStatus(
        documentNo: String,
        page: Int = 1,
        pageSize: Int = 0,
    ): Result<OrderMaterialStatus> = resultOf {
        api.orderMaterialStatus("PRODUCTION_ORDER", documentNo, page, pageSize)
    }

    /** Fetch only this machine's rows, draining bounded pages without changing order-page APIs. */
    open suspend fun deviceOrderMaterials(documentNo: String, deviceId: String, deviceNo: String): Result<List<com.company.logistics.model.OrderMaterialItem>> = resultOf {
        require(documentNo.isNotBlank() && deviceNo.isNotBlank()) { "订单号和机台编号必填" }
        val identity = api.currentIdentityEpoch()
        val generation = sessionGeneration.get()
        val items = linkedMapOf<String, com.company.logistics.model.OrderMaterialItem>()
        var requestedPage = 1
        while (true) {
            currentCoroutineContext().ensureActive()
            if (generation != sessionGeneration.get() || !api.isCurrentIdentityEpoch(identity)) {
                throw CancellationException("Machine materials belong to an older session")
            }
            val response = api.orderMaterialStatus("PRODUCTION_ORDER", documentNo, requestedPage, 200, deviceNo)
            if (generation != sessionGeneration.get() || !api.isCurrentIdentityEpoch(identity)) {
                throw CancellationException("Machine materials belong to an older session")
            }
            check(response.documentNo == documentNo && response.page == requestedPage) { "机台物料分页响应不一致" }
            response.items.filter { it.deviceId == deviceId || it.deviceNo == deviceNo }.forEach {
                items[(it.deviceId ?: "") + "|" + it.materialId] = it
            }
            if (!response.hasMore) break
            check(response.items.isNotEmpty()) { "机台物料分页未推进" }
            requestedPage++
        }
        items.values.toList()
    }

    open suspend fun orderDetail(
        orderNo: String,
        page: Int = 1,
        pageSize: Int = 20,
        materialsPage: Int = 1,
        materialsPageSize: Int = 0,
    ): Result<OrderDetail> = resultOf {
        api.orderDetail(orderNo, page, pageSize, materialsPage, materialsPageSize)
    }

    open suspend fun deviceDetail(deviceId: String): Result<DeviceDetail> = resultOf {
        api.getDeviceDetail(deviceId)
    }

    open suspend fun createException(
        clientOperationId: String,
        orderNo: String, deviceId: String, materialId: String, type: String,
        bookQuantity: Int, actualQuantity: Int, description: String? = null,
    ): Result<ExceptionSubmissionResult> = resultOf {
        api.createException(clientOperationId, orderNo, deviceId, materialId, type, bookQuantity, actualQuantity, description)
    }

    /**
     * 异常提报（离线优先）：先尝试远程，网络不可用时入本地队列待同步。
     * 队列实体字段不足以装下异常全部字段，orderNo/deviceId/type/bookQuantity/description
     * 打包为 JSON 存入 remark，quantity 存 actualQuantity，重放时解析还原。
     */
    open suspend fun submitExceptionQueued(
        materialCode: String,
        materialId: String,
        orderNo: String,
        deviceId: String,
        type: String,
        bookQuantity: Int,
        actualQuantity: Int,
        description: String,
        clientOperationId: String? = null,
        evidenceIds: List<String> = emptyList()
    ): SubmitResult {
        val payload = JSONObject().apply {
            put("orderNo", orderNo)
            put("deviceId", deviceId)
            put("type", type)
            put("bookQuantity", bookQuantity)
            put("description", description)
            put("evidenceIds", JSONArray(evidenceIds))
        }.toString()
        return submit(
            opType = OfflineOpType.EXCEPTION,
            materialCode = materialCode,
            materialId = materialId,
            quantity = actualQuantity,
            targetLocation = null,
            expectedInventoryVersion = null,
            remark = payload,
            clientOperationId = clientOperationId,
            remoteCall = { clientOpId ->
                api.createException(clientOpId, orderNo, deviceId, materialId, type, bookQuantity,
                    actualQuantity, description, evidenceIds)
            }
        )
    }

    /** Workshop assembly APIs use server-side timestamps and stable idempotency keys. */

    /** 盘点列表（分页） */
    open suspend fun stocktakes(page: Int = 1, pageSize: Int = 50): Result<com.company.logistics.model.StocktakePage> =
        resultOf { api.listStocktakes(page, pageSize) }

    /** 确认盘点（仓管/管理员） */
    open suspend fun confirmStocktake(stocktakeId: String, clientOperationId: String? = null): Result<String> = resultOf {
        api.confirmStocktake(stocktakeId, clientOperationId ?: UUID.randomUUID().toString())
    }

    /**
     * 创建盘点单：在线直连，断网入离线队列（STOCKTAKE），联网后按 clientOperationId 幂等重放。
     * 实盘数量存队列实体的 quantity，目标库位存 targetLocation（库位可空=按物料整体盘）。
     */
    open suspend fun submitStocktakeQueued(
        materialCode: String,
        locationCode: String?,
        actualQuantity: Int,
        clientOperationId: String? = null,
    ): SubmitResult = submit(
        opType = OfflineOpType.STOCKTAKE,
        materialCode = materialCode,
        materialId = null,
        quantity = actualQuantity,
        targetLocation = locationCode?.takeIf { it.isNotBlank() },
        expectedInventoryVersion = null,
        remark = null,
        clientOperationId = clientOperationId,
        remoteCall = { clientOpId ->
            api.createStocktake(clientOpId, materialCode, locationCode?.takeIf { it.isNotBlank() }, actualQuantity)
        },
    )

    /** 上传图片凭证（库位/异常/流转通用），返回服务端 fileId。 */
    open suspend fun uploadEvidence(bytes: ByteArray, fileName: String, purpose: String = "TRANSFER"): Result<String> =
        resultOf { api.uploadFile(bytes, fileName, purpose).fileId }

    /** 异常记录列表 */
    open suspend fun getExceptions(): Result<List<com.company.logistics.model.ExceptionRecord>> = resultOf {
        api.listExceptions()
    }
    open suspend fun assemblyTasks(page: Int = 1): Result<List<com.company.logistics.model.AssemblyTask>> = resultOf { api.assemblyTasks(page) }
    open suspend fun assemblyTaskPage(page: Int = 1, pageSize: Int = 20, deviceNo: String? = null, deviceId: String? = null): Result<AssemblyTaskPage> = resultOf { api.assemblyTaskPage(page, pageSize, deviceNo, deviceId) }
    /** 最近生产订单列表（订单页空态展示，服务端 created_at 倒序） */
    open suspend fun recentOrders(limit: Int = 20): Result<MaterialFlowApi.RecentOrdersPage> = resultOf { api.recentProductionOrders(limit) }
    open suspend fun acceptAssemblyMaterial(taskId: String, clientOperationId: String): Result<com.company.logistics.model.AssemblyTask> = resultOf { api.acceptAssemblyMaterial(taskId, clientOperationId) }
    open suspend fun startAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): Result<LaborRecord> = resultOf { api.startAssemblyWork(taskId, expectedVersion, clientOperationId) }
    open suspend fun submitAssemblyProgress(taskId: String, stage: Int, expectedVersion: Int, clientOperationId: String): Result<com.company.logistics.model.AssemblyTask> = resultOf { api.submitAssemblyProgress(taskId, stage, expectedVersion, clientOperationId) }
    open suspend fun completeAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): Result<com.company.logistics.model.AssemblyTask> = resultOf { api.completeAssemblyWork(taskId, expectedVersion, clientOperationId) }
    open suspend fun startAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String) = resultOf { api.startAssemblyStage(taskId, stageNo, expectedVersion, clientOperationId) }
    open suspend fun completeAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String) = resultOf { api.completeAssemblyStage(taskId, stageNo, expectedVersion, clientOperationId) }
    open suspend fun reworkAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, reason: String, clientOperationId: String) = resultOf { api.reworkAssemblyStage(taskId, stageNo, expectedVersion, reason, clientOperationId) }
    open suspend fun assignAssemblyMembers(taskId: String, assemblerIds: List<String>, clientOperationId: String): Result<AssemblyAssignmentResponse> = resultOf { api.assignAssemblyMembers(taskId, assemblerIds, clientOperationId) }
    open suspend fun removeAssemblyMember(taskId: String, assemblerId: String, clientOperationId: String): Result<AssemblyAssignmentResponse> = resultOf { api.removeAssemblyMember(taskId, assemblerId, clientOperationId) }
    open suspend fun startTemporaryTransfer(taskId: String?, remark: String, clientOperationId: String): Result<LaborRecord> = resultOf { api.startTemporaryTransfer(taskId, remark, clientOperationId) }
    open suspend fun startTemporaryTransfer(taskId: String?, remark: String, clientOperationId: String, deviceId: String?): Result<LaborRecord> = resultOf { api.startTemporaryTransfer(taskId, deviceId, remark, clientOperationId) }
    open suspend fun completeTemporaryTransfer(transferId: String, remark: String, clientOperationId: String): Result<LaborRecord> = resultOf { api.completeTemporaryTransfer(transferId, remark, clientOperationId) }
    open suspend fun workshopProgressSummary(from: String? = null, to: String? = null): Result<WorkshopProgressSummary> = resultOf { api.workshopSummary(from, to) }
    open suspend fun assemblyTaskLaborSummary(taskId: String, assemblerId: String? = null): Result<LaborSummaryPage> = resultOf {
        api.assemblyTaskLaborSummary(taskId, assemblerId)
    }
    open suspend fun workshopLaborSummary(page: Int = 1, pageSize: Int = 20, deviceId: String? = null, orderNo: String? = null, assemblerId: String? = null): Result<LaborSummaryPage> = resultOf {
        api.workshopLaborSummary(page, pageSize, deviceId, orderNo, assemblerId)
    }
    open suspend fun workshopMachineProgress(page: Int = 1, pageSize: Int = 20): Result<MachineProgressPage> = resultOf { api.workshopMachineProgress(page, pageSize) }

    /**
     * 订单维度进度（`/workshop/order-progress`）。
     *
     * 主管看板的「订单进度」只用它取数；不再拿车间 overallProgressPercent 冒充订单进度。
     * 授权车间由服务端按当前账号收敛，客户端不传也不拼装越权参数。
     */
    open suspend fun workshopOrderProgress(page: Int = 1, pageSize: Int = 50): Result<OrderProgressPage> = resultOf {
        api.orderProgress(page, pageSize)
    }
    open suspend fun workshopMachineProgress(page: Int, pageSize: Int, deviceId: String?): Result<MachineProgressPage> = resultOf { api.workshopMachineProgress(page, pageSize, deviceId) }


    open suspend fun previewBomImport(fileName: String, fileBytes: ByteArray, modelCode: String): Result<MaterialFlowApi.BomImportPreview> = resultOf { api.previewBomImport(fileName, fileBytes, modelCode) }
    open suspend fun commitBomImport(previewId: String, clientOperationId: String, publish: Boolean): Result<MaterialFlowApi.BomVersionResult> = resultOf { api.commitBomImport(previewId, clientOperationId, publish) }
    open suspend fun createProductionOrder(clientOperationId: String, orderNo: String, productName: String, plannedQuantity: Int, plannedDeliveryDate: String, models: org.json.JSONArray): Result<MaterialFlowApi.ProductionOrderCreateResult> = resultOf { api.createProductionOrder(clientOperationId, orderNo, productName, plannedQuantity, plannedDeliveryDate, models) }
    open suspend fun createDevice(clientOperationId: String, deviceNo: String, deviceName: String, workshop: String, modelCapability: String?): Result<MaterialFlowApi.DeviceCreateResult> = resultOf { api.createDevice(clientOperationId, deviceNo, deviceName, workshop, modelCapability) }
    open suspend fun assignDevice(orderNo: String, modelCode: String, clientOperationId: String, deviceId: String, expectedVersion: Int): Result<MaterialFlowApi.DeviceAssignmentResult> = resultOf { api.assignDevice(orderNo, modelCode, clientOperationId, deviceId, expectedVersion) }

    open suspend fun workspaceSummary(): Result<WorkspaceSummary> = resultOf {
        api.workspaceSummary()
    }

    /** Reads a role projection without changing the authenticated session role. */
    override open suspend fun loadRoleSummary(
        viewRole: WorkspaceViewRole?,
        requestId: String,
    ): Result<WorkspaceSummary> {
        if (viewRole != null && authenticatedRole != com.company.logistics.model.UserRole.ADMIN) {
            return Result.failure(
                ApiException(403, "ROLE_PREVIEW_ADMIN_ONLY", "只有 ADMIN 可以预览测试角色工作台")
            )
        }
        return resultOf {
            api.workspaceSummary(
                viewRole = viewRole,
                requestId = requestId,
                clientOperationId = UUID.randomUUID().toString(),
            )
        }
    }

    /**
     * 读取服务端角色工作项的一个分页。
     * pageSize 由 API 层限制为 20/50，仓储层不缓存或拼接全量列表。
     */
    open suspend fun workspaceMaterialItems(
        status: String? = null,
        orderNo: String? = null,
        page: Int = 1,
        pageSize: Int = 20
    ): Result<WorkspaceMaterialItemsPage> = resultOf {
        api.workspaceMaterialItems(status, orderNo, page, pageSize)
    }

    /** Lists only the current page of a role projection; preview remains read-only. */
    override suspend fun listWorkItems(
        viewRole: WorkspaceViewRole?,
        status: String?,
        orderNo: String?,
        page: Int,
        pageSize: Int,
        requestId: String,
    ): Result<WorkspaceMaterialItemsPage> {
        if (viewRole != null && authenticatedRole != com.company.logistics.model.UserRole.ADMIN) {
            return Result.failure(
                ApiException(403, "ROLE_PREVIEW_ADMIN_ONLY", "只有 ADMIN 可以预览测试角色工作台")
            )
        }
        return resultOf {
            api.workspaceMaterialItems(
                status = status,
                orderNo = orderNo,
                page = page,
                pageSize = pageSize,
                viewRole = viewRole,
                requestId = requestId,
                clientOperationId = UUID.randomUUID().toString(),
            )
        }
    }

    /** 读取服务端流转申请列表；status 过滤交给服务端，避免客户端状态漂移。 */
    open suspend fun transferRequests(status: String? = null): Result<TransferRequestPage> = resultOf {
        api.listTransferRequests(status)
    }

    open suspend fun transferRequestsForOrders(orderNos: Set<String>): Result<TransferRequestPage> = resultOf {
        val (generation, identity) = synchronized(sessionStateLock) {
            sessionGeneration.get() to api.currentIdentityEpoch()
        }
        currentCoroutineContext().ensureActive()
        val response = api.listTransferRequestsForOrders(orderNos, identity)
        synchronized(sessionStateLock) {
            if (sessionGeneration.get() != generation || !api.isCurrentIdentityEpoch(identity)) {
                throw CancellationException("Transfer scope belongs to an older session")
            }
        }
        currentCoroutineContext().ensureActive()
        response
    }

    /** 读取单条流转申请详情，供审批页展开查看服务端明细。 */
    open suspend fun transferRequestDetail(requestId: String): Result<TransferRequest> = resultOf {
        api.transferRequestDetail(requestId)
    }

    /** 扫码交接：按流转单勾选物料并一次性确认。 */
    open suspend fun transferHandover(
        transferRequestId: String,
        items: List<Pair<String, Int>>,
        fromLocation: String?,
        receiverUserId: String?,
        remark: String?,
        clientOperationId: String,
    ): Result<String> = resultOf {
        api.transferHandover(transferRequestId, items, fromLocation, receiverUserId, remark, clientOperationId)
    }

    /** 流转单交接记录（留痕）。 */
    open suspend fun transferHandoverRecords(transferRequestId: String): Result<String> = resultOf {
        api.transferHandoverRecords(transferRequestId)
    }

    /** 管理员只读审计分页。 */
    open suspend fun auditLogs(page: Int = 1, pageSize: Int = 50): Result<AuditLogPage> = resultOf {
        api.auditLogs(page, pageSize)
    }

    /** 创建交接保持在线；幂等键由调用方生成并原样交给 API 层。 */
    open suspend fun createHandover(
        clientOperationId: String,
        workItemId: String,
        transferRequestId: String,
        quantity: Int,
        fromLocation: String,
        deviceId: String?,
        receiverUserId: String?,
        remark: String? = null,
    ): Result<HandoverActionResult> = resultOf {
        api.createHandover(
            clientOperationId = clientOperationId,
            workItemId = workItemId,
            transferRequestId = transferRequestId,
            quantity = quantity,
            fromLocation = fromLocation,
            deviceId = deviceId,
            receiverUserId = receiverUserId,
            remark = remark,
        )
    }

    /** 确认/驳回/取消默认不进入离线队列，网络失败由 UI 明确提示并允许重试。 */
    open suspend fun decideHandover(
        handoverId: String,
        action: HandoverAction,
        clientOperationId: String,
        reason: String? = null,
        requestId: String? = null,
    ): Result<HandoverActionResult> = resultOf {
        if (requestId == null) {
            api.decideHandover(handoverId, action, clientOperationId, reason)
        } else {
            api.decideHandover(handoverId, action, clientOperationId, reason, requestId)
        }
    }

    /** 审批与执行均复用调用方生成的幂等键，网络重试不得生成新业务操作。 */
    open suspend fun approveTransferRequest(
        transferRequestId: String,
        approve: Boolean,
        comment: String,
        clientOperationId: String,
        requestId: String,
    ): Result<com.company.logistics.data.remote.ApprovalDecisionResult> = resultOf {
        api.approveTransferRequest(
            requestId = transferRequestId,
            approve = approve,
            comment = comment,
            clientOperationId = clientOperationId,
            requestIdHeader = requestId,
        )
    }

    open suspend fun executeTransferRequest(
        transferRequestId: String,
        clientOperationId: String,
        requestId: String,
    ): Result<com.company.logistics.data.remote.ApprovalDecisionResult> = resultOf {
        api.executeTransferRequest(
            requestId = transferRequestId,
            clientOperationId = clientOperationId,
            requestIdHeader = requestId,
        )
    }

    /** 只返回当前工作项的一页时间线，仓储层不拼接历史页。 */
    open suspend fun handoverTimeline(
        workItemId: String,
        page: Int = 1,
        pageSize: Int = 20,
    ): Result<HandoverTimeline> = resultOf {
        api.handoverTimeline(workItemId, page, pageSize)
    }

    // ==================== 写操作（离线优先） ====================

    /**
     * 提交厂内入库申请。
     * 失败时按错误类型决定是否落本地队列。
     */
    suspend fun submitInbound(
        material: MaterialInventory,
        quantity: Int,
        targetLocation: String?,
        remark: String?,
        clientOperationId: String? = null
    ): SubmitResult = submit(
        opType = OfflineOpType.INBOUND,
        materialCode = material.material.code,
        materialId = material.material.id,
        quantity = quantity,
        targetLocation = targetLocation ?: material.primaryLocation,
        expectedInventoryVersion = material.version,
        remark = remark,
        clientOperationId = clientOperationId,
        remoteCall = { clientOpId ->
            api.createTransferRequest(
                clientOperationId = clientOpId,
                type = "INBOUND",
                documentNo = null,
                items = listOf(
                    TransferItem(
                        materialId = material.material.id,
                        quantity = quantity,
                        targetLocationCode = targetLocation ?: material.primaryLocation,
                        expectedInventoryVersion = material.version
                    )
                ),
                remark = remark
            )
        }
    )

    /**
     * 提交厂内出库（领料）申请。documentNo 写入关联订单号，便于机台详情按订单筛出。
     * 失败时按错误类型决定是否落本地队列。
     */
    open suspend fun submitOutbound(
        deviceMaterial: com.company.logistics.model.OrderMaterialItem,
        inventory: MaterialInventory,
        documentNo: String?,
        quantity: Int,
        remark: String?,
        clientOperationId: String? = null,
        evidenceIds: List<String> = emptyList()
    ): SubmitResult = submit(
        opType = OfflineOpType.OUTBOUND,
        materialCode = inventory.material.code,
        materialId = inventory.material.id,
        quantity = quantity,
        targetLocation = null,
        expectedInventoryVersion = inventory.version,
        // 队列实体没有 documentNo 字段：入队时打包进 remark JSON，重放时还原（见 [syncOne]）。
        // 不打包会让补传的单据丢失订单号，齐套与追溯统计漏计。
        remark = packQueuedTransferRemark(documentNo, null, remark, evidenceIds),
        clientOperationId = clientOperationId,
        remoteCall = { clientOpId ->
            api.createTransferRequest(
                clientOperationId = clientOpId,
                type = "OUTBOUND",
                documentNo = documentNo,
                items = listOf(
                    TransferItem(
                        materialId = inventory.material.id,
                        quantity = quantity,
                        expectedInventoryVersion = inventory.version
                    )
                ),
                remark = remark,
                evidenceIds = evidenceIds,
            )
        }
    )

    /**
     * 提交退料申请（RETURN）：按入库审批规则处理，审批执行后增加库存与目标库位数量。
     * 离线时入队；documentNo 等打包进 remark JSON，重放时还原（见 [syncOne]）。
     */
    open suspend fun submitReturn(
        deviceMaterial: com.company.logistics.model.OrderMaterialItem,
        inventory: MaterialInventory,
        documentNo: String?,
        quantity: Int,
        targetLocation: String?,
        remark: String?,
        clientOperationId: String? = null,
        evidenceIds: List<String> = emptyList()
    ): SubmitResult {
        val target = targetLocation?.takeIf { it.isNotBlank() } ?: inventory.primaryLocation
        return submit(
            opType = OfflineOpType.RETURN,
            materialCode = inventory.material.code,
            materialId = inventory.material.id,
            quantity = quantity,
            targetLocation = target,
            expectedInventoryVersion = inventory.version,
            remark = packQueuedTransferRemark(documentNo, null, remark, evidenceIds),
            clientOperationId = clientOperationId,
            remoteCall = { clientOpId ->
                api.createTransferRequest(
                    clientOperationId = clientOpId,
                    type = "RETURN",
                    documentNo = documentNo,
                    items = listOf(
                        TransferItem(
                            materialId = inventory.material.id,
                            quantity = quantity,
                            targetLocationCode = target,
                            expectedInventoryVersion = inventory.version
                        )
                    ),
                    remark = remark,
                    evidenceIds = evidenceIds,
                )
            }
        )
    }

    /**
     * 提交厂内调拨申请（后端流转类型 TRANSFER）：来源库位扣减、目标库位增加，汇总数量不变。
     * 来源/目标库位均必填且不同（后端同样校验）。离线时入队，来源库位打包进 remark JSON。
     */
    open suspend fun submitRelocation(
        deviceMaterial: com.company.logistics.model.OrderMaterialItem,
        inventory: MaterialInventory,
        documentNo: String?,
        quantity: Int,
        sourceLocation: String,
        targetLocation: String,
        remark: String?,
        clientOperationId: String? = null,
        evidenceIds: List<String> = emptyList()
    ): SubmitResult {
        if (sourceLocation.isBlank() || targetLocation.isBlank()) {
            return SubmitResult.Failure("厂内调拨必须同时填写来源库位与目标库位", retryable = false)
        }
        if (sourceLocation == targetLocation) {
            return SubmitResult.Failure("来源库位与目标库位不能相同", retryable = false)
        }
        return submit(
            opType = OfflineOpType.RELOCATION,
            materialCode = inventory.material.code,
            materialId = inventory.material.id,
            quantity = quantity,
            targetLocation = targetLocation,
            expectedInventoryVersion = inventory.version,
            remark = packQueuedTransferRemark(documentNo, sourceLocation, remark, evidenceIds),
            clientOperationId = clientOperationId,
            remoteCall = { clientOpId ->
                api.createTransferRequest(
                    clientOperationId = clientOpId,
                    type = "TRANSFER",
                    documentNo = documentNo,
                    items = listOf(
                        TransferItem(
                            materialId = inventory.material.id,
                            quantity = quantity,
                            sourceLocationCode = sourceLocation,
                            targetLocationCode = targetLocation,
                            expectedInventoryVersion = inventory.version
                        )
                    ),
                    remark = remark,
                    evidenceIds = evidenceIds,
                )
            }
        )
    }

    /**
     * 退料/调拨/出库入队时，队列实体缺 documentNo/sourceLocation 字段，与异常同样打包为 JSON 存 remark。
     *
     * 打包体带 `v` 版本标记：解包时以它区分"我方结构"与"用户恰好输入的合法 JSON"
     * （见 [unpackQueuedTransferRemark]）。字段语义（documentNo/sourceLocation/remark）不变。
     */
    private fun packQueuedTransferRemark(
        documentNo: String?,
        sourceLocation: String?,
        remark: String?,
        evidenceIds: List<String> = emptyList(),
    ): String =
        JSONObject().apply {
            put(QUEUED_REMARK_VERSION_KEY, QUEUED_REMARK_VERSION)
            put("documentNo", documentNo ?: JSONObject.NULL)
            put("sourceLocation", sourceLocation ?: JSONObject.NULL)
            put("remark", remark ?: JSONObject.NULL)
            // 图片凭证：离线也随队列保存，联网重放时原样带上，避免"离线提交的单据没有凭证"。
            put("evidenceIds", JSONArray(evidenceIds))
        }.toString()

    /**
     * 还原 [packQueuedTransferRemark] 写出的打包 JSON，返回三态（见 [QueuedRemarkParse]）。
     *
     * 判定顺序：
     *  1. 解析成功且 `v==1` → 新打包体；
     *  2. 解析成功但无版本标记、且 `documentNo/sourceLocation/remark` 三键齐全 → 升级前的旧打包体
     *     （[packQueuedTransferRemark] 恒写三键），仍按打包体还原，保证已入队数据不丢 documentNo；
     *  3. 解析失败但形如打包体（以 `{` 开头且含 `documentNo` 键）→ 数据被写坏，调用方必须判损坏，
     *     不得降级成"旧裸文本"静默重放；
     *  4. 其余（裸业务文本、或形如 `{"documentNo":"OM-9"}` 的用户备注）→ 非打包体，
     *     按"无 documentNo"兼容重放，`remark` 原样透传。
     */
    private fun unpackQueuedTransferRemark(raw: String?): QueuedRemarkParse {
        val text = raw?.trim()
        if (text.isNullOrEmpty()) return QueuedRemarkParse.NotPacked
        val packed = try {
            JSONObject(text)
        } catch (_: Exception) {
            return if (text.startsWith("{") && text.contains("\"documentNo\"")) {
                QueuedRemarkParse.Corrupted
            } else {
                QueuedRemarkParse.NotPacked
            }
        }
        if (packed.optInt(QUEUED_REMARK_VERSION_KEY, 0) == QUEUED_REMARK_VERSION) {
            return QueuedRemarkParse.Packed(packed.toQueuedTransferRemark())
        }
        val legacyPacked = packed.has("documentNo") && packed.has("sourceLocation") && packed.has("remark")
        return if (legacyPacked) QueuedRemarkParse.Packed(packed.toQueuedTransferRemark()) else QueuedRemarkParse.NotPacked
    }

    private fun JSONObject.toQueuedTransferRemark() = QueuedTransferRemark(
        documentNo = optStringOrNull("documentNo"),
        remark = optStringOrNull("remark"),
        evidenceIds = optJSONArray("evidenceIds").toStringList(),
    )

    private fun JSONArray?.toStringList(): List<String> {
        if (this == null) return emptyList()
        return (0 until length()).mapNotNull { index -> optString(index)?.takeIf { it.isNotBlank() } }
    }

    /** 提交库位绑定（简化流程，无需审批） */
    suspend fun submitLocationBinding(
        materialCode: String,
        locationCode: String,
        quantity: Int
    ): SubmitResult = submit(
        opType = OfflineOpType.LOCATION_BIND,
        materialCode = materialCode,
        materialId = null,
        quantity = quantity,
        targetLocation = locationCode,
        expectedInventoryVersion = null,
        remark = null,
        remoteCall = { _ ->
            api.bindLocation(materialCode, locationCode, quantity)
        }
    )

    /**
     * 通用提交逻辑：先尝试远程，失败按可重试性决定是否入本地队列。
     */
    private suspend fun submit(
        opType: OfflineOpType,
        materialCode: String,
        materialId: String?,
        quantity: Int,
        targetLocation: String?,
        expectedInventoryVersion: Int?,
        remark: String?,
        clientOperationId: String? = null,
        remoteCall: suspend (String) -> Any
    ): SubmitResult {
        val clientOpId = clientOperationId ?: UUID.randomUUID().toString()

        // 数量校验（契约：非负整数）
        if (quantity < 0) {
            return SubmitResult.Failure("数量必须是非负整数", retryable = false)
        }

        return try {
            val result = remoteCall(clientOpId)
            SubmitResult.Success(result, serverTime = null)
        } catch (e: ApiException) {
            when {
                // 业务失败：不入队，直接报错
                !e.retryable && e.statusCode in 400..499 ->
                    SubmitResult.Failure(
                        e.safeMessage("提交失败，请检查数据后重试"),
                        retryable = false,
                        traceId = e.traceId,
                    )
                // 可重试错误：落本地队列
                else -> {
                    val safeReason = e.safeMessage("网络不可用，请稍后重试")
                    enqueue(opType, clientOpId, materialCode, materialId, quantity,
                        targetLocation, expectedInventoryVersion, remark, safeReason)
                    SubmitResult.Queued(clientOpId, safeReason)
                }
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (e: Exception) {
            // 网络异常：落本地队列
            val safeReason = "网络不可用，请稍后重试"
            enqueue(opType, clientOpId, materialCode, materialId, quantity,
                targetLocation, expectedInventoryVersion, remark, safeReason)
            SubmitResult.Queued(clientOpId, safeReason)
        }
    }

    private suspend fun enqueue(
        opType: OfflineOpType,
        clientOpId: String,
        materialCode: String,
        materialId: String?,
        quantity: Int,
        targetLocation: String?,
        expectedInventoryVersion: Int?,
        remark: String?,
        error: String?
    ) {
        dao.insert(
            OfflineOperationEntity(
                id = clientOpId,
                clientOperationId = clientOpId,
                materialCode = materialCode,
                operationType = opType.name,
                status = SyncStatus.PENDING.name,
                createdAt = System.currentTimeMillis(),
                quantity = quantity,
                targetLocation = targetLocation,
                materialId = materialId,
                expectedInventoryVersion = expectedInventoryVersion,
                remark = remark,
                errorMessage = error
            )
        )
        // 入队即调度：WorkManager 在网络恢复时自动重放（网络约束 + 指数退避）。
        // 以前入队后没人调度，队列只能靠用户手动点「立即同步」。
        appContext?.let { OfflineSyncScheduler.enqueue(it) }
    }

    // ==================== 离线队列 ====================

    /** 观察队列（UI 展示用） */
    fun observeQueue(): Flow<List<OfflineOperation>> =
        dao.observeAll().map { list -> list.map { it.toDomain() } }

    /** 观察待同步数量（顶部横幅用） */
    fun observePendingCount(): Flow<Int> = dao.observePendingCount()
    fun observeAttentionCount(): Flow<Int> = dao.observeAttentionCount()

    /** Whether any retryable PENDING records remain, including future backoff entries. */
    suspend fun hasPendingOperations(): Boolean = dao.hasPending()

    /**
     * 同步全部待处理记录。
     * 逐条重放，成功标记 SYNCED，失败标记 FAILED 并记录原因；
     * 冲突（409）标记 CONFLICT，交由用户决定。
     */
    suspend fun syncPending(): SyncReport = syncPendingMutex.withLock {
        coroutineScope {
            val sessionAtStart = sessionGeneration.get()
            // Fix both due-time and enqueue-time at the start: a long drain must not retry the same
            // newly deferred row after its backoff elapses or absorb writes added during this pass.
            val drainCutoff = System.currentTimeMillis()
            val attemptedIds = mutableSetOf<String>()
            var success = 0
            var failed = 0
            var conflict = 0
            var retrying = 0

            while (sessionGeneration.get() == sessionAtStart) {
                val dueBatch = dao.pendingDue(drainCutoff, drainCutoff)
                // A login/logout during the query must not start another batch in the new session.
                if (sessionGeneration.get() != sessionAtStart || dueBatch.isEmpty()) break
                val batch = dueBatch.filter { attemptedIds.add(it.id) }
                if (batch.isEmpty()) break

                val permits = kotlinx.coroutines.sync.Semaphore(MAX_CONCURRENT_OPERATIONS)
                val outcomes = batch.map { item -> async(Dispatchers.IO) {
                    permits.acquire()
                    try {
                        if (sessionGeneration.get() == sessionAtStart) syncOne(item) else null
                    } catch (cancelled: CancellationException) {
                        // Identity replacement stops this pass with its partial report. A true
                        // worker/job cancellation still propagates after syncOne releases the claim.
                        if (sessionGeneration.get() != sessionAtStart && currentCoroutineContext().isActive) null
                        else throw cancelled
                    } finally {
                        permits.release()
                    }
                } }.awaitAll()
                outcomes.filterNotNull().forEach { outcome ->
                    when (outcome) {
                        SyncOutcome.SUCCESS -> success++
                        SyncOutcome.FAILED -> failed++
                        SyncOutcome.CONFLICT -> conflict++
                        SyncOutcome.RETRY_SCHEDULED -> retrying++
                    }
                }
                if (sessionGeneration.get() != sessionAtStart || dueBatch.size < MAX_BATCH_SIZE) break
            }

            SyncReport(success = success, failed = failed, conflict = conflict, retrying = retrying)
        }
    }

    /**
     * 自动重试：第 N 次失败后按 30s/60s/2min/4min 退避，累计 [MAX_AUTO_RETRY] 次仍失败转 FAILED，
     * 交给用户在「待处理」里决定重试或放弃——不再像旧实现那样把 FAILED 也塞进每轮重放。
     */
    private suspend fun scheduleRetry(item: OfflineOperationEntity, reason: String): SyncOutcome {
        val attempt = item.retryCount + 1
        if (attempt >= MAX_AUTO_RETRY) {
            dao.updateStatus(item.id, SyncStatus.FAILED.name, "已自动重试 $attempt 次仍失败：$reason")
            return SyncOutcome.FAILED
        }
        val delay = retryDelayMillis(attempt)
        dao.scheduleRetry(
            id = item.id,
            status = SyncStatus.PENDING.name,
            error = "第 $attempt 次失败，将在 ${delay / 60_000} 分钟后自动重试：$reason",
            retryCount = attempt,
            nextRetryAt = System.currentTimeMillis() + delay,
        )
        return SyncOutcome.RETRY_SCHEDULED
    }

    private suspend fun syncOne(item: OfflineOperationEntity): SyncOutcome {
        try {
            dao.updateStatus(item.id, SyncStatus.SYNCING.name)
            when (OfflineOpType.valueOf(item.operationType)) {
                OfflineOpType.INBOUND, OfflineOpType.OUTBOUND -> {
                    if (item.materialId == null) {
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "缺少物料 ID，无法重放")
                        return SyncOutcome.FAILED
                    }
                    // OUTBOUND 入队时 documentNo 打包在 remark JSON 里（见 submitOutbound），
                    // 重放必须还原，否则补传单据不挂订单，齐套与追溯统计漏计。
                    // 旧队列的 remark 是裸业务文本：解不出打包结构时按"无 documentNo"兼容，不判数据损坏；
                    // 但形如打包体却解析失败（DB 半写、迁移、人工改表）必须判损坏——否则会降级成
                    // 无 documentNo 的重放并标 SYNCED，把要修的"齐套漏计"静默复活（与 RETURN/RELOCATION 一致）。
                    val queued = if (item.operationType == OfflineOpType.OUTBOUND.name) {
                        when (val parsed = unpackQueuedTransferRemark(item.remark)) {
                            is QueuedRemarkParse.Corrupted -> {
                                dao.updateStatus(item.id, SyncStatus.FAILED.name, "队列数据损坏，无法重放")
                                return SyncOutcome.FAILED
                            }
                            is QueuedRemarkParse.Packed -> parsed.value
                            QueuedRemarkParse.NotPacked -> null
                        }
                    } else {
                        null
                    }
                    api.createTransferRequest(
                        clientOperationId = item.clientOperationId,
                        type = if (item.operationType == OfflineOpType.INBOUND.name) "INBOUND" else "OUTBOUND",
                        documentNo = queued?.documentNo,
                        items = listOf(TransferItem(materialId = item.materialId, quantity = item.quantity,
                            targetLocationCode = item.targetLocation,
                            expectedInventoryVersion = item.expectedInventoryVersion)),
                        remark = if (queued != null) queued.remark else item.remark,
                        evidenceIds = queued?.evidenceIds ?: emptyList(),
                    )
                }
                OfflineOpType.RETURN, OfflineOpType.RELOCATION -> {
                    if (item.materialId == null || item.expectedInventoryVersion == null) {
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "缺少物料 ID 或库存版本，无法重放")
                        return SyncOutcome.FAILED
                    }
                    val packed = try {
                        JSONObject(item.remark ?: "{}")
                    } catch (_: Exception) {
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "队列数据损坏，无法重放")
                        return SyncOutcome.FAILED
                    }
                    val isReturn = item.operationType == OfflineOpType.RETURN.name
                    val source = packed.optStringOrNull("sourceLocation")
                    if (!isReturn && source == null) {
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "缺少来源库位，无法重放")
                        return SyncOutcome.FAILED
                    }
                    api.createTransferRequest(
                        clientOperationId = item.clientOperationId,
                        type = if (isReturn) "RETURN" else "TRANSFER",
                        documentNo = packed.optStringOrNull("documentNo"),
                        items = listOf(TransferItem(materialId = item.materialId, quantity = item.quantity,
                            sourceLocationCode = if (isReturn) null else source,
                            targetLocationCode = item.targetLocation,
                            expectedInventoryVersion = item.expectedInventoryVersion)),
                        remark = packed.optStringOrNull("remark"),
                        evidenceIds = packed.optJSONArray("evidenceIds").toStringList(),
                    )
                }
                OfflineOpType.LOCATION_BIND -> api.bindLocation(item.materialCode, item.targetLocation ?: "", item.quantity)
                OfflineOpType.STOCKTAKE -> api.createStocktake(
                    clientOperationId = item.clientOperationId,
                    materialCode = item.materialCode,
                    locationCode = item.targetLocation,
                    actualQuantity = item.quantity,
                )
                OfflineOpType.EXCEPTION -> {
                    if (item.materialId == null) {
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "缺少物料 ID，无法重放")
                        return SyncOutcome.FAILED
                    }
                    val payload = try {
                        JSONObject(item.remark ?: "{}")
                    } catch (_: Exception) {
                        dao.updateStatus(item.id, SyncStatus.FAILED.name, "异常数据损坏，无法重放")
                        return SyncOutcome.FAILED
                    }
                    api.createException(
                        clientOperationId = item.clientOperationId,
                        orderNo = payload.optString("orderNo"),
                        deviceId = payload.optString("deviceId"),
                        materialId = item.materialId,
                        type = payload.optString("type", "OTHER"),
                        bookQuantity = payload.optInt("bookQuantity", 0),
                        actualQuantity = item.quantity,
                        description = payload.optString("description", ""),
                        evidenceIds = payload.optJSONArray("evidenceIds").toStringList(),
                    )
                }
                else -> {
                    dao.updateStatus(item.id, SyncStatus.FAILED.name, "该操作类型暂未接入同步")
                    return SyncOutcome.FAILED
                }
            }
            dao.markSynced(item.id, SyncStatus.SYNCED.name, null)
            return SyncOutcome.SUCCESS
        } catch (e: ApiException) {
            if (e.isConflict) {
                // 409：幂等键/库存版本冲突，重试也不会变，必须人工处理
                dao.updateStatus(item.id, SyncStatus.CONFLICT.name, e.safeMessage("同步冲突，请人工处理"))
                return SyncOutcome.CONFLICT
            }
            return scheduleRetry(item, e.safeMessage("同步失败，请重试"))
        } catch (cancelled: CancellationException) {
            try {
                withContext(NonCancellable) { dao.restoreCancelledClaim(item.id) }
            } catch (cleanup: Exception) {
                cancelled.addSuppressed(cleanup)
            }
            throw cancelled
        } catch (e: Exception) {
            return scheduleRetry(item, "网络不可用，请重试")
        }
    }

    /** 清理已同步记录 */
    suspend fun clearSynced() = dao.clearSynced()

    /**
     * 恢复上次进程被杀时卡在 SYNCING 的记录：改回 PENDING 等待重放。
     * 返回恢复的条数。由 Repository.get() 在 IO 线程调用一次。
     */
    suspend fun resetStuckSyncing(): Int = dao.resetStuckSyncing()

    /**
     * 重试单条失败/冲突记录：改回 PENDING 并重新调度自动同步。
     * 返回 true 表示该记录存在并已重置。
     */
    suspend fun retryOperation(id: String): Boolean {
        if (dao.findById(id) == null) return false
        dao.resetToPending(id)
        appContext?.let { OfflineSyncScheduler.enqueue(it) }
        return true
    }

    /**
     * 放弃单条失败/冲突记录：直接删除，不再重放。
     * 返回 true 表示该记录存在并已删除。
     */
    suspend fun discardOperation(id: String): Boolean {
        if (dao.findById(id) == null) return false
        dao.deleteById(id)
        return true
    }

    /** Result 不应吞掉 CancellationException，否则页面离开后旧请求仍会写回 UI 状态。 */
    private suspend fun <T> resultOf(block: suspend () -> T): Result<T> = try {
        Result.success(block())
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (error: Throwable) {
        Result.failure(error)
    }

    companion object {
        const val MAX_BATCH_SIZE = 20
        const val MAX_CONCURRENT_OPERATIONS = 2
        /** 契约 4.1 请求中的 clientVersion 字段 */
        const val CLIENT_VERSION = "0.3.0"

        @Volatile
        private var instance: LogisticsRepository? = null

        fun get(context: Context): LogisticsRepository = instance ?: synchronized(this) {
            instance ?: LogisticsRepository(
                api = MaterialFlowApi(),
                dao = DatabaseProvider.get(context).offlineOperationDao(),
                sessionStore = SessionStore.get(context),
                appContext = context.applicationContext
            ).also { repo ->
                instance = repo
                // 进程启动时恢复一次：上次被杀时卡在 SYNCING 的记录改回 PENDING，
                // 然后调度一次自动同步（唯一任务，重复调用无副作用）。
                CoroutineScope(Dispatchers.IO).launch {
                    runCatching { repo.resetStuckSyncing() }
                    repo.appContext?.let { OfflineSyncScheduler.enqueue(it) }
                }
            }
        }
    }
}

/** 提交结果 */
sealed interface SubmitResult {
    /** 直接提交成功 */
    data class Success(val data: Any?, val serverTime: String?) : SubmitResult
    /** 已写入本地队列，等待联网同步 */
    data class Queued(val clientOperationId: String, val reason: String?) : SubmitResult
    /** 提交失败且不可重试（业务错误） */
    data class Failure(
        val message: String,
        val retryable: Boolean,
        val traceId: String? = null
    ) : SubmitResult
}

/** 同步结果报告 */
data class SyncReport(
    val success: Int,
    val failed: Int,
    val conflict: Int,
    /** 本轮失败但已排入自动重试的记录数（不算终态失败） */
    val retrying: Int = 0,
) {
    val hasIssue: Boolean get() = failed > 0 || conflict > 0
}

/** 自动重试间隔：30s、1min、2min、4min（最多 [MAX_AUTO_RETRY] 次）。 */
private fun retryDelayMillis(attempt: Int): Long {
    val shift = (attempt - 1).coerceIn(0, 5)
    return minOf(30L * 60_000L, 30_000L shl shift)
}

private enum class SyncOutcome { SUCCESS, FAILED, CONFLICT, RETRY_SCHEDULED }

/** 打包体版本标记：区分"我方写的结构"与"用户恰好输入的合法 JSON"。 */
private const val QUEUED_REMARK_VERSION_KEY = "v"
private const val QUEUED_REMARK_VERSION = 1

/** 同一操作自动重试的次数上限，超过后转 FAILED 等人工处理。 */
private const val MAX_AUTO_RETRY = 5

/**
 * 离线队列 remark 解包结果：
 *  - [Packed] 我方打包体（新格式带 `v=1`，或升级前无 `v` 但三键齐全的旧打包体）；
 *  - [NotPacked] 用户文本（含"恰好是合法 JSON"的备注）→ 按无 documentNo 兼容重放；
 *  - [Corrupted] 形如打包体（以 `{` 开头且含 `documentNo` 键）却解析失败 → 调用方必须判数据损坏。
 */
private sealed interface QueuedRemarkParse {
    data class Packed(val value: QueuedTransferRemark) : QueuedRemarkParse
    object NotPacked : QueuedRemarkParse
    object Corrupted : QueuedRemarkParse
}

/** 离线队列中打包的流转 remark：documentNo / sourceLocation / remark 三项，见 packQueuedTransferRemark。 */
private data class QueuedTransferRemark(
    val documentNo: String?,
    val remark: String?,
    val evidenceIds: List<String> = emptyList(),
)

/** 本地实体 → 领域模型 */
private fun OfflineOperationEntity.toDomain(): OfflineOperation = OfflineOperation(
    id = id,
    clientOperationId = clientOperationId,
    opType = runCatching { OfflineOpType.valueOf(operationType) }.getOrDefault(OfflineOpType.INBOUND),
    materialCode = materialCode,
    quantity = quantity,
    targetLocation = targetLocation,
    status = runCatching { SyncStatus.valueOf(status) }.getOrDefault(SyncStatus.PENDING),
    createdAt = createdAt,
    serverTime = serverTime,
    errorMessage = errorMessage
)

/** JSONObject 可空字符串读取：缺失或 JSON null 返回 null（optString 会把 null 读成 "null"）。 */
private fun JSONObject.optStringOrNull(key: String): String? =
    if (!has(key) || isNull(key)) null else optString(key).takeIf { it.isNotBlank() }
