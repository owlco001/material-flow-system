package com.company.logistics.ui.screens

import com.company.logistics.ui.components.formatServerTime
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.background
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestAction
import com.company.logistics.model.TransferRequestActionPolicy
import com.company.logistics.model.UserRole
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.ConfirmDialog
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.KeyValueCell
import com.company.logistics.ui.components.LogisticsIcons
import com.company.logistics.ui.components.PlaceholderScreen
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 审批页 —— 契约 2.2 审批状态机。
 *
 * 审批权限：仓库管理员与管理员（V1 无「主管」角色）。
 * 契约约束：审批拒绝必须填写原因；同一用户默认不得既审批又执行同一单据。
 */
@Composable
fun ApprovalScreen(
    role: UserRole,
    readOnly: Boolean = false,
    requests: List<TransferRequest>,
    requestState: WorkspaceLoadState,
    requestError: String?,
    requestFilter: String?,
    serverTime: String?,
    selectedRequestId: String?,
    detail: TransferRequest?,
    detailState: WorkspaceLoadState,
    detailError: String?,
    submittingRequestId: String?,
    onRefresh: () -> Unit,
    onRetry: () -> Unit,
    onFilter: (String?) -> Unit,
    onSelectRequest: (String) -> Unit,
    onCloseDetail: () -> Unit,
    onRetryDetail: () -> Unit,
    onApprove: (String) -> Unit,
    onReject: (String, String) -> Unit,
    onExecute: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var rejectRequest by remember { mutableStateOf<TransferRequest?>(null) }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        if (!role.canApprove) {
            EmptyState(
                title = "无审批权限",
                description = "当前角色为「${role.label}」。入库、出库与异常调整需由仓库管理员或管理员审批。"
            )
            return@Column
        }

        // ---- 渐变横幅头图 ----
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(22.dp),
            shadowElevation = 4.dp,
        ) {
            Box(
                modifier = Modifier
                    .background(
                        Brush.linearGradient(
                            colors = listOf(LogisticsColors.BrandNavy, LogisticsColors.BrandNavyDark),
                        ),
                    )
                    .padding(18.dp),
            ) {
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                "流转审批",
                                fontSize = 19.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color.White,
                            )
                            VSpace(4.dp)
                            Text(
                                "按钮只根据服务端状态出现；拒绝必须填写原因",
                                fontSize = 12.sp,
                                color = Color.White.copy(alpha = 0.78f),
                            )
                        }
                        val refreshEnabled = requestState != WorkspaceLoadState.LOADING
                        Surface(
                            shape = RoundedCornerShape(10.dp),
                            border = BorderStroke(1.5.dp, Color.White.copy(alpha = 0.65f)),
                            color = Color.Transparent,
                            modifier = Modifier.clickable(enabled = refreshEnabled, onClick = onRefresh),
                        ) {
                            Text(
                                "刷新",
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = Color.White.copy(alpha = if (refreshEnabled) 1f else 0.5f),
                                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                            )
                        }
                    }
                    serverTime?.let {
                        VSpace(Spacing.sm)
                        Text(
                            "服务端时间 " + formatServerTime(it),
                            fontSize = 11.sp,
                            fontFamily = LogisticsType.MonoFamily,
                            color = Color.White.copy(alpha = 0.72f),
                        )
                    }
                }
            }
        }

        VSpace(Spacing.md)
        SectionTitle("状态筛选")
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            listOf(
                null to "全部",
                "PENDING_APPROVAL" to "待审批",
                "APPROVED" to "已批准",
                "REJECTED" to "已驳回",
                "EXECUTED" to "已执行",
            ).forEach { (status, label) ->
                val selected = requestFilter == status
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = if (selected) LogisticsTheme.colors.primaryContainer
                    else LogisticsTheme.colors.cardBackground,
                    border = BorderStroke(
                        1.dp,
                        if (selected) LogisticsColors.Primary
                        else LogisticsTheme.colors.border,
                    ),
                    modifier = Modifier
                        .weight(1f)
                        .clickable(
                            enabled = requestState != WorkspaceLoadState.LOADING,
                            onClick = { onFilter(status) },
                        ),
                ) {
                    Text(
                        label,
                        fontSize = 11.sp,
                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                        color = if (selected) LogisticsColors.PrimaryDark
                        else LogisticsTheme.colors.textSecondary,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                }
            }
        }
        if (readOnly) {
            VSpace(Spacing.sm)
            Text(
                "测试预览只读，不能批准、驳回或执行",
                fontSize = 12.sp,
                color = LogisticsTheme.colors.warningText,
                fontWeight = FontWeight.SemiBold,
            )
        }

        when (requestState) {
            WorkspaceLoadState.IDLE -> Unit
            WorkspaceLoadState.LOADING -> {
                VSpace(Spacing.xl)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(modifier = Modifier.width(22.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(Spacing.sm))
                    Text("正在读取流转申请…", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                }
            }
            WorkspaceLoadState.ERROR -> EmptyState(
                title = "流转申请加载失败",
                description = requestError ?: "请稍后重试",
                action = { SecondaryButton(text = "重试", onClick = onRetry) },
            )
            WorkspaceLoadState.EMPTY -> EmptyState(
                title = "暂无流转申请",
                description = "服务端当前没有返回该筛选条件下的申请。",
            )
            WorkspaceLoadState.CONTENT -> {
                VSpace(Spacing.sm)
                requests.forEach { request ->
                    TransferRequestCard(
                        request = request,
                        role = role,
                        readOnly = readOnly,
                        submitting = submittingRequestId == request.id,
                        selected = selectedRequestId == request.id,
                        onSelect = {
                            if (selectedRequestId == request.id) onCloseDetail()
                            else onSelectRequest(request.id)
                        },
                        onApprove = { onApprove(request.id) },
                        onReject = { rejectRequest = request },
                        onExecute = { onExecute(request.id) },
                    )
                }
            }
        }

        if (selectedRequestId != null) {
            TransferRequestDetailCard(
                detail = detail,
                state = detailState,
                error = detailError,
                onClose = onCloseDetail,
                onRetry = onRetryDetail,
            )
        }

        VSpace(Spacing.lg)
        AppCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(width = 4.dp, height = 16.dp)
                        .clip(RoundedCornerShape(2.dp))
                        .background(LogisticsColors.Primary),
                )
                Spacer(Modifier.width(8.dp))
                Text("权限边界", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
            }
            VSpace(Spacing.sm)
            Text(
                "同一用户默认不得既审批又执行同一单据；最终权限、状态和幂等结果以服务端响应为准。",
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary,
            )
        }

        VSpace(Spacing.xxl)
    }

    rejectRequest?.let { request ->
        RejectTransferDialog(
            request = request,
            submitting = submittingRequestId == request.id,
            onDismiss = { if (submittingRequestId == null) rejectRequest = null },
            onSubmit = { reason ->
                rejectRequest = null
                onReject(request.id, reason)
            },
        )
    }
}

