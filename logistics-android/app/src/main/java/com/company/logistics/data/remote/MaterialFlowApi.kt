package com.company.logistics.data.remote

import com.company.logistics.BuildConfig
import com.company.logistics.model.MaterialInventory
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
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.MachineProgressPage
import com.company.logistics.model.WorkshopProgressSummary
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.UUID

/**
 * 物料流转 API 客户端 —— 严格对齐《V1 需求冻结与接口契约》第 4 节。
 *
 * 契约约束（第 5 节）：
 *  - 所有写操作需携带 Authorization / Idempotency-Key / X-Request-Id；
 *  - 数量为整数，禁止小数、负数、科学计数法；
 *  - 客户端不得硬编码 IP，Base URL 由配置注入（见 [ApiConfig]）。
 *
 * 说明：V1 使用 HttpURLConnection + org.json，保持与既有工程一致的零额外依赖策略；
 *      如需替换为 Retrofit/OkHttp，只需保持本类的方法签名不变。
 */
open class MaterialFlowApi(
    private val config: ApiConfig = ApiConfig
) {

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
            val conn = openConnection("/api/v1/auth/refresh", "POST")
            try {
                val body = JSONObject().apply {
                    put("refreshToken", token)
                    put("deviceId", deviceId)
                }
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.setRequestProperty("X-Request-Id", UUID.randomUUID().toString())
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }

                val (code, text) = readResponse(conn)
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
                val json = JSONObject(text ?: "")
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
            } finally {
                // readResponse disconnects on its normal path; this also covers write/parse failures.
                conn.disconnect()
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
            put("username", username)
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

    /** 登出：吊销当前设备的 access 与 refresh 令牌 */
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

    /** Assembly task list. The server applies the assembler assignment scope. */
    suspend fun assemblyTasks(page: Int = 1, pageSize: Int = 20): List<AssemblyTask> =
        assemblyTaskPage(page, pageSize).items

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

    suspend fun startTemporaryTransfer(taskId: String?, remark: String, clientOperationId: String): LaborRecord = withContext(Dispatchers.IO) {
        requireUuid(clientOperationId, "clientOperationId")
        require(remark.length in 1..500) { "临时调拨备注长度必须为 1~500 字符" }
        ApiParser.parseTemporaryTransfer(request("POST", "/api/v1/assembly/temporary-transfers/start", JSONObject().apply {
            put("taskId", taskId ?: JSONObject.NULL); put("remark", remark); put("clientOperationId", clientOperationId)
        }.toString(), idempotencyKey = clientOperationId))
    }

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

    suspend fun workshopMachineProgress(page: Int = 1, pageSize: Int = 20): MachineProgressPage = withContext(Dispatchers.IO) {
        require(page >= 1 && pageSize == 20) { "机台进度分页固定为 20 条" }
        ApiParser.parseMachineProgressPage(
            request("GET", "/api/v1/workshop/machine-progress?page=$page&pageSize=$pageSize", null)
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
        val itemArray = JSONArray()
        items.forEach { item ->
            itemArray.put(JSONObject().apply {
                put("materialId", item.materialId)
                put("quantity", item.quantity)
                item.batchNo?.let { put("batchNo", it) }
                item.sourceLocationCode?.let { put("sourceLocationCode", it) }
                item.targetLocationCode?.let { put("targetLocationCode", it) }
                item.expectedInventoryVersion?.let { put("expectedInventoryVersion", it) }
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

    /** 查询流转申请列表；列表接口由服务端按当前用户权限裁剪，客户端不本地过滤。 */
    suspend fun listTransferRequests(status: String? = null): TransferRequestPage = withContext(Dispatchers.IO) {
        val path = if (status.isNullOrBlank()) "/api/v1/transfer-requests"
        else "/api/v1/transfer-requests?status=${encodeQuery(status)}"
        ApiParser.parseTransferRequestList(request("GET", path, null), status)
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
        val boundary = "----LogisticsBoundary${UUID.randomUUID().toString().replace("-", "")}"
        val conn = openConnection("/api/v1/files?purpose=$purpose", "POST")
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        conn.setRequestProperty("Authorization", "Bearer $token")
        conn.doOutput = true

        conn.outputStream.use { out ->
            fun write(s: String) = out.write(s.toByteArray(Charsets.UTF_8))
            write("--$boundary\r\n")
            write("Content-Disposition: form-data; name=\"file\"; filename=\"$fileName\"\r\n")
            write("Content-Type: application/octet-stream\r\n\r\n")
            out.write(fileBytes)
            write("\r\n--$boundary--\r\n")
        }

        val (code, text) = readResponse(conn)
        if (code !in 200..299) throw ApiParser.parseError(code, text)
        ApiParser.parseFileUpload(
            text ?: throw ApiException(code, "EMPTY_BODY", "上传响应为空", retryable = true)
        )
    }

    // ==================== 内部实现 ====================

    private fun requireToken(): String =
        accessToken ?: throw ApiException(401, "NOT_LOGGED_IN", "未登录，请先登录")

    private fun encodeQuery(value: String): String =
        URLEncoder.encode(value, Charsets.UTF_8.name())

    private fun openConnection(path: String, method: String): HttpURLConnection {
        // 契约：客户端不得硬编码 IP，Base URL 由配置注入
        val url = URL(config.baseUrl.trimEnd('/') + path)
        return (url.openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = config.connectTimeoutMs
            readTimeout = config.readTimeoutMs
            setRequestProperty("Accept", "application/json")
        }
    }

    private fun readResponse(conn: HttpURLConnection): Pair<Int, String?> {
        return try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText)
            code to text
        } finally {
            conn.disconnect()
        }
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
        val conn = openConnection(path, method)

        if (auth) {
            conn.setRequestProperty("Authorization", "Bearer $token")
        }
        // 契约：所有请求携带 X-Request-Id；写操作额外携带幂等键。
        conn.setRequestProperty("X-Request-Id", requestId ?: UUID.randomUUID().toString())
        clientOperationId?.let { conn.setRequestProperty("X-Client-Operation-Id", it) }
        if (method != "GET") {
            conn.setRequestProperty("Idempotency-Key", idempotencyKey ?: UUID.randomUUID().toString())
        }
        if (body != null) {
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        }

        val (code, text) = readResponse(conn)

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
        text ?: throw ApiException(code, "EMPTY_BODY", "服务端返回空响应", retryable = true)
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
