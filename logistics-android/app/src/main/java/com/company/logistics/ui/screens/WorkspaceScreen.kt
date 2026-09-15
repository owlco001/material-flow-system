package com.company.logistics.ui.screens

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.Image
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.RoleWorkspaceSummary
import com.company.logistics.model.AssemblyAction
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.LaborRecord
import com.company.logistics.model.MachineProgress
import com.company.logistics.model.WorkshopProgressSummary
import com.company.logistics.model.HandoverAction
import com.company.logistics.model.HandoverActionPolicy
import com.company.logistics.model.HandoverTimeline
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItem
import com.company.logistics.model.WorkspaceMetric
import com.company.logistics.model.WorkspaceMetricKey
import com.company.logistics.model.WorkspaceViewRole
import com.company.logistics.R
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

private data class WorkspaceEntry(
    val key: WorkspaceMetricKey,
    val title: String,
    val hint: String,
    val actionLabel: String,
    val actionIsPrimary: Boolean = false
)

internal fun AssemblyTask.statusSymbol(): String = when (status) {
    com.company.logistics.model.AssemblyTaskStatus.WAITING_MATERIAL -> "!"
    com.company.logistics.model.AssemblyTaskStatus.MATERIAL_ACCEPTED -> "✓"
    com.company.logistics.model.AssemblyTaskStatus.IN_PROGRESS -> "▶"
    com.company.logistics.model.AssemblyTaskStatus.PAUSED_FOR_TEMPORARY_TRANSFER -> "Ⅱ"
    com.company.logistics.model.AssemblyTaskStatus.COMPLETED -> "✓"
}

internal fun laborMinutesText(record: LaborRecord?): String = when {
    record == null -> "—"
    record.durationMinutes != null -> "${record.durationMinutes} 分钟"
    record.startedAt.isNotBlank() -> "进行中 · 开始 ${record.startedAt}"
    else -> "进行中"
}

/**
 * 角色工作台 —— 摘要和工作项均来自独立的服务端工作台接口。
 *
 * [items] 只包含当前分页，不在客户端聚合或加载全量工作项；[total] 是服务端返回的总数。
 */
