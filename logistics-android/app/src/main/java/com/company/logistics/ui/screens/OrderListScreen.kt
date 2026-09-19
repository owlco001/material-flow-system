package com.company.logistics.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.ModelDetail
import com.company.logistics.model.ModelRequirementView
import com.company.logistics.model.PagedFlowRecords
import com.company.logistics.model.PagedProductionOrders
import com.company.logistics.model.ProductionOrderDetail
import com.company.logistics.model.ProductionOrderModel
import com.company.logistics.model.ProductionOrderSummary
import com.company.logistics.model.FlowRecordView
import com.company.logistics.model.SyncStatus
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 生产订单列表页 —— P1 切片 B（契约 §3.1 GET /api/v1/production-orders）。
 *
 * 展示订单号 / 产品 / 计划数量 / 状态 / 齐套率 / 缺料数，
 * 支持关键词检索（订单号 / 产品名）。点击订单进入订单详情。
 */
@Composable
fun OrderListScreen(
    paged: PagedProductionOrders?,
    orderDetail: com.company.logistics.model.ProductionOrderDetail?,
    orderDetailLoading: Boolean,
    orderDetailError: String?,
    loading: Boolean,
    error: String?,
    onSearch: (String?) -> Unit,
    onOpenOrder: (String) -> Unit,
    onOpenModel: (String) -> Unit,
    onBackFromDetail: () -> Unit,
    modifier: Modifier = Modifier
) {
    var keyword by remember { mutableStateOf("") }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        // 订单详情（选中订单后渲染，可返回）
        if (orderDetail != null || orderDetailLoading || orderDetailError != null) {
            ProductionOrderDetailSection(
                detail = orderDetail,
                loading = orderDetailLoading,
                error = orderDetailError,
                onOpenModel = onOpenModel,
                onBack = onBackFromDetail
            )
            return@Column
        }

        // 关键词检索
        OutlinedTextField(
            value = keyword,
            onValueChange = { keyword = it },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("订单号 / 产品名", fontSize = 14.sp, color = LogisticsTheme.colors.textTertiary) },
            shape = MaterialTheme.shapes.medium,
            singleLine = true
        )
        VSpace(Spacing.sm)
        PrimaryButton(
            text = if (keyword.isBlank()) "刷新" else "搜索",
            onClick = { onSearch(keyword.ifBlank { null }) },
            loading = loading
        )

        VSpace(Spacing.lg)

        when {
            error != null -> {
                EmptyState(
                    title = "订单加载失败",
                    description = error,
                    action = {
                        PrimaryButton(text = "重试", onClick = { onSearch(keyword.ifBlank { null }) }, loading = false)
                    }
                )
            }
            loading && paged == null -> {
                Box(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 48.dp),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                }
            }
            paged != null && paged.items.isEmpty() -> {
                EmptyState(
                    title = "暂无订单",
                    description = if (keyword.isBlank()) "当前没有生产订单" else "未找到与「$keyword」匹配的订单"
                )
            }
            else -> {
                paged?.items?.forEach { order ->
                    OrderListRow(order, onOpenOrder)
                    VSpace(Spacing.md)
                }
                if (paged != null) {
                    VSpace(Spacing.sm)
                    Text(
                        "共 ${paged.total} 条 · 第 ${paged.page} 页",
                        fontSize = 11.sp,
                        color = LogisticsTheme.colors.textTertiary,
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center
                    )
                    VSpace(Spacing.lg)
                }
            }
        }
    }
}

/** 单条订单卡片：订单号 / 产品 / 计划数量 / 状态 / 齐套率 / 缺料数 */
@Composable
private fun OrderListRow(
    order: ProductionOrderSummary,
    onOpenOrder: (String) -> Unit
) {
    val completionColor = when {
        order.shortageCount > 0 -> SyncStatus.FAILED.color
        order.materialCompletionRate < 100 -> SyncStatus.PENDING.color
        else -> SyncStatus.SYNCED.color
    }

    AppCard(
        accentColor = completionColor,
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onOpenOrder(order.orderNo) }
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                order.orderNo,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                letterSpacing = 0.5.sp,
                color = LogisticsTheme.colors.textPrimary
            )
            Spacer(Modifier.weight(1f))
            StatusTag(
                label = order.status,
                color = completionColor,
                containerColor = if (order.shortageCount > 0) {
                    SyncStatus.FAILED.color
                } else if (order.materialCompletionRate < 100) {
                    SyncStatus.PENDING.color
                } else {
                    SyncStatus.SYNCED.color
                }.copy(alpha = 0.14f),
                symbol = if (order.shortageCount > 0) "!" else "✓"
            )
        }
        VSpace(4.dp)
        Text(
            order.productName,
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textSecondary
        )
        VSpace(Spacing.md)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
        ) {
            MiniMetric("计划数量", order.plannedQuantity.toString(), Modifier.weight(1f))
            MiniMetric("齐套率", "${order.materialCompletionRate}%", Modifier.weight(1f), valueColor = completionColor)
            MiniMetric("缺料", order.shortageCount.toString(), Modifier.weight(1f), valueColor = completionColor)
            MiniMetric("机型", order.modelCount.toString(), Modifier.weight(1f))
        }
        VSpace(Spacing.sm)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                order.plannedDeliveryDate?.let { "交期 $it" } ?: "交期未定",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
            Spacer(Modifier.width(Spacing.md))
            Text(
                "最近流转：" + (order.lastFlowAt ?: "无"),
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
        }
    }
}

