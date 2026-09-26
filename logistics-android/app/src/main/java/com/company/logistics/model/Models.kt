package com.company.logistics.model

import androidx.compose.ui.graphics.Color
import com.company.logistics.ui.theme.MaterialStatusColors

/**
 * 领域模型 —— 严格对齐《V1 需求冻结与接口契约》与《厂内流转业务模型更正》。
 *
 * 重要：客户端不得自行推导核心状态，一律以服务端返回的
 *      statusCode / label / colorToken 为准，本层只做反序列化与展示映射。
 */

// ==================== 角色 ====================

/**
 * 用户角色 —— 对齐契约 1 节。
 * 注意：V1 无「主管」角色，审批能力集中在仓库管理员与管理员。
 */
enum class UserRole(val code: String, val label: String) {
    OPERATOR("OPERATOR", "操作员"),
    MATERIAL("MATERIAL", "物料员"),
    PLANNER("PLANNER", "计划员"),
    WAREHOUSE_ADMIN("WAREHOUSE_ADMIN", "仓库管理员"),
    ADMIN("ADMIN", "管理员"),
    WORKSHOP_SUPERVISOR("WORKSHOP_SUPERVISOR", "车间主管"),
    ASSEMBLER("ASSEMBLER", "装配工");

    /** 是否可审批入库/出库/异常 */
    val canApprove: Boolean get() = this == WAREHOUSE_ADMIN || this == ADMIN
    /** 是否可执行库存变更 */
    val canExecute: Boolean get() = this == WAREHOUSE_ADMIN || this == ADMIN
    /** 是否可管理用户与审计 */
    val canAdmin: Boolean get() = this == ADMIN

    companion object {
        fun from(code: String?): UserRole =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) } ?: OPERATOR
    }
}

// ==================== 物料状态（契约 2.1） ====================

/**
 * 单据物料状态枚举 —— 契约 2.1。
 * 红=缺货 / 黄=到货 / 绿=在库，接口同时返回枚举值与展示颜色。
 */
enum class MaterialStatusCode(val code: String, val label: String) {
    OUT_OF_STOCK("OUT_OF_STOCK", "缺货"),
    ARRIVED("ARRIVED", "到货"),
    IN_STOCK("IN_STOCK", "在库"),
    UNKNOWN("UNKNOWN", "未知状态");

    /** 对应色值（客户端展示用；语义以服务端 colorToken 为准） */
    val color: Color
        get() = when (this) {
            OUT_OF_STOCK -> MaterialStatusColors.Shortage
            ARRIVED -> MaterialStatusColors.Arrived
            IN_STOCK -> MaterialStatusColors.InStock
            UNKNOWN -> MaterialStatusColors.Unknown
        }

    /** 浅色容器背景 */
    val containerColor: Color
        get() = when (this) {
            OUT_OF_STOCK -> MaterialStatusColors.ShortageContainer
            ARRIVED -> MaterialStatusColors.ArrivedContainer
            IN_STOCK -> MaterialStatusColors.InStockContainer
            UNKNOWN -> MaterialStatusColors.UnknownContainer
        }

    /**
     * 状态图标语义 —— 契约要求客户端必须同时展示图标与文字。
     *
     * 本层只承载**符号语义**，不承载渲染方式：返回的字符是稳定的契约值
     * （`WorkspaceApiParserTest` 直接断言 `"✓"`），改动会破坏数据契约。
     * 实际展示由渲染层 `StatusTag` 经 `LogisticsIcons.fromSymbol` 翻译为
     * 矢量图标；未命中的字符（`!` `↓` `?`）回退文本渲染。
     */
    val symbol: String
        get() = when (this) {
            OUT_OF_STOCK -> "!"
            ARRIVED -> "↓"
            IN_STOCK -> "✓"
            UNKNOWN -> "?"
        }

    companion object {
        fun from(code: String?, label: String? = null): MaterialStatusCode =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
                ?: entries.firstOrNull { label != null && it.label == label }
                ?: UNKNOWN
    }
}

// ==================== 厂内流转状态（业务模型更正） ====================

/**
 * 厂内流转状态 —— 业务模型更正文档定义。
 * 说明：这是「流转记录」维度的状态，与物料状态的语义不同，切勿混用。
 */
enum class FlowStatus(val code: String, val label: String) {
    PENDING_ARRIVAL("PENDING_ARRIVAL", "待到料"),
    ARRIVED("ARRIVED", "已到料"),
    PENDING_INBOUND("PENDING_INBOUND", "待入库"),
    INBOUNDED("INBOUNDED", "已入库"),
    PENDING_PICK("PENDING_PICK", "待领料"),
    PICKED("PICKED", "已领料"),
    RELOCATING("RELOCATING", "厂内调拨中"),
    RETURNED("RETURNED", "已退料"),
    EXCEPTION("EXCEPTION", "异常");