@Composable
fun WorkspaceScreen(
    role: UserRole,
    authenticatedRole: UserRole,
    previewRole: WorkspaceViewRole?,
    summary: RoleWorkspaceSummary,
    items: List<WorkspaceMaterialItem>,
    summaryState: WorkspaceLoadState,
    summaryError: String?,
    summaryUnavailable: Boolean,
    itemsState: WorkspaceLoadState,
    itemsError: String?,
    itemsUnavailable: Boolean,
    page: Int,
    pageSize: Int,
    total: Int,
    totalPages: Int,
    serverTime: String?,
    onRefresh: () -> Unit,
    onPageSizeChange: (Int) -> Unit,
    onNextPage: () -> Unit,
    onPreviousPage: () -> Unit,
    onOpenApproval: () -> Unit,
    onOpenAudit: () -> Unit,
    currentUserId: String?,
    timelineItemId: String?,
    timeline: HandoverTimeline?,
    timelineState: WorkspaceLoadState,
    timelineError: String?,
    handoverSubmittingId: String?,
    onOpenTimeline: (WorkspaceMaterialItem) -> Unit,
    onRetryTimeline: () -> Unit,
    onHandoverAction: (WorkspaceMaterialItem, HandoverAction, String?) -> Unit,
    onCreateHandover: (WorkspaceMaterialItem, Int, String, String?) -> Unit,
    onEnterPreview: (WorkspaceViewRole) -> Unit,
    onExitPreview: () -> Unit,
    onOpenEndpointConfig: () -> Unit = {},
    endpointConfigured: Boolean = true,
    modifier: Modifier = Modifier,
    assemblyTasks: List<AssemblyTask> = emptyList(),
    assemblyTaskState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    assemblyTaskError: String? = null,
    assemblyTaskUnavailable: Boolean = false,
    assemblyTaskPage: Int = 1,
    assemblyTaskPageSize: Int = 20,
    assemblyTaskTotal: Int = 0,
    assemblyTaskTotalPages: Int = 0,
    assemblySubmittingTaskId: String? = null,
    assemblySubmittingAction: AssemblyAction? = null,
    assemblyActiveLabor: Map<String, LaborRecord> = emptyMap(),
    temporaryTransfer: LaborRecord? = null,
    lastCompletedTemporaryTransfer: LaborRecord? = null,
    temporaryTransferSourceTaskId: String? = null,
    temporaryTransferSubmitting: Boolean = false,
    workshopProgressSummary: WorkshopProgressSummary? = null,
    workshopSummaryState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    workshopSummaryError: String? = null,
    workshopSummaryUnavailable: Boolean = false,
    workshopMachineProgress: List<MachineProgress> = emptyList(),
    workshopMachineState: WorkspaceLoadState = WorkspaceLoadState.IDLE,
    workshopMachineError: String? = null,
    workshopMachineUnavailable: Boolean = false,
    workshopMachinePage: Int = 1,
    workshopMachinePageSize: Int = 20,
    workshopMachineHasNext: Boolean = false,
    onAcceptAssemblyMaterial: (AssemblyTask) -> Unit = {},
    onStartAssemblyWork: (AssemblyTask) -> Unit = {},
    onSubmitAssemblyProgress: (AssemblyTask, Int) -> Unit = { _, _ -> },
    onCompleteAssemblyWork: (AssemblyTask) -> Unit = {},
    onStartTemporaryTransfer: (String?, String) -> Unit = { _, _ -> },
    onCompleteTemporaryTransfer: (String) -> Unit = {},
    onScanAssemblyDevice: () -> Unit = {},
    onSubmitException: (WorkspaceMaterialItem, String, Int, String?) -> Unit = { _, _, _, _ -> },
    exceptionSubmitting: Boolean = false,
) {
    val entries = entriesFor(role)
    var createItem by remember { mutableStateOf<WorkspaceMaterialItem?>(null) }
    var reasonRequest by remember { mutableStateOf<ReasonRequest?>(null) }
    var selectorVisible by remember { mutableStateOf(false) }
    var exceptionItem by remember { mutableStateOf<WorkspaceMaterialItem?>(null) }

    LaunchedEffect(previewRole) {
        if (previewRole != null) {
            createItem = null
            reasonRequest = null
        }
    }

    if (role == UserRole.ASSEMBLER) {
        AssemblerWorkspaceScreen(
            authenticatedRole = authenticatedRole,
            previewRole = previewRole,
            tasks = assemblyTasks,
            taskState = assemblyTaskState,
            taskError = assemblyTaskError,
            taskUnavailable = assemblyTaskUnavailable,
            page = assemblyTaskPage,
            pageSize = assemblyTaskPageSize,
            total = assemblyTaskTotal,
            totalPages = assemblyTaskTotalPages,
            submittingTaskId = assemblySubmittingTaskId,
            submittingAction = assemblySubmittingAction,
            activeLabor = assemblyActiveLabor,
            temporaryTransfer = temporaryTransfer ?: lastCompletedTemporaryTransfer,
            temporaryTransferSourceTaskId = temporaryTransferSourceTaskId,
            temporaryTransferSubmitting = temporaryTransferSubmitting,
            onRefresh = onRefresh,
            onPreviousPage = onPreviousPage,
            onNextPage = onNextPage,
            onAcceptMaterial = onAcceptAssemblyMaterial,
            onStartWork = onStartAssemblyWork,
            onProgress = onSubmitAssemblyProgress,
            onCompleteWork = onCompleteAssemblyWork,
            onStartTemporaryTransfer = onStartTemporaryTransfer,
            onCompleteTemporaryTransfer = onCompleteTemporaryTransfer,
            onEnterPreview = onEnterPreview,
            onExitPreview = onExitPreview,
            onScanDevice = onScanAssemblyDevice,
            modifier = modifier,
        )
        return
    }

    if (role == UserRole.WORKSHOP_SUPERVISOR) {
        WorkshopSupervisorScreen(
            authenticatedRole = authenticatedRole,
            previewRole = previewRole,
            summary = workshopProgressSummary,
            summaryState = workshopSummaryState,
            summaryError = workshopSummaryError,
            summaryUnavailable = workshopSummaryUnavailable,
            machines = workshopMachineProgress,
            machineState = workshopMachineState,
            machineError = workshopMachineError,
            machineUnavailable = workshopMachineUnavailable,
            page = workshopMachinePage,
            pageSize = workshopMachinePageSize,
            hasNextPage = workshopMachineHasNext,
            onRefresh = onRefresh,
            onPreviousPage = onPreviousPage,
            onNextPage = onNextPage,
            onEnterPreview = onEnterPreview,
            onExitPreview = onExitPreview,
            modifier = modifier,
        )
        return
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        Spacer(Modifier.height(Spacing.sm))

        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Image(
                    painter = painterResource(R.mipmap.ic_launcher_foreground),
                    contentDescription = "公司 Logo",
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.width(52.dp).height(52.dp),
                )
                Spacer(Modifier.width(Spacing.sm))
                Column(Modifier.weight(1f)) {
                    Text(titleFor(role), fontSize = 20.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                    VSpace(6.dp)
                    Text(subtitleFor(role), fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                }
                SecondaryButton(
                    text = "刷新",
                    onClick = onRefresh,
                    enabled = summaryState != WorkspaceLoadState.LOADING && itemsState != WorkspaceLoadState.LOADING,
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
            if (AdminRolePreviewUiPolicy.canShowEntry(authenticatedRole)) {
                VSpace(Spacing.sm)
                if (previewRole == null) {
                    SecondaryButton(
                        text = "测试角色视图",
                        onClick = { selectorVisible = true },
                    )
                } else {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            AdminRolePreviewUiPolicy.BANNER_TEXT,
                            modifier = Modifier.weight(1f),
                            fontSize = 12.sp,
                            color = LogisticsTheme.colors.warning,
                            fontWeight = FontWeight.Bold,
                        )
                        Spacer(Modifier.width(Spacing.sm))
                        SecondaryButton(
                            text = "恢复 ADMIN",
                            onClick = onExitPreview,
                            modifier = Modifier.width(112.dp),
                        )
                    }
                }
            }
            VSpace(Spacing.sm)
            if (endpointConfigured) {
                SecondaryButton(
                    text = "后端设置",
                    onClick = onOpenEndpointConfig,
                    modifier = Modifier.fillMaxWidth(),
                )
            } else {
                AppCard(accentColor = LogisticsTheme.colors.warning) {
                    Text(
                        text = "尚未配置后端",
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary,
                    )
                    VSpace(6.dp)
                    Text(
                        text = "请先设置服务端地址，才能加载工作台数据。",
                        fontSize = 13.sp,
                        color = LogisticsTheme.colors.textSecondary,
                    )
                    VSpace(Spacing.sm)
                    PrimaryButton(
                        text = "立即设置后端",
                        onClick = onOpenEndpointConfig,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }

        VSpace(Spacing.md)
        WorkspaceDataSourceNotice(
            summaryState = summaryState,
            summaryError = summaryError,
            summaryUnavailable = summaryUnavailable,
            itemsState = itemsState,
            itemsError = itemsError,
            itemsUnavailable = itemsUnavailable,
            page = page,
            total = total,
            serverTime = serverTime,
            onRefresh = onRefresh,
        )

        if (summaryState == WorkspaceLoadState.LOADING || itemsState == WorkspaceLoadState.LOADING) {
            VSpace(Spacing.lg)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CircularProgressIndicator(modifier = Modifier.width(24.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(Spacing.sm))
                Text("正在加载服务端工作台…", color = LogisticsTheme.colors.textSecondary)
            }
        }

        VSpace(Spacing.md)
        entries.forEach { entry ->
            WorkspaceMetricCard(
                entry = entry,
                metric = summary.metric(entry.key),
                onClick = when (entry.key) {
                    WorkspaceMetricKey.PENDING_APPROVAL -> onOpenApproval
                    WorkspaceMetricKey.AUDIT -> onOpenAudit
                    else -> onRefresh
                },
            )
            VSpace(Spacing.sm)
        }

        when (itemsState) {
            WorkspaceLoadState.EMPTY -> EmptyState(
                title = "当前没有工作项",
                description = "服务端返回了空的当前页（总数为 $total），可以刷新后重试。",
                action = { SecondaryButton(text = "刷新工作台", onClick = onRefresh) },
            )
            WorkspaceLoadState.ERROR -> EmptyState(
                title = if (itemsUnavailable) "工作项接口不可用" else "工作项加载失败",
                description = itemsError ?: "未能读取服务端工作项，请检查网络后重试。",
                action = { PrimaryButton(text = "重试", onClick = onRefresh) },
            )
            WorkspaceLoadState.IDLE -> EmptyState(
                title = "等待工作台数据",
                description = "刷新后从服务端读取当前角色可见的分页工作项。",
                action = { PrimaryButton(text = "加载工作台", onClick = onRefresh) },
            )
            WorkspaceLoadState.LOADING, WorkspaceLoadState.CONTENT -> {
                if (items.isNotEmpty()) {
                    items.forEach { item ->
                        WorkspaceItemCard(
                            item = item,
                            role = role,
                            currentUserId = currentUserId,
                            timelineItemId = timelineItemId,
                            timeline = timeline,
                            timelineState = timelineState,
                            timelineError = timelineError,
                            handoverSubmittingId = handoverSubmittingId,
                            readOnly = previewRole != null,
                            onOpenTimeline = onOpenTimeline,
                            onRetryTimeline = onRetryTimeline,
                            onHandoverAction = { action ->
                                if (action.requiresReason) {
                                    reasonRequest = ReasonRequest(item, action)
                                } else {
                                    onHandoverAction(item, action, null)
                                }
                            },
                            onCreateHandover = { createItem = item },
                            onSubmitException = { exceptionItem = item },
                            exceptionSubmitting = exceptionSubmitting,
                        )
                        VSpace(Spacing.sm)
                    }
                    WorkspacePager(
                        page = page,
                        pageSize = pageSize,
                        total = total,
                        totalPages = totalPages,
                        onPrevious = onPreviousPage,
                        onNext = onNextPage,
                        enabled = itemsState != WorkspaceLoadState.LOADING,
                        readOnly = previewRole != null,
                        onPageSizeChange = onPageSizeChange,
                    )
                }
            }
        }

        VSpace(Spacing.xxl)
    }

    createItem?.let { item ->
        CreateHandoverDialog(
            item = item,
            submitting = item.id == handoverSubmittingId,
            onDismiss = { createItem = null },
            onSubmit = { quantity, fromLocation, remark ->
                createItem = null
                onCreateHandover(item, quantity, fromLocation, remark)
            },
        )
    }

    exceptionItem?.let { item ->
        ExceptionReportDialog(
            item = item,
            onDismiss = { exceptionItem = null },
            onSubmit = { type, actual, description ->
                onSubmitException(item, type, actual, description)
            },
            submitting = exceptionSubmitting,
        )
    }

    reasonRequest?.let { request ->
        ReasonDialog(
            request = request,
            onDismiss = { reasonRequest = null },
            onSubmit = { reason ->
                reasonRequest = null
                onHandoverAction(request.item, request.action, reason)
            },
        )
    }

    if (selectorVisible) {
        AdminRolePreviewDialog(
            onDismiss = { selectorVisible = false },
            onSelect = { selectedRole ->
                selectorVisible = false
                onEnterPreview(selectedRole)
            },
        )
    }
}

@Composable
internal fun AdminRolePreviewDialog(
    onDismiss: () -> Unit,
    onSelect: (WorkspaceViewRole) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("选择测试角色视图") },
        text = {
            Column {
                Text(
                    AdminRolePreviewUiPolicy.BANNER_TEXT,
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.warning,
                )
                VSpace(Spacing.sm)
                WorkspaceViewRole.entries.forEach { role ->
                    TextButton(
                        onClick = { onSelect(role) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(role.label, modifier = Modifier.fillMaxWidth())
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun WorkspaceDataSourceNotice(
    summaryState: WorkspaceLoadState,
    summaryError: String?,
    summaryUnavailable: Boolean,
    itemsState: WorkspaceLoadState,
    itemsError: String?,
    itemsUnavailable: Boolean,
    page: Int,
    total: Int,
    serverTime: String?,
    onRefresh: () -> Unit,
) {
    AppCard(
        accentColor = if (summaryState == WorkspaceLoadState.ERROR || itemsState == WorkspaceLoadState.ERROR) {
            LogisticsTheme.colors.warning
        } else {
            LogisticsTheme.colors.success
        },
    ) {
        Text(
            text = when {
                summaryUnavailable || itemsUnavailable -> "工作台接口不可用"
                summaryState == WorkspaceLoadState.ERROR || itemsState == WorkspaceLoadState.ERROR -> "工作台读取失败"
                summaryState == WorkspaceLoadState.CONTENT && itemsState == WorkspaceLoadState.CONTENT -> "已连接服务端工作台"
                else -> "工作台数据加载中"
            },
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary,
        )
        VSpace(4.dp)
        Text(
            text = when {
                summaryUnavailable -> summaryError ?: "服务端未提供摘要接口，相关指标不可用"
                itemsUnavailable -> itemsError ?: "服务端未提供工作项接口，分页工作项不可用"
                summaryState == WorkspaceLoadState.ERROR -> summaryError ?: "摘要读取失败"
                else -> "当前页 $page · 服务端总计 $total"
            },
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
        if (itemsState == WorkspaceLoadState.ERROR && !itemsUnavailable) {
            VSpace(4.dp)
            Text(itemsError ?: "工作项读取失败", fontSize = 11.sp, color = LogisticsTheme.colors.danger)
        }
        if (serverTime != null) {
            VSpace(4.dp)
            Text("服务端时间：$serverTime", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        }
        if (summaryState == WorkspaceLoadState.ERROR || itemsState == WorkspaceLoadState.ERROR) {
            VSpace(Spacing.sm)
            SecondaryButton(text = "重试", onClick = onRefresh)
        }
    }
}

@Composable
private fun WorkspaceMetricCard(entry: WorkspaceEntry, metric: WorkspaceMetric, onClick: () -> Unit) {
    AppCard(accentColor = if (metric.available) MaterialTheme.colorScheme.primary else LogisticsTheme.colors.border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(entry.title, fontSize = 15.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text(
                    text = if (metric.available) entry.hint else "服务端当前未提供该指标",
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary,
                )
            }
            Text(
                text = metric.count?.toString() ?: "—",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = if (metric.available) MaterialTheme.colorScheme.primary else LogisticsTheme.colors.textTertiary,
            )
            Spacer(Modifier.width(Spacing.sm))
            if (entry.actionIsPrimary) {
                PrimaryButton(text = entry.actionLabel, onClick = onClick, modifier = Modifier.width(96.dp))
            } else {
                SecondaryButton(text = entry.actionLabel, onClick = onClick, modifier = Modifier.width(96.dp))
            }
        }
        if (!metric.available) {
            VSpace(Spacing.sm)
            Text("— 表示接口未提供数据，不代表数量为 0", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        }
    }
}

@Composable
private fun ExceptionReportDialog(
    item: WorkspaceMaterialItem,
    onDismiss: () -> Unit,
    onSubmit: (String, Int, String?) -> Unit,
    submitting: Boolean = false,
) {
    var actual by remember(item.id) { mutableStateOf("") }
    var description by remember(item.id) { mutableStateOf("") }
    val actualValue = actual.toIntOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("提报物料异常") },
        text = {
            Column {
                Text("订单 ${item.orderNo} · ${item.deviceNo ?: "机台"} · ${item.materialCode}", fontSize = 12.sp)
                VSpace(Spacing.sm)
                OutlinedTextField(actual, { if (it.all(Char::isDigit)) actual = it }, label = { Text("实际数量") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                VSpace(Spacing.sm)
                OutlinedTextField(description, { if (it.length <= 500) description = it }, label = { Text("说明（必填）") }, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = { TextButton(onClick = { onSubmit("OTHER", actualValue ?: 0, description.trim()) }, enabled = !submitting && actualValue != null && actualValue >= 0 && description.isNotBlank()) { Text(if (submitting) "提交中…" else "提交") } },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("取消") } },
    )
}

private data class ReasonRequest(
    val item: WorkspaceMaterialItem,
    val action: HandoverAction,
)

@Composable
private fun WorkspaceItemCard(
    item: WorkspaceMaterialItem,
    role: UserRole,
    currentUserId: String?,
    timelineItemId: String?,
    timeline: HandoverTimeline?,
    timelineState: WorkspaceLoadState,
    timelineError: String?,
    handoverSubmittingId: String?,
    readOnly: Boolean,
    onOpenTimeline: (WorkspaceMaterialItem) -> Unit,
    onRetryTimeline: () -> Unit,
    onHandoverAction: (HandoverAction) -> Unit,
    onCreateHandover: () -> Unit,
    onSubmitException: () -> Unit,
    exceptionSubmitting: Boolean = false,
) {
    AppCard(accentColor = LogisticsTheme.colors.border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(item.materialCode, fontSize = 15.sp, fontWeight = FontWeight.Bold, fontFamily = LogisticsType.MonoFamily, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text(item.materialName, fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            }
            StatusTag(
                label = item.statusLabel.ifBlank { "未知状态" },
                color = item.statusColor,
                containerColor = item.statusContainerColor,
                symbol = item.statusSymbol,
            )
        }
        VSpace(Spacing.sm)
        Text(
            text = listOfNotNull(
                item.orderNo.takeIf { it.isNotBlank() }?.let { "订单 $it" },
                item.deviceNo?.takeIf { it.isNotBlank() }?.let { "机台 $it" },
                (item.responsibilitySummary?.currentOwnerName ?: item.currentOwnerName)
                    ?.takeIf { it.isNotBlank() }?.let { "当前责任人 $it" },
            ).joinToString(" · ").ifBlank { "服务端未返回附加责任信息" },
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textSecondary,
        )
        VSpace(4.dp)
        Text(
            text = "服务端状态 ${item.statusLabel.ifBlank { "未知状态" }} · 状态码 ${item.statusCode.ifBlank { "—" }}",
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
        VSpace(4.dp)
        Text(
            text = "需求 ${item.requiredQuantity ?: "—"} · 到料 ${item.arrivedQuantity ?: "—"} · 在库 ${item.inStockQuantity ?: "—"}",
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
        item.lastHandover?.let { handover ->
            VSpace(4.dp)
            Text(
                text = listOfNotNull(
                    handover.status?.let { "最近交接 $it" },
                    handover.quantity?.let { "数量 $it" },
                    handover.fromLocation?.let { "出库库位 $it" },
                    handover.initiatedAt?.let { "发起 $it" },
                    handover.confirmedAt?.let { "完成 $it" },
                ).joinToString(" · "),
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
            )
            Text(
                text = listOfNotNull(
                    handover.senderName?.let { "发起人 $it" },
                    handover.receiverName?.let { "接收人 $it" },
                ).joinToString(" · ").ifBlank { "服务端未返回交接参与人" },
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
            )
        }
        if (item.lastHandover == null && item.lastHandoverStatus != null) {
            Text(
                text = "最近交接 ${item.lastHandoverStatus}",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
            )
        }

        val actions = HandoverActionPolicy.actionsFor(role, currentUserId, item)
        val canCreate = HandoverActionPolicy.canCreate(role, item)
        val canReportException = role == UserRole.OPERATOR && !readOnly && !item.deviceId.isNullOrBlank()
        if (actions.isNotEmpty() || canCreate || canReportException || !item.lastHandoverId.isNullOrBlank()) {
            VSpace(Spacing.sm)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (canCreate) {
                    PrimaryButton(
                        text = "发起交接",
                        onClick = onCreateHandover,
                        enabled = AdminRolePreviewUiPolicy.actionsEnabled(readOnly) && handoverSubmittingId == null,
                        modifier = Modifier.weight(1f),
                    )
                }
                actions.forEach { action ->
                    SecondaryButton(
                        text = action.label,
                        onClick = { onHandoverAction(action) },
                        enabled = AdminRolePreviewUiPolicy.actionsEnabled(readOnly) && handoverSubmittingId == null,
                        modifier = Modifier.weight(1f),
                    )
                }
                if (canReportException) {
                    SecondaryButton(text = "提报异常", onClick = onSubmitException, enabled = !exceptionSubmitting, modifier = Modifier.weight(1f))
                }
                if (!item.lastHandoverId.isNullOrBlank()) {
                    SecondaryButton(
                        text = if (timelineItemId == item.id) "收起时间线" else "时间线",
                        onClick = { onOpenTimeline(item) },
                        enabled = handoverSubmittingId == null,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
        if (timelineItemId == item.id) {
            HandoverTimelineCard(
                timeline = timeline,
                state = timelineState,
                error = timelineError,
                onRetry = onRetryTimeline,
            )
        }
    }
}

@Composable
private fun HandoverTimelineCard(
    timeline: HandoverTimeline?,
    state: WorkspaceLoadState,
    error: String?,
    onRetry: () -> Unit,
) {
    VSpace(Spacing.sm)
    AppCard(accentColor = MaterialTheme.colorScheme.primary) {
        Text("交接时间线（服务端）", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
        when (state) {
            WorkspaceLoadState.LOADING -> {
                VSpace(Spacing.sm)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.width(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(Spacing.sm))
                    Text("正在读取时间线…", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                }
            }
            WorkspaceLoadState.ERROR -> {
                VSpace(Spacing.sm)
                Text(error ?: "时间线读取失败", fontSize = 12.sp, color = LogisticsTheme.colors.danger)
                VSpace(Spacing.sm)
                SecondaryButton(text = "重试时间线", onClick = onRetry)
            }
            WorkspaceLoadState.EMPTY -> {
                VSpace(Spacing.sm)
                Text("服务端未返回时间线事件", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
            WorkspaceLoadState.CONTENT -> timeline?.items?.forEach { event ->
                VSpace(Spacing.sm)
                Text(
                    text = listOfNotNull(
                        event.eventType,
                        event.serverTime,
                        event.actorRole?.let { "角色 $it" },
                    ).joinToString(" · "),
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            WorkspaceLoadState.IDLE -> Unit
        }
    }
}

@Composable
private fun ReasonDialog(
    request: ReasonRequest,
    onDismiss: () -> Unit,
    onSubmit: (String) -> Unit,
) {
    var reason by remember(request.item.id, request.action) { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("${request.action.label}交接") },
        text = {
            OutlinedTextField(
                value = reason,
                onValueChange = { if (it.length <= 500) reason = it },
                label = { Text("原因（必填）") },
                supportingText = { Text("${reason.length}/500") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = false,
            )
        },
        confirmButton = {
            TextButton(onClick = { onSubmit(reason.trim()) }, enabled = reason.isNotBlank()) {
                Text(request.action.label)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("返回") } },
    )
}

@Composable
private fun CreateHandoverDialog(
    item: WorkspaceMaterialItem,
    submitting: Boolean,
    onDismiss: () -> Unit,
    onSubmit: (Int, String, String?) -> Unit,
) {
    var quantity by remember(item.id) {
        mutableStateOf(item.issuedQuantity?.takeIf { it > 0 }?.toString().orEmpty())
    }
    var fromLocation by remember(item.id) { mutableStateOf("") }
    var remark by remember(item.id) { mutableStateOf("") }
    val parsedQuantity = quantity.toIntOrNull()
    AlertDialog(
        onDismissRequest = { if (!submitting) onDismiss() },
        title = { Text("发起出库交接") },
        text = {
            Column {
                Text("订单 ${item.orderNo} · ${item.materialCode}", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                VSpace(Spacing.sm)
                OutlinedTextField(
                    value = quantity,
                    onValueChange = { if (it.length <= 9 && it.all { char -> char.isDigit() }) quantity = it },
                    label = { Text("交接数量") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                VSpace(Spacing.sm)
                OutlinedTextField(
                    value = fromLocation,
                    onValueChange = { if (it.length <= 128) fromLocation = it },
                    label = { Text("出库库位") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                VSpace(Spacing.sm)
                OutlinedTextField(
                    value = remark,
                    onValueChange = { if (it.length <= 500) remark = it },
                    label = { Text("备注（可选）") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = false,
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSubmit(parsedQuantity ?: 0, fromLocation.trim(), remark.trim().ifBlank { null }) },
                enabled = !submitting && parsedQuantity != null && parsedQuantity > 0 && fromLocation.isNotBlank(),
            ) { Text(if (submitting) "提交中…" else "发起") }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("返回") } },
    )
}

@Composable
private fun WorkspacePager(
    page: Int,
    pageSize: Int,
    total: Int,
    totalPages: Int,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    enabled: Boolean,
    readOnly: Boolean,
    onPageSizeChange: (Int) -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        SecondaryButton(text = "上一页", onClick = onPrevious, enabled = enabled && page > 1, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(Spacing.sm))
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("$page / ${totalPages.coerceAtLeast(1)} · 共 $total", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            Row {
                listOf(20, 50).forEach { size ->
                    SecondaryButton(
                        text = "$size/页",
                        onClick = { onPageSizeChange(size) },
                        enabled = enabled && !readOnly && pageSize != size,
                        modifier = Modifier.width(64.dp),
                    )
                    if (size == 20) Spacer(Modifier.width(4.dp))
                }
            }
        }
        Spacer(Modifier.width(Spacing.sm))
        SecondaryButton(text = "下一页", onClick = onNext, enabled = enabled && page < totalPages, modifier = Modifier.weight(1f))
    }
}

private fun titleFor(role: UserRole): String = when (role) {
    UserRole.OPERATOR -> "操作员工作台"
    UserRole.MATERIAL -> "物料员工作台"
    UserRole.WAREHOUSE_ADMIN -> "仓库管理工作台"
    UserRole.ADMIN -> "管理员工作台"
    UserRole.WORKSHOP_SUPERVISOR -> "车间主管工作台"
    UserRole.ASSEMBLER -> "装配工工作台"
}

private fun subtitleFor(role: UserRole): String = when (role) {
    UserRole.OPERATOR -> "只查看服务端登记的本人责任范围与交接状态"
    UserRole.MATERIAL -> "处理服务端返回的待出库与已出库工作项"
    UserRole.WAREHOUSE_ADMIN -> "查看服务端返回的待审批与待交接工作项"
    UserRole.ADMIN -> "查看服务端角色范围内的工作项与明确可用指标"
    UserRole.WORKSHOP_SUPERVISOR -> "查看车间总工时、装配工时、临时调拨工时与机台进度"
    UserRole.ASSEMBLER -> "接受物料、记录装配工时并按 1/2/3 提交进度"
}

private fun entriesFor(role: UserRole): List<WorkspaceEntry> = when (role) {
    UserRole.OPERATOR -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.CLAIMED, "已领取", "服务端确认的领取记录", "刷新", true),
        WorkspaceEntry(WorkspaceMetricKey.AT_STATION, "已到机台", "服务端确认目标机台的交接", "刷新"),
    )
    UserRole.MATERIAL -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.OUTBOUND_PENDING, "待出库", "服务端返回的待出库状态", "刷新", true),
        WorkspaceEntry(WorkspaceMetricKey.OUTBOUND_CONFIRMED, "已出库", "服务端返回的已出库状态", "刷新"),
    )
    UserRole.WAREHOUSE_ADMIN -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.PENDING_APPROVAL, "待审批", "服务端返回的审批状态", "去审批", true),
        WorkspaceEntry(WorkspaceMetricKey.PENDING_HANDOVER, "待交接", "服务端返回的交接状态", "刷新"),
    )
    UserRole.ADMIN -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.ALL, "全量工作项", "服务端分页接口返回的 total", "刷新", true),
        WorkspaceEntry(WorkspaceMetricKey.OUT_OF_STOCK, "缺货", "服务端摘要返回的缺货数量", "刷新"),
        WorkspaceEntry(WorkspaceMetricKey.EXCEPTION, "异常", "摘要接口未提供该计数", "刷新"),
        WorkspaceEntry(WorkspaceMetricKey.AUDIT, "审计入口", "审计接口未在本迭代提供", "打开审计"),
    )
    UserRole.WORKSHOP_SUPERVISOR -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.TOTAL_LABOR_MINUTES, "总工时（分钟）", "装配工时 + 临时调拨工时", "刷新", true),
        WorkspaceEntry(WorkspaceMetricKey.ASSEMBLY_LABOR_MINUTES, "装配工时（分钟）", "服务端装配工时汇总", "刷新"),
        WorkspaceEntry(WorkspaceMetricKey.TEMPORARY_TRANSFER_LABOR_MINUTES, "临时调拨工时（分钟）", "独立临时调拨工时", "刷新"),
        WorkspaceEntry(WorkspaceMetricKey.OVERALL_PROGRESS_PERCENT, "订单总进度", "已完工任务占比", "刷新"),
    )
    UserRole.ASSEMBLER -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.ALL, "我的装配任务", "服务端仅返回分配给本人的任务", "刷新", true),
        WorkspaceEntry(WorkspaceMetricKey.OVERALL_PROGRESS_PERCENT, "当前进度", "按服务端阶段 1/2/3 提交", "刷新"),
    )
}
