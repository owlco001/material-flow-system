package com.company.logistics.ui.screens

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.draw.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.TransferRequestItem
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.LogisticsIcons
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 扫码交接页 —— 交接方扫流转码后进入。
 *
 * 真实场景：搬运/接收人员扫到流转码，看到这单的物料清单，
 * 轻点勾选实际交接的物料（可部分交接），点"确认交接"即留痕。
 *
 * 视觉：渐变横幅 + 勾选进度条 + 卡片式物料行（选中态动画）+ 底部吸底确认栏。
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
    Column(modifier = modifier.fillMaxSize()) {
        when (detailState) {
            WorkspaceLoadState.LOADING -> {
                VSpace(Spacing.xl)
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Dimens.PagePadding),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(modifier = Modifier.width(22.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(Spacing.sm))
                    Text("正在读取流转单…", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                }
            }
            WorkspaceLoadState.ERROR -> {
                VSpace(Spacing.xl)
                EmptyState(
                    title = "流转单加载失败",
                    description = detailError ?: "请稍后重试",
                    action = { SecondaryButton(text = "重试", onClick = onRetry) },
                )
            }
            WorkspaceLoadState.CONTENT -> {
                val request = detail
                if (request == null) {
                    VSpace(Spacing.xl)
                    EmptyState(
                        title = "未找到流转单",
                        description = "请重新扫码",
                        action = { SecondaryButton(text = "返回", onClick = onBack) },
                    )
                } else {
                    // 勾选状态：默认全选
                    val checked = remember(request.id) {
                        mutableStateMapOf<String, Boolean>().apply {
                            request.items.forEach { put(it.materialId, true) }
                        }
                    }
                    val selectedCount = checked.count { it.value }
                    val total = request.items.size

                    HandoverBanner(
                        request = request,
                        selectedCount = selectedCount,
                        checked = checked,
                        onBack = onBack,
                    )

                    // 物料列表（滚动区）
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .verticalScroll(rememberScrollState())
                            .padding(horizontal = Dimens.PagePadding),
                    ) {
                        VSpace(Spacing.md)
                        if (request.items.isEmpty()) {
                            AppCard {
                                Text(
                                    "服务端未返回明细",
                                    fontSize = 12.sp,
                                    color = LogisticsTheme.colors.textTertiary,
                                )
                            }
                        } else {
                            request.items.forEach { item ->
                                val isChecked = checked[item.materialId] == true
                                HandoverItemCard(
                                    item = item,
                                    checked = isChecked,
                                    onToggle = { checked[item.materialId] = !isChecked },
                                )
                                VSpace(Spacing.sm)
                            }
                        }
                        VSpace(Spacing.md)
                    }

                    // 底部吸底确认栏
                    HandoverBottomBar(
                        selectedCount = selectedCount,
                        submitting = submitting,
                        submitError = submitError,
                        onConfirm = {
                            val selected = request.items
                                .filter { checked[it.materialId] == true }
                                .map { it.materialId to it.quantity }
                            onConfirm(selected)
                        },
                    )
                }
            }
            else -> {
                VSpace(Spacing.xl)
                EmptyState(
                    title = "未找到流转单",
                    description = "请重新扫码",
                    action = { SecondaryButton(text = "返回", onClick = onBack) },
                )
            }
        }
    }
}

/** 顶部渐变横幅：标题 + 单号 + 勾选进度 + 全选/清空。 */
@Composable
private fun HandoverBanner(
    request: TransferRequest,
    selectedCount: Int,
    checked: SnapshotStateMap<String, Boolean>,
    onBack: () -> Unit,
) {
    val total = request.items.size
    val progress by animateFloatAsState(
        targetValue = if (total > 0) selectedCount.toFloat() / total else 0f,
        animationSpec = tween(300),
        label = "handoverProgress",
    )
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = Dimens.PagePadding)
            .padding(top = Spacing.sm),
        shape = RoundedCornerShape(22.dp),
        shadowElevation = 4.dp,
    ) {
        Box(
            modifier = Modifier
                .background(
                    Brush.linearGradient(
                        colors = listOf(LogisticsColors.Primary, LogisticsColors.PrimaryDark),
                    ),
                )
                .padding(18.dp),
        ) {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            "扫码交接",
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Bold,
                            color = Color.White,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            request.documentNo?.takeIf { it.isNotBlank() } ?: "流转单",
                            fontSize = 12.sp,
                            fontFamily = LogisticsType.MonoFamily,
                            color = Color.White.copy(alpha = 0.8f),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    TextButton(onClick = onBack) {
                        Text("返回", fontSize = 14.sp, color = Color.White)
                    }
                }

                Spacer(Modifier.height(14.dp))

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "已选 $selectedCount / $total 项",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = Color.White,
                    )
                    Spacer(Modifier.weight(1f))
                    TextButton(
                        onClick = {
                            request.items.forEach { checked[it.materialId] = true }
                        },
                    ) {
                        Text("全选", fontSize = 13.sp, color = Color.White.copy(alpha = 0.92f))
                    }
                    TextButton(
                        onClick = {
                            request.items.forEach { checked[it.materialId] = false }
                        },
                    ) {
                        Text("清空", fontSize = 13.sp, color = Color.White.copy(alpha = 0.72f))
                    }
                }

                Spacer(Modifier.height(8.dp))
                LinearProgressIndicator(
                    progress = { progress },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(7.dp)
                        .clip(RoundedCornerShape(4.dp)),
                    color = Color.White,
                    trackColor = Color.White.copy(alpha = 0.25f),
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "轻点勾选实际交接的物料，可部分交接",
                    fontSize = 11.sp,
                    color = Color.White.copy(alpha = 0.72f),
                )
            }
        }
    }
}