    val color: Color
        get() = when (this) {
            PENDING_ARRIVAL, PENDING_INBOUND, PENDING_PICK -> MaterialStatusColors.Arrived
            ARRIVED, INBOUNDED, PICKED, RETURNED -> MaterialStatusColors.InStock
            RELOCATING -> MaterialStatusColors.InStock
            EXCEPTION -> MaterialStatusColors.Shortage
        }

    companion object {
        fun from(code: String?, label: String? = null): FlowStatus =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
                ?: entries.firstOrNull { label != null && it.label == label }
                ?: PENDING_ARRIVAL
    }
}

// ==================== 流转类型 ====================

/** 流转操作类型 —— 业务模型更正的层级中列出的 8 类动作 */
enum class FlowType(val code: String, val label: String, val symbol: String) {
    RECEIVE("RECEIVE", "收货登记", "⇩"),
    INBOUND("INBOUND", "厂内入库", "↓"),
    LOCATION_BIND("LOCATION_BIND", "库位绑定", "⊞"),
    PICK("PICK", "领料/出库", "↑"),
    RETURN("RETURN", "退料", "↩"),
    RELOCATION("RELOCATION", "厂内调拨", "⇄"),
    STOCKTAKE("STOCKTAKE", "盘点", "☰"),
    EXCEPTION("EXCEPTION", "异常", "!");

    val color: Color
        get() = when (this) {
            INBOUND, RECEIVE -> MaterialStatusColors.InStock
            PICK -> MaterialStatusColors.Arrived
            RELOCATION -> Color(0xFF0E5FD8)
            STOCKTAKE -> Color(0xFF7B61FF)
            LOCATION_BIND -> Color(0xFF0E5FD8)
            RETURN -> Color(0xFF6B7785)
            EXCEPTION -> MaterialStatusColors.Shortage
        }

    companion object {
        fun from(code: String?, label: String? = null): FlowType =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
                ?: entries.firstOrNull { label != null && it.label == label }
                ?: RECEIVE
    }
}

// ==================== 审批状态（契约 2.2） ====================

/**
 * 入库/出库申请审批状态机 —— 契约 2.2。
 * DRAFT → SUBMITTED → PENDING_APPROVAL → APPROVED → EXECUTED
 *                              └────────→ REJECTED
 */
enum class ApprovalStatus(val code: String, val label: String) {
    DRAFT("DRAFT", "草稿"),
    SUBMITTED("SUBMITTED", "已提交"),
    PENDING_APPROVAL("PENDING_APPROVAL", "待审批"),
    APPROVED("APPROVED", "已批准"),
    REJECTED("REJECTED", "已驳回"),
    EXECUTED("EXECUTED", "已执行");

    val color: Color
        get() = when (this) {
            DRAFT -> Color(0xFF6B7785)
            SUBMITTED, PENDING_APPROVAL -> MaterialStatusColors.Arrived
            APPROVED -> Color(0xFF0E5FD8)
            REJECTED -> MaterialStatusColors.Shortage
            EXECUTED -> MaterialStatusColors.InStock
        }

    /** 是否允许审批操作（仅待审批态可批） */
    val isApprovable: Boolean get() = this == PENDING_APPROVAL
    /** 是否允许执行（仅批准后可执行） */
    val isExecutable: Boolean get() = this == APPROVED

    companion object {
        fun from(code: String?, label: String? = null): ApprovalStatus =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
                ?: entries.firstOrNull { label != null && it.label == label }
                ?: DRAFT
    }
}

// ==================== 扫码类型（契约 3 / 业务模型更正） ====================

/**
 * 扫码类型。
 * 严格对齐《物料流转系统-V1-API契约冻结补遗》第 1.1 节，只允许 5 个值：
 * PRODUCTION_ORDER / FLOW_NO / MATERIAL_CODE / LOCATION_CODE / UNKNOWN。
 * 设备码以及其他未知/历史类型统一归 UNKNOWN。
 */
enum class ScanType(val code: String, val label: String) {
    PRODUCTION_ORDER("PRODUCTION_ORDER", "生产订单号"),
    MATERIAL_CODE("MATERIAL_CODE", "料号"),
    LOCATION_CODE("LOCATION_CODE", "库位码"),
    FLOW_NO("FLOW_NO", "流转单号"),
    DEVICE_CODE("DEVICE_CODE", "机台码"),
    UNKNOWN("UNKNOWN", "未识别");

    val isKnown: Boolean get() = this != UNKNOWN

