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
    WAREHOUSE_ADMIN("WAREHOUSE_ADMIN", "仓库管理员"),
    ADMIN("ADMIN", "管理员");

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
    IN_STOCK("IN_STOCK", "在库");

    /** 对应色值（客户端展示用；语义以服务端 colorToken 为准） */
    val color: Color
        get() = when (this) {
            OUT_OF_STOCK -> MaterialStatusColors.Shortage
            ARRIVED -> MaterialStatusColors.Arrived
            IN_STOCK -> MaterialStatusColors.InStock
        }

    /** 浅色容器背景 */
    val containerColor: Color
        get() = when (this) {
            OUT_OF_STOCK -> MaterialStatusColors.ShortageContainer
            ARRIVED -> MaterialStatusColors.ArrivedContainer
            IN_STOCK -> MaterialStatusColors.InStockContainer
        }

    /**
     * 状态图标语义 —— 契约要求客户端必须同时展示图标与文字。
     * 返回值用于选择等宽 emoji 风格的简单符号，避免引入图标资源依赖。
     */
    val symbol: String
        get() = when (this) {
            OUT_OF_STOCK -> "!"
            ARRIVED -> "↓"
            IN_STOCK -> "✓"
        }

    companion object {
        fun from(code: String?, label: String? = null): MaterialStatusCode =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
                ?: entries.firstOrNull { label != null && it.label == label }
                ?: OUT_OF_STOCK
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
 * 扫码结果类型 —— 契约 3 节，并经业务模型更正。
 * 注意：已删除 LOGISTICS_NO 概念，仅保留厂内物料流转语义。
 */
enum class ScanType(val code: String, val label: String) {
    PRODUCTION_ORDER("PRODUCTION_ORDER", "生产订单号"),
    MATERIAL_CODE("MATERIAL_CODE", "料号"),
    LOCATION_CODE("LOCATION_CODE", "库位码"),
    FLOW_RECORD("FLOW_RECORD", "流转单号"),
    UNKNOWN("UNKNOWN", "未识别");

    val isKnown: Boolean get() = this != UNKNOWN

    companion object {
        fun from(code: String?): ScanType =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) } ?: UNKNOWN
    }
}

// ==================== 数据模型 ====================

/** 当前登录用户 —— 契约 4.1 响应 */
data class User(
    val id: String,
    val username: String,
    val displayName: String,
    val role: UserRole
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
    val materialId: String,
    val materialCode: String,
    val name: String,
    val requiredQuantity: Int,
    val arrivedQuantity: Int,
    val inStockQuantity: Int,
    val statusCode: MaterialStatusCode,
    val label: String,
    /** 服务端下发的颜色 token，客户端不得只依赖颜色 */
    val colorToken: String
) {
    /** 缺口数量 */
    val shortageQuantity: Int get() = (requiredQuantity - inStockQuantity).coerceAtLeast(0)
}

/** 生产订单物料状态聚合 —— 契约 4.3 */
data class OrderMaterialStatus(
    val documentNo: String,
    val documentType: String,
    val items: List<OrderMaterialItem>,
    val serverTime: String?
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
