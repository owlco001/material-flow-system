package com.company.logistics.ui.screens

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.AssemblyAction
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.AssemblyTaskStatus
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.MachineProgress
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.WorkspaceViewRole
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing

@Composable
fun AssemblerWorkspaceScreen(
    authenticatedRole: UserRole,
    previewRole: WorkspaceViewRole?,
    tasks: List<AssemblyTask>,
    taskState: WorkspaceLoadState,
    taskError: String?,
    taskUnavailable: Boolean,
    page: Int,
    pageSize: Int,
    total: Int,
    totalPages: Int,
    deviceFilter: String?,
    materialStatus: com.company.logistics.model.OrderMaterialStatus?,
    materialState: WorkspaceLoadState,
    materialError: String?,
    submittingTaskId: String?,
    submittingAction: AssemblyAction?,
    activeLabor: Map<String, LaborRecord>,
    laborSummary: Map<String, com.company.logistics.model.LaborSummaryItem>,
    temporaryTransfer: LaborRecord?,
    temporaryTransferSourceTaskId: String?,
    temporaryTransferSubmitting: Boolean,
    onRefresh: () -> Unit,
    onPreviousPage: () -> Unit,
    onNextPage: () -> Unit,
    onAcceptMaterial: (AssemblyTask) -> Unit,
    onStartWork: (AssemblyTask) -> Unit,
    onProgress: (AssemblyTask, Int) -> Unit,
    onCompleteWork: (AssemblyTask) -> Unit,
    onStartStage: (AssemblyTask, Int) -> Unit,
    onCompleteStage: (AssemblyTask, Int) -> Unit,
    onReworkStage: (AssemblyTask, Int, String) -> Unit,
    onStartTemporaryTransfer: (String?, String) -> Unit,
    onCompleteTemporaryTransfer: (String) -> Unit,
    onEnterPreview: (WorkspaceViewRole) -> Unit,
    onExitPreview: () -> Unit,
    onScanDevice: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var selectorVisible by remember { mutableStateOf(false) }
    var temporaryTransferTaskId by remember { mutableStateOf<String?>(null) }
    var startTransferDialog by remember { mutableStateOf(false) }
    var completeTransferDialog by remember { mutableStateOf(false) }
    var reworkStage by remember { mutableStateOf<Pair<AssemblyTask, Int>?>(null) }

    LaunchedEffect(previewRole) {
        if (previewRole != null) {
            selectorVisible = false
            startTransferDialog = false
            completeTransferDialog = false
        }
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        VSpace(Spacing.sm)
        RoleWorkspaceHeader(
            title = "装配工工作台",
            subtitle = "接受物料、记录装配工时并按 1/2/3 提交进度",
            role = UserRole.ASSEMBLER,
            authenticatedRole = authenticatedRole,
            previewRole = previewRole,
            loading = taskState == WorkspaceLoadState.LOADING || temporaryTransferSubmitting,
            onRefresh = onRefresh,
            onShowPreviewSelector = { selectorVisible = true },
            onExitPreview = onExitPreview,
            onScanDevice = onScanDevice,
        )

        VSpace(Spacing.md)
        AssemblyDataNotice(
            state = taskState,
            error = taskError,
            unavailable = taskUnavailable,
            page = page,
            total = total,
            onRefresh = onRefresh,
        )

        MaterialStatusTable(materialStatus, materialState, materialError, onRefresh)

        temporaryTransfer?.let { transfer ->
            VSpace(Spacing.md)
            TemporaryTransferCard(
                transfer = transfer,
                sourceTaskId = temporaryTransferSourceTaskId,
                readOnly = previewRole != null,
                submitting = temporaryTransferSubmitting,
                onComplete = { completeTransferDialog = true },
            )
        }

        when (taskState) {
            WorkspaceLoadState.IDLE -> EmptyState(
                title = "等待装配任务",
                description = "刷新后从服务端读取分配给当前装配工的任务。",
                action = { PrimaryButton(text = "加载任务", onClick = onRefresh) },
            )
            WorkspaceLoadState.EMPTY -> {
                EmptyState(
                    title = if (deviceFilter != null) {
                        "未匹配到该机台的装配任务"
                    } else {
                        "当前没有装配任务"
                    },
                    description = if (deviceFilter != null) {
                        "当前机台没有匹配任务，请确认机台编号后重新扫码或刷新。"
                    } else {
                        "服务端当前没有返回分配给本人的任务。"
                    },
                    action = { SecondaryButton(text = "刷新任务", onClick = onRefresh) },
                )
            }
            WorkspaceLoadState.ERROR -> EmptyState(
                title = if (taskUnavailable) "装配任务接口不可用" else "装配任务加载失败",
                description = taskError ?: "未能读取服务端任务，请检查网络后重试。",
                action = { PrimaryButton(text = "重试", onClick = onRefresh) },
            )
            WorkspaceLoadState.LOADING, WorkspaceLoadState.CONTENT -> {
                tasks.forEach { task ->
                    VSpace(Spacing.md)
                    AssemblyTaskCard(
                        task = task,
                        activeLabor = activeLabor[task.id],
                        laborSummary = laborSummary[task.id] ?: laborSummary.values.firstOrNull { it.taskId == task.id },
                        temporaryTransfer = temporaryTransfer,
                        submitting = submittingTaskId == task.id,
                        submittingAction = submittingAction,
                        readOnly = previewRole != null,
                        onAcceptMaterial = { onAcceptMaterial(task) },
                        onStartWork = { onStartWork(task) },
                        onProgress = { stage -> onProgress(task, stage) },
                        onCompleteWork = { onCompleteWork(task) },
                        onStartStage = { stage -> onStartStage(task, stage) },
                        onCompleteStage = { stage -> onCompleteStage(task, stage) },
                        onReworkStage = { stage -> reworkStage = task to stage },
                        onStartTemporaryTransfer = {
                            temporaryTransferTaskId = task.id
                            startTransferDialog = true
                        },
                    )
                }
                if (tasks.isNotEmpty()) {
                    VSpace(Spacing.md)
                    AssemblyPager(
                        page = page,
                        totalPages = totalPages,
                        total = total,
                        enabled = taskState != WorkspaceLoadState.LOADING,
                        onPrevious = onPreviousPage,
                        onNext = onNextPage,
                    )
                }
            }
        }

        if (previewRole == null && activeLabor.isEmpty() && temporaryTransfer?.status?.equals("ACTIVE", ignoreCase = true) != true && taskState != WorkspaceLoadState.ERROR) {
            VSpace(Spacing.md)
            SecondaryButton(
                text = "无任务临时调拨",
                onClick = {
                    temporaryTransferTaskId = null
                    startTransferDialog = true
                },
            )
        }
        VSpace(Spacing.xxl)
    }

    if (selectorVisible) {
        AdminRolePreviewDialog(
            onDismiss = { selectorVisible = false },
            onSelect = {
                selectorVisible = false
                onEnterPreview(it)
            },
        )
    }
    if (startTransferDialog) {
        TemporaryTransferStartDialog(
            submitting = temporaryTransferSubmitting,
            onDismiss = { if (!temporaryTransferSubmitting) startTransferDialog = false },
            onSubmit = { remark ->
                startTransferDialog = false
                onStartTemporaryTransfer(temporaryTransferTaskId, remark)
            },
        )
    }
    if (completeTransferDialog) {
        TemporaryTransferCompleteDialog(
            submitting = temporaryTransferSubmitting,
            onDismiss = { if (!temporaryTransferSubmitting) completeTransferDialog = false },
            onSubmit = { remark ->
                completeTransferDialog = false
                onCompleteTemporaryTransfer(remark)
            },
        )
    }
    reworkStage?.let { (task, stage) ->
        AssemblyReworkDialog(
            submitting = submittingTaskId == task.id,
            onDismiss = { if (submittingTaskId != task.id) reworkStage = null },
            onSubmit = { reason -> reworkStage = null; onReworkStage(task, stage, reason) },
        )
    }
}

