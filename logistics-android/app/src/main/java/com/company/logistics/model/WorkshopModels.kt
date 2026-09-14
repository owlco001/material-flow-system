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

data class AssemblyTask(
    val id: String, val orderNo: String, val deviceId: String, val deviceNo: String,
    val materialSummary: String, val status: AssemblyTaskStatus, val progressStage: Int,
    val taskVersion: Int, val currentLaborRecordId: String?, val currentLaborStartedAt: String?,
    val accumulatedLaborMinutes: Int, val assignedAssemblerId: String?, val assignedAssemblerName: String?
)
data class LaborRecord(
    val id: String, val taskId: String?, val type: LaborType, val startedAt: String,
    val endedAt: String?, val durationMinutes: Int?, val remark: String?
)
data class MachineProgress(val deviceId: String, val deviceNo: String, val taskCount: Int, val completedTaskCount: Int, val progressPercent: Int, val laborMinutes: Int)
data class WorkshopProgressSummary(
    val workshopId: String?, val totalTasks: Int, val completedTasks: Int, val overallProgressPercent: Int,
    val totalLaborMinutes: Int, val assemblyLaborMinutes: Int, val temporaryTransferLaborMinutes: Int,
    val machines: List<MachineProgress>, val generatedAt: String
)

data class AssemblyActionRequest(val clientOperationId: String, val expectedVersion: Int? = null, val stage: Int? = null, val remark: String? = null)