/** 单条物料卡片：选中态背景/边框动画 + 自定义勾选框。 */
@Composable
private fun HandoverItemCard(
    item: TransferRequestItem,
    checked: Boolean,
    onToggle: () -> Unit,
) {
    val containerColor by animateColorAsState(
        targetValue = if (checked) LogisticsTheme.colors.primaryContainer
        else LogisticsTheme.colors.cardBackground,
        animationSpec = tween(220),
        label = "itemBg",
    )
    val borderColor = if (checked) LogisticsColors.Primary else LogisticsTheme.colors.border

    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onToggle),
        shape = RoundedCornerShape(16.dp),
        color = containerColor,
        border = BorderStroke(1.5.dp, borderColor),
        shadowElevation = if (checked) 2.dp else 0.dp,
    ) {
        Row(
            modifier = Modifier.padding(13.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CheckIndicator(checked = checked)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    item.materialId,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = LogisticsTheme.colors.textPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    buildString {
                        append("数量 ${item.quantity}")
                        item.batchNo?.let { append(" · 批次 $it") }
                        item.sourceLocationCode?.let { append(" · 从 $it") }
                        item.targetLocationCode?.let { append(" · 到 $it") }
                    },
                    fontSize = 12.sp,
                    fontFamily = LogisticsType.MonoFamily,
                    color = LogisticsTheme.colors.textSecondary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (checked) {
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = LogisticsColors.Primary,
                ) {
                    Text(
                        "×${item.quantity}",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = LogisticsType.MonoFamily,
                        color = Color.White,
                        modifier = Modifier.padding(horizontal = 9.dp, vertical = 5.dp),
                    )
                }
            }
        }
    }
}

/** 自定义勾选框：缩放 + 填充动画。 */
@Composable
private fun CheckIndicator(checked: Boolean) {
    val scale by animateFloatAsState(
        targetValue = if (checked) 1f else 0.88f,
        animationSpec = spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = 500f),
        label = "checkScale",
    )
    Box(
        modifier = Modifier
            .size(28.dp)
            .graphicsLayer(scaleX = scale, scaleY = scale)
            .clip(RoundedCornerShape(9.dp))
            .background(if (checked) LogisticsColors.Primary else Color.Transparent),
        contentAlignment = Alignment.Center,
    ) {
        if (!checked) {
            // 未选中：描边圆角框
            androidx.compose.foundation.Canvas(modifier = Modifier.size(28.dp)) {
                drawRoundRect(
                    color = LogisticsColors.Neutral.copy(alpha = 0.55f),
                    style = androidx.compose.ui.graphics.drawscope.Stroke(width = 2.dp.toPx()),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(9.dp.toPx()),
                )
            }
        } else {
            Icon(
                imageVector = LogisticsIcons.Check,
                contentDescription = "已选中",
                tint = Color.White,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

/** 底部吸底确认栏。 */
@Composable
private fun HandoverBottomBar(
    selectedCount: Int,
    submitting: Boolean,
    submitError: String?,
    onConfirm: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = LogisticsTheme.colors.cardBackground,
        shadowElevation = 8.dp,
    ) {
        Column(
            modifier = Modifier.padding(
                horizontal = Dimens.PagePadding,
                vertical = 12.dp,
            ),
        ) {
            submitError?.takeIf { it.isNotBlank() }?.let {
                Text(it, fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
                VSpace(Spacing.sm)
            }
            PrimaryButton(
                text = when {
                    submitting -> "提交中…"
                    selectedCount == 0 -> "请先勾选物料"
                    else -> "确认交接（已选 $selectedCount 项）"
                },
                onClick = onConfirm,
                enabled = !submitting && selectedCount > 0,
                modifier = Modifier.fillMaxWidth(),
            )
            VSpace(Spacing.xs)
            Text(
                "确认后即生成交接记录（交接人、时间、物料明细），可在流转单详情查看",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}