@Composable
fun WorkshopSupervisorScreen(
    authenticatedRole: UserRole,
    previewRole: WorkspaceViewRole?,
    summary: WorkshopProgressSummary?,
    summaryState: WorkspaceLoadState,
    summaryError: String?,
    summaryUnavailable: Boolean,
    machines: List<MachineProgress>,
    machineState: WorkspaceLoadState,
    machineError: String?,
    machineUnavailable: Boolean,
    page: Int,
    pageSize: Int,
    hasNextPage: Boolean,
    onRefresh: () -> Unit,
    onPreviousPage: () -> Unit,
    onNextPage: () -> Unit,
    onEnterPreview: (WorkspaceViewRole) -> Unit,
    onExitPreview: () -> Unit,
    laborSummary: com.company.logistics.model.LaborSummaryPage?,
    laborState: WorkspaceLoadState,
    laborError: String?,
    laborDeviceFilter: String?,
    onRefreshLabor: (String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    var selectorVisible by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        VSpace(Spacing.sm)
        RoleWorkspaceHeader(
            title = "车间主管工作台",
            subtitle = "只读查看总工时、机台进度与订单总进度",
            role = UserRole.WORKSHOP_SUPERVISOR,
            authenticatedRole = authenticatedRole,
            previewRole = previewRole,
            loading = summaryState == WorkspaceLoadState.LOADING || machineState == WorkspaceLoadState.LOADING,
            onRefresh = onRefresh,
            onShowPreviewSelector = { selectorVisible = true },
            onExitPreview = onExitPreview,
        )

        VSpace(Spacing.md)
        WorkshopDataNotice(
            summaryState = summaryState,
            summaryError = summaryError,
            summaryUnavailable = summaryUnavailable,
            machineState = machineState,
            machineError = machineError,
            machineUnavailable = machineUnavailable,
            generatedAt = summary?.generatedAt,
            onRefresh = onRefresh,
        )

        WorkshopLaborSummarySection(laborSummary, laborState, laborError, laborDeviceFilter, onRefreshLabor, previewRole != null)

        when (summaryState) {
            WorkspaceLoadState.LOADING -> LoadingRow("正在加载服务端工时统计…")
            WorkspaceLoadState.ERROR -> Unit
            WorkspaceLoadState.IDLE -> EmptyState(
                title = "等待车间统计",
                description = "刷新后从服务端读取统计事实。",
                action = { PrimaryButton(text = "加载统计", onClick = onRefresh) },
            )
            WorkspaceLoadState.EMPTY, WorkspaceLoadState.CONTENT -> summary?.let {
                VSpace(Spacing.md)
                LaborSummaryCards(it)
            }
        }

        VSpace(Spacing.lg)
        Text("机台进度", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
        when (machineState) {
            WorkspaceLoadState.LOADING -> LoadingRow("正在加载机台进度…")
            WorkspaceLoadState.ERROR -> EmptyState(
                title = if (machineUnavailable) "机台进度接口不可用" else "机台进度加载失败",
                description = machineError ?: "未能读取服务端机台进度。",
                action = { PrimaryButton(text = "重试", onClick = onRefresh) },
            )
            WorkspaceLoadState.EMPTY -> EmptyState(
                title = "没有机台任务",
                description = "服务端当前没有返回机台进度。",
                action = { SecondaryButton(text = "刷新统计", onClick = onRefresh) },
            )
            WorkspaceLoadState.IDLE -> Unit
            WorkspaceLoadState.CONTENT -> machines.forEach { MachineProgressCard(it) }
        }
        if (machineState == WorkspaceLoadState.CONTENT) {
            VSpace(Spacing.md)
            MachinePager(
                page = page,
                pageSize = pageSize,
                hasNext = hasNextPage,
                onPrevious = onPreviousPage,
                onNext = onNextPage,
            )
        }
        VSpace(Spacing.xxl)
    }

    if (selectorVisible) {
        AdminRolePreviewDialog(
            onDismiss = { selectorVisible = false },
            onSelect = {
                selectorVisible = false
                onEnterPreview(it)
            },
        )
    }
}

@Composable
private fun WorkshopLaborSummarySection(
    summary: com.company.logistics.model.LaborSummaryPage?,
    state: WorkspaceLoadState,
    error: String?,
    deviceFilter: String?,
    onRefresh: (String?) -> Unit,
    readOnly: Boolean,
) {
    var deviceId by remember(deviceFilter) { mutableStateOf(deviceFilter.orEmpty()) }
    VSpace(Spacing.md)
    SectionTitle("工时汇总 / 机台筛选", trailing = "服务端明细")
    OutlinedTextField(value = deviceId, onValueChange = { deviceId = it }, label = { Text("机台 deviceId") }, singleLine = true, enabled = !readOnly && state != WorkspaceLoadState.LOADING, modifier = Modifier.fillMaxWidth())
    VSpace(Spacing.sm)
    PrimaryButton(text = if (state == WorkspaceLoadState.LOADING) "加载中…" else "加载工时汇总", onClick = { onRefresh(deviceId.trim().ifBlank { null }) }, enabled = !readOnly && state != WorkspaceLoadState.LOADING)
    if (readOnly) Text("预览态只读", color = LogisticsTheme.colors.warning, fontSize = 12.sp)
    when (state) {
        WorkspaceLoadState.LOADING -> LoadingRow("正在加载服务端人员/任务工时…")
        WorkspaceLoadState.ERROR -> EmptyState("工时汇总加载失败", error ?: "请检查网络后重试", action = { PrimaryButton(text = "重试", onClick = { onRefresh(deviceId.trim().ifBlank { null }) }) })
        WorkspaceLoadState.IDLE -> EmptyState("等待工时汇总", "输入 deviceId 或直接加载服务端明细")
        WorkspaceLoadState.EMPTY -> EmptyState("暂无工时明细", "服务端没有返回符合机台筛选条件的记录")
        WorkspaceLoadState.CONTENT -> {
            Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                Column(Modifier.width(860.dp)) {
                    LaborSummaryRow("人员", "任务", "机台", "装配分钟", "临时调拨分钟", "总分钟", true)
                    summary?.items.orEmpty().forEach { item -> LaborSummaryRow(item.assemblerName ?: item.assemblerId ?: "—", item.taskId ?: item.orderNo ?: "—", item.deviceNo ?: item.deviceId ?: "—", item.assemblyLaborMinutes?.toString() ?: "—", item.temporaryTransferLaborMinutes?.toString() ?: "—", item.totalLaborMinutes?.toString() ?: "—") }
                }
            }
        }
    }
}

