package com.company.logistics.data.remote

import com.company.logistics.model.ApprovalStatus
import com.company.logistics.model.FlowStatus
import com.company.logistics.model.FlowType
import com.company.logistics.model.Inventory
import com.company.logistics.model.LocationStock
import com.company.logistics.model.LoginResult
import com.company.logistics.model.Material
import com.company.logistics.model.MaterialInventory
import com.company.logistics.model.MaterialStatusCode
import com.company.logistics.model.OrderMaterialItem
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.ScanResult
import com.company.logistics.model.ScanType
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import org.json.JSONArray
import org.json.JSONObject

/**
 * 接口响应解析器 —— 严格对齐《V1 需求冻结与接口契约》第 4 节。
 *
 * 约定：
 *  - 服务端返回的 statusCode / label / colorToken 为准，客户端不推导状态；
 *  - 数量为整数，解析时统一按 Int 处理；
 *  - 解析失败抛 [ApiException]，由上层统一转换为可展示的错误文案。
 */
object ApiParser {

    fun parseLogin(json: String): LoginResult {
        val root = JSONObject(json)
        val u = root.getJSONObject("user")
        return LoginResult(
            accessToken = root.getString("accessToken"),
            refreshToken = root.optString("refreshToken").takeIf { it.isNotBlank() },
            expiresAt = root.optString("expiresAt").takeIf { it.isNotBlank() },
            mustChangePassword = root.optBoolean("mustChangePassword", false),
            user = User(
                id = u.optString("id"),
                username = u.optString("username"),
                displayName = u.optString("displayName"),
                role = UserRole.from(u.optString("role"))
            )
        )
    }

    /** 契约 4.2 扫码解析 —— 已移除 LOGISTICS_NO 语义 */
    fun parseScanResolve(json: String): ScanResult {
        val root = JSONObject(json)
        return ScanResult(
            type = ScanType.from(root.optString("type")),
            normalizedValue = root.optString("normalizedValue"),
            resourceId = root.optString("resourceId").takeIf { it.isNotBlank() }
        )
    }

    /** 契约 4.4 料号库存 */
    fun parseMaterialInventory(json: String): MaterialInventory {
        val root = JSONObject(json)
        val m = root.getJSONObject("material")
        val inv = root.getJSONObject("inventory")

        val locations = mutableListOf<LocationStock>()
        val arr: JSONArray = inv.optJSONArray("locations") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            locations += LocationStock(
                locationCode = o.optString("locationCode"),
                quantity = o.optInt("quantity")
            )
        }

