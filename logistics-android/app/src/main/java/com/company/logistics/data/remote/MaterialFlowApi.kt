package com.company.logistics.data.remote

import com.company.logistics.BuildConfig
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.ScanResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
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
class MaterialFlowApi(
    private val config: ApiConfig = ApiConfig
) {

    /** 会话 token，由登录写入；为空表示未登录 */
    @Volatile
    var accessToken: String? = null
        private set

    fun updateToken(token: String?) {
        accessToken = token
    }

    // ==================== 4.1 登录 ====================

    suspend fun login(
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
        result
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

    // ==================== 4.4 料号库存 ====================

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
        comment: String
    ): ApprovalDecisionResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("decision", if (approve) "APPROVE" else "REJECT")
            put("comment", comment)
        }
        ApiParser.parseApprovalDecision(
            request("POST", "/api/v1/transfer-requests/$requestId/approve", body.toString())
        )
    }

    /** 执行已批准的申请 —— 仅在审批通过后允许 */
    suspend fun executeTransferRequest(requestId: String): ApprovalDecisionResult =
        withContext(Dispatchers.IO) {
            ApiParser.parseApprovalDecision(
                request("POST", "/api/v1/transfer-requests/$requestId/execute", "{}")
            )
        }

    /** 查询流转申请列表 */
    suspend fun listTransferRequests(status: String? = null): String = withContext(Dispatchers.IO) {
        val path = if (status.isNullOrBlank()) "/api/v1/transfer-requests"
        else "/api/v1/transfer-requests?status=$status"
        request("GET", path, null)
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
        val boundary = "----LogisticsBoundary${UUID.randomUUID().toString().replace("-", "")}"
        val conn = openConnection("/api/v1/files?purpose=$purpose", "POST")
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
        conn.setRequestProperty("Authorization", "Bearer ${requireToken()}")
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
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use(BufferedReader::readText)
        conn.disconnect()
        return code to text
    }

    private suspend fun request(
        method: String,
        path: String,
        body: String?,
        auth: Boolean = true,
        idempotencyKey: String? = null
    ): String = withContext(Dispatchers.IO) {
        val conn = openConnection(path, method)

        if (auth) {
            conn.setRequestProperty("Authorization", "Bearer ${requireToken()}")
        }
        // 契约第 5 节：所有写操作需携带 Idempotency-Key 与 X-Request-Id
        if (method != "GET") {
            conn.setRequestProperty("X-Request-Id", UUID.randomUUID().toString())
            conn.setRequestProperty("Idempotency-Key", idempotencyKey ?: UUID.randomUUID().toString())
        }
        if (body != null) {
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        }

        val (code, text) = readResponse(conn)
        if (code !in 200..299) {
            // 401 时清空会话，触发重新登录
            if (code == 401) accessToken = null
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