@Composable
private fun LaborSummaryRow(person: String, task: String, device: String, assembly: String, transfer: String, total: String, header: Boolean = false) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp, horizontal = 8.dp)) {
        listOf(person to 150.dp, task to 170.dp, device to 130.dp, assembly to 120.dp, transfer to 150.dp, total to 100.dp).forEach { (value, width) -> Text(value, Modifier.width(width), fontSize = if (header) 11.sp else 12.sp, fontWeight = if (header) FontWeight.Bold else FontWeight.Normal, color = if (header) LogisticsTheme.colors.textSecondary else LogisticsTheme.colors.textPrimary) }
    }
}

@Composable
private fun MaterialStatusTable(
    status: com.company.logistics.model.OrderMaterialStatus?,
    state: WorkspaceLoadState,
    error: String?,
    onRetry: () -> Unit,
) {
    VSpace(Spacing.md)
    SectionTitle("机台物料情况", trailing = "服务端状态")
    when (state) {
        WorkspaceLoadState.LOADING -> LoadingRow("正在加载机台物料…")
        WorkspaceLoadState.ERROR -> EmptyState("物料加载失败", error ?: "请检查网络后重试", action = { PrimaryButton(text = "重试", onClick = onRetry) })
        WorkspaceLoadState.EMPTY, WorkspaceLoadState.IDLE -> EmptyState("暂无物料数据", "服务端未返回当前机台物料")
        WorkspaceLoadState.CONTENT -> {
            val items = status?.items.orEmpty()
            Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                Column(Modifier.width(760.dp)) {
                    AssemblyMaterialRow("物料编码", "物料名称", "单位", "需求", "已到货", "已入库", "缺口", "状态", true)
                    items.forEach { item -> AssemblyMaterialRow(item.materialCode, item.name, item.unit, item.requiredQuantity.toString(), item.arrivedQuantity.toString(), item.inStockQuantity.toString(), item.shortageQuantity.toString(), "${item.effectiveStatusCode} · ${item.effectiveStatusLabel}") }
                }
            }
        }
    }
}
@Composable
private fun AssemblyMaterialRow(code: String, name: String, unit: String, required: String, arrived: String, inStock: String, shortage: String, status: String, header: Boolean = false) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp, horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        listOf(code to 120.dp, name to 150.dp, unit to 60.dp, required to 60.dp, arrived to 70.dp, inStock to 70.dp, shortage to 60.dp, status to 170.dp).forEach { (value, width) ->
            Text(value, Modifier.width(width), fontSize = if (header) 11.sp else 12.sp, fontWeight = if (header) FontWeight.Bold else FontWeight.Normal, color = if (header) LogisticsTheme.colors.textSecondary else LogisticsTheme.colors.textPrimary)
        }
    }
}