    companion object {
        fun from(code: String?): ScanType =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) } ?: UNKNOWN
    }
}

/** 服务端异常提报结果；提交成功只表示服务端创建了待审批记录。 */
data class ExceptionSubmissionResult(
    val exceptionId: String,
    val status: String,
    val difference: Int,
    val serverTime: String?
)

// ==================== 数据模型 ====================

/** 当前登录用户 —— 契约 4.1 响应 */
data class User(
    val id: String,
    val username: String,
    val displayName: String,
    val role: UserRole
)

data class ManagedUser(
    val id: String,
    val username: String,
    val displayName: String,
    val role: UserRole,
    val active: Boolean,
    val mustChangePassword: Boolean,
    val createdAt: String?
)

/** 登录结果 */
data class LoginResult(
    val accessToken: String,
    val refreshToken: String?,
    val expiresAt: String?,
    val mustChangePassword: Boolean,
    val user: User
)

/** 物料基础信息 —— 契约 4.4 */
data class Material(
    val id: String,
    val code: String,
    val name: String,
    val specification: String? = null,
    val unit: String = "件",
    val batchNo: String? = null,
    val expiryDate: String? = null
)

/** 库位库存条目 —— 契约 4.4 inventory.locations */
data class LocationStock(
    val locationCode: String,
    val quantity: Int
)

/** 料号库存 —— 契约 4.4 */
data class Inventory(
    val totalQuantity: Int,
    val availableQuantity: Int,
    val reservedQuantity: Int = 0,
    val locations: List<LocationStock> = emptyList()
)

/** 料号库存聚合结果 */
data class MaterialInventory(
    val material: Material,
    val inventory: Inventory,
    /** 乐观锁版本号，写操作回传 expectedInventoryVersion */
    val version: Int
) {
    /** 主库位（首个绑定的库位） */
    val primaryLocation: String?
        get() = inventory.locations.firstOrNull()?.locationCode
}

/** 订单物料状态条目 —— 契约 4.3 */
data class OrderMaterialItem(
    val deviceId: String?,
    val deviceType: String?,
    val deviceNo: String?,
    val materialId: String,
    val materialCode: String,
    val name: String,
    val specification: String?,
    val requiredQuantity: Int,
    val arrivedQuantity: Int,
    val inStockQuantity: Int,
    val statusCode: MaterialStatusCode,
    val label: String,
    /** 服务端下发的颜色 token，客户端不得只依赖颜色 */
    val colorToken: String,
    /** 原始服务端状态码。保留未知值，避免错误地把新状态显示成缺货或成功。 */
    val serverStatusCode: String? = null,
    /** 工作台状态域字段；旧版订单响应没有这些字段时保持 null。 */
    val workflowStatusCode: String? = null,
    val workflowStatusLabel: String? = null,
    val pickedQuantity: Int? = null,
    val issuedQuantity: Int? = null,
    val approvalStatusCode: String? = null,
    val handoverStatusCode: String? = null,
    val lastHandoverId: String? = null,
    val currentOwnerUserId: String? = null,
    val currentOwnerName: String? = null,
    val updatedAt: String? = null,
    val unit: String = "件"
) {
    /** 缺口数量 */
    val shortageQuantity: Int get() = (requiredQuantity - inStockQuantity).coerceAtLeast(0)

    /** 服务端状态码；手工构造的旧模型继续使用既有枚举值。 */
    val effectiveStatusCode: String
        get() = workflowStatusCode?.takeIf { it.isNotBlank() }
            ?: serverStatusCode?.takeIf { it.isNotBlank() }
            ?: statusCode.code

    /** 服务端状态标签；未知状态也必须保留服务端原文。 */
    val effectiveStatusLabel: String
        get() = label.ifBlank {
            when (serverStatusCode?.uppercase(java.util.Locale.ROOT)) {
                "OUT_OF_STOCK" -> MaterialStatusCode.OUT_OF_STOCK.label
                "ARRIVED" -> MaterialStatusCode.ARRIVED.label
                "IN_STOCK" -> MaterialStatusCode.IN_STOCK.label
                null -> statusCode.label
                else -> "未知状态"
            }
        }
}

/** 生产订单物料状态聚合 —— 契约 4.3 */
data class OrderMaterialStatus(
    val documentNo: String,
    val documentType: String,
    val items: List<OrderMaterialItem>,
    val serverTime: String?,
    val orderId: String? = null,
    val productName: String? = null,
    val orderStatus: String? = null
) {
    /** 齐套率：已入库数量 / 需求数量 */
    val fulfillmentRate: Float
        get() {
            val required = items.sumOf { it.requiredQuantity }
            if (required <= 0) return 0f
            return (items.sumOf { it.inStockQuantity }.toFloat() / required).coerceIn(0f, 1f)
        }

    /** 缺料物料数量 */
    val shortageCount: Int get() = items.count { it.statusCode == MaterialStatusCode.OUT_OF_STOCK }
}

