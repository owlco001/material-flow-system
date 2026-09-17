package com.company.logistics.data.remote

import com.company.logistics.model.ApprovalStatus
import com.company.logistics.model.AuditLog
import com.company.logistics.model.AuditLogPage
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
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestItem
import com.company.logistics.model.TransferRequestPage
import com.company.logistics.model.HandoverActionResult
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.HandoverTimelineEvent
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.MachineProgress
import com.company.logistics.model.MachineProgressPage
import com.company.logistics.model.AssemblyTaskPage
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.AssemblyTaskStatus
import com.company.logistics.model.AssemblyMember
import com.company.logistics.model.AssemblyAssignmentResponse
import com.company.logistics.model.AssemblyStage
import com.company.logistics.model.AssemblyStageStatus
import com.company.logistics.model.AssemblyStageOperationResult
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.LaborType
import com.company.logistics.model.LaborSummaryItem
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.OrderDetail
import com.company.logistics.model.OrderDetailLaborSummary
import com.company.logistics.model.OrderDetailMaterialSummary
import com.company.logistics.model.OrderDetailMaterialSummaryItem
import com.company.logistics.model.OrderDetailTimelineEvent
import com.company.logistics.model.ExceptionSubmissionResult
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

    fun parseSetupStatus(json: String): SetupStatusDto {
        val root = JSONObject(json)
        return SetupStatusDto(root.optBoolean("initialized"), root.optString("admin_username", root.optString("adminUsername")), root.optBoolean("must_change_password", root.optBoolean("mustChangePassword")), root.optString("server_time", root.optString("serverTime")))
    }

    fun parseInitializeAdmin(json: String): InitializeAdminResponseDto {
        val root = JSONObject(json)
        return InitializeAdminResponseDto(root.optBoolean("initialized"), root.optString("username"), root.optBoolean("mustChangePassword", root.optBoolean("must_change_password")), root.optString("serverTime", root.optString("server_time")), root.optString("traceId", root.optString("trace_id")))
    }

    fun parseDeleteUser(json: String): DeleteUserResult {
        val root = JSONObject(json)
        return DeleteUserResult(
            userId = root.optString("userId", root.optString("user_id")),
            status = root.optString("status"),
            idempotent = root.optBoolean("idempotent", false),
            serverTime = nullableStringAny(root, "serverTime", "server_time"),
            traceId = nullableStringAny(root, "traceId", "trace_id"),
        )
    }

    private fun parseOrderDetailTask(o: JSONObject): AssemblyTask = AssemblyTask(
        id = o.optString("taskId"), orderNo = "", deviceId = o.optString("deviceId"), deviceNo = o.optString("deviceNo"),
        materialSummary = "", status = AssemblyTaskStatus.from(o.optString("status")), progressStage = o.optInt("progressStage"),
        taskVersion = o.optInt("taskVersion"), currentLaborRecordId = null, currentLaborStartedAt = null,
        accumulatedLaborMinutes = null, assignedAssemblerId = nullableString(o, "assignedAssemblerId"), assignedAssemblerName = null
    )
    fun parseOrderDetail(json: String): OrderDetail {
        val root = JSONObject(json)
        val materials = buildList {
            val array = root.optJSONArray("materials") ?: JSONArray()
            for (i in 0 until array.length()) {
                val o = array.getJSONObject(i)
                val code = nullableString(o, "statusCode")
                val label = nullableString(o, "statusLabel") ?: ""
                add(OrderMaterialItem(
                    deviceId = nullableString(o, "deviceId"), deviceType = nullableString(o, "deviceType"), deviceNo = nullableString(o, "deviceNo"),
                    materialId = o.optString("materialId"), materialCode = o.optString("materialCode"), name = o.optString("materialName"),
                    specification = nullableString(o, "specification"), requiredQuantity = o.optInt("requiredQuantity"),
                    arrivedQuantity = o.optInt("arrivedQuantity"), inStockQuantity = o.optInt("inStockQuantity"),
                    statusCode = MaterialStatusCode.from(code, label), label = label, colorToken = o.optString("colorToken"), serverStatusCode = code
                ))
            }
        }
        val tasks = root.optJSONArray("assemblyTasks")?.let { arr -> buildList { for (i in 0 until arr.length()) add(parseOrderDetailTask(arr.getJSONObject(i))) } }.orEmpty()
        val labor = root.optJSONObject("laborSummary") ?: JSONObject()
        val materialSummary = root.optJSONObject("materialSummary")?.let { summary ->
            val items = buildList {
                val array = summary.optJSONArray("items") ?: JSONArray()
                for (i in 0 until array.length()) {
                    val item = array.optJSONObject(i) ?: continue
                    add(OrderDetailMaterialSummaryItem(
                        materialId = item.optString("materialId"),
                        materialCode = item.optString("materialCode"),
                        materialName = item.optString("materialName"),
                        unit = item.optString("unit"),
                        requiredQuantity = item.optInt("requiredQuantity"),
                        arrivedQuantity = item.optInt("arrivedQuantity"),
                        inStockQuantity = item.optInt("inStockQuantity"),
                        shortageQuantity = item.optInt("shortageQuantity"),
                        statusCode = item.optString("statusCode"),
                        statusLabel = item.optString("statusLabel"),
                    ))
                }
            }
            OrderDetailMaterialSummary(
                items = items,
                totalMaterialTypes = summary.optInt("totalMaterialTypes"),
                totalRequiredQuantity = summary.optInt("totalRequiredQuantity"),
                totalArrivedQuantity = summary.optInt("totalArrivedQuantity"),
                totalInStockQuantity = summary.optInt("totalInStockQuantity"),
                totalShortageQuantity = summary.optInt("totalShortageQuantity"),
            )
        } ?: OrderDetailMaterialSummary()
        val timeline = root.optJSONArray("timeline")?.let { arr -> buildList { for (i in 0 until arr.length()) { val e = arr.getJSONObject(i); add(OrderDetailTimelineEvent(e.optString("type"), e.optString("entityId"), nullableString(e, "status"), nullableString(e, "serverTime"), nullableString(e, "actorId"))) } } }.orEmpty()
        return OrderDetail(
            orderId = root.optString("orderId"), orderNo = root.optString("orderNo"), productName = nullableString(root, "productName"), orderStatus = nullableString(root, "orderStatus"),
            materials = materials, assemblyTasks = tasks,
            laborSummary = OrderDetailLaborSummary(labor.optInt("assemblyLaborMinutes"), labor.optInt("temporaryTransferLaborMinutes"), labor.optInt("totalLaborMinutes")),
            timeline = timeline, page = root.optInt("page", 1), pageSize = root.optInt("pageSize", 20), total = root.optInt("total", tasks.size),
            materialSummary = materialSummary
        )
    }

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

    fun parseExceptionSubmission(json: String): ExceptionSubmissionResult {
        val root = JSONObject(json)
        return ExceptionSubmissionResult(
            exceptionId = root.optString("exceptionId"),
            status = root.optString("status"),
            difference = root.optInt("difference"),
            serverTime = nullableStringAny(root, "serverTime", "server_time"),
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
                unit = o.optString("unit").ifBlank { "件" },
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
            serverTime = root.optString("serverTime").takeIf { it.isNotBlank() },
            orderId = nullableString(root, "orderId"),
            productName = nullableString(root, "productName"),
            orderStatus = nullableString(root, "orderStatus")
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
            traceId = nullableString(root, "traceId"),
            preview = root.optBoolean("preview", false),
            authenticatedRole = UserRole.from(nullableString(root, "authenticatedRole"))
                .takeIf { nullableString(root, "authenticatedRole") != null },
            totalLaborMinutes = nullableInt(root, "totalLaborMinutes")
                ?: nullableInt(root, "total_labor_minutes"),
            assemblyLaborMinutes = nullableInt(root, "assemblyLaborMinutes")
                ?: nullableInt(root, "assembly_labor_minutes"),
            temporaryTransferLaborMinutes = nullableInt(root, "temporaryTransferLaborMinutes")
                ?: nullableInt(root, "temporary_transfer_labor_minutes"),
            overallProgressPercent = nullableInt(root, "overallProgressPercent")
                ?: nullableInt(root, "overall_progress_percent"),
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
            traceId = nullableString(root, "traceId"),
            preview = root.optBoolean("preview", false),
            authenticatedRole = UserRole.from(nullableString(root, "authenticatedRole"))
                .takeIf { nullableString(root, "authenticatedRole") != null },
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

    /** GET /api/v1/transfer-requests；后端当前返回最多 100 条，不在客户端拼接全量。 */
    fun parseTransferRequestList(json: String, statusFilter: String? = null): TransferRequestPage {
        val root = JSONObject(json)
        val items = mutableListOf<TransferRequest>()
        val array = root.optJSONArray("items") ?: JSONArray()
        for (i in 0 until array.length()) {
            items += parseTransferRequest(array.getJSONObject(i))
        }
        return TransferRequestPage(
            items = items,
            statusFilter = statusFilter,
            serverTime = nullableStringAny(root, "serverTime", "server_time"),
            traceId = nullableStringAny(root, "traceId", "trace_id"),
        )
    }

    /** 别名供调用方按“分页/列表”语义读取同一个响应。 */
    fun parseTransferRequestPage(json: String, statusFilter: String? = null): TransferRequestPage =
        parseTransferRequestList(json, statusFilter)

    fun parseTransferRequests(json: String, statusFilter: String? = null): TransferRequestPage =
        parseTransferRequestList(json, statusFilter)

    /** GET /api/v1/transfer-requests/{requestId}；详情只投影安全可展示字段。 */
    fun parseTransferRequestDetail(json: String): TransferRequest =
        parseTransferRequest(JSONObject(json))

    private fun parseTransferRequest(json: JSONObject): TransferRequest {
        val payload = nullableStringAny(json, "payloadJson", "payload_json")?.let { raw ->
            runCatching { JSONObject(raw) }.getOrNull()
        }
        val itemsArray = json.optJSONArray("items") ?: payload?.optJSONArray("items") ?: JSONArray()
        val items = buildList {
            for (i in 0 until itemsArray.length()) {
                val item = itemsArray.optJSONObject(i) ?: continue
                add(
                    TransferRequestItem(
                        materialId = nullableStringAny(item, "materialId", "material_id").orEmpty(),
                        quantity = item.optInt("quantity"),
                        batchNo = nullableStringAny(item, "batchNo", "batch_no"),
                        sourceLocationCode = nullableStringAny(
                            item, "sourceLocationCode", "source_location_code"
                        ),
                        targetLocationCode = nullableStringAny(
                            item, "targetLocationCode", "target_location_code"
                        ),
                        expectedInventoryVersion = nullableIntAny(
                            item, "expectedInventoryVersion", "expected_inventory_version"
                        ),
                    )
                )
            }
        }
        return TransferRequest(
            id = nullableStringAny(json, "id", "requestId", "request_id").orEmpty(),
            clientOperationId = nullableStringAny(
                json, "clientOperationId", "client_operation_id"
            ),
            type = nullableStringAny(json, "type").orEmpty(),
            documentNo = nullableStringAny(json, "documentNo", "document_no")
                ?: nullableStringAny(payload, "documentNo", "document_no"),
            status = nullableStringAny(json, "status", "statusCode", "status_code").orEmpty(),
            statusLabel = nullableStringAny(json, "statusLabel", "status_label"),
            items = items,
            remark = nullableStringAny(json, "remark") ?: nullableStringAny(payload, "remark"),
            createdBy = nullableStringAny(json, "createdBy", "created_by"),
            createdAt = nullableStringAny(json, "createdAt", "created_at"),
            approvedBy = nullableStringAny(json, "approvedBy", "approved_by"),
            approvedAt = nullableStringAny(json, "approvedAt", "approved_at"),
            executedAt = nullableStringAny(json, "executedAt", "executed_at"),
            serverTime = nullableStringAny(json, "serverTime", "server_time"),
            traceId = nullableStringAny(json, "traceId", "trace_id"),
        )
    }

    /** GET /api/v1/audit-logs；兼容后端 snake_case 数据库投影和契约 camelCase 字段。 */
    fun parseAuditLogs(json: String): AuditLogPage {
        val root = JSONObject(json)
        val page = root.optInt("page", 1).coerceAtLeast(1)
        val pageSize = root.optInt("pageSize", 50).coerceIn(1, 100)
        val array = root.optJSONArray("items") ?: JSONArray()
        val items = buildList {
            for (i in 0 until array.length()) {
                val item = array.optJSONObject(i) ?: continue
                add(
                    AuditLog(
                        id = longValue(item, "id"),
                        operatorId = nullableStringAny(item, "operatorId", "operator_id"),
                        role = nullableStringAny(item, "role"),
                        action = nullableStringAny(item, "action").orEmpty(),
                        resourceType = nullableStringAny(
                            item, "resourceType", "resource_type"
                        ).orEmpty(),
                        resourceId = nullableStringAny(item, "resourceId", "resource_id"),
                        requestId = nullableStringAny(item, "requestId", "request_id"),
                        deviceId = nullableStringAny(item, "deviceId", "device_id"),
                        occurredAt = nullableStringAny(item, "occurredAt", "occurred_at"),
                        result = nullableStringAny(item, "result").orEmpty(),
                    )
                )
            }
        }
        val hasNext = if (root.has("hasNext") && !root.isNull("hasNext")) {
            root.optBoolean("hasNext")
        } else {
            // 当前后端没有 total；满页意味着可能仍有下一页，避免一次拉取全量审计。
            items.size >= pageSize && items.isNotEmpty()
        }
        return AuditLogPage(
            items = items,
            page = page,
            pageSize = pageSize,
            hasNext = hasNext,
            serverTime = nullableStringAny(root, "serverTime", "server_time"),
            traceId = nullableStringAny(root, "traceId", "trace_id"),
        )
    }

    fun parseAuditLogPage(json: String): AuditLogPage = parseAuditLogs(json)

    private fun nullableString(json: JSONObject?, key: String): String? =
        json?.optString(key)?.takeIf { it.isNotBlank() && it != "null" }

    private fun nullableStringAny(json: JSONObject?, vararg keys: String): String? =
        keys.asSequence().mapNotNull { key -> nullableString(json, key) }.firstOrNull()

    private fun nullableInt(json: JSONObject?, key: String): Int? =
        if (json != null && json.has(key) && !json.isNull(key)) json.optInt(key) else null

    private fun nullableIntAny(json: JSONObject?, vararg keys: String): Int? =
        keys.asSequence().mapNotNull { key -> nullableInt(json, key) }.firstOrNull()

    private fun longValue(json: JSONObject, key: String): Long = when (val value = json.opt(key)) {
        is Number -> value.toLong()
        else -> value?.toString()?.toLongOrNull() ?: 0L
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
            status = ApprovalStatus.from(root.optString("status")),
            idempotent = root.optBoolean("idempotent", false),
            serverTime = nullableStringAny(root, "serverTime", "server_time"),
            traceId = nullableStringAny(root, "traceId", "trace_id"),
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

    /** C14 GET labor summary endpoints. Exact camelCase contract fields; absent minutes stay null. */
    fun parseLaborSummary(json: String): LaborSummaryPage {
        val root = JSONObject(json)
        fun item(o: JSONObject): LaborSummaryItem = LaborSummaryItem(
            taskId = nullableString(o, "taskId"), orderNo = nullableString(o, "orderNo"),
            deviceId = nullableString(o, "deviceId"), deviceNo = nullableString(o, "deviceNo"),
            assemblerId = nullableString(o, "assemblerId"), assemblerName = nullableString(o, "assemblerName"),
            assemblyLaborMinutes = nullableInt(o, "assemblyLaborMinutes"),
            temporaryTransferLaborMinutes = nullableInt(o, "temporaryTransferLaborMinutes"),
            totalLaborMinutes = nullableInt(o, "totalLaborMinutes"),
        )
        val items = buildList {
            val array = root.optJSONArray("items") ?: JSONArray()
            for (i in 0 until array.length()) array.optJSONObject(i)?.let { add(item(it)) }
        }
        val task = root.optJSONObject("task")?.let(::item)
        return LaborSummaryPage(items, root.optInt("page", 1), root.optInt("pageSize", 20), nullableInt(root, "total"), task)
    }

    fun parseTaskLaborSummary(json: String): LaborSummaryPage = parseLaborSummary(json)


    fun parseAssemblyTaskPage(json: String): AssemblyTaskPage {
        val root = JSONObject(json)
        val array = root.optJSONArray("items") ?: JSONArray()
        val items = buildList { for (i in 0 until array.length()) add(parseAssemblyTask(array.getJSONObject(i))) }
        return AssemblyTaskPage(
            items = items,
            page = root.optInt("page", 1),
            pageSize = root.optInt("pageSize", 20),
            total = root.optInt("total", items.size),
            totalPages = root.optInt("totalPages", if (items.isEmpty()) 0 else 1),
            serverTime = nullableStringAny(root, "serverTime", "generatedAt"),
        )
    }

    fun parseAssemblyTask(json: String): AssemblyTask = parseAssemblyTask(JSONObject(json))

    fun parseAssemblyAssignmentResponse(json: String): AssemblyAssignmentResponse {
        val root = JSONObject(json)
        val array = root.optJSONArray("members") ?: JSONArray()
        val members = (0 until array.length()).mapNotNull { i -> array.optJSONObject(i)?.let { member ->
            AssemblyMember(nullableStringAny(member, "assemblerId", "assembler_id").orEmpty(), member.optString("assignmentRole", member.optString("assignment_role")), nullableStringAny(member, "assignedBy", "assigned_by"), nullableStringAny(member, "assignedAt", "assigned_at"), nullableStringAny(member, "removedAt", "removed_at"))
        } }
        return AssemblyAssignmentResponse(nullableStringAny(root, "taskId", "task_id").orEmpty(), members, nullableStringAny(root, "traceId", "trace_id"), root.optBoolean("idempotent", false), nullableStringAny(root, "serverTime", "server_time"))
    }

    private fun parseAssemblyTask(o: JSONObject): AssemblyTask = AssemblyTask(
        id = o.optString("id"), orderNo = o.optString("orderNo", o.optString("order_no")),
        deviceId = o.optString("deviceId", o.optString("device_id")), deviceNo = o.optString("deviceNo", o.optString("device_no")),
        materialSummary = o.optString("materialSummary", o.optString("material_summary")),
        status = AssemblyTaskStatus.from(o.optString("status")), progressStage = o.optInt("progressStage", o.optInt("progress_stage", 0)),
        taskVersion = o.optInt("taskVersion", o.optInt("task_version", 1)), currentLaborRecordId = nullableStringAny(o, "currentLaborRecordId", "current_labor_record_id"),
        currentLaborStartedAt = nullableStringAny(o, "currentLaborStartedAt", "current_labor_started_at"), accumulatedLaborMinutes = nullableIntAny(o, "accumulatedLaborMinutes", "accumulated_labor_minutes"),
        assignedAssemblerId = nullableStringAny(o, "assignedAssemblerId", "assigned_assembler_id"), assignedAssemblerName = nullableStringAny(o, "assignedAssemblerName", "assigned_assembler_name"),
        serverTime = nullableStringAny(o, "serverTime", "updatedAt", "updated_at"),
        stages = parseStages(o), members = parseAssemblyMembers(o.optJSONArray("members")),
    )

    private fun parseAssemblyMembers(array: JSONArray?): List<AssemblyMember> = (0 until (array?.length() ?: 0)).mapNotNull { i -> array?.optJSONObject(i)?.let { member ->
        AssemblyMember(nullableStringAny(member, "assemblerId", "assembler_id").orEmpty(), member.optString("assignmentRole", member.optString("assignment_role")), nullableStringAny(member, "assignedBy", "assigned_by"), nullableStringAny(member, "assignedAt", "assigned_at"), nullableStringAny(member, "removedAt", "removed_at"))
    } }

    private fun parseStages(o: JSONObject): List<AssemblyStage> {
        val array = o.optJSONArray("stages") ?: return (1..3).map { AssemblyStage(it, AssemblyStageStatus.NOT_STARTED) }
        return (0 until array.length()).mapNotNull { i -> array.optJSONObject(i)?.let { s -> AssemblyStage(s.optInt("stageNo", s.optInt("stage_no")), AssemblyStageStatus.from(s.optString("status")), s.optInt("version", 1), nullableStringAny(s, "startedAt", "started_at"), nullableStringAny(s, "completedAt", "completed_at"), nullableStringAny(s, "reworkReason", "rework_reason")) } }.ifEmpty { (1..3).map { AssemblyStage(it, AssemblyStageStatus.NOT_STARTED) } }
    }

    fun parseAssemblyStageOperation(json: String): AssemblyStageOperationResult {
        val o = JSONObject(json)
        return AssemblyStageOperationResult(o.optString("taskId"), o.optInt("stageNo"), AssemblyStageStatus.from(o.optString("status")), o.optInt("version"), nullableStringAny(o, "startedAt", "started_at"), nullableStringAny(o, "completedAt", "completed_at"), nullableStringAny(o, "reworkReason", "rework_reason"), nullableStringAny(o, "serverTime", "server_time"), nullableStringAny(o, "traceId", "trace_id"), o.optBoolean("idempotent", false))
    }

    fun parseLaborRecord(json: String): LaborRecord = parseLaborRecord(JSONObject(json), LaborType.ASSEMBLY)

    private fun parseLaborRecord(o: JSONObject, defaultType: LaborType): LaborRecord {
        val laborRecordId = nullableStringAny(o, "laborRecordId", "labor_record_id")
        val temporaryTransferId = nullableStringAny(o, "temporaryTransferId", "temporary_transfer_id")
        val id = nullableString(o, "id") ?: laborRecordId ?: temporaryTransferId.orEmpty()
        val type = runCatching { LaborType.valueOf(o.optString("type")) }.getOrDefault(defaultType)
        return LaborRecord(
            id = id,
            taskId = nullableStringAny(o, "taskId", "task_id"),
            type = type,
            startedAt = nullableStringAny(o, "startedAt", "started_at").orEmpty(),
            endedAt = nullableStringAny(o, "endedAt", "ended_at"),
            durationMinutes = nullableIntAny(o, "durationMinutes", "duration_minutes"),
            remark = nullableString(o, "remark"),
            laborRecordId = laborRecordId,
            temporaryTransferId = temporaryTransferId,
            status = nullableString(o, "status"),
            taskVersion = nullableIntAny(o, "taskVersion", "task_version"),
            serverTime = nullableStringAny(o, "serverTime", "server_time"),
        )
    }

    fun parseTemporaryTransfer(json: String): LaborRecord =
        parseLaborRecord(JSONObject(json), LaborType.TEMPORARY_TRANSFER)

    fun parseWorkshopSummary(json: String): WorkshopProgressSummary {
        val root = JSONObject(json); val machines = root.optJSONArray("machines") ?: JSONArray()
        return WorkshopProgressSummary(
            workshopId = nullableStringAny(root, "workshopId", "workshop_id"), totalTasks = root.optInt("totalTasks", root.optInt("total_tasks")),
            completedTasks = root.optInt("completedTasks", root.optInt("completed_tasks")), overallProgressPercent = root.optInt("overallProgressPercent", root.optInt("overall_progress_percent")),
            totalLaborMinutes = root.optInt("totalLaborMinutes", root.optInt("total_labor_minutes")), assemblyLaborMinutes = root.optInt("assemblyLaborMinutes", root.optInt("assembly_labor_minutes")),
            temporaryTransferLaborMinutes = root.optInt("temporaryTransferLaborMinutes", root.optInt("temporary_transfer_labor_minutes")),
            machines = buildList { for (i in 0 until machines.length()) { val m = machines.getJSONObject(i); add(MachineProgress(m.optString("deviceId", m.optString("device_id")), m.optString("deviceNo", m.optString("device_no")), m.optInt("taskCount", m.optInt("task_count")), m.optInt("completedTaskCount", m.optInt("completed_task_count")), m.optInt("progressPercent", m.optInt("progress_percent")), m.optInt("laborMinutes", m.optInt("labor_minutes")))) } },
            generatedAt = root.optString("generatedAt", root.optString("generated_at"))
        )
    }

    fun parseMachineProgressPage(json: String): MachineProgressPage {
        val root = JSONObject(json)
        val array = root.optJSONArray("items") ?: JSONArray()
        return MachineProgressPage(
            items = buildList {
                for (i in 0 until array.length()) {
                    val item = array.getJSONObject(i)
                    add(
                        MachineProgress(
                            deviceId = item.optString("deviceId", item.optString("device_id")),
                            deviceNo = item.optString("deviceNo", item.optString("device_no")),
                            taskCount = item.optInt("taskCount", item.optInt("task_count")),
                            completedTaskCount = item.optInt("completedTaskCount", item.optInt("completed_task_count")),
                            progressPercent = item.optInt("progressPercent", item.optInt("progress_percent")),
                            laborMinutes = nullableIntAny(item, "laborMinutes", "labor_minutes"),
                        )
                    )
                }
            },
            page = root.optInt("page", 1),
            pageSize = root.optInt("pageSize", 20),
        )
    }


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
data class SetupStatusDto(val initialized: Boolean, val adminUsername: String, val mustChangePassword: Boolean, val serverTime: String)
data class InitializeAdminResponseDto(val initialized: Boolean, val username: String, val mustChangePassword: Boolean, val serverTime: String, val traceId: String)
data class DeleteUserResult(
    val userId: String,
    val status: String,
    val idempotent: Boolean,
    val serverTime: String?,
    val traceId: String?,
)

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
    val status: ApprovalStatus,
    val idempotent: Boolean = false,
    val serverTime: String? = null,
    val traceId: String? = null,
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

/**
 * Convert server-controlled error text to a safe user-facing message.
 * Tokens, request headers, and backend stack traces must never reach UI or local queue text.
 */
fun ApiException.safeMessage(fallback: String): String = when {
    isUnauthorized -> "登录已失效，请重新登录"
    isForbidden -> "当前账号无权完成该操作"
    isConflict -> "数据状态已变化，请刷新后重试"
    retryable || statusCode >= 500 -> "网络或服务暂时不可用，请重试"
    else -> fallback
}
