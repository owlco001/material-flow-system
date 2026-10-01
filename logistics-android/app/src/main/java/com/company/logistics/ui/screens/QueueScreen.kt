package com.company.logistics.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.SyncStatus
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.ConfirmDialog
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.LogisticsIcons
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.tagTextColor
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 离线暂存 / 同步队列页。
 *
 * 契约约束：
 *  - 所有写操作携带幂等键，重放由服务端去重，因此同步本地队列不会产生重复单据；
 *  - 服务端时间为审计唯一依据，本地时间仅作展示参考；
 *  - 冲突（409）需交由用户处理，不能静默丢弃。
 */
@Composable
fun QueueScreen(
    queue: List<OfflineOperation>,
    syncing: Boolean,
    readOnly: Boolean = false,
    onSync: () -> Unit,
    onClearSynced: () -> Unit,
    onRetry: (String) -> Unit = {},
    onDiscard: (String) -> Unit = {},
    modifier: Modifier = Modifier
) {
    val pending = queue.count { it.status == SyncStatus.PENDING || it.status == SyncStatus.FAILED }
    val conflicts = queue.count { it.status == SyncStatus.CONFLICT }
    val syncedCount = queue.count { it.status == SyncStatus.SYNCED }
    // 清理已同步是批量不可逆删除：必须二次确认
    var showClearConfirm by remember { mutableStateOf(false) }

    if (showClearConfirm) {
        ConfirmDialog(
            title = "清理已同步记录？",
            message = "将删除本机 $syncedCount 条已同步的离线记录。服务端数据不受影响，未同步的记录不会被删除。",
            confirmText = "清理",
            danger = true,
            onConfirm = {
                showClearConfirm = false
                onClearSynced()
            },
            onDismiss = { showClearConfirm = false }
        )
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        if (queue.isEmpty()) {
            EmptyState(
                title = "暂无离线记录",
                description = "网络不可用时提交的流转操作会暂存在此，联网后自动上传"
            )
            return@Column
        }

        // 概览横幅（渐变）
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
                                "待同步 ${pending + conflicts} 条",
                                fontSize = 22.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = LogisticsType.MonoFamily,
                                color = Color.White,
                            )
                            VSpace(4.dp)
                            Text(
                                buildString {
                                    append("共 ${queue.size} 条记录")
                                    if (conflicts > 0) append(" · 冲突 $conflicts 条")
                                },
                                fontSize = 12.sp,
                                color = Color.White.copy(alpha = 0.78f),
                            )
                        }
                        if (conflicts > 0) {
                            Surface(
                                shape = RoundedCornerShape(10.dp),
                                color = LogisticsTheme.colors.danger,
                            ) {
                                Text(
                                    "! 有冲突",
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = Color.White,
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                                )
                            }
                        }
                    }
                    VSpace(Spacing.md)
                    val syncEnabled = !readOnly && !syncing && (pending > 0 || conflicts > 0)
                    Surface(
                        shape = RoundedCornerShape(12.dp),
                        color = Color.White,
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable(enabled = syncEnabled, onClick = onSync),
                    ) {
                        Text(
                            if (syncing) "同步中…" else "立即同步",
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold,
                            color = LogisticsColors.BrandNavyDark.copy(
                                alpha = if (syncEnabled) 1f else 0.4f,
                            ),
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(vertical = 13.dp),
                        )
                    }
                    if (queue.any { it.status == SyncStatus.SYNCED }) {
                        VSpace(Spacing.sm)
                        val clearEnabled = !readOnly && !syncing
                        Surface(
                            shape = RoundedCornerShape(12.dp),
                            border = BorderStroke(1.5.dp, Color.White.copy(alpha = 0.65f)),
                            color = Color.Transparent,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable(enabled = clearEnabled, onClick = { showClearConfirm = true }),
                        ) {
                            Text(
                                "清理已同步记录",
                                fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = Color.White.copy(alpha = if (clearEnabled) 1f else 0.5f),
                                textAlign = TextAlign.Center,
                                modifier = Modifier.padding(vertical = 12.dp),
                            )
                        }
                    }
                    if (readOnly) {
                        VSpace(Spacing.sm)
                        Text(
                            "测试预览只读，不能同步或清理离线写操作",
                            fontSize = 12.sp,
                            color = Color.White.copy(alpha = 0.85f),
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
            }
        }

        VSpace(Spacing.lg)
        SectionTitle("队列明细（${queue.size}）")
        VSpace(Spacing.sm)

        queue.forEach { op ->
            QueueItemCard(
                op = op,
                readOnly = readOnly,
                onRetry = onRetry,
                onDiscard = onDiscard,
            )
            VSpace(Spacing.sm)
        }

        VSpace(Spacing.xxl)
    }
}