/** 工作台上的可用性与计数。count 为 null 表示服务端当前没有提供该状态域数据。 */
data class WorkspaceMetric(
    val count: Int?,
    val available: Boolean
) {
    companion object {
        fun unavailable() = WorkspaceMetric(count = null, available = false)
        fun of(count: Int) = WorkspaceMetric(count = count, available = true)
    }
}

/** 工作台摘要入口对应的业务指标。 */
enum class WorkspaceMetricKey(val label: String, val description: String) {
    CLAIMED("已领取", "服务端确认的领取记录"),
    AT_STATION("已到机台", "服务端确认目标机台的交接"),
    OUTBOUND_PENDING("待出库", "服务端返回的待出库状态"),
    OUTBOUND_CONFIRMED("已出库", "服务端返回的已出库状态"),
    PENDING_APPROVAL("待审批", "服务端返回的审批状态"),
    PENDING_HANDOVER("待交接", "服务端返回的交接状态"),
    ALL("全量工作项", "服务端分页工作台接口返回的全量数量"),
    EXCEPTION("异常", "服务端明确标记的异常项"),
    OUT_OF_STOCK("缺货", "服务端摘要返回的缺货数量"),
    AUDIT("审计记录", "服务端审计接口返回的记录"),
    TOTAL_LABOR_MINUTES("总工时（分钟）", "服务端汇总的装配与临时调拨工时"),
    ASSEMBLY_LABOR_MINUTES("装配工时（分钟）", "服务端汇总的装配工时"),
    TEMPORARY_TRANSFER_LABOR_MINUTES("临时调拨工时（分钟）", "服务端独立临时调拨工时"),
    OVERALL_PROGRESS_PERCENT("订单总进度", "服务端完成任务比例")
}

/**
 * 角色工作台摘要。
 *
 * 该模型不保存客户端推导出的业务状态。没有服务端字段时使用 unavailable，
 * UI 会显示“—”，而不是把库存或订单条数冒充领取、审批或审计数据。
 */
data class RoleWorkspaceSummary(
    val role: UserRole,
    val sourceOrderNo: String?,
    val serverTime: String?,
    val metrics: Map<WorkspaceMetricKey, WorkspaceMetric>
) {
    fun metric(key: WorkspaceMetricKey): WorkspaceMetric =
        metrics[key] ?: WorkspaceMetric.unavailable()

    companion object {
        fun empty(role: UserRole): RoleWorkspaceSummary = RoleWorkspaceSummary(
            role = role,
            sourceOrderNo = null,
            serverTime = null,
            metrics = emptyMap()
        )
    }
}

/**
 * GET /api/v1/workspace/summary 的服务端响应。
 *
 * 数量使用可空 Int：字段缺失代表服务端没有提供该指标，不能与服务端明确返回的 0 混淆。
 */
data class WorkspaceSummary(
    val role: UserRole = UserRole.OPERATOR,
    val pendingApprovalCount: Int? = null,
    val pendingOutboundCount: Int? = null,
    val pendingHandoverCount: Int? = null,
    val atStationCount: Int? = null,
    val pickedUpCount: Int? = null,
    val outOfStockCount: Int? = null,
    val generatedAt: String? = null,
    val serverTime: String? = null,
    val traceId: String? = null,
    val outboundConfirmedCount: Int? = null,
    val preview: Boolean = false,
    val authenticatedRole: UserRole? = null,
    val totalLaborMinutes: Int? = null,
    val assemblyLaborMinutes: Int? = null,
    val temporaryTransferLaborMinutes: Int? = null,
    val overallProgressPercent: Int? = null,
)

/** 工作台分页工作项响应。客户端只保留当前页，避免一次性加载全量数据。 */
data class WorkspaceMaterialItemsPage(
    val items: List<WorkspaceMaterialItem>,
    val page: Int,
    val pageSize: Int,
    val total: Int,
    val totalPages: Int,
    val serverTime: String?,
    val traceId: String?,
    val preview: Boolean = false,
    val authenticatedRole: UserRole? = null,
)

/** 流转申请明细条目；详情接口中的 payload 只投影到可展示字段。 */
data class TransferRequestItem(
    val materialId: String,
    val quantity: Int,
    val batchNo: String? = null,
    val sourceLocationCode: String? = null,
    val targetLocationCode: String? = null,
    val expectedInventoryVersion: Int? = null,
)

