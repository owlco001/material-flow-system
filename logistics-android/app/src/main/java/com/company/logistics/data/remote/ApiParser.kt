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
import com.company.logistics.model.WorkspaceHandoverSummary
import com.company.logistics.model.WorkspaceLastHandover
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceResponsibilitySummary
import com.company.logistics.model.WorkspaceSummary
import com.company.logistics.model.HandoverActionResult
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.HandoverTimelineEvent
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
            val serverStatusCode = o.optString("statusCode")
                .takeIf { it.isNotBlank() && it != "null" }
            val label = o.optString("statusLabel")
                .takeIf { it.isNotBlank() && it != "null" }
                ?: o.optString("label")
                    .takeIf { it.isNotBlank() && it != "null" }
                ?: when (serverStatusCode?.uppercase(java.util.Locale.ROOT)) {
                    "OUT_OF_STOCK" -> MaterialStatusCode.OUT_OF_STOCK.label
                    "ARRIVED" -> MaterialStatusCode.ARRIVED.label
                    "IN_STOCK" -> MaterialStatusCode.IN_STOCK.label
                    null -> ""
                    else -> "未知状态"
                }
            items += OrderMaterialItem(
                deviceId = o.optString("deviceId").takeIf { it.isNotBlank() },
                deviceType = o.optString("deviceType").takeIf { it.isNotBlank() },
                deviceNo = o.optString("deviceNo").takeIf { it.isNotBlank() },
                materialId = o.optString("materialId"),
                materialCode = o.optString("materialCode"),
                name = o.optString("materialName").ifBlank { o.optString("name") },
                specification = o.optString("specification").takeIf { it.isNotBlank() },
                requiredQuantity = o.optInt("requiredQuantity"),
                arrivedQuantity = o.optInt("arrivedQuantity"),
                inStockQuantity = o.optInt("inStockQuantity"),
                statusCode = MaterialStatusCode.from(serverStatusCode, label),
                label = label,
                colorToken = o.optString("colorToken"),
                serverStatusCode = serverStatusCode,
                workflowStatusCode = nullableString(o, "workflowStatusCode")
                    ?: nullableString(o, "workStatusCode"),
                workflowStatusLabel = nullableString(o, "workflowStatusLabel")
                    ?: nullableString(o, "workStatusLabel"),
                pickedQuantity = nullableInt(o, "pickedQuantity"),
                issuedQuantity = nullableInt(o, "issuedQuantity"),
                approvalStatusCode = nullableString(o, "approvalStatus"),
                handoverStatusCode = nullableString(o, "handoverStatus"),
                lastHandoverId = nullableString(o, "lastHandoverId"),
                currentOwnerUserId = nullableString(o, "currentOwnerUserId"),
                currentOwnerName = nullableString(o, "currentOwnerName"),
                updatedAt = nullableString(o, "updatedAt")
            )
        }
        return OrderMaterialStatus(
            documentNo = root.optString("documentNo"),
            documentType = root.optString("documentType"),
            items = items,
            serverTime = root.optString("serverTime").takeIf { it.isNotBlank() }
        )
    }

    /** GET /api/v1/workspace/summary —— 指标缺失时保留 null，表示接口未提供。 */
    fun parseWorkspaceSummary(json: String): WorkspaceSummary {
        val root = JSONObject(json)
        val statusCounts = root.optJSONObject("statusCounts")
        return WorkspaceSummary(
            role = UserRole.from(nullableString(root, "role")),
            pendingApprovalCount = nullableInt(root, "pendingApprovalCount"),
            pendingOutboundCount = nullableInt(root, "pendingOutboundCount"),
            outboundConfirmedCount = nullableInt(root, "outboundConfirmedCount")
                ?: nullableInt(statusCounts, "OUTBOUND_CONFIRMED"),
            pendingHandoverCount = nullableInt(root, "pendingHandoverCount"),
            atStationCount = nullableInt(root, "atStationCount"),
            pickedUpCount = nullableInt(root, "pickedUpCount"),
            outOfStockCount = nullableInt(root, "outOfStockCount"),
            generatedAt = nullableString(root, "generatedAt"),
            serverTime = nullableString(root, "serverTime"),
            traceId = nullableString(root, "traceId")
        )
    }

    /** POST /api/v1/handovers 以及确认/驳回/取消响应。 */
    fun parseHandoverAction(json: String): HandoverActionResult {
        val root = JSONObject(json)
        val eventTypes = root.optJSONArray("eventTypes")?.let { array ->
            buildList {
                for (i in 0 until array.length()) add(array.optString(i))
            }
        }.orEmpty()
        return HandoverActionResult(
            handoverId = root.optString("handoverId"),
            status = root.optString("status"),
            transferRequestId = nullableString(root, "transferRequestId"),
            eventTypes = eventTypes,
            traceId = nullableString(root, "traceId"),
            idempotent = root.optBoolean("idempotent", false),
        )
    }

    /** GET /api/v1/handovers/{workItemId}/timeline；只保留客户端内存分页上限。 */
    fun parseHandoverTimeline(json: String, maxItems: Int = 20): HandoverTimeline {
        val root = JSONObject(json)
        val events = mutableListOf<HandoverTimelineEvent>()
        val array = root.optJSONArray("items") ?: JSONArray()
        val startIndex = (array.length() - maxItems).coerceAtLeast(0)
        for (i in startIndex until array.length()) {
            val event = array.getJSONObject(i)
            events += HandoverTimelineEvent(
                id = nullableString(event, "id") ?: "event-$i",
                eventType = nullableString(event, "eventType")
                    ?: nullableString(event, "event_type")
                    ?: "UNKNOWN",
                actorUserId = nullableString(event, "actorUserId")
                    ?: nullableString(event, "actor_user_id"),
                actorRole = nullableString(event, "actorRole")
                    ?: nullableString(event, "actor_role"),
                requestId = nullableString(event, "requestId")
                    ?: nullableString(event, "request_id"),
                clientOperationId = nullableString(event, "clientOperationId")
                    ?: nullableString(event, "client_operation_id"),
                serverTime = nullableString(event, "serverTime")
                    ?: nullableString(event, "server_time"),
                result = nullableString(event, "result"),
            )
        }
        return HandoverTimeline(
            handoverId = root.optString("handoverId"),
            workItemId = root.optString("workItemId"),
            status = root.optString("status"),
            workspaceStatus = root.optString("workspaceStatus"),
            items = events,
            serverTime = nullableString(root, "serverTime"),
        )
    }

    /** GET /api/v1/workspace/material-items —— 仅解析服务端当前页。 */
    fun parseWorkspaceMaterialItems(json: String): WorkspaceMaterialItemsPage {
        val root = JSONObject(json)
        val items = mutableListOf<WorkspaceMaterialItem>()
        val arr = root.optJSONArray("items") ?: JSONArray()
        for (i in 0 until arr.length()) {
            items += parseWorkspaceMaterialItem(arr.getJSONObject(i))
        }
        return WorkspaceMaterialItemsPage(
            items = items,
            page = root.optInt("page", 1),
            pageSize = root.optInt("pageSize", 20),
            total = root.optInt("total", 0),
            totalPages = root.optInt("totalPages", 0),
            serverTime = nullableString(root, "serverTime"),
            traceId = nullableString(root, "traceId")
        )
    }

    private fun parseWorkspaceMaterialItem(json: JSONObject): WorkspaceMaterialItem {
        val responsibility = json.optJSONObject("responsibilitySummary")?.let { summary ->
            WorkspaceResponsibilitySummary(
                assignedUserId = nullableString(summary, "assignedUserId"),
                assignedUserName = nullableString(summary, "assignedUserName"),
                currentOwnerUserId = nullableString(summary, "currentOwnerUserId"),
                currentOwnerName = nullableString(summary, "currentOwnerName")
            )
        }
        val lastHandover = json.optJSONObject("lastHandover")?.let { handover ->
            WorkspaceLastHandover(
                id = handover.optString("id"),
                status = nullableString(handover, "status"),
                quantity = nullableInt(handover, "quantity"),
                fromLocation = nullableString(handover, "fromLocation"),
                deviceId = nullableString(handover, "deviceId"),
                transferRequestId = nullableString(handover, "transferRequestId"),
                senderUserId = nullableString(handover, "senderUserId"),
                senderName = nullableString(handover, "senderName"),
                receiverUserId = nullableString(handover, "receiverUserId"),
                receiverName = nullableString(handover, "receiverName"),
                initiatedAt = nullableString(handover, "initiatedAt"),
                confirmedBy = nullableString(handover, "confirmedBy"),
                confirmedAt = nullableString(handover, "confirmedAt"),
                remark = nullableString(handover, "remark")
            )
        }
        val handoverSummary = json.optJSONObject("handoverSummary")?.let { summary ->
            WorkspaceHandoverSummary(
                lastHandoverId = nullableString(summary, "lastHandoverId"),
                lastStatus = nullableString(summary, "lastStatus"),
                lastInitiatedAt = nullableString(summary, "lastInitiatedAt"),
                lastConfirmedAt = nullableString(summary, "lastConfirmedAt"),
                count = nullableInt(summary, "count")
            )
        }
        return WorkspaceMaterialItem(
            id = json.optString("id"),
            requirementId = nullableString(json, "requirementId"),
            orderNo = json.optString("orderNo"),
            productName = nullableString(json, "productName"),
            orderStatus = nullableString(json, "orderStatus"),
            deviceId = nullableString(json, "deviceId"),
            deviceType = nullableString(json, "deviceType"),
            deviceNo = nullableString(json, "deviceNo"),
            materialId = json.optString("materialId"),
            materialCode = json.optString("materialCode"),
            materialName = json.optString("materialName"),
            specification = nullableString(json, "specification"),
            unit = nullableString(json, "unit"),
            requiredQuantity = nullableInt(json, "requiredQuantity"),
            arrivedQuantity = nullableInt(json, "arrivedQuantity"),
            inStockQuantity = nullableInt(json, "inStockQuantity"),
            issuedQuantity = nullableInt(json, "issuedQuantity"),
            pickedQuantity = nullableInt(json, "pickedQuantity"),
            statusCode = json.optString("statusCode"),
            statusLabel = nullableString(json, "statusLabel")
                ?: nullableString(json, "label")
                ?: "未知状态",
            colorToken = json.optString("colorToken"),
            statusDomain = json.optString("statusDomain"),
            updatedAt = nullableString(json, "updatedAt"),
            assignedUserId = nullableString(json, "assignedUserId"),
            assignedUserName = nullableString(json, "assignedUserName"),
            currentOwnerUserId = nullableString(json, "currentOwnerUserId"),
            currentOwnerName = nullableString(json, "currentOwnerName"),
            responsibilitySummary = responsibility,
            lastHandoverId = nullableString(json, "lastHandoverId"),
            lastHandoverStatus = nullableString(json, "lastHandoverStatus"),
            lastHandover = lastHandover,
            handoverSummary = handoverSummary,
            transferRequestId = nullableString(json, "transferRequestId"),
            transferStatus = nullableString(json, "transferStatus")
        )
    }

    private fun nullableString(json: JSONObject?, key: String): String? =
        json?.optString(key)?.takeIf { it.isNotBlank() && it != "null" }

    private fun nullableInt(json: JSONObject?, key: String): Int? =
        if (json != null && json.has(key) && !json.isNull(key)) json.optInt(key) else null

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
                val error = root.optJSONObject("error") ?: root
                // FastAPI 默认错误为 {"detail": "..."}；契约错误结构为 code/message
                val detail = error.optString("detail")
                val msg = error.optString("message")
                when {
                    msg.isNotBlank() -> message = msg
                    detail.isNotBlank() -> message = detail
                }
                code = error.optString("code").ifBlank { code }
                traceId = error.optString("traceId").takeIf { it.isNotBlank() }
                if (error.has("retryable")) retryable = error.optBoolean("retryable")
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
