package com.company.logistics.ui.screens

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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import com.company.logistics.model.MaterialStatusCode
import com.company.logistics.model.OrderMaterialItem
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 生产订单物料状态页 —— 契约 4.3。
 *
 * 契约要求：状态颜色固定（红缺货 / 黄到货 / 绿在库），
 * 且接口必须返回 statusCode、label、colorToken，客户端还必须展示图标和文字。
 * 因此本页状态标签一律使用 [StatusTag]，同时给出符号与文字。
 *
 * 业务层级（业务模型更正）：生产订单 → 机型 → 物料需求 → 厂内流转记录。
 * V1 接口返回订单级物料清单，机型维度待后端接口就绪后展开。
 */
@Composable
fun OrderDetailScreen(
    status: OrderMaterialStatus?,
    detail: com.company.logistics.model.OrderDetail? = null,
    loading: Boolean,
    onRefresh: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    var selectedDeviceId by remember(status) { mutableStateOf<String?>(null) }
    val selectedItems = status?.items?.filter { it.deviceId == selectedDeviceId }.orEmpty()
    if (status != null && selectedDeviceId != null && selectedItems.isNotEmpty()) {
        DeviceMaterialDetail(
            deviceType = selectedItems.first().deviceType.orEmpty(),
            deviceNo = selectedItems.first().deviceNo.orEmpty(),
            items = selectedItems,
            onBack = { selectedDeviceId = null },
            modifier = modifier,
        )
        return
    }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        if (status == null) {
            EmptyState(
                title = "尚未查询订单",
                description = "扫描生产订单号后，在此展示订单下各物料的需求、到料与在库情况"
            )
            return@Column
        }

        // 订单头卡
        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Text(
                "生产订单号",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
            VSpace(4.dp)
            Text(
                status.documentNo,
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                letterSpacing = 0.5.sp,
                color = LogisticsTheme.colors.textPrimary
            )
            Text(
                "订单状态：${status.orderStatus ?: "服务端未提供"}",
                fontSize = 13.sp,
                color = LogisticsTheme.colors.textSecondary,
            )
            status.productName?.takeIf { it.isNotBlank() }?.let {
                Text("产品：$it", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            }
            Text("服务端时间：${status.serverTime ?: "未提供"}", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
            androidx.compose.material3.TextButton(onClick = onRefresh, enabled = !loading) { Text("刷新订单事实") }
            VSpace(Spacing.md)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "齐套率",
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary
                )
                Spacer(Modifier.width(Spacing.sm))
                Text(
                    "${(status.fulfillmentRate * 100).toInt()}%",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = LogisticsType.MonoFamily,
                    color = MaterialTheme.colorScheme.primary
                )
                Spacer(Modifier.weight(1f))
                if (status.shortageCount > 0) {
                    StatusTag(
                        label = "缺料 ${status.shortageCount} 项",
                        color = MaterialStatusCode.OUT_OF_STOCK.color,
                        containerColor = MaterialStatusCode.OUT_OF_STOCK.containerColor,
                        symbol = "!"
                    )
                } else {
                    StatusTag(
                        label = "物料齐套",
                        color = MaterialStatusCode.IN_STOCK.color,
                        containerColor = MaterialStatusCode.IN_STOCK.containerColor,
                        symbol = "✓"
                    )
                }
            }
            VSpace(Spacing.sm)
            LinearProgressIndicator(
                progress = { status.fulfillmentRate },
                modifier = Modifier.fillMaxWidth().height(6.dp),
                color = if (status.shortageCount > 0) MaterialStatusCode.ARRIVED.color
                else MaterialStatusCode.IN_STOCK.color,
                trackColor = LogisticsTheme.colors.border
            )
            if (status.serverTime != null) {
                VSpace(Spacing.sm)
                Text(
                    "服务端时间：${status.serverTime}",
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary
                )
            }
        }

        detail?.let { aggregate ->
            VSpace(Spacing.md)
            SectionTitle("装配任务（${aggregate.assemblyTasks.size}/${aggregate.total}）")
            aggregate.assemblyTasks.forEach { task ->
                AppCard {
                    Text("${task.deviceNo} · ${task.status.label}", fontWeight = FontWeight.Bold)
                    Text("进度阶段：${task.progressStage}  版本：${task.taskVersion}", fontSize = 12.sp)
                    Text("责任人：${task.assignedAssemblerId ?: "服务端未提供"}", fontSize = 12.sp)
                }
                VSpace(Spacing.sm)
            }
            SectionTitle("工时（分钟）")
            AppCard {
                Text("装配：${aggregate.laborSummary.assemblyLaborMinutes}  临时调拨：${aggregate.laborSummary.temporaryTransferLaborMinutes}")
                Text("总工时：${aggregate.laborSummary.totalLaborMinutes}", fontWeight = FontWeight.Bold)
            }
            VSpace(Spacing.md)
            SectionTitle("流转时间线（${aggregate.timeline.size}）")
            aggregate.timeline.forEach { event ->
                Text("${event.serverTime ?: "服务端未提供"} · ${event.type} · ${event.status ?: "服务端未提供"} · ${event.actorId ?: "服务端未提供"}", fontSize = 12.sp)
            }
        }

        VSpace(Spacing.lg)

        SectionTitle("物料需求（${status.items.size}）", trailing = "需求 / 到料 / 在库")

        VSpace(Spacing.sm)

        if (status.items.isEmpty()) {
            EmptyState(
                title = "该订单暂无物料需求",
                description = "请确认订单号是否正确，或联系仓库管理员维护物料需求"
            )
        } else {
            status.items.groupBy { it.deviceId ?: "__ungrouped__" }.values.forEach { deviceItems ->
                val first = deviceItems.first()
                if (first.deviceId != null) {
                    DeviceCard(
                        deviceType = first.deviceType.orEmpty(),
                        deviceNo = first.deviceNo.orEmpty(),
                        items = deviceItems,
                        onClick = { selectedDeviceId = first.deviceId },
                    )
                } else {
                    deviceItems.forEach { item -> OrderMaterialRow(item) }
                }
                VSpace(Spacing.sm)
            }
        }

        VSpace(Spacing.xxl)
    }
}