/** GET /api/v1/transfer-requests 及详情接口的服务端记录。 */
data class TransferRequest(
    val id: String,
    val clientOperationId: String? = null,
    val type: String,
    val documentNo: String? = null,
    /** 保留服务端原始状态码，按钮策略不得从数量或本地推导。 */
    val status: String,
    val statusLabel: String? = null,
    val items: List<TransferRequestItem> = emptyList(),
    val remark: String? = null,
    val createdBy: String? = null,
    val createdAt: String? = null,
    val approvedBy: String? = null,
    val approvedAt: String? = null,
    val executedAt: String? = null,
    val serverTime: String? = null,
    val traceId: String? = null,
) {
    val requestId: String get() = id

    val effectiveStatusCode: String
        get() = status.trim().uppercase(java.util.Locale.ROOT)

    val displayStatusLabel: String
        get() = statusLabel?.takeIf { it.isNotBlank() }
            ?: ApprovalStatus.entries.firstOrNull { it.code == effectiveStatusCode }?.label
            ?: status.ifBlank { "未知状态" }
}

/** 列表接口当前最多返回服务端允许的 100 条记录。 */
data class TransferRequestPage(
    val items: List<TransferRequest>,
    val statusFilter: String? = null,
    val serverTime: String? = null,
    val traceId: String? = null,
)

typealias TransferRequestList = TransferRequestPage

/** 管理员审计列表的安全投影；不保存 IP、前后 JSON 等不需要在移动端展示的字段。 */
data class AuditLog(
    val id: Long,
    val operatorId: String?,
    val role: String?,
    val action: String,
    val resourceType: String,
    val resourceId: String?,
    val requestId: String?,
    val deviceId: String?,
    val occurredAt: String?,
    val result: String,
)

/** GET /api/v1/audit-logs 的服务端分页结果。总数不是接口契约的一部分，以 hasNext 控制翻页。 */
data class AuditLogPage(
    val items: List<AuditLog>,
    val page: Int,
    val pageSize: Int,
    val hasNext: Boolean,
    val serverTime: String? = null,
    val traceId: String? = null,
)

/** 管理员/仓库管理员可见的流转申请写操作。 */
enum class TransferRequestAction(val label: String) {
    APPROVE("批准"),
    REJECT("驳回"),
    EXECUTE("执行"),
}

/** 只根据角色和服务端状态决定按钮；服务端仍是最终权限与状态裁决者。 */
object TransferRequestActionPolicy {
    fun actionsFor(
        role: UserRole,
        request: TransferRequest,
    ): List<TransferRequestAction> {
        if (!role.canApprove) return emptyList()
        return when (request.effectiveStatusCode) {
            "PENDING_APPROVAL" -> listOf(
                TransferRequestAction.APPROVE,
                TransferRequestAction.REJECT,
            )
            // 审批人与执行人隔离由服务端裁决；这里仍按服务端状态显示执行入口，
            // 409 会被统一映射为“状态已变化/不可执行”，避免客户端复制权限事实。
            "APPROVED" -> listOf(TransferRequestAction.EXECUTE)
            else -> emptyList()
        }
    }
}

/** 工作台责任人投影；数量和状态事实仍来自服务端工作台接口。 */
data class WorkspaceResponsibilitySummary(
    val assignedUserId: String?,
    val assignedUserName: String?,
    val currentOwnerUserId: String?,
    val currentOwnerName: String?
)

/** 工作台最近交接记录。 */
data class WorkspaceLastHandover(
    val id: String,
    val status: String?,
    val quantity: Int?,
    val fromLocation: String?,
    val deviceId: String?,
    val transferRequestId: String?,
    val senderUserId: String?,
    val senderName: String?,
    val receiverUserId: String?,
    val receiverName: String?,
    val initiatedAt: String?,
    val confirmedBy: String?,
    val confirmedAt: String?,
    val remark: String?
)

/** 工作台交接摘要。 */
data class WorkspaceHandoverSummary(
    val lastHandoverId: String?,
    val lastStatus: String?,
    val lastInitiatedAt: String?,
    val lastConfirmedAt: String?,
    val count: Int?
)

/**
 * GET /api/v1/workspace/material-items 的单项。
 *
 * statusCode/statusLabel/statusDomain/colorToken 始终保留服务端原值；客户端不把库存数量
 * 推导成领取、审批、交接或审计状态。
 */