@Composable
private fun MiniMetric(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = LogisticsTheme.colors.textPrimary
) {
    Column(modifier = modifier) {
        Text(label, fontSize = 10.sp, color = LogisticsTheme.colors.textTertiary)
        Spacer(Modifier.height(3.dp))
        Text(
            value,
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = LogisticsType.MonoFamily,
            color = valueColor
        )
    }
}

// ==================== 订单详情（机型列表） ====================

/** 订单详情卡 + 机型列表（齐套率 / 缺料），点击机型进 MODEL_DETAIL */
@Composable
private fun ProductionOrderDetailSection(
    detail: ProductionOrderDetail?,
    loading: Boolean,
    error: String?,
    onOpenModel: (String) -> Unit,
    onBack: () -> Unit
) {
    if (loading && detail == null && error == null) {
        Box(
            modifier = Modifier.fillMaxWidth().padding(vertical = 48.dp),
            contentAlignment = Alignment.Center
        ) {
            CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
        }
        return
    }

    if (error != null) {
        EmptyState(
            title = "订单加载失败",
            description = error,
            action = {
                PrimaryButton(text = "返回列表", onClick = onBack, loading = false)
            }
        )
        return
    }

    detail?.let { d ->
        val order = d.order
        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    order.orderNo,
                    fontSize = 19.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = LogisticsType.MonoFamily,
                    letterSpacing = 0.5.sp,
                    color = LogisticsTheme.colors.textPrimary
                )
                Spacer(Modifier.weight(1f))
                StatusTag(
                    label = order.status,
                    color = MaterialTheme.colorScheme.primary,
                    containerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.14f),
                    symbol = "✓"
                )
            }
            VSpace(4.dp)
            Text(order.productName, fontSize = 14.sp, color = LogisticsTheme.colors.textSecondary)
            VSpace(Spacing.md)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
            ) {
                MiniMetric("计划数量", order.plannedQuantity.toString(), Modifier.weight(1f))
                MiniMetric("交期", order.plannedDeliveryDate ?: "未定", Modifier.weight(1f))
            }
        }
        VSpace(Spacing.lg)

        SectionTitle("机型（${d.models.size}）", trailing = "齐套率 / 缺料")
        VSpace(Spacing.sm)

        if (d.models.isEmpty()) {
            EmptyState(
                title = "该订单暂无机型",
                description = "订单尚未维护机型与物料需求"
            )
        } else {
            d.models.forEach { model ->
                ModelRowCard(model, onOpenModel)
                VSpace(Spacing.sm)
            }
        }

        VSpace(Spacing.lg)
        LinkBackButton(onBack)
    } ?: run {
        EmptyState(
            title = "订单不存在",
            description = "请确认订单号是否正确",
            action = { PrimaryButton(text = "返回列表", onClick = onBack, loading = false) }
        )
    }
}

@Composable
private fun ModelRowCard(model: ProductionOrderModel, onOpenModel: (String) -> Unit) {
    val color = when {
        model.shortageMaterialCount > 0 -> com.company.logistics.ui.theme.MaterialStatusColors.Shortage
        model.completionRate < 100 -> com.company.logistics.ui.theme.MaterialStatusColors.Arrived
        else -> com.company.logistics.ui.theme.MaterialStatusColors.InStock
    }
    AppCard(
        accentColor = color,
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onOpenModel(model.modelCode) }
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    model.modelCode,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = LogisticsType.MonoFamily,
                    letterSpacing = 0.5.sp,
                    color = LogisticsTheme.colors.textPrimary
                )
                VSpace(2.dp)
                Text(model.modelName, fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            }
            StatusTag(
                label = model.status,
                color = color,
                containerColor = color.copy(alpha = 0.14f),
                symbol = if (model.shortageMaterialCount > 0) "!" else "✓"
            )
        }
        VSpace(Spacing.md)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
        ) {
            MiniMetric("计划", model.plannedQuantity.toString(), Modifier.weight(1f))
            MiniMetric("齐套率", "${model.completionRate}%", Modifier.weight(1f), valueColor = color)
            MiniMetric(
                "缺料", model.shortageMaterialCount.toString(),
                Modifier.weight(1f),
                valueColor = if (model.shortageMaterialCount > 0) color else LogisticsTheme.colors.textPrimary
            )
            MiniMetric("物料", model.requiredMaterialCount.toString(), Modifier.weight(1f))
        }
    }
}

