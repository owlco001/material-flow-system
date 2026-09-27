package com.company.logistics.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Surface
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.ApprovalStatus
import com.company.logistics.model.TransferHandoverRecord
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestItem
import com.company.logistics.model.UserRole
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.FlowQrCodeDialog
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 流转单详情页 —— 扫流转码直达。
 *
 * 真实场景：仓库/产线人员扫贴在物料或托盘上的流转码，直接看到这单的完整信息，
 * 审核通过后可展示流转码供交接方扫码，交接方勾选物料确认交接，全程留痕。
 * 所有角色可进入，按钮按角色与服务端状态出现。
 *
 * 视觉：渐变状态横幅 + 分区卡片 + 物料徽章 + 交接时间线。
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
                FlowDetailHeader(request = request, onBack = onBack)
                VSpace(Spacing.md)
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

/** 顶部渐变横幅：单号 + 类型 + 状态徽章。 */
@Composable
private fun FlowDetailHeader(
    request: TransferRequest,
    onBack: () -> Unit,
) {
    val approval = ApprovalStatus.from(request.effectiveStatusCode, request.displayStatusLabel)
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
                            request.documentNo?.takeIf { it.isNotBlank() } ?: "流转单详情",
                            fontSize = 19.sp,
                            fontWeight = FontWeight.Bold,
                            color = Color.White,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            request.type.ifBlank { "流转申请" },
                            fontSize = 12.sp,
                            color = Color.White.copy(alpha = 0.78f),
                        )
                    }
                    TextButton(onClick = onBack) {
                        Text("返回", fontSize = 14.sp, color = Color.White)
                    }
                }
                Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    // 状态徽章：白底 + 状态色文字
                    Surface(
                        shape = RoundedCornerShape(10.dp),
                        color = Color.White,
                    ) {
                        Text(
                            approval.label,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            color = approval.color,
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        )
                    }
                    Spacer(Modifier.width(10.dp))
                    request.createdAt?.let {
                        Text(
                            it,
                            fontSize = 11.sp,
                            fontFamily = LogisticsType.MonoFamily,
                            color = Color.White.copy(alpha = 0.72f),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
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

    SectionCard(title = "基本信息") {
        FlowKv("单号", request.documentNo ?: request.id, mono = true)
        FlowKv("类型", request.type.ifBlank { "—" })
        request.createdBy?.let { FlowKv("创建人", it) }
        request.createdAt?.let { FlowKv("创建时间", it, mono = true) }
        request.approvedBy?.let { FlowKv("审批人", it) }
        request.approvedAt?.let { FlowKv("审批时间", it, mono = true) }
        request.executedAt?.let { FlowKv("执行时间", it, mono = true) }
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
                        text = "开始交接",
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

    VSpace(Spacing.md)

    SectionCard(title = "物料明细（${request.items.size}）") {
        if (request.items.isEmpty()) {
            Text("服务端未返回明细", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
        } else {
            request.items.forEachIndexed { index, item ->
                if (index > 0) VSpace(Spacing.sm)
                FlowMaterialRow(index = index + 1, item = item)
            }
        }
    }

    VSpace(Spacing.md)

    // 交接闭环：显示关联交接状态，待确认时接收人可一键确认收货
    val handoverStatus = request.handoverStatus?.uppercase(java.util.Locale.ROOT)
    SectionCard(title = "交接状态") {
        when {
            handoverStatus.isNullOrBlank() -> {
                HandoverStatusPill(
                    text = when (request.effectiveStatusCode) {
                        "PENDING_APPROVAL" -> "等待审批"
                        "APPROVED" -> "已审批 · 未交接"
                        "EXECUTED" -> "已执行"
                        "REJECTED" -> "已驳回"
                        else -> "暂无交接"
                    },
                    color = LogisticsTheme.colors.textSecondary,
                )
                VSpace(Spacing.xs)
                Text(
                    when (request.effectiveStatusCode) {
                        "PENDING_APPROVAL" -> "审批通过后可执行出库并交接"
                        "APPROVED" -> "尚未发起交接，交接方扫码后勾选物料确认"
                        else -> ""
                    },
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            handoverStatus == "PENDING" -> {
                HandoverStatusPill(text = "待确认收货", color = LogisticsTheme.colors.warning)
                VSpace(Spacing.xs)
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
                HandoverStatusPill(text = "已确认收货", color = LogisticsTheme.colors.success)
            }
            handoverStatus == "REJECTED" -> {
                HandoverStatusPill(text = "交接已驳回", color = LogisticsTheme.colors.danger)
            }
            handoverStatus == "CANCELLED" -> {
                HandoverStatusPill(text = "交接已取消", color = LogisticsTheme.colors.textSecondary)
            }
            else -> {
                Text(handoverStatus, fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
        }
    }

    VSpace(Spacing.md)

    // 交接留痕：历次扫码交接记录（时间线）
    SectionCard(title = "交接记录（${handoverRecords.size}）") {
        when {
            handoverRecordsState == WorkspaceLoadState.LOADING -> {
                Text("正在加载交接记录…", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
            handoverRecords.isEmpty() -> {
                Text("暂无交接记录", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
            }
            else -> {
                handoverRecords.forEachIndexed { index, record ->
                    HandoverTimelineRow(
                        record = record,
                        isLast = index == handoverRecords.lastIndex,
                    )
                }
            }
        }
    }
}

/** 分区卡片：统一标题样式。 */
@Composable
private fun SectionCard(
    title: String,
    content: @Composable () -> Unit,
) {
    AppCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(width = 4.dp, height = 16.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(LogisticsColors.Primary),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                title,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary,
            )
        }
        VSpace(Spacing.sm)
        content()
    }
}

/** 物料行：序号徽章 + 信息 + 数量徽章。 */
@Composable
private fun FlowMaterialRow(index: Int, item: TransferRequestItem) {
    Surface(
        shape = RoundedCornerShape(14.dp),
        color = LogisticsTheme.colors.cardBackground,
        border = BorderStroke(1.dp, LogisticsTheme.colors.border),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 序号徽章
            Box(
                modifier = Modifier
                    .size(26.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(LogisticsTheme.colors.primaryContainer),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "$index",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsColors.PrimaryDark,
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    item.materialId,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = LogisticsTheme.colors.textPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    buildString {
                        item.batchNo?.let { append("批次 $it · ") }
                        item.sourceLocationCode?.let { append("从 $it ") }
                        item.targetLocationCode?.let { append("到 $it") }
                    }.ifBlank { "—" },
                    fontSize = 11.sp,
                    fontFamily = LogisticsType.MonoFamily,
                    color = LogisticsTheme.colors.textSecondary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Surface(
                shape = RoundedCornerShape(9.dp),
                color = LogisticsTheme.colors.primaryContainer,
            ) {
                Text(
                    "×${item.quantity}",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = LogisticsType.MonoFamily,
                    color = LogisticsColors.PrimaryDark,
                    modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp),
                )
            }
        }
    }
}

/** 交接状态徽章：浅色底 + 状态色文字。 */
@Composable
private fun HandoverStatusPill(text: String, color: Color) {
    Surface(
        shape = RoundedCornerShape(10.dp),
        color = color.copy(alpha = 0.12f),
    ) {
        Text(
            text,
            fontSize = 13.sp,
            fontWeight = FontWeight.Bold,
            color = color,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
        )
    }
}

/** 交接记录时间线行。 */
@Composable
private fun HandoverTimelineRow(record: TransferHandoverRecord, isLast: Boolean) {
    Row(modifier = Modifier.height(IntrinsicSize.Min)) {
        // 时间线
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(top = 4.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(RoundedCornerShape(5.dp))
                    .background(LogisticsColors.Primary),
            )
            if (!isLast) {
                Box(
                    modifier = Modifier
                        .width(2.dp)
                        .fillMaxHeight()
                        .padding(vertical = 4.dp)
                        .clip(RoundedCornerShape(1.dp))
                        .background(LogisticsTheme.colors.border),
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.padding(bottom = if (isLast) 0.dp else 14.dp)) {
            Text(
                record.createdByName ?: "—",
                fontSize = 13.sp,
                fontWeight = FontWeight.SemiBold,
                color = LogisticsTheme.colors.textPrimary,
            )
            record.createdAt?.let {
                Text(
                    it,
                    fontSize = 11.sp,
                    fontFamily = LogisticsType.MonoFamily,
                    color = LogisticsTheme.colors.textTertiary,
                )
            }
            Spacer(Modifier.height(4.dp))
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

@Composable
private fun FlowKv(label: String, value: String, mono: Boolean = false) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Text(
                label,
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textTertiary,
                modifier = Modifier.width(72.dp),
            )
            Text(
                value,
                fontSize = 13.sp,
                fontFamily = if (mono) LogisticsType.MonoFamily else null,
                color = LogisticsTheme.colors.textPrimary,
                modifier = Modifier.weight(1f),
            )
        }
        Spacer(Modifier.height(5.dp))
        HorizontalDivider(color = LogisticsTheme.colors.border.copy(alpha = 0.6f), thickness = 0.5.dp)
    }
}