@Composable
private fun RoleWorkspaceHeader(
    title: String,
    subtitle: String,
    role: UserRole,
    authenticatedRole: UserRole,
    previewRole: WorkspaceViewRole?,
    loading: Boolean,
    onRefresh: () -> Unit,
    onShowPreviewSelector: () -> Unit,
    onExitPreview: () -> Unit,
    onScanDevice: () -> Unit = {},
) {
    AppCard(accentColor = MaterialTheme.colorScheme.primary) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = 20.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(6.dp)
                Text(subtitle, fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            }
            SecondaryButton(
                text = "刷新",
                onClick = onRefresh,
                enabled = !loading,
                modifier = Modifier.width(84.dp),
            )
        }
        VSpace(Spacing.md)
        StatusTag(
            label = role.label,
            color = MaterialTheme.colorScheme.primary,
            containerColor = LogisticsTheme.colors.primaryContainer,
            symbol = "●",
        )
        if (role == UserRole.ASSEMBLER) {
            VSpace(Spacing.sm)
            PrimaryButton(text = "扫码机台码", onClick = onScanDevice)
        }
        if (AdminRolePreviewUiPolicy.canShowEntry(authenticatedRole)) {
            VSpace(Spacing.sm)
            if (previewRole == null) {
                SecondaryButton(text = "测试角色视图", onClick = onShowPreviewSelector)
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        AdminRolePreviewUiPolicy.BANNER_TEXT,
                        modifier = Modifier.weight(1f),
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.warning,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.width(Spacing.sm))
                    SecondaryButton(text = "恢复 ADMIN", onClick = onExitPreview, modifier = Modifier.width(112.dp))
                }
            }
        }
    }
}

