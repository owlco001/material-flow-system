package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import com.company.logistics.model.TransferHandoverRecord
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.UserRole
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.FlowQrCodeDialog
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 流转单详情页 —— 扫流转码直达。
 *
 * 真实场景：仓库/产线人员扫贴在物料或托盘上的流转码，直接看到这单的完整信息，
 * 审核通过后可展示流转码供交接方扫码，交接方勾选物料确认交接，全程留痕。
 * 所有角色可进入，按钮按角色与服务端状态出现。
 */
@Composable
fun FlowDetailScreen(
    role: UserRole,
    detail: TransferRequest?,
    detailState: WorkspaceLoadState,
    detailError: String?,
    handoverSubmitting: Boolean,
    handoverRecords: List<TransferHandoverRecord>,
    handoverRecordsState: WorkspaceLoadState,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onConfirmHandover: (String) -> Unit,
    onStartHandover: () -> Unit,
    onLoadHandoverRecords: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var showQrCode by remember { mutableStateOf(false) }

    // 进入详情即加载交接记录（留痕）
    LaunchedEffect(detail?.id) {
        if (detail != null) onLoadHandoverRecords()
    }

    detail?.documentNo?.takeIf { it.isNotBlank() }?.let { docNo ->
        if (showQrCode) {
            FlowQrCodeDialog(documentNo = docNo, onDismiss = { showQrCode = false })
        }
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
                Column(Modifier.weight(1f)) {
                    Text(
                        detail?.documentNo?.takeIf { it.isNotBlank() } ?: "流转单详情",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary,
                    )
                    VSpace(6.dp)
                    Text(
                        "扫码直达；操作按钮只根据角色与服务端状态出现",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary,
                    )
                }
                TextButton(onClick = onBack) { Text("返回") }
            }
        }

        VSpace(Spacing.md)

        when (detailState) {
            WorkspaceLoadState.LOADING -> {
                VSpace(Spacing.xl)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(modifier = Modifier.width(22.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(Spacing.sm))
                    Text("正在读取流转单…", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                }
            }
            WorkspaceLoadState.ERROR -> EmptyState(
                title = "流转单加载失败",
                description = detailError ?: "请稍后重试",
                action = { SecondaryButton(text = "重试", onClick = onRetry) },
            )
            WorkspaceLoadState.CONTENT -> detail?.let { request ->
                FlowDetailContent(
                    request = request,
                    role = role,
                    handoverSubmitting = handoverSubmitting,
                    handoverRecords = handoverRecords,
                    handoverRecordsState = handoverRecordsState,
                    onConfirmHandover = onConfirmHandover,
                    onShowQrCode = { showQrCode = true },
                    onStartHandover = onStartHandover,
                )
            }
            else -> EmptyState(
                title = "未找到流转单",
                description = "请重新扫码",
                action = { SecondaryButton(text = "返回", onClick = onBack) },
            )
        }

        VSpace(Spacing.xxl)
    }
}