        return MaterialInventory(
            material = Material(
                id = m.optString("id"),
                code = m.optString("code"),
                name = m.optString("name"),
                specification = m.optString("specification").takeIf { it.isNotBlank() && it != "null" },
                unit = m.optString("unit").ifBlank { "件" },
                batchNo = m.optString("batchNo").takeIf { it.isNotBlank() && it != "null" },
                expiryDate = m.optString("expiryDate").takeIf { it.isNotBlank() && it != "null" }
            ),
            inventory = Inventory(
                totalQuantity = inv.optInt("totalQuantity"),
                availableQuantity = inv.optInt("availableQuantity"),
                reservedQuantity = inv.optInt("reservedQuantity"),
                locations = locations
            ),
            version = root.optInt("version", 1)
        )
    }

    /** 契约 4.3 生产订单物料状态 */
    fun parseOrderMaterialStatus(json: String): OrderMaterialStatus {
        val root = JSONObject(json)
        val items = mutableListOf<OrderMaterialItem>()
        val arr = root.optJSONArray("items") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            val label = o.optString("label")
            items += OrderMaterialItem(
                deviceId = o.optString("deviceId").takeIf { it.isNotBlank() },
                deviceType = o.optString("deviceType").takeIf { it.isNotBlank() },
                deviceNo = o.optString("deviceNo").takeIf { it.isNotBlank() },
                materialId = o.optString("materialId"),
                materialCode = o.optString("materialCode"),
                name = o.optString("name"),
                requiredQuantity = o.optInt("requiredQuantity"),
                arrivedQuantity = o.optInt("arrivedQuantity"),
                inStockQuantity = o.optInt("inStockQuantity"),
                statusCode = MaterialStatusCode.from(o.optString("statusCode"), label),
                label = label,
                colorToken = o.optString("colorToken")
            )
        }
        return OrderMaterialStatus(
            documentNo = root.optString("documentNo"),
            documentType = root.optString("documentType"),
            items = items,
            serverTime = root.optString("serverTime").takeIf { it.isNotBlank() }
        )
    }

    /** 契约 4.5 创建流转申请 —— 返回 requestId / status */
    fun parseTransferRequest(json: String): TransferRequestResult {
        val root = JSONObject(json)
        return TransferRequestResult(
            requestId = root.optString("requestId"),
            status = ApprovalStatus.from(root.optString("status")),
            serverTime = root.optString("serverTime").takeIf { it.isNotBlank() },
            idempotent = root.optBoolean("idempotent", false)
        )
    }

    /** 契约 4.5 审批结果 */
    fun parseApprovalDecision(json: String): ApprovalDecisionResult {
        val root = JSONObject(json)
        return ApprovalDecisionResult(
            requestId = root.optString("requestId"),
            status = ApprovalStatus.from(root.optString("status"))
        )
    }

    /** 库位绑定结果 —— 契约 4.6 */
    fun parseLocationBinding(json: String): BindingResult {
        val root = JSONObject(json)
        return BindingResult(
            bindingId = root.optString("bindingId"),
            status = root.optString("status"),
            serverTime = root.optString("serverTime").takeIf { it.isNotBlank() }
        )
    }

    /** 图片上传结果 —— 契约 4.7 */
    fun parseFileUpload(json: String): FileUploadResult {
        val root = JSONObject(json)
        return FileUploadResult(
            fileId = root.optString("fileId"),
            purpose = root.optString("purpose"),
            size = root.optInt("size"),
            sha256 = root.optString("sha256")
        )
    }

    /**
     * 契约 5 统一错误结构：code / message / traceId / retryable / details
     */
    fun parseError(statusCode: Int, body: String?): ApiException {
        var message = "请求失败（HTTP $statusCode）"
        var code = "HTTP_$statusCode"
        var traceId: String? = null
        var retryable = statusCode >= 500

        if (!body.isNullOrBlank()) {
            runCatching {
                val root = JSONObject(body)
                // FastAPI 默认错误为 {"detail": "..."}；契约错误结构为 code/message
                val detail = root.optString("detail")
                val msg = root.optString("message")
                when {
                    msg.isNotBlank() -> message = msg
                    detail.isNotBlank() -> message = detail
                }
                code = root.optString("code").ifBlank { code }
                traceId = root.optString("traceId").takeIf { it.isNotBlank() }
                if (root.has("retryable")) retryable = root.optBoolean("retryable")
            }
        }
        return ApiException(statusCode, code, message, traceId, retryable)
    }
}

/** 创建流转申请结果 */
data class TransferRequestResult(
    val requestId: String,
    val status: ApprovalStatus,
    val serverTime: String?,
    /** 幂等命中：服务端已存在同一 clientOperationId 的记录 */
    val idempotent: Boolean
)

/** 审批结果 */
data class ApprovalDecisionResult(
    val requestId: String,
    val status: ApprovalStatus
)

/** 库位绑定结果 */
data class BindingResult(
    val bindingId: String,
    val status: String,
    val serverTime: String?
)

/** 文件上传结果 */
data class FileUploadResult(
    val fileId: String,
    val purpose: String,
    val size: Int,
    val sha256: String
)

/**
 * 统一 API 异常。
 * 携带契约错误结构字段，便于 UI 决定是否展示「重试」。
 */
class ApiException(
    val statusCode: Int,
    val code: String,
    override val message: String,
    val traceId: String? = null,
    val retryable: Boolean = false
) : Exception(message) {

    /** 会话失效，需要重新登录 */
    val isUnauthorized: Boolean get() = statusCode == 401
    /** 权限不足 */
    val isForbidden: Boolean get() = statusCode == 403
    /** 业务冲突（如库存不足、状态不允许） */
    val isConflict: Boolean get() = statusCode == 409
}