@Composable
private fun AssemblyDataNotice(
    state: WorkspaceLoadState,
    error: String?,
    unavailable: Boolean,
    page: Int,
    total: Int,
    onRefresh: () -> Unit,
) {
    AppCard(accentColor = if (state == WorkspaceLoadState.ERROR) LogisticsTheme.colors.warning else LogisticsTheme.colors.success) {
        Text(
            when {
                unavailable -> "装配任务接口不可用"
                state == WorkspaceLoadState.ERROR -> "装配任务读取失败"
                state == WorkspaceLoadState.CONTENT || state == WorkspaceLoadState.EMPTY -> "已连接服务端装配任务"
                else -> "正在读取服务端装配任务"
            },
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary,
        )
        VSpace(4.dp)
        Text(
            if (state == WorkspaceLoadState.ERROR) error ?: "请检查网络后重试" else "当前页 $page · 服务端总计 $total · 每页固定 20 条",
            fontSize = 11.sp,
            color = if (state == WorkspaceLoadState.ERROR) LogisticsTheme.colors.danger else LogisticsTheme.colors.textTertiary,
        )
        if (state == WorkspaceLoadState.ERROR) {
            VSpace(Spacing.sm)
            SecondaryButton(text = "重试", onClick = onRefresh)
        }
    }
}