@Composable
private fun FlowDetailContent(
    request: TransferRequest,
    role: UserRole,
    handoverSubmitting: Boolean,
    handoverRecords: List<TransferHandoverRecord>,
    handoverRecordsState: WorkspaceLoadState,
    onConfirmHandover: (String) -> Unit,
    onShowQrCode: () -> Unit,
    onStartHandover: () -> Unit,
) {
    val statusCode = request.effectiveStatusCode
    // 审核通过后生成流转码：APPROVED / EXECUTED 状态可展示
    val canShowQr = statusCode in setOf("APPROVED", "EXECUTED") && !request.documentNo.isNullOrBlank()
    val canHandover = statusCode in setOf("APPROVED", "EXECUTED") &&
        role in setOf(UserRole.MATERIAL, UserRole.OPERATOR, UserRole.WAREHOUSE_ADMIN, UserRole.ADMIN)

    AppCard(accentColor = MaterialTheme.colorScheme.primary) {
        Text("基本信息", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
        VSpace(Spacing.sm)
        FlowKv("单号", request.documentNo ?: request.id)
        FlowKv("类型", request.type.ifBlank { "—" })
        FlowKv("状态", request.displayStatusLabel)
        request.createdBy?.let { FlowKv("创建人", it) }
        request.createdAt?.let { FlowKv("创建时间", it) }
        request.approvedBy?.let { FlowKv("审批人", it) }
        request.approvedAt?.let { FlowKv("审批时间", it) }
        request.executedAt?.let { FlowKv("执行时间", it) }
        request.remark?.takeIf { it.isNotBlank() }?.let { FlowKv("备注", it) }

        if (canShowQr || canHandover) {
            VSpace(Spacing.sm)
            Row(modifier = Modifier.fillMaxWidth()) {
                if (canShowQr) {
                    SecondaryButton(
                        text = "流转码",
                        onClick = onShowQrCode,
                        modifier = Modifier.weight(1f),
                    )
                }
                if (canShowQr && canHandover) {
                    Spacer(Modifier.width(Spacing.sm))
                }
                if (canHandover) {
                    PrimaryButton(
                        text = "扫码交接",
                        onClick = onStartHandover,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
            if (canShowQr) {
                VSpace(Spacing.xs)
                Text(
                    "审核已通过，流转码已生成；出示给交接方扫码",
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary,
                )
            }
        }
    }

    VSpace(Spacing.sm)

    AppCard {
        Text("物料明细（${request.items.size}）", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
        if (request.items.isEmpty()) {
            VSpace(Spacing.sm)
            Text("服务端未返回明细", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
        } else {
            request.items.forEachIndexed { index, item ->
                VSpace(Spacing.sm)
                Text(
                    "${index + 1}. ${item.materialId}",
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                Text(
                    "数量 ${item.quantity}" +
                        (item.batchNo?.let { " · 批次 $it" } ?: "") +
                        (item.sourceLocationCode?.let { " · 从 $it" } ?: "") +
                        (item.targetLocationCode?.let { " · 到 $it" } ?: ""),
                    fontSize = 12.sp,
                    fontFamily = LogisticsType.MonoFamily,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
        }
    }

    VSpace(Spacing.sm)

    // 交接闭环：显示关联交接状态，待确认时接收人可一键确认收货
    val handoverStatus = request.handoverStatus?.uppercase(java.util.Locale.ROOT)
    AppCard(accentColor = LogisticsTheme.colors.warning) {
        Text("交接状态", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
        VSpace(Spacing.sm)
        when {
            handoverStatus.isNullOrBlank() -> {
                Text(
                    when (request.effectiveStatusCode) {
                        "PENDING_APPROVAL" -> "等待审批，审批通过后可执行出库并交接"
                        "APPROVED" -> "已审批，尚未发起交接"
                        "EXECUTED" -> "已执行"
                        "REJECTED" -> "已驳回"
                        else -> "暂无交接记录"
                    },
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            handoverStatus == "PENDING" -> {
                Text("待确认收货", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = LogisticsTheme.colors.warning)
                VSpace(Spacing.sm)
                Text("物料已发出，等待接收人确认", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                if (role in setOf(UserRole.OPERATOR, UserRole.WAREHOUSE_ADMIN, UserRole.ADMIN)) {
                    VSpace(Spacing.sm)
                    PrimaryButton(
                        text = if (handoverSubmitting) "提交中…" else "确认收货",
                        onClick = { request.lastHandoverId?.let(onConfirmHandover) },
                        enabled = !handoverSubmitting && !request.lastHandoverId.isNullOrBlank(),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
            handoverStatus == "CONFIRMED" -> {
                Text("已确认收货", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = LogisticsTheme.colors.success)
            }
            handoverStatus == "REJECTED" -> {
                Text("交接已驳回", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = LogisticsTheme.colors.danger)
            }
            handoverStatus == "CANCELLED" -> {
                Text("交接已取消", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            }
            else -> {
                Text(handoverStatus, fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
        }
    }

    VSpace(Spacing.sm)

    // 交接留痕：历次扫码交接记录
    AppCard {
        Text("交接记录", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
        VSpace(Spacing.sm)
        when {
            handoverRecordsState == WorkspaceLoadState.LOADING -> {
                Text("正在加载交接记录…", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
            handoverRecords.isEmpty() -> {
                Text("暂无交接记录", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
            }
            else -> {
                handoverRecords.forEach { record ->
                    VSpace(Spacing.xs)
                    Text(
                        "${record.createdByName ?: "—"} · ${record.createdAt ?: ""}",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = LogisticsTheme.colors.textPrimary,
                    )
                    val itemText = if (record.items.isEmpty()) {
                        "共 ${record.quantity} 件"
                    } else {
                        record.items.joinToString("、") { "${it.materialId}×${it.quantity}" }
                    }
                    Text(
                        itemText,
                        fontSize = 12.sp,
                        fontFamily = LogisticsType.MonoFamily,
                        color = LogisticsTheme.colors.textSecondary,
                    )
                    record.remark?.takeIf { it.isNotBlank() }?.let {
                        Text("备注：$it", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
                    }
                }
            }
        }
    }
}

@Composable
private fun FlowKv(label: String, value: String) {
    Row(modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary, modifier = Modifier.width(72.dp))
        Text(value, fontSize = 12.sp, color = LogisticsTheme.colors.textPrimary, modifier = Modifier.weight(1f))
    }
}