data class WorkspaceMaterialItem(
    val id: String,
    val requirementId: String?,
    val orderNo: String,
    val productName: String?,
    val orderStatus: String?,
    val deviceId: String?,
    val deviceType: String?,
    val deviceNo: String?,
    val materialId: String,
    val materialCode: String,
    val materialName: String,
    val specification: String?,
    val unit: String?,
    val requiredQuantity: Int?,
    val arrivedQuantity: Int?,
    val inStockQuantity: Int?,
    val issuedQuantity: Int?,
    val pickedQuantity: Int?,
    val statusCode: String,
    val statusLabel: String,
    val colorToken: String,
    val statusDomain: String,
    val updatedAt: String?,
    val assignedUserId: String?,
    val assignedUserName: String?,
    val currentOwnerUserId: String?,
    val currentOwnerName: String?,
    val responsibilitySummary: WorkspaceResponsibilitySummary?,
    val lastHandoverId: String?,
    val lastHandoverStatus: String?,
    val lastHandover: WorkspaceLastHandover?,
    val handoverSummary: WorkspaceHandoverSummary?,
    val transferRequestId: String?,
    val transferStatus: String?
) {
    /** 按服务端 colorToken 展示；未知 token 使用安全中性色。 */
    val statusColor: Color
        get() = if (!hasKnownStatus()) MaterialStatusColors.Unknown else when (colorToken.uppercase(java.util.Locale.ROOT)) {
            "STATUS-RED" -> MaterialStatusColors.Shortage
            "STATUS-YELLOW" -> MaterialStatusColors.Arrived
            "STATUS-GREEN" -> MaterialStatusColors.InStock
            "STATUS-BLUE" -> Color(0xFF0E5FD8)
            else -> MaterialStatusColors.Unknown
        }

    val statusContainerColor: Color
        get() = if (!hasKnownStatus()) MaterialStatusColors.UnknownContainer else when (colorToken.uppercase(java.util.Locale.ROOT)) {
            "STATUS-RED" -> MaterialStatusColors.ShortageContainer
            "STATUS-YELLOW" -> MaterialStatusColors.ArrivedContainer
            "STATUS-GREEN" -> MaterialStatusColors.InStockContainer
            else -> MaterialStatusColors.UnknownContainer
        }

    /** 服务端没有认识该状态时使用安全中性色符号，不把它显示成成功。 */
    val statusSymbol: String
        get() = when (statusCode.uppercase(java.util.Locale.ROOT)) {
            "AT_STATION", "PICKED_UP", "OUTBOUND_CONFIRMED", "CONFIRMED", "IN_STOCK" -> "✓"
            "OUT_OF_STOCK", "EXCEPTION", "REJECTED", "CANCELLED" -> "!"
            "PENDING", "OUTBOUND_PENDING", "OUTBOUND_APPROVED", "ARRIVED" -> "↓"
            else -> "?"
        }

    private fun hasKnownStatus(): Boolean = statusCode.uppercase(java.util.Locale.ROOT) in setOf(
        "OUT_OF_STOCK", "ARRIVED", "IN_STOCK",
        "OUTBOUND_PENDING", "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED",
        "PENDING", "PICKED_UP", "AT_STATION", "REJECTED", "CANCELLED"
    )
}

/** 交接写操作；服务端状态与角色策略共同决定是否可以展示。 */
enum class HandoverAction(val label: String, val pathSegment: String, val requiresReason: Boolean) {
    CONFIRM("确认", "confirm", false),
    REJECT("驳回", "reject", true),
    CANCEL("取消", "cancel", true)
}

/**
 * 交接按钮策略只读取服务端返回的工作项/交接状态，不从数量推导状态。
 * 这层策略同时供 UI 和 ViewModel 使用，避免仅隐藏按钮而仍可发起越权请求。
 */
object HandoverActionPolicy {
    fun actionsFor(role: UserRole, userId: String?, item: WorkspaceMaterialItem): List<HandoverAction> {
        if (!item.lastHandoverId.isNullOrBlank() &&
            item.lastHandoverStatus.equals("PENDING", ignoreCase = true)
        ) {
            return when (role) {
                UserRole.OPERATOR -> if (isAssignedReceiver(userId, item)) {
                    listOf(HandoverAction.CONFIRM)
                } else {
                    emptyList()
                }
                UserRole.MATERIAL, UserRole.PLANNER -> if (isSender(userId, item)) {
                    listOf(HandoverAction.CANCEL)
                } else {
                    emptyList()
                }
                UserRole.WAREHOUSE_ADMIN, UserRole.ADMIN -> listOf(
                    HandoverAction.CONFIRM,
                    HandoverAction.REJECT,
                    HandoverAction.CANCEL,
                )
                UserRole.WORKSHOP_SUPERVISOR, UserRole.ASSEMBLER -> emptyList()
            }
        }
        return emptyList()
    }

