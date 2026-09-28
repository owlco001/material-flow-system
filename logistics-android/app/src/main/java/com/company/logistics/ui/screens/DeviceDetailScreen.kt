package com.company.logistics.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.Model3dActivity
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.DeviceDetail
import com.company.logistics.model.DeviceModelMap
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.OrderMaterialItem
import com.company.logistics.model.TransferRequest
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 机台详情页。
 *
 * 视觉：渐变横幅头图（机台编号 + 名称 + 状态徽章 + 3D 入口）+ 分区卡片：
 * 基本信息 / 物料情况 / 物料流转申请 / 装配进度 / 工时 / 人员 / 关联订单。
 */
@Composable
fun DeviceDetailScreen(
    detail: DeviceDetail?,
    loading: Boolean,
    error: String?,
    assemblyTasks: List<AssemblyTask>,
    materials: List<OrderMaterialItem>,
    transferRequests: List<TransferRequest>,
    labor: LaborSummaryPage?,
    onBack: () -> Unit,
    onSubmitMaterialRequest: (material: OrderMaterialItem, orderNo: String, quantity: Int, remark: String) -> Unit = { _, _, _, _ -> },
) {
    val context = LocalContext.current
    var showMaterialRequestDialog by remember { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        Spacer(Modifier.height(Spacing.sm))
        when {
            loading -> {
                VSpace(Spacing.xl)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(modifier = Modifier.width(22.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(Spacing.sm))
                    Text("正在加载机台详情…", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                }
            }
            error != null -> EmptyState(
                title = "机台详情加载失败",
                description = error,
            )
            detail == null -> EmptyState(
                title = "暂无数据",
                description = "请返回后重新选择机台",
            )
            else -> {
                // ---- 渐变横幅头图 + 3D 入口 ----
                // 优先使用后端绑定的 3D 模型，未绑定时按 App 内置映射表兜底
                val modelCode = remember(detail.deviceNo, detail.model3dCode) {
                    detail.model3dCode?.takeIf { it.isNotBlank() }
                        ?: DeviceModelMap.modelCodeFor("", detail.deviceNo)
                }
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
                                        detail.deviceNo,
                                        fontSize = 22.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = LogisticsType.MonoFamily,
                                        color = Color.White,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                    VSpace(4.dp)
                                    Text(
                                        detail.deviceName,
                                        fontSize = 14.sp,
                                        color = Color.White.copy(alpha = 0.85f),
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                                TextButton(onClick = onBack) {
                                    Text("返回", fontSize = 14.sp, color = Color.White)
                                }
                            }
                            VSpace(Spacing.sm)
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Surface(
                                    shape = RoundedCornerShape(10.dp),
                                    color = Color.White,
                                ) {
                                    Text(
                                        detail.status,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = LogisticsColors.BrandNavyDark,
                                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                                    )
                                }
                                Spacer(Modifier.width(Spacing.sm))
                                if (modelCode != null) {
                                    TextButton(
                                        onClick = {
                                            context.startActivity(
                                                Model3dActivity.intent(context, modelCode, "机台 ${detail.deviceNo}", detail.deviceNo)
                                            )
                                        }
                                    ) {
                                        Text("3D 模型 ›", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Color.White)
                                    }
                                } else {
                                    Text(
                                        "暂无 3D 模型",
                                        fontSize = 12.sp,
                                        color = Color.White.copy(alpha = 0.6f),
                                    )
                                }
                            }
                        }
                    }
                }

                VSpace(Spacing.md)

                // ---- 基本信息 ----
                AppCard {
                    SectionBarTitle("基本信息")
                    VSpace(Spacing.sm)
                    DeviceKv("编号", detail.deviceNo, mono = true)
                    DeviceKv("名称", detail.deviceName)
                    detail.workshop?.let { DeviceKv("车间", it) }
                    detail.modelCapability?.let { DeviceKv("机型", it) }
                }

                VSpace(Spacing.md)

                // ---- 物料情况 ----
                AppCard {
                    SectionBarTitle("物料情况（${materials.size}）")
                    VSpace(Spacing.sm)
                    if (materials.isEmpty()) {
                        Text(
                            "该机台暂无关联物料",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        materials.forEachIndexed { index, m ->
                            if (index > 0) VSpace(Spacing.sm)
                            Column(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(LogisticsTheme.colors.cardBackground)
                                    .padding(12.dp),
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            m.name,
                                            fontSize = 14.sp,
                                            fontWeight = FontWeight.Bold,
                                            color = LogisticsTheme.colors.textPrimary,
                                            maxLines = 1,
                                            overflow = TextOverflow.Ellipsis,
                                        )
                                        Text(
                                            m.materialCode,
                                            fontSize = 12.sp,
                                            fontFamily = LogisticsType.MonoFamily,
                                            color = LogisticsTheme.colors.textSecondary,
                                        )
                                    }
                                    Surface(
                                        shape = RoundedCornerShape(8.dp),
                                        color = LogisticsColors.Primary.copy(alpha = 0.12f),
                                    ) {
                                        Text(
                                            m.label,
                                            fontSize = 11.sp,
                                            fontWeight = FontWeight.Bold,
                                            color = LogisticsColors.PrimaryDark,
                                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                        )
                                    }
                                }
                                m.specification?.takeIf { it.isNotBlank() }?.let {
                                    VSpace(4.dp)
                                    Text("规格：$it", fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
                                }
                                VSpace(4.dp)
                                Text(
                                    "需求 ${m.requiredQuantity} · 已到 ${m.arrivedQuantity} · 在库 ${m.inStockQuantity}",
                                    fontSize = 12.sp,
                                    color = LogisticsTheme.colors.textSecondary,
                                )
                            }
                        }
                    }
                }

                VSpace(Spacing.md)

                // ---- 物料流转申请 ----
                AppCard {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Box(Modifier.weight(1f)) {
                            SectionBarTitle("物料流转申请（${transferRequests.size}）")
                        }
                        TextButton(
                            onClick = { showMaterialRequestDialog = true },
                            enabled = materials.isNotEmpty(),
                        ) {
                            Text("申请物料", fontSize = 13.sp, color = LogisticsColors.PrimaryDark)
                        }
                    }
                    VSpace(Spacing.sm)
                    if (transferRequests.isEmpty()) {
                        Text(
                            "该机台关联订单暂无流转申请",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        transferRequests.forEachIndexed { index, t ->
                            if (index > 0) VSpace(Spacing.sm)
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(LogisticsTheme.colors.cardBackground)
                                    .padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        t.documentNo ?: t.id,
                                        fontSize = 14.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = LogisticsType.MonoFamily,
                                        color = LogisticsTheme.colors.textPrimary,
                                    )
                                    VSpace(2.dp)
                                    Text(
                                        "类型 ${t.type}" + (t.createdAt?.let { " · $it" } ?: ""),
                                        fontSize = 12.sp,
                                        color = LogisticsTheme.colors.textSecondary,
                                    )
                                }
                                Surface(
                                    shape = RoundedCornerShape(8.dp),
                                    color = LogisticsColors.Primary.copy(alpha = 0.12f),
                                ) {
                                    Text(
                                        t.displayStatusLabel,
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = LogisticsColors.PrimaryDark,
                                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                    )
                                }
                            }
                        }
                    }
                }

                VSpace(Spacing.md)

                // ---- 装配进度 ----
                AppCard {
                    SectionBarTitle("装配进度（${assemblyTasks.size}）")
                    VSpace(Spacing.sm)
                    if (assemblyTasks.isEmpty()) {
                        Text(
                            "该机台暂无装配任务",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        assemblyTasks.forEachIndexed { index, task ->
                            if (index > 0) VSpace(Spacing.sm)
                            Column(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(LogisticsTheme.colors.cardBackground)
                                    .padding(12.dp),
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            "订单 ${task.orderNo.ifBlank { "—" }}",
                                            fontSize = 14.sp,
                                            fontWeight = FontWeight.Bold,
                                            fontFamily = LogisticsType.MonoFamily,
                                            color = LogisticsTheme.colors.textPrimary,
                                        )
                                        Text(
                                            task.status.label,
                                            fontSize = 12.sp,
                                            color = LogisticsTheme.colors.textSecondary,
                                        )
                                    }
                                    Text(
                                        "阶段 ${task.progressStage}/3",
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = LogisticsColors.PrimaryDark,
                                    )
                                }
                                VSpace(Spacing.sm)
                                LinearProgressIndicator(
                                    progress = { (task.progressStage.coerceIn(0, 3) / 3f) },
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(6.dp)
                                        .clip(RoundedCornerShape(3.dp)),
                                )
                                if (task.stages.isNotEmpty()) {
                                    VSpace(Spacing.sm)
                                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                        task.stages.forEach { stage ->
                                            Surface(
                                                shape = RoundedCornerShape(8.dp),
                                                color = LogisticsTheme.colors.primaryContainer,
                                            ) {
                                                Text(
                                                    "S${stage.stageNo} ${stage.status.label}",
                                                    fontSize = 11.sp,
                                                    color = LogisticsColors.PrimaryDark,
                                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                VSpace(Spacing.md)

                // ---- 工时 ----
                val laborItems = labor?.items.orEmpty()
                val totalMinutes = laborItems.sumOf { it.totalLaborMinutes ?: 0 }
                val assemblyMinutes = laborItems.sumOf { it.assemblyLaborMinutes ?: 0 }
                val transferMinutes = laborItems.sumOf { it.temporaryTransferLaborMinutes ?: 0 }
                AppCard {
                    SectionBarTitle("工时")
                    VSpace(Spacing.sm)
                    if (labor == null || laborItems.isEmpty()) {
                        Text(
                            "该机台暂无工时记录",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        DeviceKv("总工时", formatMinutes(totalMinutes))
                        DeviceKv("装配工时", formatMinutes(assemblyMinutes))
                        DeviceKv("调拨工时", formatMinutes(transferMinutes))
                    }
                }

                VSpace(Spacing.md)

                // ---- 人员 ----
                val assemblerNames = remember(assemblyTasks) {
                    assemblyTasks.flatMap { task ->
                        listOfNotNull(task.assignedAssemblerName?.takeIf { it.isNotBlank() }) +
                            task.members.mapNotNull { it.assemblerId.takeIf { id -> id.isNotBlank() } }
                    }.distinct()
                }
                AppCard {
                    SectionBarTitle("人员（${assemblerNames.size}）")
                    VSpace(Spacing.sm)
                    if (assemblerNames.isEmpty()) {
                        Text(
                            "该机台暂无指派人员",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        assemblerNames.forEachIndexed { index, name ->
                            if (index > 0) VSpace(4.dp)
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(
                                    modifier = Modifier
                                        .size(26.dp)
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(LogisticsTheme.colors.primaryContainer),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    Text(
                                        name.first().toString(),
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Bold,
                                        color = LogisticsColors.PrimaryDark,
                                    )
                                }
                                Spacer(Modifier.width(10.dp))
                                Text(
                                    name,
                                    fontSize = 14.sp,
                                    color = LogisticsTheme.colors.textPrimary,
                                )
                            }
                        }
                    }
                }

                VSpace(Spacing.md)

                // ---- 关联订单 ----
                AppCard {
                    SectionBarTitle("关联订单（${detail.orders.size}）")
                    VSpace(Spacing.sm)
                    if (detail.orders.isEmpty()) {
                        Text(
                            "未绑定任何生产订单",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        detail.orders.forEachIndexed { index, o ->
                            if (index > 0) VSpace(Spacing.sm)
                            Surface(
                                shape = RoundedCornerShape(12.dp),
                                color = LogisticsTheme.colors.cardBackground,
                                border = androidx.compose.foundation.BorderStroke(
                                    1.dp,
                                    LogisticsTheme.colors.border,
                                ),
                            ) {
                                Row(
                                    modifier = Modifier.padding(12.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Box(
                                        modifier = Modifier
                                            .size(26.dp)
                                            .clip(RoundedCornerShape(8.dp))
                                            .background(LogisticsTheme.colors.primaryContainer),
                                        contentAlignment = Alignment.Center,
                                    ) {
                                        Text(
                                            "${index + 1}",
                                            fontSize = 12.sp,
                                            fontWeight = FontWeight.Bold,
                                            color = LogisticsColors.PrimaryDark,
                                        )
                                    }
                                    Spacer(Modifier.width(10.dp))
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            o.orderNo,
                                            fontSize = 14.sp,
                                            fontWeight = FontWeight.Bold,
                                            fontFamily = LogisticsType.MonoFamily,
                                            color = LogisticsTheme.colors.textPrimary,
                                        )
                                        o.productName?.let {
                                            Text(
                                                "产品：$it",
                                                fontSize = 12.sp,
                                                color = LogisticsTheme.colors.textSecondary,
                                            )
                                        }
                                    }
                                    o.assignStatus?.let {
                                        Surface(
                                            shape = RoundedCornerShape(8.dp),
                                            color = LogisticsColors.Primary.copy(alpha = 0.12f),
                                        ) {
                                            Text(
                                                it,
                                                fontSize = 11.sp,
                                                fontWeight = FontWeight.Bold,
                                                color = LogisticsColors.PrimaryDark,
                                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        VSpace(Spacing.xxl)
    }

    // ---- 申请物料对话框 ----
    if (showMaterialRequestDialog && detail != null) {
        DeviceMaterialRequestDialog(
            materials = materials,
            orders = detail.orders,
            onDismiss = { showMaterialRequestDialog = false },
            onConfirm = { material, orderNo, qty, remark ->
                onSubmitMaterialRequest(material, orderNo, qty, remark)
                showMaterialRequestDialog = false
            },
        )
    }
}

private fun formatMinutes(minutes: Int): String {
    if (minutes <= 0) return "0 分钟"
    val h = minutes / 60
    val m = minutes % 60
    return if (h > 0) "${h}小时${m}分钟" else "${m}分钟"
}

@Composable
private fun SectionBarTitle(title: String) {
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
}

@Composable
private fun DeviceKv(label: String, value: String, mono: Boolean = false) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Text(
                label,
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textTertiary,
                modifier = Modifier.width(56.dp),
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
        androidx.compose.material3.HorizontalDivider(
            color = LogisticsTheme.colors.border.copy(alpha = 0.6f),
            thickness = 0.5.dp,
        )
    }
}

/**
 * 机台详情申请物料对话框。
 * 从本机物料清单下拉选物料；关联订单下拉选（订单号写入 documentNo，便于详情页筛出）；
 * 填写数量与备注。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeviceMaterialRequestDialog(
    materials: List<OrderMaterialItem>,
    orders: List<com.company.logistics.model.DeviceOrder>,
    onDismiss: () -> Unit,
    onConfirm: (material: OrderMaterialItem, orderNo: String, quantity: Int, remark: String) -> Unit,
) {
    var selectedMaterial by remember(materials) { mutableStateOf(materials.firstOrNull()) }
    var materialExpanded by remember { mutableStateOf(false) }
    var selectedOrder by remember(orders) { mutableStateOf(orders.singleOrNull()?.orderNo ?: "") }
    var orderExpanded by remember { mutableStateOf(false) }
    var qtyText by remember { mutableStateOf("") }
    var remark by remember { mutableStateOf("") }
    val canConfirm = selectedMaterial != null && selectedOrder.isNotBlank() && (qtyText.toIntOrNull() ?: 0) > 0

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("申请物料", fontSize = 16.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth()) {
                ExposedDropdownMenuBox(expanded = materialExpanded, onExpandedChange = { materialExpanded = it }) {
                    OutlinedTextField(
                        value = selectedMaterial?.let { "${it.name}（${it.materialCode}）" } ?: "",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("物料") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(materialExpanded) },
                        modifier = Modifier.menuAnchor().fillMaxWidth(),
                    )
                    ExposedDropdownMenu(expanded = materialExpanded, onDismissRequest = { materialExpanded = false }) {
                        materials.forEach { m ->
                            DropdownMenuItem(
                                text = { Text("${m.name}（${m.materialCode}）", fontSize = 13.sp) },
                                onClick = { selectedMaterial = m; materialExpanded = false },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                ExposedDropdownMenuBox(expanded = orderExpanded, onExpandedChange = { orderExpanded = it }) {
                    OutlinedTextField(
                        value = selectedOrder,
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("关联订单") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(orderExpanded) },
                        modifier = Modifier.menuAnchor().fillMaxWidth(),
                    )
                    ExposedDropdownMenu(expanded = orderExpanded, onDismissRequest = { orderExpanded = false }) {
                        orders.forEach { o ->
                            DropdownMenuItem(
                                text = { Text(o.orderNo + (o.productName?.let { " · $it" } ?: ""), fontSize = 13.sp) },
                                onClick = { selectedOrder = o.orderNo; orderExpanded = false },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = qtyText,
                    onValueChange = { qtyText = it.filter { c -> c.isDigit() } },
                    label = { Text("申请数量") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = remark,
                    onValueChange = { remark = it },
                    label = { Text("备注（可选）") },
                    minLines = 2,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val m = selectedMaterial
                    if (m != null) onConfirm(m, selectedOrder, qtyText.toIntOrNull() ?: 0, remark)
                },
                enabled = canConfirm,
            ) {
                Text("提交申请")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
