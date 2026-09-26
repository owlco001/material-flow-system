package com.company.logistics.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.TransferRequest
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 扫码交接页 —— 交接方扫流转码后进入。
 *
 * 真实场景：搬运/接收人员扫到流转码，看到这单的物料清单，
 * 轻点勾选实际交接的物料（可部分交接），点"确认交接"即留痕。
 */
@Composable
fun FlowHandoverScreen(
    detail: TransferRequest?,
    detailState: WorkspaceLoadState,
    detailError: String?,
    submitting: Boolean,
    submitError: String?,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onConfirm: (selected: List<Pair<String, Int>>) -> Unit,
    modifier: Modifier = Modifier,
) {
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
                        "扫码交接",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary,
                    )
                    VSpace(6.dp)
                    Text(
                        detail?.documentNo?.takeIf { it.isNotBlank() } ?: "勾选实际交接的物料后确认",
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
                Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
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
                FlowHandoverContent(
                    request = request,
                    submitting = submitting,
                    submitError = submitError,
                    onConfirm = onConfirm,
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
private fun FlowHandoverContent(
    request: TransferRequest,
    submitting: Boolean,
    submitError: String?,
    onConfirm: (List<Pair<String, Int>>) -> Unit,
) {
    val checked = remember(request.id) {
        mutableStateMapOf<String, Boolean>().apply {
            request.items.forEach { put(it.materialId, true) }
        }
    }

    AppCard {
        Text(
            "交接物料（${request.items.size}）",
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary,
        )
        VSpace(Spacing.xs)
        Text(
            "轻点勾选实际交接的物料，可部分交接",
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textSecondary,
        )
        if (request.items.isEmpty()) {
            VSpace(Spacing.sm)
            Text("服务端未返回明细", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
        } else {
            request.items.forEach { item ->
                val isChecked = checked[item.materialId] == true
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { checked[item.materialId] = !isChecked }
                        .padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = isChecked, onCheckedChange = { checked[item.materialId] = it })
                    Spacer(Modifier.width(8.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            item.materialId,
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
        }
    }

    VSpace(Spacing.md)

    submitError?.takeIf { it.isNotBlank() }?.let {
        Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        VSpace(Spacing.sm)
    }

    val selectedCount = checked.count { it.value }
    PrimaryButton(
        text = when {
            submitting -> "提交中…"
            selectedCount == 0 -> "请先勾选物料"
            else -> "确认交接（已选 $selectedCount 项）"
        },
        onClick = {
            val selected = request.items
                .filter { checked[it.materialId] == true }
                .map { it.materialId to it.quantity }
            onConfirm(selected)
        },
        enabled = !submitting && selectedCount > 0,
        modifier = Modifier.fillMaxWidth(),
    )
    VSpace(Spacing.xs)
    Text(
        "确认后即生成交接记录（交接人、时间、物料明细），可在流转单详情查看",
        fontSize = 11.sp,
        color = LogisticsTheme.colors.textTertiary,
    )
}