    /** 只有服务端明确返回已审批/可执行的出库单时才显示发起入口。 */
    fun canCreate(role: UserRole, item: WorkspaceMaterialItem): Boolean =
        role == UserRole.MATERIAL &&
            item.lastHandoverId.isNullOrBlank() &&
            item.lastHandoverStatus.isNullOrBlank() &&
            !item.transferRequestId.isNullOrBlank() &&
            item.transferStatus?.uppercase(java.util.Locale.ROOT) in setOf("APPROVED", "EXECUTED")

    private fun isAssignedReceiver(userId: String?, item: WorkspaceMaterialItem): Boolean {
        if (userId.isNullOrBlank()) return false
        return userId == item.lastHandover?.receiverUserId ||
            userId == item.assignedUserId ||
            userId == item.responsibilitySummary?.assignedUserId
    }

    private fun isSender(userId: String?, item: WorkspaceMaterialItem): Boolean =
        !userId.isNullOrBlank() && userId == item.lastHandover?.senderUserId
}

/** 交接动作响应；服务端返回的状态是唯一事实来源。 */
data class HandoverActionResult(
    val handoverId: String,
    val status: String,
    val transferRequestId: String? = null,
    val eventTypes: List<String> = emptyList(),
    val traceId: String? = null,
    val idempotent: Boolean = false
)

/** 时间线中的审计事件；仅保留展示所需字段，不暴露来源 IP 等敏感字段。 */
data class HandoverTimelineEvent(
    val id: String,
    val eventType: String,
    val actorUserId: String?,
    val actorRole: String?,
    val requestId: String?,
    val clientOperationId: String?,
    val serverTime: String?,
    val result: String?
)

/** 当前工作项的交接时间线；客户端最多保留一条、最多 20 个事件。 */
data class HandoverTimeline(
    val handoverId: String,
    val workItemId: String,
    val status: String,
    val workspaceStatus: String,
    val items: List<HandoverTimelineEvent>,
    val serverTime: String?
)

/** 用服务端摘要构造工作台展示模型；缺失字段明确保持 unavailable。 */
object ServerWorkspaceSummaryFactory {
    fun from(summary: WorkspaceSummary): RoleWorkspaceSummary = RoleWorkspaceSummary(
        role = summary.role,
        sourceOrderNo = null,
        serverTime = summary.serverTime ?: summary.generatedAt,
        metrics = mapOf(
            WorkspaceMetricKey.CLAIMED to metric(summary.pickedUpCount),
            WorkspaceMetricKey.AT_STATION to metric(summary.atStationCount),
            WorkspaceMetricKey.OUTBOUND_PENDING to metric(summary.pendingOutboundCount),
            WorkspaceMetricKey.OUTBOUND_CONFIRMED to metric(summary.outboundConfirmedCount),
            WorkspaceMetricKey.PENDING_APPROVAL to metric(summary.pendingApprovalCount),
            WorkspaceMetricKey.PENDING_HANDOVER to metric(summary.pendingHandoverCount),
            // 全量数量由分页接口的 server total 提供，不能从当前页推导。
            WorkspaceMetricKey.ALL to WorkspaceMetric.unavailable(),
            // 摘要契约没有异常计数和审计接口数据。
            WorkspaceMetricKey.EXCEPTION to WorkspaceMetric.unavailable(),
            WorkspaceMetricKey.OUT_OF_STOCK to metric(summary.outOfStockCount),
            WorkspaceMetricKey.AUDIT to WorkspaceMetric.unavailable(),
            WorkspaceMetricKey.TOTAL_LABOR_MINUTES to metric(summary.totalLaborMinutes),
            WorkspaceMetricKey.ASSEMBLY_LABOR_MINUTES to metric(summary.assemblyLaborMinutes),
            WorkspaceMetricKey.TEMPORARY_TRANSFER_LABOR_MINUTES to metric(summary.temporaryTransferLaborMinutes),
            WorkspaceMetricKey.OVERALL_PROGRESS_PERCENT to metric(summary.overallProgressPercent)
        )
    )

    private fun metric(count: Int?): WorkspaceMetric =
        count?.let { WorkspaceMetric.of(it) } ?: WorkspaceMetric.unavailable()
}

/** 从既有订单物料响应生成摘要；不调用新后端接口，也不推断缺失状态。 */
object RoleWorkspaceSummaryFactory {
    private val workflowStatusCodes = setOf(
        "PICKED_UP", "AT_STATION", "OUTBOUND_PENDING", "OUTBOUND_APPROVED",
        "OUTBOUND_CONFIRMED", "EXCEPTION"
    )

