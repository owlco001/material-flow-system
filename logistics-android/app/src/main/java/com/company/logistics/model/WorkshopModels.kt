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

data class AssemblyTask(
    val id: String, val orderNo: String = "", val deviceId: String, val deviceNo: String,
    val materialSummary: String = "", val status: AssemblyTaskStatus, val progressStage: Int,
    val taskVersion: Int, val currentLaborRecordId: String? = null, val currentLaborStartedAt: String? = null,
    val accumulatedLaborMinutes: Int? = null, val assignedAssemblerId: String? = null, val assignedAssemblerName: String? = null,
    val serverTime: String? = null,
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

data class OrderDetail(
    val orderId: String, val orderNo: String, val productName: String?, val orderStatus: String?,
    val materials: List<OrderMaterialItem>, val assemblyTasks: List<AssemblyTask>,
    val laborSummary: OrderDetailLaborSummary, val timeline: List<OrderDetailTimelineEvent>,
    val page: Int, val pageSize: Int, val total: Int
)

data class AssemblyActionRequest(val clientOperationId: String, val expectedVersion: Int? = null, val stage: Int? = null, val remark: String? = null)