@Composable
private fun LinkBackButton(onBack: () -> Unit) {
    Text(
        "← 返回订单列表",
        modifier = Modifier.clickable { onBack() }.padding(vertical = Spacing.sm),
        fontSize = 14.sp,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.primary
    )
}

// ==================== 机型详情页（P1 切片 B） ====================

/** 服务端 colorToken → 展示色（红/黄/绿，与既有 MaterialStatusColors 风格一致） */
private fun requirementColor(token: String, statusCode: String): Color = when (token) {
    "status-red" -> com.company.logistics.ui.theme.MaterialStatusColors.Shortage
    "status-yellow" -> com.company.logistics.ui.theme.MaterialStatusColors.Arrived
    "status-green" -> com.company.logistics.ui.theme.MaterialStatusColors.InStock
    else -> when (statusCode) {
        "SHORTAGE" -> com.company.logistics.ui.theme.MaterialStatusColors.Shortage
        "IN_PROCESS" -> com.company.logistics.ui.theme.MaterialStatusColors.Arrived
        else -> com.company.logistics.ui.theme.MaterialStatusColors.InStock
    }
}

private fun requirementContainer(color: Color): Color = color.copy(alpha = 0.14f)

/** 机型状态码 → 展示色（缺料红线 / 其余中性） */
@Composable
private fun modelStatusColor(status: String, shortageCount: Int): Color = when {
    shortageCount > 0 || status == "SHORTAGE" -> com.company.logistics.ui.theme.MaterialStatusColors.Shortage
    status == "READY" || status == "COMPLETED" -> com.company.logistics.ui.theme.MaterialStatusColors.InStock
    else -> LogisticsTheme.colors.info
}

/**
 * 机型详情页 —— 机型摘要 + 物料需求（红黄绿着色）+ 该机型流转记录时间线。
 * 数据由 [com.company.logistics.ui.LogisticsViewModel.loadModelDetail] 预加载。
 */
@Composable
fun ModelDetailScreen(
    model: ProductionOrderModel?,
    detail: ModelDetail?,
    flowRecords: PagedFlowRecords?,
    loading: Boolean,
    error: String?,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        if (loading && detail == null && flowRecords == null) {
            Box(
                modifier = Modifier.fillMaxWidth().padding(vertical = 48.dp),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
            }
            return@Column
        }

        val activeModel = detail?.model ?: model
        if (activeModel != null) {
            val color = modelStatusColor(activeModel.status, activeModel.shortageMaterialCount)
            AppCard(accentColor = color) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            activeModel.modelCode,
                            fontSize = 17.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = LogisticsType.MonoFamily,
                            letterSpacing = 0.5.sp,
                            color = LogisticsTheme.colors.textPrimary
                        )
                        VSpace(2.dp)
                        Text(
                            activeModel.modelName,
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary
                        )
                    }
                    StatusTag(
                        label = activeModel.status,
                        color = color,
                        containerColor = requirementContainer(color),
                        symbol = if (activeModel.shortageMaterialCount > 0) "!" else "✓"
                    )
                }
                VSpace(Spacing.md)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
                ) {
                    MiniMetric("计划", activeModel.plannedQuantity.toString(), Modifier.weight(1f))
                    MiniMetric("完成度", "${activeModel.completionRate}%", Modifier.weight(1f), valueColor = color)
                    MiniMetric(
                        "缺料", activeModel.shortageMaterialCount.toString(),
                        Modifier.weight(1f),
                        valueColor = if (activeModel.shortageMaterialCount > 0) color else LogisticsTheme.colors.textPrimary
                    )
                    MiniMetric("物料", activeModel.requiredMaterialCount.toString(), Modifier.weight(1f))
                }
            }
            VSpace(Spacing.lg)
        }

        if (error != null) {
            EmptyState(
                title = "加载失败",
                description = error,
                action = {
                    PrimaryButton(text = "返回", onClick = onBack, loading = false)
                }
            )
            return@Column
        }

        val requirements = detail?.requirements.orEmpty()
        SectionTitle(
            "物料需求（${requirements.size}）",
            trailing = "需求 / 可用 / 缺口"
        )
        VSpace(Spacing.sm)

        if (requirements.isEmpty()) {
            EmptyState(
                title = "该机型暂无物料需求",
                description = "请确认机型是否正确，或联系仓库管理员维护物料需求"
            )
        } else {
            requirements.forEach { req ->
                ModelRequirementRow(req)
                VSpace(Spacing.sm)
            }
        }

        VSpace(Spacing.lg)
        FlowRecordsSection(
            records = flowRecords,
            loading = loading && detail != null,
            onBack = onBack
        )
        VSpace(Spacing.xl)
    }
}