    fun from(role: UserRole, status: OrderMaterialStatus?): RoleWorkspaceSummary {
        if (status == null) return RoleWorkspaceSummary.empty(role)

        val items = status.items
        // 订单的库存状态（OUT_OF_STOCK / ARRIVED / IN_STOCK）不能冒充工作流状态。
        // 只有服务端明确返回独立 workflowStatusCode，才纳入工作台计数。
        fun workspaceStatus(item: OrderMaterialItem): String? =
            item.workflowStatusCode
                ?.uppercase(java.util.Locale.ROOT)
                ?.takeIf { it in workflowStatusCodes }

        val workflowItems = items.filter { workspaceStatus(it) != null }
        val workflowAvailable = workflowItems.isNotEmpty()
        val approvalAvailable = items.any { !it.approvalStatusCode.isNullOrBlank() }
        val handoverAvailable = items.any { !it.handoverStatusCode.isNullOrBlank() }

        fun workflow(code: String): WorkspaceMetric =
            if (workflowAvailable) WorkspaceMetric.of(
                workflowItems.count { workspaceStatus(it).equals(code, ignoreCase = true) }
            ) else WorkspaceMetric.unavailable()

        fun approval(code: String): WorkspaceMetric =
            if (approvalAvailable) WorkspaceMetric.of(
                items.count { it.approvalStatusCode.equals(code, ignoreCase = true) }
            ) else WorkspaceMetric.unavailable()

        fun handover(code: String): WorkspaceMetric =
            if (handoverAvailable) WorkspaceMetric.of(
                items.count { it.handoverStatusCode.equals(code, ignoreCase = true) }
            ) else WorkspaceMetric.unavailable()

        return RoleWorkspaceSummary(
            role = role,
            sourceOrderNo = status.documentNo,
            serverTime = status.serverTime,
            metrics = mapOf(
                WorkspaceMetricKey.CLAIMED to workflow("PICKED_UP"),
                WorkspaceMetricKey.AT_STATION to workflow("AT_STATION"),
                WorkspaceMetricKey.OUTBOUND_PENDING to workflow("OUTBOUND_PENDING"),
                WorkspaceMetricKey.OUTBOUND_CONFIRMED to workflow("OUTBOUND_CONFIRMED"),
                WorkspaceMetricKey.PENDING_APPROVAL to approval("PENDING_APPROVAL"),
                WorkspaceMetricKey.PENDING_HANDOVER to handover("PENDING"),
                WorkspaceMetricKey.ALL to WorkspaceMetric.of(items.size),
                WorkspaceMetricKey.EXCEPTION to workflow("EXCEPTION"),
                // 审计记录没有出现在订单物料响应中，必须保持不可用。
                WorkspaceMetricKey.AUDIT to WorkspaceMetric.unavailable()
            )
        )
    }

    /** 新工作台摘要入口；保留旧 from(role, orderStatus) 以兼容订单查询。 */
    fun from(summary: WorkspaceSummary): RoleWorkspaceSummary =
        ServerWorkspaceSummaryFactory.from(summary)
}

/** 扫码解析结果 —— 契约 4.2 */
data class ScanResult(
    val type: ScanType,
    val normalizedValue: String,
    val resourceId: String?
)

// ==================== 离线队列 ====================

/** 离线操作类型 */
enum class OfflineOpType(val label: String) {
    INBOUND("厂内入库"),
    OUTBOUND("领料/出库"),
    LOCATION_BIND("库位绑定"),
    RELOCATION("厂内调拨"),
    STOCKTAKE("盘点"),
    EXCEPTION("异常提报")
}

/** 离线队列同步状态 */
enum class SyncStatus(val label: String, val color: Color) {
    PENDING("待同步", MaterialStatusColors.Arrived),
    SYNCING("同步中", Color(0xFF0E5FD8)),
    SYNCED("已同步", MaterialStatusColors.InStock),
    FAILED("同步失败", MaterialStatusColors.Shortage),
    CONFLICT("有冲突", MaterialStatusColors.Shortage)
}

/** 离线暂存记录 */
data class OfflineOperation(
    val id: String,
    /** 幂等键，契约要求所有写操作携带 clientOperationId */
    val clientOperationId: String,
    val opType: OfflineOpType,
    val materialCode: String,
    val quantity: Int,
    val targetLocation: String? = null,
    val status: SyncStatus = SyncStatus.PENDING,
    val createdAt: Long,
    /** 服务端时间戳（同步后回填，服务端时间为唯一审计依据） */
    val serverTime: String? = null,
    val errorMessage: String? = null
)

/** 机台详情 */
data class DeviceDetail(
    val deviceId: String,
    val deviceNo: String,
    val deviceName: String,
    val workshop: String?,
    val modelCapability: String?,
    val status: String,
    val orders: List<DeviceOrder> = emptyList()
)

data class DeviceOrder(
    val orderNo: String,
    val productName: String?,
    val assignStatus: String?
)