@Composable
private fun AssemblyTaskCard(
    task: AssemblyTask,
    activeLabor: LaborRecord?,
    laborSummary: com.company.logistics.model.LaborSummaryItem?,
    temporaryTransfer: LaborRecord?,
    submitting: Boolean,
    submittingAction: AssemblyAction?,
    readOnly: Boolean,
    onAcceptMaterial: () -> Unit,
    onStartWork: () -> Unit,
    onProgress: (Int) -> Unit,
    onCompleteWork: () -> Unit,
    onStartStage: (Int) -> Unit,
    onCompleteStage: (Int) -> Unit,
    onReworkStage: (Int) -> Unit,
    onStartTemporaryTransfer: () -> Unit,
) {
    val statusColor = when (task.status) {
        AssemblyTaskStatus.WAITING_MATERIAL -> LogisticsTheme.colors.warning
        AssemblyTaskStatus.MATERIAL_ACCEPTED -> LogisticsTheme.colors.info
        AssemblyTaskStatus.IN_PROGRESS -> LogisticsTheme.colors.success
        AssemblyTaskStatus.PAUSED_FOR_TEMPORARY_TRANSFER -> LogisticsTheme.colors.warning
        AssemblyTaskStatus.COMPLETED -> LogisticsTheme.colors.success
    }
    val nextStage = (task.progressStage + 1).takeIf { it in 1..3 }
    AppCard(accentColor = statusColor) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("订单 ${task.orderNo}", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text("机台 ${task.deviceNo} · 任务 ${task.id}", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
            StatusTag(
                label = task.status.label,
                color = statusColor,
                containerColor = statusColor.copy(alpha = 0.12f),
                symbol = task.statusSymbol(),
            )
        }
        VSpace(Spacing.sm)
        Text("任务版本 ${task.taskVersion}", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        VSpace(Spacing.sm)
        ProgressStages(task.stages)
        if (!readOnly && task.status != AssemblyTaskStatus.COMPLETED) {
            task.stages.sortedBy { it.stageNo }.forEach { stage ->
                VSpace(4.dp)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("阶段 ${stage.stageNo}：${stage.status.label}", modifier = Modifier.weight(1f), fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                    when (stage.status) {
                        com.company.logistics.model.AssemblyStageStatus.NOT_STARTED,
                        com.company.logistics.model.AssemblyStageStatus.REWORK_REQUIRED -> SecondaryButton(text = "开工", onClick = { onStartStage(stage.stageNo) }, enabled = !submitting, modifier = Modifier.width(84.dp))
                        com.company.logistics.model.AssemblyStageStatus.IN_PROGRESS -> SecondaryButton(text = "完工", onClick = { onCompleteStage(stage.stageNo) }, enabled = !submitting, modifier = Modifier.width(84.dp))
                        else -> Unit
                    }
                    Spacer(Modifier.width(4.dp))
                    if (stage.status != com.company.logistics.model.AssemblyStageStatus.COMPLETED) TextButton(onClick = { onReworkStage(stage.stageNo) }, enabled = !submitting) { Text("返工") }
                }
            }
        }
        Text(
            if (laborSummary != null) {
                "装配工时：${laborSummary.assemblyLaborMinutes?.let { "$it 分钟" } ?: "无数据"} · 临时调拨工时：${laborSummary.temporaryTransferLaborMinutes?.let { "$it 分钟" } ?: "无数据"} · 总工时：${laborSummary.totalLaborMinutes?.let { "$it 分钟" } ?: "无数据"}"
            } else {
                "工时汇总：无数据"
            },
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textSecondary,
        )

        if (!readOnly && task.status != AssemblyTaskStatus.COMPLETED) {
            VSpace(Spacing.sm)
            when (task.status) {
                AssemblyTaskStatus.WAITING_MATERIAL -> PrimaryButton(
                    text = "接受物料",
                    onClick = onAcceptMaterial,
                    loading = submitting && submittingAction == AssemblyAction.ACCEPT_MATERIAL,
                )
                AssemblyTaskStatus.MATERIAL_ACCEPTED,
                AssemblyTaskStatus.PAUSED_FOR_TEMPORARY_TRANSFER -> PrimaryButton(
                    text = if (task.status == AssemblyTaskStatus.PAUSED_FOR_TEMPORARY_TRANSFER) "继续开工" else "开工",
                    onClick = onStartWork,
                    loading = submitting && submittingAction == AssemblyAction.START_WORK,
                )
                AssemblyTaskStatus.IN_PROGRESS -> {
                    nextStage?.let { stage ->
                        PrimaryButton(
                            text = "提交进度 $stage",
                            onClick = { onProgress(stage) },
                            loading = submitting && submittingAction == AssemblyAction.PROGRESS,
                        )
                        VSpace(Spacing.sm)
                    }
                    SecondaryButton(
                        text = "完工",
                        onClick = onCompleteWork,
                        enabled = !submitting,
                    )
                }
                AssemblyTaskStatus.COMPLETED -> Unit
            }
            if (temporaryTransfer?.status?.equals("ACTIVE", ignoreCase = true) != true) {
                VSpace(Spacing.sm)
                SecondaryButton(text = "开始临时调拨", onClick = onStartTemporaryTransfer, enabled = !submitting)
            }
        }
    }
}

@Composable
private fun AssemblyReworkDialog(
    submitting: Boolean,
    onDismiss: () -> Unit,
    onSubmit: (String) -> Unit,
) {
    var reason by remember { mutableStateOf("") }
    val valid = reason.trim().length in 1..500
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("提交返工") },
        text = {
            OutlinedTextField(value = reason, onValueChange = { if (it.length <= 500) reason = it }, label = { Text("返工原因（必填）") }, supportingText = { Text("${reason.length}/500") }, modifier = Modifier.fillMaxWidth(), singleLine = false)
        },
        confirmButton = { TextButton(onClick = { onSubmit(reason.trim()) }, enabled = valid && !submitting) { Text(if (submitting) "提交中…" else "提交") } },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("返回") } },
    )
}