/** 单条物料需求（红黄绿 label + colorToken 着色，图标/文字/颜色三重表达） */
@Composable
private fun ModelRequirementRow(req: ModelRequirementView) {
    val color = requirementColor(req.colorToken, req.statusCode)

    AppCard(accentColor = color) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                req.materialCode,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                letterSpacing = 0.5.sp,
                color = LogisticsTheme.colors.textPrimary
            )
            Spacer(Modifier.weight(1f))
            StatusTag(
                label = req.label,
                color = color,
                containerColor = requirementContainer(color),
                symbol = when (req.statusCode) {
                    "SHORTAGE" -> "!"
                    "IN_PROCESS" -> "↓"
                    else -> "✓"
                }
            )
        }
        VSpace(4.dp)
        Text(
            buildList {
                add(req.materialName)
                req.specification?.let { add("规格 $it") }
                req.batchNo?.let { add("批次 $it") }
            }.joinToString(" · "),
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textSecondary
        )
        VSpace(Spacing.md)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
        ) {
            MiniMetric("需求", req.requiredQuantity.toString(), Modifier.weight(1f))
            MiniMetric("到料", req.arrivedQuantity.toString(), Modifier.weight(1f))
            MiniMetric("在库", req.inStockQuantity.toString(), Modifier.weight(1f))
            MiniMetric("已发", req.issuedQuantity.toString(), Modifier.weight(1f))
            MiniMetric("可用", req.availableQuantity.toString(), Modifier.weight(1f), valueColor = color)
        }
        if (req.shortageQuantity > 0) {
            VSpace(Spacing.sm)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .background(color, androidx.compose.foundation.shape.CircleShape)
                )
                Spacer(Modifier.width(Spacing.sm))
                Text(
                    "缺口 ${req.shortageQuantity} ${req.unit}",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = color
                )
            }
        }
    }
}

/** 该机型流转记录时间线 */
@Composable
private fun FlowRecordsSection(
    records: PagedFlowRecords?,
    loading: Boolean,
    onBack: () -> Unit
) {
    SectionTitle("流转记录", trailing = records?.let { "共 ${it.total} 条" })
    VSpace(Spacing.sm)

    when {
        loading -> Box(
            modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
            contentAlignment = Alignment.Center
        ) {
            CircularProgressIndicator(
                modifier = Modifier.size(24.dp),
                color = MaterialTheme.colorScheme.primary
            )
        }
        records == null || records.items.isEmpty() -> {
            EmptyState(
                title = "暂无流转记录",
                description = "该机型尚无厂内流转单据；入库 / 领料等申请会在此展示",
                action = { PrimaryButton(text = "返回", onClick = onBack, loading = false) }
            )
        }
        else -> {
            records.items.forEach { record ->
                FlowRecordRow(record)
                VSpace(Spacing.sm)
            }
        }
    }
}

/** 单条流转记录（时间线条目） */
@Composable
private fun FlowRecordRow(record: FlowRecordView) {
    val statusLabel = when (record.status) {
        "PENDING_APPROVAL" -> "待审批"
        "APPROVED" -> "已批准"
        "EXECUTED" -> "已执行"
        "REJECTED" -> "已驳回"
        else -> record.status
    }
    val color = when (record.status) {
        "PENDING_APPROVAL" -> LogisticsTheme.colors.warning
        "APPROVED" -> LogisticsTheme.colors.info
        "EXECUTED" -> LogisticsTheme.colors.success
        "REJECTED" -> com.company.logistics.ui.theme.MaterialStatusColors.Shortage
        else -> LogisticsTheme.colors.textSecondary
    }

    AppCard(accentColor = color) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                record.flowNo,
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                letterSpacing = 0.3.sp,
                color = LogisticsTheme.colors.textPrimary
            )
            Spacer(Modifier.weight(1f))
            StatusTag(
                label = statusLabel,
                color = color,
                containerColor = requirementContainer(color)
            )
        }
        VSpace(4.dp)
        Text(
            "${record.type} · 数量 ${record.quantityTotal} · ${record.createdBy}",
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textSecondary
        )
        VSpace(Spacing.xs)
        Text(
            "创建 ${record.createdAt}" +
                (record.approvedBy?.let { " · 审批 $it@${record.approvedAt ?: "-"}" } ?: "") +
                (record.executedAt?.let { " · 执行 ${it}" } ?: ""),
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary
        )
        if (record.materialCodes.isNotEmpty()) {
            VSpace(Spacing.xs)
            Text(
                record.materialCodes.joinToString("、"),
                fontSize = 12.sp,
                fontFamily = LogisticsType.MonoFamily,
                color = LogisticsTheme.colors.textSecondary
            )
        }
    }
}