@Composable
private fun TransferRequestCard(
    request: TransferRequest,
    role: UserRole,
    readOnly: Boolean,
    submitting: Boolean,
    selected: Boolean,
    onSelect: () -> Unit,
    onApprove: () -> Unit,
    onReject: () -> Unit,
    onExecute: () -> Unit,
) {
    val actions = if (readOnly) emptyList() else TransferRequestActionPolicy.actionsFor(role, request)
    // 批准/执行会改变申请状态：必须二次确认，文案写明后果
    var showApproveConfirm by remember { mutableStateOf(false) }
    var showExecuteConfirm by remember { mutableStateOf(false) }
    if (showApproveConfirm) {
        ConfirmDialog(
            title = "批准这条流转申请？",
            message = "申请 ${request.documentNo ?: request.id} 将进入「已批准」状态，申请人可继续发起流转交接。",
            confirmText = "批准",
            onConfirm = {
                showApproveConfirm = false
                onApprove()
            },
            onDismiss = { showApproveConfirm = false }
        )
    }
    if (showExecuteConfirm) {
        ConfirmDialog(
            title = "执行这条流转申请？",
            message = "申请 ${request.documentNo ?: request.id} 将被执行并产生流转业务记录，物料归属随之变更。",
            confirmText = "执行",
            onConfirm = {
                showExecuteConfirm = false
                onExecute()
            },
            onDismiss = { showExecuteConfirm = false }
        )
    }
    AppCard(
        accentColor = requestStatusColor(request.status),
        borderColor = if (selected) MaterialTheme.colorScheme.primary else null,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "${request.type.ifBlank { "未知类型" }} · ${request.documentNo ?: "无关联单据"}",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                VSpace(4.dp)
                Text(
                    "申请 ${request.id}",
                    fontSize = 11.sp,
                    fontFamily = LogisticsType.MonoFamily,
                    color = LogisticsTheme.colors.textTertiary,
                )
            }
            StatusTag(
                label = request.displayStatusLabel,
                color = requestStatusTextColor(request.status),
                containerColor = requestStatusContainer(request.status),
                symbol = requestStatusSymbol(request.status),
            )
        }
        VSpace(Spacing.sm)
        Text(
            listOfNotNull(
                (request.createdByName ?: request.createdBy)?.let { "创建人 $it" },
                request.createdAt?.let { "创建 " + formatServerTime(it) },
                "明细 ${request.items.size} 条",
            ).joinToString(" · "),
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
        VSpace(Spacing.sm)
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm)) {
            SecondaryButton(
                text = if (selected) "收起详情" else "查看详情",
                onClick = onSelect,
                enabled = !submitting,
                modifier = Modifier.weight(1f),
            )
            if (TransferRequestAction.APPROVE in actions) {
                PrimaryButton(
                    text = "批准",
                    onClick = { showApproveConfirm = true },
                    enabled = !submitting,
                    loading = submitting,
                    modifier = Modifier.weight(1f),
                )
            }
            if (TransferRequestAction.REJECT in actions) {
                SecondaryButton(
                    text = "驳回",
                    onClick = onReject,
                    enabled = !submitting,
                    modifier = Modifier.weight(1f),
                )
            }
            if (TransferRequestAction.EXECUTE in actions) {
                PrimaryButton(
                    text = "执行",
                    onClick = { showExecuteConfirm = true },
                    enabled = !submitting,
                    loading = submitting,
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
    VSpace(Spacing.sm)
}

@Composable
private fun TransferRequestDetailCard(
    detail: TransferRequest?,
    state: WorkspaceLoadState,
    error: String?,
    onClose: () -> Unit,
    onRetry: () -> Unit,
) {
    AppCard(accentColor = MaterialTheme.colorScheme.primary) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("申请详情", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onClose) { Text("关闭") }
        }
        when (state) {
            WorkspaceLoadState.LOADING -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(modifier = Modifier.width(20.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(Spacing.sm))
                Text("正在读取详情…", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
            WorkspaceLoadState.ERROR -> {
                Text(error ?: "详情加载失败", fontSize = 12.sp, color = LogisticsTheme.colors.danger)
                TextButton(onClick = onRetry) { Text("重试") }
            }
            WorkspaceLoadState.CONTENT -> detail?.let { request ->
                Text(
                    listOfNotNull(
                        "状态 ${request.displayStatusLabel}",
                        request.approvedBy?.let { "审批人 $it" },
                        request.executedAt?.let { "执行 $it" },
                    ).joinToString(" · "),
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
                request.remark?.takeIf { it.isNotBlank() }?.let {
                    VSpace(Spacing.sm)
                    Text("备注 $it", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                }
                request.items.forEachIndexed { index, item ->
                    VSpace(Spacing.sm)
                    Text(
                        "${index + 1}. ${item.materialId} · 数量 ${item.quantity} · 版本 ${item.expectedInventoryVersion ?: "—"}",
                        fontSize = 12.sp,
                        fontFamily = LogisticsType.MonoFamily,
                        color = LogisticsTheme.colors.textTertiary,
                    )
                }
            }
            else -> Unit
        }
    }
    VSpace(Spacing.sm)
}

@Composable
private fun RejectTransferDialog(
    request: TransferRequest,
    submitting: Boolean,
    onDismiss: () -> Unit,
    onSubmit: (String) -> Unit,
) {
    var reason by remember(request.id) { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = { if (!submitting) onDismiss() },
        properties = DialogProperties(decorFitsSystemWindows = false),
        modifier = Modifier.imePadding(),
        title = { Text("驳回流转申请") },
        text = {
            Column {
                Text("申请 ${request.id}", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                VSpace(Spacing.sm)
                OutlinedTextField(
                    value = reason,
                    onValueChange = { if (it.length <= 500) reason = it },
                    label = { Text("原因（必填）") },
                    supportingText = { Text("${reason.length}/500") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = false,
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSubmit(reason.trim()) },
                enabled = !submitting && reason.trim().isNotBlank(),
            ) { Text(if (submitting) "提交中…" else "驳回") }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("返回") } },
    )
}

@Composable
private fun requestStatusColor(status: String): Color = when (status.uppercase(java.util.Locale.ROOT)) {
    "PENDING_APPROVAL" -> LogisticsTheme.colors.warning
    "APPROVED" -> MaterialTheme.colorScheme.primary
    "EXECUTED" -> LogisticsTheme.colors.success
    "REJECTED" -> LogisticsTheme.colors.danger
    else -> LogisticsTheme.colors.textTertiary
}

/** 标签文字用主题感知的深色变体；色块语义沿用 requestStatusColor */
@Composable
private fun requestStatusTextColor(status: String): Color = when (status.uppercase(java.util.Locale.ROOT)) {
    "PENDING_APPROVAL" -> LogisticsTheme.colors.warningText
    "EXECUTED" -> LogisticsTheme.colors.successText
    else -> requestStatusColor(status)
}

@Composable
private fun requestStatusContainer(status: String): Color = when (status.uppercase(java.util.Locale.ROOT)) {
    "PENDING_APPROVAL" -> LogisticsTheme.colors.warning.copy(alpha = 0.14f)
    "APPROVED" -> LogisticsTheme.colors.primaryContainer
    "EXECUTED" -> LogisticsTheme.colors.success.copy(alpha = 0.14f)
    "REJECTED" -> LogisticsTheme.colors.danger.copy(alpha = 0.14f)
    else -> LogisticsTheme.colors.border
}

private fun requestStatusSymbol(status: String): String = when (status.uppercase(java.util.Locale.ROOT)) {
    "PENDING_APPROVAL" -> "↓"
    "APPROVED" -> "→"
    "EXECUTED" -> "✓"
    "REJECTED" -> "!"
    else -> "?"
}

/** 流转申请类型中文映射 */
private fun transferTypeLabel(type: String): String = when (type.trim().uppercase(java.util.Locale.ROOT)) {
    "OUTBOUND" -> "出库"
    "INBOUND" -> "入库"
    "TRANSFER" -> "调拨"
    else -> type.ifBlank { "—" }
}

/** 库存查询页（列表入口，扫码进入详情） */
@Composable
fun InventoryScreen(
    onGoScan: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))
        EmptyState(
            title = "扫码查询库存",
            description = "扫描料号条码查看实时库存与库位分布；支持绑定库位与提交流转申请",
            action = {
                PrimaryButton(text = "去扫码", onClick = onGoScan)
            }
        )
    }
}

/** 我的页 —— 展示角色、权限、服务端配置与离线队列摘要 */
@Composable
fun ProfileScreen(
    userName: String,
    role: UserRole,
    queue: List<OfflineOperation>,
    endpointUrl: String,
    endpointConfigured: Boolean,
    onOpenQueue: () -> Unit,
    onOpenEndpointConfig: () -> Unit,
    onOpenMyExceptions: () -> Unit,
    myTransferRequests: List<TransferRequest> = emptyList(),
    myTransferRequestsLoading: Boolean = false,
    myTransferRequestsError: String? = null,
    onRetryMyTransfers: () -> Unit = {},
    onLogout: () -> Unit,
    modifier: Modifier = Modifier
) {
    // 退出登录会清本地登录态：必须二次确认
    var showLogoutConfirm by remember { mutableStateOf(false) }
    if (showLogoutConfirm) {
        ConfirmDialog(
            title = "退出登录？",
            message = "将清除本机的登录状态。离线队列中未同步的记录会保留在本地，下次登录后可继续同步。",
            confirmText = "退出登录",
            onConfirm = {
                showLogoutConfirm = false
                onLogout()
            },
            onDismiss = { showLogoutConfirm = false }
        )
    }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                androidx.compose.foundation.layout.Box(
                    modifier = Modifier
                        .size(48.dp)
                        .background(MaterialTheme.colorScheme.primaryLight(), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        userName.take(1).ifBlank { "?" },
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary
                    )
                }
                Spacer(Modifier.width(Spacing.md))
                Column(Modifier.weight(1f)) {
                    Text(
                        userName.ifBlank { "未登录" },
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(4.dp)
                    StatusTag(
                        label = role.label,
                        color = MaterialTheme.colorScheme.primary,
                        containerColor = MaterialTheme.colorScheme.primaryLight()
                    )
                }
            }
        }

        VSpace(Spacing.md)

        AppCard {
            Text("权限范围", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
            VSpace(Spacing.sm)
            PermissionRow("扫码查看物料", true)
            PermissionRow("提交流转申请", true)
            PermissionRow("绑定库位", true)
            PermissionRow("审批入库/出库", role.canApprove)
            PermissionRow("执行库存变更", role.canExecute)
            PermissionRow("用户与审计管理", role.canAdmin)
        }

        VSpace(Spacing.md)

        // 服务端配置入口 —— 现场换环境时无需重新打包
        AppCard(
            accentColor = if (endpointConfigured)
                LogisticsTheme.colors.success else LogisticsTheme.colors.warning
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "服务端地址",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(3.dp)
                    Text(
                        endpointUrl,
                        fontSize = 11.sp,
                        fontFamily = LogisticsType.MonoFamily,
                        color = LogisticsTheme.colors.textTertiary,
                        maxLines = 1,
                        overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis
                    )
                }
                SecondaryButton(
                    text = "配置",
                    onClick = onOpenEndpointConfig,
                    modifier = Modifier.width(96.dp)
                )
            }
        }

        VSpace(Spacing.md)

        AppCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "离线队列",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(3.dp)
                    Text(
                        "共 ${queue.size} 条记录",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary
                    )
                }
                SecondaryButton(
                    text = "查看",
                    onClick = onOpenQueue,
                    modifier = Modifier.width(96.dp)
                )
            }
        }

        VSpace(Spacing.md)

        // 我的异常入口 —— 本人提报的异常及审批进度
        AppCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "我的异常",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(3.dp)
                    Text(
                        "查看本人提报的异常及审批进度",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary
                    )
                }
                SecondaryButton(
                    text = "查看",
                    onClick = onOpenMyExceptions,
                    modifier = Modifier.width(96.dp)
                )
            }
        }

        VSpace(Spacing.md)

        // 我的流转申请 —— 本人提交的出入库/调拨申请及审批进度
        AppCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "我的流转申请（${myTransferRequests.size}）",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                    modifier = Modifier.weight(1f)
                )
                TextButton(
                    onClick = onRetryMyTransfers,
                    enabled = !myTransferRequestsLoading,
                    contentPadding = PaddingValues(horizontal = 8.dp)
                ) {
                    Text(
                        if (myTransferRequestsLoading) "刷新中…" else "刷新",
                        fontSize = 12.sp
                    )
                }
            }
            VSpace(Spacing.sm)
            when {
                myTransferRequestsLoading -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(12.dp),
                        strokeWidth = 2.dp
                    )
                    Spacer(Modifier.width(Spacing.sm))
                    Text(
                        "正在加载流转申请…",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary
                    )
                }
                myTransferRequestsError != null -> Column {
                    Text(
                        myTransferRequestsError,
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.danger
                    )
                    VSpace(4.dp)
                    TextButton(
                        onClick = onRetryMyTransfers,
                        contentPadding = PaddingValues(horizontal = 0.dp)
                    ) { Text("重试", fontSize = 12.sp) }
                }
                myTransferRequests.isEmpty() -> Text(
                    "暂无流转申请记录；在机台详情勾选物料后可批量提出申请",
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textTertiary
                )
                else -> Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "单据",
                            fontSize = 10.sp,
                            color = LogisticsTheme.colors.textTertiary,
                            modifier = Modifier.weight(1f)
                        )
                        Text(
                            "类型",
                            fontSize = 10.sp,
                            color = LogisticsTheme.colors.textTertiary,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.width(40.dp)
                        )
                        Text(
                            "状态",
                            fontSize = 10.sp,
                            color = LogisticsTheme.colors.textTertiary,
                            modifier = Modifier.width(76.dp)
                        )
                        Text(
                            "时间",
                            fontSize = 10.sp,
                            color = LogisticsTheme.colors.textTertiary,
                            textAlign = TextAlign.End,
                            modifier = Modifier.width(84.dp)
                        )
                    }
                    VSpace(4.dp)
                    myTransferRequests.forEach { request ->
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(vertical = 5.dp)
                        ) {
                            Text(
                                request.documentNo?.takeIf { it.isNotBlank() }
                                    ?: request.id.takeLast(8),
                                fontSize = 11.sp,
                                fontFamily = LogisticsType.MonoFamily,
                                color = LogisticsTheme.colors.textPrimary,
                                maxLines = 1,
                                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f)
                            )
                            Text(
                                transferTypeLabel(request.type),
                                fontSize = 11.sp,
                                color = LogisticsTheme.colors.textSecondary,
                                textAlign = TextAlign.Center,
                                modifier = Modifier.width(40.dp)
                            )
                            StatusTag(
                                label = request.displayStatusLabel,
                                color = requestStatusTextColor(request.status),
                                containerColor = requestStatusContainer(request.status),
                                symbol = requestStatusSymbol(request.status),
                                modifier = Modifier.width(76.dp)
                            )
                            Text(
                                request.createdAt?.take(16)?.replace('T', ' ') ?: "—",
                                fontSize = 10.sp,
                                fontFamily = LogisticsType.MonoFamily,
                                color = LogisticsTheme.colors.textTertiary,
                                textAlign = TextAlign.End,
                                modifier = Modifier.width(84.dp)
                            )
                        }
                    }
                }
            }
        }

        VSpace(Spacing.lg)
        SecondaryButton(text = "退出登录", onClick = { showLogoutConfirm = true })

        VSpace(Spacing.xxl)
    }
}

@Composable
private fun PermissionRow(label: String, allowed: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        val allowedColor = if (allowed) LogisticsTheme.colors.success else LogisticsTheme.colors.textTertiary
        if (allowed) {
            Image(
                imageVector = LogisticsIcons.Check,
                contentDescription = null,
                colorFilter = ColorFilter.tint(allowedColor),
                modifier = Modifier.size(13.dp)
            )
        } else {
            Text(
                "—",
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
                color = allowedColor
            )
        }
        Spacer(Modifier.width(Spacing.sm))
        Text(
            label,
            fontSize = 13.sp,
            color = if (allowed) LogisticsTheme.colors.textPrimary else LogisticsTheme.colors.textTertiary
        )
    }
}

/** ColorScheme 扩展：浅色主色容器 */
@Composable
private fun androidx.compose.material3.ColorScheme.primaryLight(): Color =
    com.company.logistics.ui.theme.LogisticsColors.PrimaryLight
