package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.ExceptionRecord
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/** 本人提报的异常记录：只读查看审批进度，审批操作在 Web 管理后台完成。 */
@Composable
fun MyExceptionsScreen(
    records: List<ExceptionRecord>,
    state: WorkspaceLoadState,
    error: String?,
    onRefresh: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        Spacer(Modifier.height(Spacing.sm))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("我的异常", fontSize = 19.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text(
                    "本人提报的异常 · 审批请在 Web 管理后台处理",
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            SecondaryButton(text = "刷新", onClick = onRefresh)
        }
        VSpace(Spacing.md)

        when (state) {
            WorkspaceLoadState.LOADING -> {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                    CircularProgressIndicator()
                }
            }
            WorkspaceLoadState.ERROR -> {
                EmptyState(title = "加载失败", description = error ?: "请重试")
                SecondaryButton(text = "重试", onClick = onRefresh)
            }
            WorkspaceLoadState.EMPTY, WorkspaceLoadState.IDLE -> {
                EmptyState(title = "暂无异常记录", description = "提报的异常会在这里显示审批进度。")
            }
            WorkspaceLoadState.CONTENT -> {
                records.forEach { record ->
                    ExceptionRecordCard(record)
                    VSpace(Spacing.sm)
                }
            }
        }

        VSpace(Spacing.md)
        SecondaryButton(text = "返回", onClick = onBack, modifier = Modifier.fillMaxWidth())
        VSpace(Spacing.xxl)
    }
}

@Composable
private fun ExceptionRecordCard(record: ExceptionRecord) {
    AppCard(accentColor = LogisticsTheme.colors.border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    record.typeLabel,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                VSpace(2.dp)
                Text(
                    record.description ?: "无说明",
                    fontSize = 13.sp,
                    color = LogisticsTheme.colors.textSecondary,
                    maxLines = 2,
                )
            }
            StatusTag(
                label = record.statusLabel,
                color = when (record.status) {
                    "APPROVED", "RESOLVED" -> LogisticsColors.Success
                    "REJECTED" -> LogisticsColors.Danger
                    else -> LogisticsColors.Warning
                },
                containerColor = LogisticsTheme.colors.cardBackground,
            )
        }
        VSpace(Spacing.sm)
        Text(
            "账面 ${record.bookQuantity} · 实际 ${record.actualQuantity} · 差异 ${record.difference}",
            fontSize = 12.sp,
            fontFamily = LogisticsType.MonoFamily,
            color = LogisticsTheme.colors.textSecondary,
        )
        VSpace(2.dp)
        Text(
            listOfNotNull(
                record.orderNo?.let { "订单 $it" },
                record.createdAt?.take(16)?.replace("T", " "),
            ).joinToString(" · ").ifBlank { "—" },
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
        )
        if (record.reviewedAt != null) {
            VSpace(2.dp)
            Text(
                "审批时间 ${record.reviewedAt.take(16).replace("T", " ")}",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
            )
        }
    }
}