@Composable
private fun ProgressStages(stages: List<com.company.logistics.model.AssemblyStage>) {
    Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm), verticalAlignment = Alignment.CenterVertically) {
        stages.sortedBy { it.stageNo }.forEach { stage ->
            val color = when (stage.status) {
                com.company.logistics.model.AssemblyStageStatus.COMPLETED -> LogisticsTheme.colors.success
                com.company.logistics.model.AssemblyStageStatus.IN_PROGRESS -> LogisticsTheme.colors.info
                com.company.logistics.model.AssemblyStageStatus.REWORK_REQUIRED -> LogisticsTheme.colors.warning
                else -> LogisticsTheme.colors.textTertiary
            }
            StatusTag(label = "阶段 ${stage.stageNo} · ${stage.status.label}", color = color, containerColor = color.copy(alpha = 0.12f), symbol = if (stage.status == com.company.logistics.model.AssemblyStageStatus.COMPLETED) "✓" else "○")
        }
    }
}

@Composable
private fun TemporaryTransferCard(
    transfer: LaborRecord,
    sourceTaskId: String?,
    readOnly: Boolean,
    submitting: Boolean,
    onComplete: () -> Unit,
) {
    val active = transfer.status.equals("ACTIVE", ignoreCase = true)
    AppCard(accentColor = LogisticsTheme.colors.purple) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("独立临时调拨工时", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text(
                    if (sourceTaskId.isNullOrBlank()) "来源任务：无（独立工作）" else "来源任务：$sourceTaskId",
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            StatusTag(
                label = if (active) "进行中" else "已完成",
                color = if (active) LogisticsTheme.colors.warning else LogisticsTheme.colors.success,
                containerColor = if (active) LogisticsTheme.colors.warning.copy(alpha = 0.12f) else LogisticsTheme.colors.success.copy(alpha = 0.12f),
                symbol = if (active) "▶" else "✓",
            )
        }
        VSpace(Spacing.sm)
        Text("调拨 ID：${transfer.temporaryTransferId ?: "服务端未返回"}", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        transfer.remark?.takeIf { it.isNotBlank() }?.let {
            Text("备注：$it", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
        }
        Text(
            "工时：${when {
                transfer.durationMinutes != null -> "${transfer.durationMinutes} 分钟"
                active -> laborMinutesText(transfer)
                else -> "服务端响应未提供分钟数"
            }}",
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textSecondary,
        )
        if (transfer.startedAt.isNotBlank()) Text("服务端开始时间：${transfer.startedAt}", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        if (active && !readOnly) {
            VSpace(Spacing.sm)
            PrimaryButton(text = "完成临时调拨", onClick = onComplete, enabled = !submitting, loading = submitting)
        } else if (!active) {
            VSpace(4.dp)
            Text("独立调拨工时不会计入原装配任务工时。", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        }
    }
}

@Composable
private fun TemporaryTransferStartDialog(
    submitting: Boolean,
    onDismiss: () -> Unit,
    onSubmit: (String) -> Unit,
) {
    var remark by remember { mutableStateOf("") }
    val valid = remark.trim().length in 1..500
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("开始临时调拨") },
        text = {
            OutlinedTextField(
                value = remark,
                onValueChange = { if (it.length <= 500) remark = it },
                label = { Text("备注（必填）") },
                supportingText = { Text("${remark.length}/500") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = false,
            )
        },
        confirmButton = { TextButton(onClick = { onSubmit(remark.trim()) }, enabled = valid && !submitting) { Text(if (submitting) "提交中…" else "开始") } },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("返回") } },
    )
}

@Composable
private fun TemporaryTransferCompleteDialog(
    submitting: Boolean,
    onDismiss: () -> Unit,
    onSubmit: (String) -> Unit,
) {
    var remark by remember { mutableStateOf("") }
    val valid = remark.trim().length in 1..500
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("完成临时调拨") },
        text = {
            OutlinedTextField(
                value = remark,
                onValueChange = { if (it.length <= 500) remark = it },
                label = { Text("完成备注（必填）") },
                supportingText = { Text("${remark.length}/500") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = false,
            )
        },
        confirmButton = { TextButton(onClick = { onSubmit(remark.trim()) }, enabled = valid && !submitting) { Text(if (submitting) "提交中…" else "完成") } },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("返回") } },
    )
}

