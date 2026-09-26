package com.company.logistics.data.remote

import com.company.logistics.BuildConfig
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.DeviceDetail
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.ScanResult
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.HandoverActionResult
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.AuditLogPage
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestPage
import com.company.logistics.model.WorkspaceViewRole
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.AssemblyTaskPage
import com.company.logistics.model.AssemblyAssignmentResponse
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.MachineProgressPage
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.OrderDetail
import com.company.logistics.model.ExceptionSubmissionResult
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.io.OutputStream
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * 物料流转 API 客户端 —— 严格对齐《V1 需求冻结与接口契约》第 4 节。
 *
 * 契约约束（第 5 节）：
 *  - 所有写操作需携带 Authorization / Idempotency-Key / X-Request-Id；
 *  - 数量为整数，禁止小数、负数、科学计数法；
 *  - 客户端不得硬编码 IP，Base URL 由配置注入（见 [ApiConfig]）。
 *
 * 说明：V1 使用 OkHttp + org.json，保持与既有工程一致的零额外序列化依赖策略；
 *      全局共享一个 OkHttpClient（连接池 + HTTP/2 复用），扫码解析等高频请求
 *      不再每次重建 TCP/TLS；本类所有公开方法签名保持不变，调用方零改动。
 */
open class MaterialFlowApi(
    private val config: ApiConfig = ApiConfig
) {
    data class BomImportError(val lineNo: Int, val field: String, val code: String, val message: String)
    data class BomImportPreview(val previewId: String, val modelCode: String, val totalRows: Int, val validRows: Int, val invalidRows: Int, val canCommit: Boolean, val errors: List<BomImportError>)
    data class BomVersionResult(val bomVersionId: String, val modelCode: String, val versionNo: Int, val status: String, val itemCount: Int)
    data class ProductionOrderCreateResult(val orderNo: String, val orderId: String, val status: String)
    data class DeviceCreateResult(val deviceId: String, val deviceNo: String, val status: String)
    data class DeviceAssignmentResult(val taskId: String, val orderNo: String, val modelCode: String, val deviceId: String, val expectedVersion: Int)


    /** 会话 token，由登录写入；为空表示未登录 */
    @Volatile
    var accessToken: String? = null
        private set

    /**
     * 刷新令牌（长效，30 天）。
     *
     * 与 accessToken 分离存储：access 过期时用它在后台静默换新，
     * 现场作业不会因为 token 到期被打断。
     * 持久化由上层负责（[com.company.logistics.data.SessionStore]）。
     */
    @Volatile
    var refreshToken: String? = null
        private set

    /** 当前设备标识，刷新时需要回传（审计归因到具体设备） */
    @Volatile
    var deviceId: String = "unknown"

    /**
     * 令牌轮转回调。
     *
     * 刷新发生在网络层内部，上层（SessionStore）无从感知。
     * 若不回调，新令牌只留在内存 —— 下次冷启动会拿着已被消费的旧令牌
     * 去刷新，被服务端判定为重放并吊销整族会话。
     */
    @Volatile
    var onTokensRotated: ((access: String?, refresh: String?) -> Unit)? = null

    fun updateToken(token: String?) {
        accessToken = token
        if (token == null) refreshToken = null
    }

    /** Clear both in-memory tokens and notify the session owner exactly once. */
    private fun clearSession() {
        val hadToken = accessToken != null || refreshToken != null
        accessToken = null
        refreshToken = null
        if (hadToken) onTokensRotated?.invoke(null, null)
    }

    /** 从持久化存储恢复会话（冷启动时调用） */
    fun restoreSession(access: String?, refresh: String?, device: String) {
        accessToken = access
        refreshToken = refresh
        deviceId = device
    }

    /**
     * 并发刷新去重锁。
     *
     * 多个请求同时收到 401 时会争相刷新，而刷新令牌是一次性的：
     * 第二个请求拿着已被消费的旧令牌去刷新，会被服务端判定为
     * 「令牌重放」并吊销整族会话 —— 用户被莫名踢下线。
     * 因此刷新必须是串行的，且后来者直接复用第一次的结果。
     */
    private val refreshLock = Any()

    @Volatile
    private var lastRefreshAt = 0L

    /**
     * 全局共享的 OkHttpClient：连接池 + HTTP/2 复用。
     * 原来每个请求都 new HttpURLConnection + disconnect()，扫码解析这类高频请求
     * 每次都要重建 TCP/TLS；现在同一 host 的连接会被复用。
     * 超时取 ApiConfig 构建时的配置值（运行时未被修改过，见 ApiConfig）。
     */
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(config.connectTimeoutMs.toLong(), TimeUnit.MILLISECONDS)
            .readTimeout(config.readTimeoutMs.toLong(), TimeUnit.MILLISECONDS)
            .build()
    }

    /**
     * 用刷新令牌换取新的令牌对。
     *
     * 区分令牌失效与临时网络失败：只有前者清理会话，后者保留令牌并允许再次重试。
     */
    private enum class RefreshOutcome {
        REFRESHED,
        INVALID,
        RETRYABLE_FAILURE,
    }

    private suspend fun refreshAccessToken(): RefreshOutcome = withContext(Dispatchers.IO) {
        val token = refreshToken ?: return@withContext RefreshOutcome.INVALID

        synchronized(refreshLock) {
            // 双重检查：若刚才已有其他协程刷新成功，直接用新令牌
            if (accessToken != null && lastRefreshAt > System.currentTimeMillis() - 1000) {
                return@withContext RefreshOutcome.REFRESHED
            }
            val requestBody = JSONObject().apply {
                put("refreshToken", token)
                put("deviceId", deviceId)
            }.toString().toRequestBody(JSON_MEDIA_TYPE)
            val httpRequest = Request.Builder()
                .url(config.baseUrl.trimEnd('/') + "/api/v1/auth/refresh")
                .header("Content-Type", "application/json; charset=utf-8")
                .header("X-Request-Id", UUID.randomUUID().toString())
                .post(requestBody)
                .build()
            try {
                val (code, text) = httpClient.newCall(httpRequest).execute().use { response ->
                    response.code to (response.body?.string() ?: "")
                }
                if (code !in 200..299) {
                    // 4xx 表示 refresh token 已失效；5xx/网关错误仍可重试，不能把用户
                    // 因一次临时网络故障踢回登录页。
                    return@withContext if (code in 400..499) {
                        clearSession()
                        RefreshOutcome.INVALID
                    } else {
                        RefreshOutcome.RETRYABLE_FAILURE
                    }
                }
                val json = JSONObject(text)
                val newAccess = json.optString("accessToken").takeIf { it.isNotEmpty() }
                    ?: return@withContext RefreshOutcome.RETRYABLE_FAILURE
                accessToken = newAccess
                json.optString("refreshToken")
                    .takeIf { it.isNotEmpty() }
                    ?.let { refreshToken = it }
                lastRefreshAt = System.currentTimeMillis()
                // 通知上层落盘，否则新令牌只存在于内存
                onTokensRotated?.invoke(accessToken, refreshToken)
                RefreshOutcome.REFRESHED
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // 网络异常：保留令牌，下次请求再试
                RefreshOutcome.RETRYABLE_FAILURE
            }
        }
    }

    // ==================== 4.1 登录 ====================

    open suspend fun login(
        username: String,
        password: String,
        deviceId: String,
        clientVersion: String
    ): LoginResultDto = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            // 服务端同时兼容 username / employeeNo；Android 使用员工工号字段。
            put("employeeNo", username)
            // 密码不写入日志（契约 4.1）
            put("password", password)
            put("deviceId", deviceId)
            put("clientVersion", clientVersion)
        }
        val json = request("POST", "/api/v1/auth/login", body.toString(), auth = false)
        val result = ApiParser.parseLogin(json)
        accessToken = result.accessToken
        refreshToken = result.refreshToken
        // 显式限定，否则参数 shadow 掉属性变成自赋值空操作
        this@MaterialFlowApi.deviceId = deviceId
        result
    }

    suspend fun changePassword(oldPassword: String, newPassword: String) = withContext(Dispatchers.IO) {
        require(oldPassword.isNotBlank() && newPassword.isNotBlank()) { "密码不能为空" }
        request("POST", "/api/v1/auth/change-password", JSONObject().apply {
            put("oldPassword", oldPassword); put("newPassword", newPassword)
        }.toString())
    }

    suspend fun setupStatus(): SetupStatusDto = withContext(Dispatchers.IO) {
        ApiParser.parseSetupStatus(request("GET", "/api/v1/setup/status", null, auth = false))
    }

    suspend fun initializeAdmin(password: String, confirmPassword: String, clientOperationId: String): InitializeAdminResponseDto = withContext(Dispatchers.IO) {
        require(password.length in 8..256) { "密码长度必须为 8~256 个字符" }
        require(password == confirmPassword) { "两次输入的密码不一致" }
        requireUuid(clientOperationId, "clientOperationId")
        val body = JSONObject().apply {
            put("password", password)
            put("confirmPassword", confirmPassword)
            put("clientOperationId", clientOperationId)
        }
        ApiParser.parseInitializeAdmin(request("POST", "/api/v1/setup/initialize-admin", body.toString(), auth = false, idempotencyKey = clientOperationId))
    }

    suspend fun listUsers(): List<com.company.logistics.model.ManagedUser> = withContext(Dispatchers.IO) {
        val root = JSONObject(request("GET", "/api/v1/users", null))
        val items = root.optJSONArray("items") ?: JSONArray()
        (0 until items.length()).map { i ->
            val u = items.getJSONObject(i)
            com.company.logistics.model.ManagedUser(
                id = u.optString("id"), username = u.optString("username"),
                displayName = u.optString("display_name", u.optString("displayName")),
                role = com.company.logistics.model.UserRole.from(u.optString("role")),
                active = u.optBoolean("active", true),
                mustChangePassword = u.optBoolean("must_change_password", u.optBoolean("mustChangePassword", false)),
                createdAt = u.optString("created_at", u.optString("createdAt")).takeIf { it.isNotBlank() }
            )
        }
    }

    suspend fun addUser(employeeNo: String, displayName: String, role: String, password: String, managerId: String?) = withContext(Dispatchers.IO) {
        request("POST", "/api/v1/admin/users", JSONObject().apply {
            put("employeeNo", employeeNo); put("displayName", displayName); put("role", role); put("password", password)
            if (managerId.isNullOrBlank()) put("managerId", JSONObject.NULL) else put("managerId", managerId)
        }.toString())
    }

    suspend fun deleteUser(userId: String, clientOperationId: String): DeleteUserResult = withContext(Dispatchers.IO) {
        require(userId.isNotBlank()) { "userId 不能为空" }
        requireUuid(clientOperationId, "clientOperationId")
        ApiParser.parseDeleteUser(
            request(
                "DELETE",
                "/api/v1/admin/users/$userId",
                JSONObject().apply { put("clientOperationId", clientOperationId) }.toString(),
                idempotencyKey = clientOperationId,
            )
        )
    }

    suspend fun previewBomImport(fileName: String, fileBytes: ByteArray, modelCode: String): BomImportPreview = withContext(Dispatchers.IO) {
        require(fileBytes.size <= MAX_BOM_FILE_BYTES) { "BOM 文件不能超过 10 MB" }
        require(modelCode.isNotBlank()) { "modelCode 不能为空" }
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("modelCode", modelCode)
            .addFormDataPart("file", fileName, fileBytes.toRequestBody("text/csv".toMediaType()))
            .build()
        val httpRequest = Request.Builder()
            .url(config.baseUrl.trimEnd('/') + "/api/v1/boms/import/preview")
            .header("Authorization", "Bearer ${requireToken()}")
            .header("X-Request-Id", UUID.randomUUID().toString())
            .post(multipart)
            .build()
        val (code, text) = httpClient.newCall(httpRequest).execute().use { response ->
            response.code to (response.body?.string() ?: "")
        }
        if (code !in 200..299) throw ApiParser.parseError(code, text)
        val root = JSONObject(text.ifEmpty { throw ApiException(code, "EMPTY_BODY", "预览响应为空", retryable = true) })
        val errors = root.optJSONArray("errors") ?: JSONArray()
        BomImportPreview(root.optString("previewId"), root.optString("modelCode"), root.optInt("totalRows"), root.optInt("validRows"), root.optInt("invalidRows"), root.optBoolean("canCommit"), (0 until errors.length()).map { i -> val e = errors.getJSONObject(i); BomImportError(e.optInt("lineNo"), e.optString("field"), e.optString("code"), e.optString("message")) })
    }

    suspend fun commitBomImport(previewId: String, clientOperationId: String, publish: Boolean): BomVersionResult = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId")
        val root = JSONObject(request("POST", "/api/v1/boms/import/commit", JSONObject().apply { put("previewId", previewId); put("clientOperationId", clientOperationId); put("publish", publish) }.toString(), idempotencyKey = clientOperationId))
        BomVersionResult(root.optString("bomVersionId"), root.optString("modelCode"), root.optInt("versionNo"), root.optString("status"), root.optInt("itemCount"))
    }

    suspend fun createProductionOrder(clientOperationId: String, orderNo: String, productName: String, plannedQuantity: Int, plannedDeliveryDate: String, models: JSONArray): ProductionOrderCreateResult = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId"); require(orderNo.isNotBlank() && productName.isNotBlank() && plannedQuantity > 0)
        val root = JSONObject(request("POST", "/api/v1/production-orders", JSONObject().apply { put("clientOperationId", clientOperationId); put("orderNo", orderNo); put("productName", productName); put("plannedQuantity", plannedQuantity); put("plannedDeliveryDate", plannedDeliveryDate); put("models", models) }.toString(), idempotencyKey = clientOperationId))
        ProductionOrderCreateResult(root.optString("orderNo"), root.optString("orderId"), root.optString("status"))
    }

    suspend fun createDevice(clientOperationId: String, deviceNo: String, deviceName: String, workshop: String, modelCapability: String?): DeviceCreateResult = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId"); require(deviceNo.isNotBlank() && deviceName.isNotBlank() && workshop.isNotBlank())
        val root = JSONObject(request("POST", "/api/v1/devices", JSONObject().apply { put("clientOperationId", clientOperationId); put("deviceNo", deviceNo); put("deviceName", deviceName); put("workshop", workshop); put("modelCapability", modelCapability ?: JSONObject.NULL) }.toString(), idempotencyKey = clientOperationId))
        DeviceCreateResult(root.optString("deviceId"), root.optString("deviceNo"), root.optString("status"))
    }

    suspend fun assignDevice(orderNo: String, modelCode: String, clientOperationId: String, deviceId: String, expectedVersion: Int): DeviceAssignmentResult = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId"); require(orderNo.isNotBlank() && modelCode.isNotBlank() && deviceId.isNotBlank() && expectedVersion > 0)
        val root = JSONObject(request("POST", "/api/v1/production-orders/${encodeQuery(orderNo)}/models/${encodeQuery(modelCode)}/assign-device", JSONObject().apply { put("clientOperationId", clientOperationId); put("deviceId", deviceId); put("expectedVersion", expectedVersion) }.toString(), idempotencyKey = clientOperationId))
        DeviceAssignmentResult(root.optString("taskId"), root.optString("orderNo"), root.optString("modelCode"), root.optString("deviceId"), root.optInt("expectedVersion"))
    }


    suspend fun logout(): Unit = withContext(Dispatchers.IO) {
        runCatching { request("POST", "/api/v1/auth/logout", "{}") }
        accessToken = null
        refreshToken = null
    }

    // ==================== 4.2 扫码解析 ====================

    suspend fun resolveScan(rawValue: String): ScanResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("rawValue", rawValue)
            put("clientOperationId", UUID.randomUUID().toString())
        }
        ApiParser.parseScanResolve(request("POST", "/api/v1/scan/resolve", body.toString()))
    }

    /** GET /api/v1/devices/{id}：机台详情 */
    suspend fun getDeviceDetail(deviceId: String): DeviceDetail = withContext(Dispatchers.IO) {
        ApiParser.parseDeviceDetail(request("GET", "/api/v1/devices/$deviceId"))
    }

    /** POST /api/v1/exceptions；服务端创建待审批异常，不在客户端伪造成功。 */
    suspend fun createException(
        clientOperationId: String,
        orderNo: String,
        deviceId: String,
        materialId: String,
        type: String,
        bookQuantity: Int,
        actualQuantity: Int,
        description: String? = null,
        evidenceIds: List<String> = emptyList(),
    ): ExceptionSubmissionResult = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId")
        require(orderNo.isNotBlank() && deviceId.isNotBlank() && materialId.isNotBlank()) { "异常必须关联订单、机台和物料" }
        require(bookQuantity >= 0 && actualQuantity >= 0) { "数量必须是非负整数" }
        val body = JSONObject().apply {
            put("orderNo", orderNo); put("deviceId", deviceId); put("materialId", materialId)
            put("type", type); put("bookQuantity", bookQuantity); put("actualQuantity", actualQuantity)
            put("clientOperationId", clientOperationId)
            description?.let { put("description", it) }
            put("evidenceIds", JSONArray(evidenceIds))
        }
        ApiParser.parseExceptionSubmission(request("POST", "/api/v1/exceptions", body.toString(), idempotencyKey = clientOperationId))
    }

    // ==================== 4.3 生产订单物料状态 ====================

    suspend fun orderMaterialStatus(
        documentType: String,
        documentNo: String
    ): OrderMaterialStatus = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("documentType", documentType)
            put("documentNo", documentNo)
        }
        ApiParser.parseOrderMaterialStatus(
            request("POST", "/api/v1/orders/material-status", body.toString())
        )
    }

    suspend fun orderDetail(orderNo: String, page: Int = 1, pageSize: Int = 20): OrderDetail = withContext(Dispatchers.IO) {
        require(orderNo.isNotBlank()) { "orderNo 不能为空" }
        require(page >= 1 && pageSize == 20) { "订单详情分页固定为 20 条" }
        ApiParser.parseOrderDetail(request("GET", "/api/v1/orders/${encodeQuery(orderNo)}/detail?page=$page&pageSize=$pageSize", null))
    }

    // ==================== 4.3 工作台 ====================

    /** 读取当前登录角色或 ADMIN 预览角色的服务端工作台摘要。 */
    open suspend fun workspaceSummary(
        viewRole: WorkspaceViewRole? = null,
        requestId: String? = null,
        clientOperationId: String? = null,
    ) = withContext(Dispatchers.IO) {
        val query = viewRole?.let { "?viewRole=${encodeQuery(it.code)}" }.orEmpty()
        ApiParser.parseWorkspaceSummary(
            request(
                "GET",
                "/api/v1/workspace/summary$query",
                null,
                requestId = requestId,
                clientOperationId = clientOperationId,
            )
        )
    }

    /**
     * 读取当前角色可见的工作台物料项。
     *
     * 只允许产品约束中的 20/50 页大小；调用方必须通过分页读取，不能请求全量数据。
     */
    open suspend fun workspaceMaterialItems(
        status: String? = null,
        orderNo: String? = null,
        page: Int = 1,
        pageSize: Int = 20,
        viewRole: WorkspaceViewRole? = null,
        requestId: String? = null,
        clientOperationId: String? = null,
    ) = withContext(Dispatchers.IO) {
        require(page >= 1) { "page 必须从 1 开始" }
        require(pageSize == 20 || pageSize == 50) { "pageSize 只能是 20 或 50" }
        val query = buildList {
            status?.takeIf { it.isNotBlank() }?.let { add("status=${encodeQuery(it)}") }
            orderNo?.takeIf { it.isNotBlank() }?.let { add("orderNo=${encodeQuery(it)}") }
            viewRole?.let { add("viewRole=${encodeQuery(it.code)}") }
            add("page=$page")
            add("pageSize=$pageSize")
        }.joinToString("&")
        ApiParser.parseWorkspaceMaterialItems(
            request(
                "GET",
                "/api/v1/workspace/material-items?$query",
                null,
                requestId = requestId,
                clientOperationId = clientOperationId,
            )
        )
    }

    // ==================== 4.3 交接 ====================

    /** 物料员发起已审批出库单的交接；clientOperationId 同时写入 body 和幂等请求头。 */
    suspend fun createHandover(
        clientOperationId: String,
        workItemId: String,
        transferRequestId: String,
        quantity: Int,
        fromLocation: String,
        deviceId: String?,
        receiverUserId: String?,
        remark: String? = null,
    ): HandoverActionResult = withContext(Dispatchers.IO) {
        require(quantity > 0) { "交接数量必须大于 0" }
        require(fromLocation.isNotBlank()) { "交接出库库位不能为空" }
        val body = JSONObject().apply {
            put("workItemId", workItemId)
            put("transferRequestId", transferRequestId)
            put("quantity", quantity)
            put("fromLocation", fromLocation)
            put("deviceId", deviceId ?: JSONObject.NULL)
            put("receiverUserId", receiverUserId ?: JSONObject.NULL)
            put("remark", remark ?: JSONObject.NULL)
            put("clientOperationId", clientOperationId)
        }
        ApiParser.parseHandoverAction(
            request("POST", "/api/v1/handovers", body.toString(), idempotencyKey = clientOperationId)
        )
    }

    /** 确认、驳回或取消交接；动作请求默认在线执行，避免离线重复确认。 */
    suspend fun decideHandover(
        handoverId: String,
        action: HandoverAction,
        clientOperationId: String,
        reason: String? = null,
        requestId: String = UUID.randomUUID().toString(),
    ): HandoverActionResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("clientOperationId", clientOperationId)
            put("requestId", requestId)
            reason?.let { put("reason", it) }
        }
        ApiParser.parseHandoverAction(
            request(
                "POST",
                "/api/v1/handovers/$handoverId/${action.pathSegment}",
                body.toString(),
                idempotencyKey = clientOperationId,
            )
        )
    }

    /** 交接时间线只请求并保留 20 条，满足中端设备的内存约束。 */
    suspend fun handoverTimeline(
        workItemId: String,
        page: Int = 1,
        pageSize: Int = 20,
    ): HandoverTimeline = withContext(Dispatchers.IO) {
        require(page >= 1) { "page 必须从 1 开始" }
        require(pageSize == 20) { "交接时间线每页只能是 20" }
        ApiParser.parseHandoverTimeline(
            request(
                "GET",
                "/api/v1/handovers/$workItemId/timeline?page=$page&pageSize=$pageSize",
                null,
            )
        )
    }

    /** C14 task labor snapshot; server owns all minute values. */
    suspend fun assemblyTaskLaborSummary(taskId: String, assemblerId: String? = null): LaborSummaryPage = withContext(Dispatchers.IO) {
        require(taskId.isNotBlank()) { "taskId 不能为空" }
        val query = assemblerId?.takeIf { it.isNotBlank() }?.let { "?assemblerId=${encodeQuery(it)}" }.orEmpty()
        ApiParser.parseTaskLaborSummary(request("GET", "/api/v1/assembly/tasks/${encodeQuery(taskId)}/labor-summary$query", null))
    }

    /** C14 workshop labor snapshot; filters are sent to the server, never applied locally. */
    suspend fun workshopLaborSummary(
        page: Int = 1, pageSize: Int = 20, deviceId: String? = null,
        orderNo: String? = null, assemblerId: String? = null,
    ): LaborSummaryPage = withContext(Dispatchers.IO) {
        require(page >= 1 && pageSize == 20) { "pageSize 固定为 20" }
        val query = buildList {
            add("page=$page"); add("pageSize=$pageSize")
            deviceId?.takeIf { it.isNotBlank() }?.let { add("deviceId=${encodeQuery(it)}") }
            orderNo?.takeIf { it.isNotBlank() }?.let { add("orderNo=${encodeQuery(it)}") }
            assemblerId?.takeIf { it.isNotBlank() }?.let { add("assemblerId=${encodeQuery(it)}") }
        }.joinToString("&")
        ApiParser.parseLaborSummary(request("GET", "/api/v1/workshop/labor-summary?$query", null))
    }

    suspend fun assemblyTasks(page: Int = 1, pageSize: Int = 20): List<AssemblyTask> =
        assemblyTaskPage(page, pageSize).items

    open suspend fun publishedAssemblyModel(modelCode: String): AssemblyModelMeta = withContext(Dispatchers.IO) {
        require(modelCode.isNotBlank()) { "modelCode 不能为空" }
        ApiParser.parseAssemblyModelMeta(request("GET", "/api/v1/assembly-models/${encodeQuery(modelCode)}/published", null))
    }

    suspend fun uploadAssemblyModel(
        modelCode: String,
        modelName: String,
        fileName: String,
        contentType: String,
        contentLength: Long,
        content: InputStream,
        requestId: UUID,
        operationId: UUID,
        onProgress: (Long) -> Unit = {},
    ): AssemblyModelMeta = withContext(Dispatchers.IO) {
        val boundary = "----MaterialFlowAssemblyModel${UUID.randomUUID().toString().replace("-", "")}"
        val prefix = "--$boundary\r\nContent-Disposition: form-data; name=\"modelCode\"\r\n\r\n$modelCode\r\n" +
            "--$boundary\r\nContent-Disposition: form-data; name=\"modelName\"\r\n\r\n$modelName\r\n" +
            "--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"${fileName.replace(Regex("[\"\\r\\n]"), "_")}\"\r\n" +
            "Content-Type: $contentType\r\n\r\n"
        val suffix = "\r\n--$boundary--\r\n"
        val totalLength = prefix.toByteArray(Charsets.UTF_8).size + contentLength + suffix.toByteArray(Charsets.UTF_8).size
        // 流式请求体：前缀 + 文件流 + 后缀，避免把整个模型读进内存；wire 格式与原来手写的一致。
        val streamingBody = object : RequestBody() {
            override fun contentType() = "multipart/form-data; boundary=$boundary".toMediaType()
            override fun contentLength() = totalLength
            override fun writeTo(sink: BufferedSink) {
                sink.write(prefix.toByteArray(Charsets.UTF_8))
                val buffer = ByteArray(64 * 1024)
                var sent = 0L
                while (true) {
                    val count = content.read(buffer)
                    if (count < 0) break
                    sink.write(buffer, 0, count)
                    sent += count
                    onProgress(sent)
                }
                check(sent == contentLength) { "文件大小在上传期间发生变化" }
                sink.write(suffix.toByteArray(Charsets.UTF_8))
            }
        }
        val httpRequest = Request.Builder()
            .url(fullUrl("/api/v1/assembly-models"))
            .header("Authorization", "Bearer ${requireToken()}")
            .header("Idempotency-Key", operationId.toString())
            .header("X-Request-Id", requestId.toString())
            .post(streamingBody)
            .build()
        try {
            val (code, text) = executeOnce(httpRequest)
            if (code !in 200..299) throw ApiParser.parseError(code, text)
            ApiParser.parseAssemblyModelMeta(text.ifEmpty { throw ApiException(code, "EMPTY_BODY", "上传响应为空", retryable = true) })
        } finally { content.close() }
    }

    /** Streams an authorized model response without retaining the complete GLB in memory. */
    open suspend fun downloadAssemblyModelContent(
        meta: AssemblyModelMeta,
        output: OutputStream,
        onProgress: (Long) -> Unit = {},
    ): Long = withContext(Dispatchers.IO) {
        val httpRequest = Request.Builder()
            .url(fullUrl("/api/v1/assembly-models/${encodeQuery(meta.modelCode)}/versions/${meta.version}/content"))
            .header("Authorization", "Bearer ${requireToken()}")
            .get()
            .build()
        httpClient.newCall(httpRequest).execute().use { response ->
            if (response.code !in 200..299) {
                throw ApiParser.parseError(response.code, response.body?.string() ?: "")
            }
            response.body!!.byteStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                var received = 0L
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    output.write(buffer, 0, count)
                    received += count
                    onProgress(received)
                }
                received
            }
        }
    }

    suspend fun assemblyTaskPage(page: Int = 1, pageSize: Int = 20): AssemblyTaskPage = withContext(Dispatchers.IO) {
        require(page >= 1 && pageSize == 20) { "装配任务分页固定为 20 条" }
        ApiParser.parseAssemblyTaskPage(
            request("GET", "/api/v1/assembly/tasks?page=$page&pageSize=$pageSize", null)
        )
    }

    suspend fun acceptAssemblyMaterial(taskId: String, clientOperationId: String): AssemblyTask = assemblyTaskAction(taskId, "accept-material", clientOperationId, null)
    suspend fun startAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): LaborRecord = assemblyLaborAction(taskId, "start", clientOperationId, expectedVersion)
    suspend fun submitAssemblyProgress(taskId: String, stage: Int, expectedVersion: Int, clientOperationId: String): AssemblyTask = assemblyTaskAction(taskId, "progress", clientOperationId, expectedVersion, stage)
    suspend fun completeAssemblyWork(taskId: String, expectedVersion: Int, clientOperationId: String): AssemblyTask = assemblyTaskAction(taskId, "complete", clientOperationId, expectedVersion)

    suspend fun startAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String) = stageOperation(taskId, stageNo, expectedVersion, clientOperationId, "start")
    suspend fun completeAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String) = stageOperation(taskId, stageNo, expectedVersion, clientOperationId, "complete")
    suspend fun reworkAssemblyStage(taskId: String, stageNo: Int, expectedVersion: Int, reason: String, clientOperationId: String) = stageOperation(taskId, stageNo, expectedVersion, clientOperationId, "rework", reason)

    suspend fun assignAssemblyMembers(taskId: String, assemblerIds: List<String>, clientOperationId: String): AssemblyAssignmentResponse = withContext(Dispatchers.IO) {
        require(taskId.isNotBlank()); require(assemblerIds.isNotEmpty() && assemblerIds.size <= 20 && assemblerIds.distinct().size == assemblerIds.size); require(assemblerIds.all { it.isNotBlank() }); requireUuid(clientOperationId, "clientOperationId")
        val body = JSONObject().apply { put("assemblerIds", JSONArray(assemblerIds)); put("clientOperationId", clientOperationId) }
        ApiParser.parseAssemblyAssignmentResponse(request("POST", "/api/v1/assembly/tasks/$taskId/assignments", body.toString(), idempotencyKey = clientOperationId))
    }

    suspend fun removeAssemblyMember(taskId: String, assemblerId: String, clientOperationId: String): AssemblyAssignmentResponse = withContext(Dispatchers.IO) {
        require(taskId.isNotBlank()); require(assemblerId.isNotBlank()); requireUuid(clientOperationId, "clientOperationId")
        ApiParser.parseAssemblyAssignmentResponse(request("DELETE", "/api/v1/assembly/tasks/$taskId/assignments/$assemblerId", JSONObject().put("clientOperationId", clientOperationId).toString(), idempotencyKey = clientOperationId))
    }

    private suspend fun stageOperation(taskId: String, stageNo: Int, expectedVersion: Int, clientOperationId: String, action: String, reason: String? = null): com.company.logistics.model.AssemblyStageOperationResult = withContext(Dispatchers.IO) {
        require(taskId.isNotBlank()); require(stageNo in 1..3); requireUuid(clientOperationId, "clientOperationId")
        if (action == "rework") require(!reason.isNullOrBlank() && reason.length <= 500)
        val body = JSONObject().apply { put("expectedVersion", expectedVersion); put("clientOperationId", clientOperationId); if (reason != null) put("reworkReason", reason) }
        ApiParser.parseAssemblyStageOperation(request("POST", "/api/v1/assembly/tasks/$taskId/stages/$stageNo/$action", body.toString(), idempotencyKey = clientOperationId))
    }

    suspend fun startTemporaryTransfer(taskId: String?, deviceId: String? = null, remark: String, clientOperationId: String): LaborRecord = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId")
        require(remark.length in 1..500) { "临时调拨备注长度必须为 1~500 字符" }
        ApiParser.parseTemporaryTransfer(request("POST", "/api/v1/assembly/temporary-transfers/start", JSONObject().apply {
            put("taskId", taskId ?: JSONObject.NULL); put("deviceId", deviceId ?: JSONObject.NULL)
            put("remark", remark); put("clientOperationId", clientOperationId)
        }.toString(), idempotencyKey = clientOperationId))
    }

    /** Source-compatible overload for callers that do not have a device filter yet. */
    suspend fun startTemporaryTransfer(taskId: String?, remark: String, clientOperationId: String): LaborRecord =
        startTemporaryTransfer(taskId, null, remark, clientOperationId)

    suspend fun completeTemporaryTransfer(transferId: String, remark: String, clientOperationId: String): LaborRecord = withContext(Dispatchers.IO) {
        require(transferId.isNotBlank()) { "temporaryTransferId 不能为空" }
        requireUuid(clientOperationId, "clientOperationId")
        require(remark.length in 1..500) { "临时调拨备注长度必须为 1~500 字符" }
        ApiParser.parseTemporaryTransfer(request("POST", "/api/v1/assembly/temporary-transfers/$transferId/complete", JSONObject().apply {
            put("remark", remark); put("clientOperationId", clientOperationId)
        }.toString(), idempotencyKey = clientOperationId))
    }

    suspend fun workshopSummary(from: String? = null, to: String? = null): WorkshopProgressSummary = withContext(Dispatchers.IO) {
        require(from == null && to == null) { "真实统计接口不支持日期范围参数" }
        ApiParser.parseWorkshopSummary(request("GET", "/api/v1/workshop/summary", null))
    }

    suspend fun workshopMachineProgress(page: Int = 1, pageSize: Int = 20, deviceId: String? = null): MachineProgressPage = withContext(Dispatchers.IO) {
        require(page >= 1 && pageSize == 20) { "机台进度分页固定为 20 条" }
        ApiParser.parseMachineProgressPage(
            request("GET", "/api/v1/workshop/machine-progress?page=$page&pageSize=$pageSize" +
                (deviceId?.takeIf { it.isNotBlank() }?.let { "&deviceId=${encodeQuery(it)}" } ?: ""), null)
        )
    }

    private suspend fun assemblyTaskAction(taskId: String, action: String, operationId: String, expectedVersion: Int?, stage: Int? = null): AssemblyTask = withContext(Dispatchers.IO) {
        require(taskId.isNotBlank()) { "taskId 不能为空" }
        requireUuid(operationId, "clientOperationId")
        require(stage == null || stage in 1..3) { "进度阶段必须为 1、2 或 3" }
        ApiParser.parseAssemblyTask(request("POST", "/api/v1/assembly/tasks/$taskId/$action", JSONObject().apply {
            put("clientOperationId", operationId); expectedVersion?.let { put("expectedVersion", it) }; stage?.let { put("stage", it) }
        }.toString(), idempotencyKey = operationId))
    }

    private suspend fun assemblyLaborAction(taskId: String, action: String, operationId: String, expectedVersion: Int): LaborRecord = withContext(Dispatchers.IO) {
        require(taskId.isNotBlank()) { "taskId 不能为空" }
        requireUuid(operationId, "clientOperationId")
        ApiParser.parseLaborRecord(request("POST", "/api/v1/assembly/tasks/$taskId/$action", JSONObject().apply {
            put("clientOperationId", operationId); put("expectedVersion", expectedVersion)
        }.toString(), idempotencyKey = operationId))
    }

    private fun requireUuid(value: String, field: String) {
        require(runCatching { UUID.fromString(value) }.isSuccess) { "$field 必须是合法 UUID" }
    }



    suspend fun materialInventory(materialCode: String): MaterialInventory = withContext(Dispatchers.IO) {
        ApiParser.parseMaterialInventory(
            request("GET", "/api/v1/materials/$materialCode/inventory", null)
        )
    }

    // ==================== 4.5 入库/出库申请 ====================

    /**
     * 创建流转申请。
     *
     * @param clientOperationId 幂等键，同一键重复提交返回既有记录
     * @param expectedInventoryVersion 乐观锁版本，服务端事务内校验
     */
    suspend fun createTransferRequest(
        clientOperationId: String,
        type: String,
        documentNo: String?,
        items: List<TransferItem>,
        remark: String? = null,
        evidenceIds: List<String> = emptyList()
    ): TransferRequestResult = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId")
        require(type == "INBOUND" || type == "OUTBOUND") { "流转类型无效" }
        require(items.isNotEmpty()) { "流转申请至少包含一个物料" }
        val itemArray = JSONArray()
        items.forEach { item ->
            require(item.materialId.isNotBlank()) { "物料不能为空" }
            require(item.quantity > 0) { "流转数量必须为正整数" }
            require(item.expectedInventoryVersion != null && item.expectedInventoryVersion >= 1) {
                "expectedInventoryVersion 必须为不小于 1 的整数"
            }
            itemArray.put(JSONObject().apply {
                put("materialId", item.materialId)
                put("quantity", item.quantity)
                item.batchNo?.let { put("batchNo", it) }
                item.sourceLocationCode?.let { put("sourceLocationCode", it) }
                item.targetLocationCode?.let { put("targetLocationCode", it) }
                put("expectedInventoryVersion", item.expectedInventoryVersion)
            })
        }
        val body = JSONObject().apply {
            put("clientOperationId", clientOperationId)
            put("type", type)
            put("documentNo", documentNo ?: JSONObject.NULL)
            put("items", itemArray)
            put("remark", remark ?: JSONObject.NULL)
            put("evidenceIds", JSONArray(evidenceIds))
        }
        ApiParser.parseTransferRequest(
            request("POST", "/api/v1/transfer-requests", body.toString(), idempotencyKey = clientOperationId)
        )
    }

    /** 审批入库/出库申请 —— 仅仓库管理员与管理员可操作 */
    suspend fun approveTransferRequest(
        requestId: String,
        approve: Boolean,
        comment: String,
        clientOperationId: String,
        requestIdHeader: String = UUID.randomUUID().toString()
    ): ApprovalDecisionResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("clientOperationId", clientOperationId)
            put("requestId", requestIdHeader)
            put("decision", if (approve) "APPROVE" else "REJECT")
            put("comment", comment)
        }
        ApiParser.parseApprovalDecision(
            request(
                "POST", "/api/v1/transfer-requests/$requestId/approve", body.toString(),
                idempotencyKey = clientOperationId,
            )
        )
    }

    /** 执行已批准的申请 —— 仅在审批通过后允许 */
    suspend fun executeTransferRequest(
        requestId: String,
        clientOperationId: String,
        requestIdHeader: String = UUID.randomUUID().toString()
    ): ApprovalDecisionResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("clientOperationId", clientOperationId)
            put("requestId", requestIdHeader)
        }
        ApiParser.parseApprovalDecision(
            request(
                "POST", "/api/v1/transfer-requests/$requestId/execute", body.toString(),
                idempotencyKey = clientOperationId,
            )
        )
    }

    /** 查询流转申请列表；列表接口由服务端按当前用户权限裁剪，客户端不本地过滤。
     *  服务端分页（每页 100 条）：循环拉取直到取完，保证超过 100 条的申请全部可见。
     *  终止条件取「本页不满一页」与「服务端 total」双保险，兼容未升级分页的老后端。 */
    suspend fun listTransferRequests(status: String? = null): TransferRequestPage = withContext(Dispatchers.IO) {
        val all = mutableListOf<TransferRequest>()
        var serverTime: String? = null
        var page = 1
        while (page <= TRANSFER_REQUEST_MAX_PAGES) {
            val path = buildString {
                append("/api/v1/transfer-requests?page=$page&pageSize=$TRANSFER_REQUEST_PAGE_SIZE")
                if (!status.isNullOrBlank()) append("&status=${encodeQuery(status)}")
            }
            val json = request("GET", path, null)
            val pageResult = ApiParser.parseTransferRequestList(json, status)
            all += pageResult.items
            if (pageResult.serverTime != null) serverTime = pageResult.serverTime
            if (pageResult.items.size < TRANSFER_REQUEST_PAGE_SIZE) break
            val total = JSONObject(json).optInt("total", -1)
            if (total >= 0 && all.size >= total) break
            page++
        }
        TransferRequestPage(items = all, statusFilter = status, serverTime = serverTime)
    }

    /** 查询流转申请详情；详情响应中的 payload 由 parser 投影为明细条目。 */
    suspend fun transferRequestDetail(requestId: String): TransferRequest = withContext(Dispatchers.IO) {
        require(requestId.isNotBlank()) { "requestId 不能为空" }
        ApiParser.parseTransferRequestDetail(
            request("GET", "/api/v1/transfer-requests/${encodeQuery(requestId)}", null)
        )
    }

    /** 管理员只读审计分页；服务端返回多少条就展示多少条，不在客户端聚合全量。 */
    suspend fun auditLogs(page: Int = 1, pageSize: Int = 50): AuditLogPage = withContext(Dispatchers.IO) {
        require(page >= 1) { "page 必须从 1 开始" }
        require(pageSize in 1..100) { "pageSize 必须在 1 到 100 之间" }
        ApiParser.parseAuditLogs(
            request("GET", "/api/v1/audit-logs?page=$page&pageSize=$pageSize", null)
        )
    }

    // ==================== 4.6 库位绑定 ====================

    suspend fun bindLocation(
        materialCode: String,
        locationCode: String,
        quantity: Int,
        evidenceIds: List<String> = emptyList()
    ): BindingResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("materialCode", materialCode)
            put("locationCode", locationCode)
            put("quantity", quantity)
            put("evidenceIds", JSONArray(evidenceIds))
        }
        ApiParser.parseLocationBinding(request("POST", "/api/v1/location-bindings", body.toString()))
    }

    // ==================== 4.7 图片上传 ====================

    /**
     * 上传图片凭证（库位 / 异常 / 流转）。
     * 契约限制：JPEG/PNG/WebP，单张 <= 5MB，单单据最多 9 张。
     */
    suspend fun uploadFile(
        fileBytes: ByteArray,
        fileName: String,
        purpose: String
    ): FileUploadResult = withContext(Dispatchers.IO) {
        val token = requireToken()
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", fileName, fileBytes.toRequestBody("application/octet-stream".toMediaType()))
            .build()
        val httpRequest = Request.Builder()
            .url(fullUrl("/api/v1/files?purpose=$purpose"))
            .header("Authorization", "Bearer $token")
            .post(multipart)
            .build()

        val (code, text) = executeOnce(httpRequest)
        if (code !in 200..299) throw ApiParser.parseError(code, text)
        ApiParser.parseFileUpload(
            text.ifEmpty { throw ApiException(code, "EMPTY_BODY", "上传响应为空", retryable = true) }
        )
    }

    companion object {
        const val MAX_BOM_FILE_BYTES = 10 * 1024 * 1024
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

        /** 流转申请分页拉取：每页大小与服务端上限对齐。 */
        private const val TRANSFER_REQUEST_PAGE_SIZE = 100
        /** 翻页保护：服务端异常时避免无限循环。 */
        private const val TRANSFER_REQUEST_MAX_PAGES = 50
    }

    // ==================== 内部实现 ====================

    private fun requireToken(): String =
        accessToken ?: throw ApiException(401, "NOT_LOGGED_IN", "未登录，请先登录")

    private fun encodeQuery(value: String): String =
        URLEncoder.encode(value, Charsets.UTF_8.name())

    /** 契约：客户端不得硬编码 IP，Base URL 由配置注入 */
    private fun fullUrl(path: String): String = config.baseUrl.trimEnd('/') + path

    /**
     * 同步执行一次 HTTP 调用，返回（状态码，响应体文本）。
     * ResponseBody.string() 读完即关闭；外层 use 确保连接可被连接池复用。
     */
    private fun executeOnce(httpRequest: Request): Pair<Int, String> =
        httpClient.newCall(httpRequest).execute().use { response ->
            response.code to (response.body?.string() ?: "")
        }

    private suspend fun request(
        method: String,
        path: String,
        body: String?,
        auth: Boolean = true,
        idempotencyKey: String? = null,
        allowRetry: Boolean = true,
        requestId: String? = null,
        clientOperationId: String? = null,
    ): String = withContext(Dispatchers.IO) {
        val token = if (auth) requireToken() else null
        val builder = Request.Builder()
            .url(fullUrl(path))
            .header("Accept", "application/json")
            // 契约：所有请求携带 X-Request-Id；写操作额外携带幂等键。
            .header("X-Request-Id", requestId ?: UUID.randomUUID().toString())
        if (auth) {
            builder.header("Authorization", "Bearer $token")
        }
        clientOperationId?.let { builder.header("X-Client-Operation-Id", it) }
        if (method != "GET") {
            builder.header("Idempotency-Key", idempotencyKey ?: UUID.randomUUID().toString())
        }
        // GET 不带 body；DELETE 允许带 body（服务端删除用户接口需要）。
        builder.method(method, body?.toRequestBody(JSON_MEDIA_TYPE))

        val (code, text) = executeOnce(builder.build())

        // access token 过期 → 静默刷新后重试一次。
        // allowRetry 防死循环：刷新后的新令牌若仍 401（如账号被停用），
        // 说明不是时效问题，直接按失败处理。
        if (code == 401 && auth && allowRetry) {
            when (refreshAccessToken()) {
                RefreshOutcome.REFRESHED -> {
                    return@withContext request(
                        method = method,
                        path = path,
                        body = body,
                        auth = auth,
                        idempotencyKey = idempotencyKey,
                        allowRetry = false,
                        requestId = requestId,
                        clientOperationId = clientOperationId,
                    )
                }
                RefreshOutcome.RETRYABLE_FAILURE -> {
                    // Keep the old token pair. A later user retry can attempt refresh again.
                    throw ApiException(
                        statusCode = 503,
                        code = "REFRESH_UNAVAILABLE",
                        message = "网络暂时不可用，请重试",
                        retryable = true,
                    )
                }
                RefreshOutcome.INVALID -> Unit
            }
        }

        if (code !in 200..299) {
            // 401 且无法刷新 → 清空会话并擦除磁盘副本
            if (code == 401) {
                clearSession()
            }
            throw ApiParser.parseError(code, text)
        }
        if (text.isEmpty()) throw ApiException(code, "EMPTY_BODY", "服务端返回空响应", retryable = true)
        text
    }
}