@Composable
private fun QueueItemCard(
    op: OfflineOperation,
    readOnly: Boolean,
    onRetry: (String) -> Unit,
    onDiscard: (String) -> Unit,
) {
    val statusColor = op.status.color
    // 放弃是不可逆删除：必须二次确认，文案写明后果
    var showDiscardConfirm by remember { mutableStateOf(false) }
    val timeText = remember(op.createdAt) {
        SimpleDateFormat("MM-dd HH:mm", Locale.CHINA).format(Date(op.createdAt))
    }

    if (showDiscardConfirm) {
        ConfirmDialog(
            title = "放弃这条离线记录？",
            message = "「${op.opType.label} · ${op.materialCode}」将从本机永久删除，不再同步到服务端。已上传的服务端数据不受影响。",
            confirmText = "放弃记录",
            dismissText = "再想想",
            danger = true,
            onConfirm = {
                showDiscardConfirm = false
                onDiscard(op.id)
            },
            onDismiss = { showDiscardConfirm = false }
        )
    }

    AppCard(accentColor = statusColor) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(30.dp)
                    .background(statusColor.copy(alpha = 0.14f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    op.opType.name.first().toString(),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = statusColor
                )
            }
            Spacer(Modifier.width(Spacing.sm))
            Column(Modifier.weight(1f)) {
                Text(
                    op.opType.label,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary
                )
                VSpace(2.dp)
                Text(
                    // 服务端时间优先展示，未同步时展示本地时间并标注
                    if (op.serverTime != null) "服务端 ${op.serverTime}" else "本地 $timeText（待服务端确认）",
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary
                )
            }
            StatusTag(
                label = op.status.label,
                color = op.status.tagTextColor(),
                containerColor = statusColor.copy(alpha = 0.14f)
            )
        }

        VSpace(Spacing.md)

        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                op.materialCode,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                color = MaterialTheme.colorScheme.primary
            )
            Spacer(Modifier.weight(1f))
            Text(
                buildString {
                    append("${op.quantity} 件")
                    if (op.targetLocation != null) append(" → ${op.targetLocation}")
                },
                fontSize = 13.sp,
                fontFamily = LogisticsType.MonoFamily,
                fontWeight = FontWeight.SemiBold,
                color = LogisticsTheme.colors.textPrimary
            )
        }

        if (op.status == SyncStatus.CONFLICT || op.status == SyncStatus.FAILED) {
            if (op.errorMessage != null) {
                VSpace(Spacing.sm)
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            statusColor.copy(alpha = 0.08f),
                            MaterialTheme.shapes.small
                        )
                        .padding(10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Image(
                        imageVector = LogisticsIcons.Alert,
                        contentDescription = null,
                        colorFilter = ColorFilter.tint(statusColor),
                        modifier = Modifier.size(12.dp)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        op.errorMessage,
                        fontSize = 12.sp,
                        color = statusColor,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
            // 失败 / 冲突不静默丢弃：用户可手动重试（回 PENDING 等待重放）或放弃（删除）。
            VSpace(Spacing.sm)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
            ) {
                SecondaryButton(
                    text = "重试",
                    onClick = { onRetry(op.id) },
                    enabled = !readOnly,
                    modifier = Modifier.weight(1f),
                )
                TextButton(
                    onClick = { showDiscardConfirm = true },
                    enabled = !readOnly,
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        "放弃",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = LogisticsTheme.colors.danger,
                    )
                }
            }
        }

        VSpace(Spacing.sm)
        Text(
            "幂等键 ${op.clientOperationId.take(8)}…",
            fontSize = 10.sp,
            color = LogisticsTheme.colors.textTertiary,
            fontFamily = LogisticsType.MonoFamily
        )
    }
}