@Composable
private fun AssemblyPager(
    page: Int,
    totalPages: Int,
    total: Int,
    enabled: Boolean,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        SecondaryButton(text = "上一页", onClick = onPrevious, enabled = enabled && page > 1, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(Spacing.sm))
        Text("$page / ${totalPages.coerceAtLeast(1)} · 共 $total", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
        Spacer(Modifier.width(Spacing.sm))
        SecondaryButton(text = "下一页", onClick = onNext, enabled = enabled && page < totalPages, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun WorkshopDataNotice(
    summaryState: WorkspaceLoadState,
    summaryError: String?,
    summaryUnavailable: Boolean,
    machineState: WorkspaceLoadState,
    machineError: String?,
    machineUnavailable: Boolean,
    generatedAt: String?,
    onRefresh: () -> Unit,
) {
    AppCard(accentColor = if (summaryState == WorkspaceLoadState.ERROR || machineState == WorkspaceLoadState.ERROR) LogisticsTheme.colors.warning else LogisticsTheme.colors.success) {
        Text(
            when {
                summaryUnavailable || machineUnavailable -> "车间统计接口不可用"
                summaryState == WorkspaceLoadState.ERROR || machineState == WorkspaceLoadState.ERROR -> "车间统计读取失败"
                summaryState == WorkspaceLoadState.CONTENT && machineState == WorkspaceLoadState.CONTENT -> "已连接服务端车间统计"
                else -> "正在读取服务端车间统计"
            },
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary,
        )
        VSpace(4.dp)
        Text(
            summaryError ?: machineError ?: "统计与机台进度分别来自服务端接口；客户端不计算总进度",
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
        generatedAt?.let {
            VSpace(4.dp)
            Text("服务端时间：$it", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        }
        if (summaryState == WorkspaceLoadState.ERROR || machineState == WorkspaceLoadState.ERROR) {
            VSpace(Spacing.sm)
            SecondaryButton(text = "重试", onClick = onRefresh)
        }
    }
}

@Composable
private fun LaborSummaryCards(summary: WorkshopProgressSummary) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
        LaborMetricCard("总工时（分钟）", summary.totalLaborMinutes, "装配工时 + 临时调拨工时", LogisticsTheme.colors.primary)
        LaborMetricCard("装配工时（分钟）", summary.assemblyLaborMinutes, "服务端 ASSEMBLY 汇总", LogisticsTheme.colors.info)
        LaborMetricCard("临时调拨工时（分钟）", summary.temporaryTransferLaborMinutes, "独立 TEMPORARY_TRANSFER 汇总", LogisticsTheme.colors.purple)
        LaborMetricCard("订单总进度", summary.overallProgressPercent, "服务端完成任务与阶段汇总", LogisticsTheme.colors.success, suffix = "%")
        Text("任务总数 ${summary.totalTasks} · 已完工 ${summary.completedTasks}", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
    }
}

@Composable
private fun LaborMetricCard(title: String, value: Int, hint: String, color: androidx.compose.ui.graphics.Color, suffix: String = "") {
    AppCard(accentColor = color) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text(hint, fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
            }
            Text("$value$suffix", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = color)
        }
    }
}

@Composable
private fun MachineProgressCard(machine: MachineProgress) {
    AppCard(accentColor = LogisticsTheme.colors.info) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("机台 ${machine.deviceNo}", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text("设备 ${machine.deviceId}", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
            }
            Text("${machine.progressPercent}%", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.info)
        }
        VSpace(Spacing.sm)
        Text(
            "任务 ${machine.completedTaskCount}/${machine.taskCount} 已完工 · " +
                (machine.laborMinutes?.let { "工时 $it 分钟" } ?: "服务端未返回机台工时"),
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textSecondary,
        )
    }
}

@Composable
private fun MachinePager(
    page: Int,
    pageSize: Int,
    hasNext: Boolean,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        SecondaryButton(text = "上一页", onClick = onPrevious, enabled = page > 1, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(Spacing.sm))
        Text("第 $page 页 · 每页 $pageSize 条", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
        Spacer(Modifier.width(Spacing.sm))
        SecondaryButton(text = "下一页", onClick = onNext, enabled = hasNext, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun LoadingRow(text: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = Spacing.lg),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CircularProgressIndicator(modifier = Modifier.width(24.dp), strokeWidth = 2.dp)
        Spacer(Modifier.width(Spacing.sm))
        Text(text, fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
    }
}