/** 流转申请的物料条目 —— 契约 4.5 items */
data class TransferItem(
    val materialId: String,
    val quantity: Int,
    val batchNo: String? = null,
    val sourceLocationCode: String? = null,
    val targetLocationCode: String? = null,
    val expectedInventoryVersion: Int? = null
)

/** 登录结果别名，避免与 model 层命名冲突 */
typealias LoginResultDto = com.company.logistics.model.LoginResult

/**
 * API 配置。
 * 契约要求：生产环境通过 HTTPS 配置，客户端不得硬编码 IP。
 * 地址一律经 BuildConfig 构建期注入，源码不落任何真实端点。
 *
 * 注入方式（任选其一，均由 app/build.gradle.kts 读取）：
 *   1. 命令行：./gradlew assembleDebug -PapiBaseUrl=https://api.example.com
 *   2. gradle.properties：apiBaseUrl=https://api.example.com
 *   3. 环境变量：API_BASE_URL=https://api.example.com
 *
 * 未注入时回落到占位地址，运行时调用会失败并提示配置，避免误连生产。
 */
object ApiConfig {
    /**
     * 构建期注入的 API 根地址。占位值不含真实端点，
     * 详见 docs/部署配置说明。
     * TODO(部署): 由 CI 注入 HTTPS 正式域名，并移除明文流量许可（AndroidManifest）。
     */
    var baseUrl: String = BuildConfig.API_BASE_URL
    var connectTimeoutMs: Int = 10_000
    var readTimeoutMs: Int = 15_000
}
