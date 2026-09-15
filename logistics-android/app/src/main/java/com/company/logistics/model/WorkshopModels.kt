package com.company.logistics.model

/** Assembly workflow models. Server timestamps remain the source of labor truth. */
enum class AssemblyTaskStatus(val code: String, val label: String) {
    WAITING_MATERIAL("WAITING_MATERIAL", "待接受物料"),
    MATERIAL_ACCEPTED("MATERIAL_ACCEPTED", "物料已接受"),
    IN_PROGRESS("IN_PROGRESS", "装配中"),
    PAUSED_FOR_TEMPORARY_TRANSFER("PAUSED_FOR_TEMPORARY_TRANSFER", "临时调拨暂停"),
    COMPLETED("COMPLETED", "已完工");
    companion object { fun from(value: String?) = entries.firstOrNull { it.code.equals(value, true) } ?: WAITING_MATERIAL }
}

enum class LaborType { ASSEMBLY, TEMPORARY_TRANSFER }

enum class AssemblyAction(val label: String) {
    ACCEPT_MATERIAL("接受物料"),
    START_WORK("开工"),
    PROGRESS("提交进度"),
    COMPLETE_WORK("完工"),
}

enum class AssemblyStageStatus(val code: String, val label: String) {
    NOT_STARTED("NOT_STARTED", "未开始"), IN_PROGRESS("IN_PROGRESS", "进行中"),
    COMPLETED("COMPLETED", "已完成"), REWORK_REQUIRED("REWORK_REQUIRED", "待返工");
    companion object { fun from(value: String?) = entries.firstOrNull { it.code.equals(value, true) } ?: NOT_STARTED }
}

data class AssemblyStage(
    val stageNo: Int, val status: AssemblyStageStatus, val version: Int = 1,
    val startedAt: String? = null, val completedAt: String? = null, val reworkReason: String? = null,
)

data class AssemblyStageOperationResult(
    val taskId: String, val stageNo: Int, val status: AssemblyStageStatus, val version: Int,
    val startedAt: String?, val completedAt: String?, val reworkReason: String?,
    val serverTime: String?, val traceId: String?, val idempotent: Boolean = false,
)

data class AssemblyTask(
    val id: String, val orderNo: String = "", val deviceId: String, val deviceNo: String,
    val materialSummary: String = "", val status: AssemblyTaskStatus, val progressStage: Int,
    val taskVersion: Int, val currentLaborRecordId: String? = null, val currentLaborStartedAt: String? = null,
    val accumulatedLaborMinutes: Int? = null, val assignedAssemblerId: String? = null, val assignedAssemblerName: String? = null,
    val serverTime: String? = null, val stages: List<AssemblyStage> = listOf(1, 2, 3).map { AssemblyStage(it, AssemblyStageStatus.NOT_STARTED) },
)

data class AssemblyTaskPage(
    val items: List<AssemblyTask>,
    val page: Int,
    val pageSize: Int,
    val total: Int,
    val totalPages: Int,
    val serverTime: String? = null,
)

data class LaborRecord(
    val id: String, val taskId: String?, val type: LaborType, val startedAt: String,
    val endedAt: String?, val durationMinutes: Int?, val remark: String?,
    val laborRecordId: String? = null,
    val temporaryTransferId: String? = null,
    val status: String? = null,
    val taskVersion: Int? = null,
    val serverTime: String? = null,
)

data class MachineProgress(val deviceId: String, val deviceNo: String, val taskCount: Int, val completedTaskCount: Int, val progressPercent: Int, val laborMinutes: Int?)

data class MachineProgressPage(
    val items: List<MachineProgress>,
    val page: Int,
    val pageSize: Int,
)

data class WorkshopProgressSummary(
    val workshopId: String?, val totalTasks: Int, val completedTasks: Int, val overallProgressPercent: Int,
    val totalLaborMinutes: Int, val assemblyLaborMinutes: Int, val temporaryTransferLaborMinutes: Int,
    val machines: List<MachineProgress>, val generatedAt: String
)

data class OrderDetailTimelineEvent(
    val type: String, val entityId: String, val status: String?, val serverTime: String?, val actorId: String?
)

data class OrderDetailLaborSummary(
    val assemblyLaborMinutes: Int, val temporaryTransferLaborMinutes: Int, val totalLaborMinutes: Int
)

data class OrderDetailMaterialSummaryItem(
    val materialId: String,
    val materialCode: String,
    val materialName: String,
    val unit: String,
    val requiredQuantity: Int,
    val arrivedQuantity: Int,
    val inStockQuantity: Int,
    val shortageQuantity: Int,
    val statusCode: String,
    val statusLabel: String,
)

data class OrderDetailMaterialSummary(
    val items: List<OrderDetailMaterialSummaryItem> = emptyList(),
    val totalMaterialTypes: Int = 0,
    val totalRequiredQuantity: Int = 0,
    val totalArrivedQuantity: Int = 0,
    val totalInStockQuantity: Int = 0,
    val totalShortageQuantity: Int = 0,
)

data class OrderDetail(
    val orderId: String, val orderNo: String, val productName: String?, val orderStatus: String?,
    val materials: List<OrderMaterialItem>, val assemblyTasks: List<AssemblyTask>,
    val laborSummary: OrderDetailLaborSummary, val timeline: List<OrderDetailTimelineEvent>,
    val page: Int, val pageSize: Int, val total: Int,
    val materialSummary: OrderDetailMaterialSummary = OrderDetailMaterialSummary(),
)

data class AssemblyActionRequest(val clientOperationId: String, val expectedVersion: Int? = null, val stage: Int? = null, val remark: String? = null)