private fun deviceDisplayName(type: String): String = when (type) {
    "HORIZONTAL_CONVEYOR", "横向输送机" -> "横向输送机"
    "CROSS_CONVEYOR", "十字输送机" -> "十字输送机"
    "BUFFER", "缓存" -> "缓存"
    "SHEET_ASSEMBLER", "合片机" -> "合片机"
    else -> type.ifBlank { "未命名机台" }
}

@Composable
private fun DeviceCard(
    deviceType: String,
    deviceNo: String,
    items: List<OrderMaterialItem>,
    onClick: () -> Unit,
) {
    AppCard(modifier = Modifier.clickable(onClick = onClick)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(deviceDisplayName(deviceType), fontSize = 15.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(4.dp)
                Text(deviceNo, fontSize = 13.sp, fontFamily = LogisticsType.MonoFamily, color = LogisticsTheme.colors.textSecondary)
            }
            Text("物料 ${items.size} 项  ›", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
        }
        VSpace(Spacing.sm)
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.sm)) {
            DeviceStatusSummary(items)
        }
        VSpace(4.dp)
        Text("点击查看机台物料详情", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
    }
}

@Composable
private fun DeviceStatusSummary(items: List<OrderMaterialItem>) {
    val counts = items.groupingBy { it.statusCode }.eachCount()
    MaterialStatusCode.entries.forEach { status ->
        val count = counts[status] ?: 0
        if (count > 0) {
            StatusTag(
                label = "${status.label} $count",
                color = status.color,
                containerColor = status.containerColor,
                symbol = status.symbol,
            )
        }
    }
}

@Composable
private fun DeviceMaterialDetail(
    deviceType: String,
    deviceNo: String,
    items: List<OrderMaterialItem>,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = Dimens.PagePadding)) {
        Spacer(Modifier.height(Spacing.sm))
        Text("‹ 返回订单", modifier = Modifier.clickable(onClick = onBack), color = MaterialTheme.colorScheme.primary, fontSize = 14.sp)
        VSpace(Spacing.md)
        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Text(deviceDisplayName(deviceType), fontSize = 20.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
            VSpace(4.dp)
            Text(deviceNo, fontSize = 15.sp, fontFamily = LogisticsType.MonoFamily, color = LogisticsTheme.colors.textSecondary)
            VSpace(Spacing.sm)
            Text("物料详情（${items.size}）", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
        }
        VSpace(Spacing.md)
        items.forEach {
            OrderMaterialRow(it)
            VSpace(Spacing.sm)
        }
        VSpace(Spacing.xxl)
    }
}

/**
 * 单条物料需求。
 * 三色状态由服务端下发，客户端只做展示映射，不自行推导。
 */
@Composable
private fun OrderMaterialRow(item: OrderMaterialItem) {
    val statusColor = item.statusCode.color

    AppCard(
        accentColor = statusColor,
        borderColor = if (item.statusCode == MaterialStatusCode.OUT_OF_STOCK) {
            statusColor.copy(alpha = 0.4f)
        } else null
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                item.materialCode,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                letterSpacing = 0.5.sp,
                color = LogisticsTheme.colors.textPrimary
            )
            Spacer(Modifier.weight(1f))
            StatusTag(
                label = item.effectiveStatusLabel,
                color = statusColor,
                containerColor = item.statusCode.containerColor,
                symbol = item.statusCode.symbol
            )
        }

        VSpace(4.dp)
        Text(
            item.name,
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textSecondary
        )

        item.specification?.let {
            VSpace(4.dp)
            Text(
                "规格：$it",
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textTertiary
            )
        }

        VSpace(Spacing.md)

        // 三列数据：需求 / 到料 / 在库
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm)
        ) {
            MiniMetric("需求", item.requiredQuantity, Modifier.weight(1f))
            MiniMetric("到料", item.arrivedQuantity, Modifier.weight(1f))
            MiniMetric(
                "在库",
                item.inStockQuantity,
                Modifier.weight(1f),
                valueColor = statusColor
            )
        }

        if (item.shortageQuantity > 0) {
            VSpace(Spacing.sm)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(6.dp)
                        .background(statusColor, CircleShape)
                )
                Spacer(Modifier.width(Spacing.sm))
                Text(
                    "缺口 ${item.shortageQuantity}",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = statusColor
                )
            }
        }
    }
}

@Composable
private fun MiniMetric(
    label: String,
    value: Int,
    modifier: Modifier = Modifier,
    valueColor: Color = LogisticsTheme.colors.textPrimary
) {
    Column(modifier = modifier) {
        Text(label, fontSize = 10.sp, color = LogisticsTheme.colors.textTertiary)
        VSpace(3.dp)
        Text(
            "$value",
            fontSize = 17.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = LogisticsType.MonoFamily,
            color = valueColor
        )
    }
}
