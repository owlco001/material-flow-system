package com.company.logistics.ui.screens

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.foundation.border
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.DialogProperties
import com.company.logistics.Model3dActivity
import com.company.logistics.model.AssemblyTask
import com.company.logistics.model.DeviceDetail
import com.company.logistics.model.DeviceModelMap
import com.company.logistics.model.EXCEPTION_TYPE_OPTIONS
import com.company.logistics.model.LaborSummaryPage
import com.company.logistics.model.OrderMaterialItem
import com.company.logistics.model.TransferRequest
import com.company.logistics.model.UserRole
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.components.LogisticsIcons
import com.company.logistics.ui.components.tagTextColor
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
    onSubmitMaterialRequestBatch: (items: List<OrderMaterialItem>, orderNo: String, remark: String) -> Unit = { _, _, _ -> },
    onSubmitException: (deviceId: String, materialId: String, materialCode: String, orderNo: String, type: String, bookQuantity: Int, actualQuantity: Int, description: String) -> Unit = { _, _, _, _, _, _, _, _ -> },
    role: UserRole = UserRole.OPERATOR,
    drawings: @Composable (deviceId: String) -> Unit = {},
) {
    val context = LocalContext.current
    var showMaterialRequestDialog by remember { mutableStateOf(false) }
    var exceptionMaterial by remember { mutableStateOf<OrderMaterialItem?>(null) }
    // 勾选物料批量流转申请（materialId 集合）；detail 重新加载时保留已勾选项
    var checkedMaterialIds by remember { mutableStateOf(setOf<String>()) }
    var showBatchTransferDialog by remember { mutableStateOf(false) }
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
                                        deviceStatusLabel(detail.status),
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

                // ---- 基本信息（压缩：编号/名称已在头部横幅展示，仅保留车间/机型两列）----
                if (detail.workshop != null || detail.modelCapability != null) {
                    AppCard {
                        Row {
                            detail.workshop?.let { DeviceKv("车间", it, modifier = Modifier.weight(1f)) }
                            detail.modelCapability?.let { DeviceKv("机型", it, modifier = Modifier.weight(1f)) }
                        }
                    }
                }

                VSpace(Spacing.md)

                // ---- 机台图纸 ----
                drawings(detail.deviceId)

                VSpace(Spacing.md)

                // ---- 物料情况：搜索 + 类型标签汇聚 + 紧凑列表 + 勾选批量流转 ----
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
                        var query by remember(materials) { mutableStateOf("") }
                        var selectedType by remember(materials) { mutableStateOf<String?>(null) }
                        // 智能分页：每页 20 条，搜索/分类变化时回到第 1 页
                        var materialPage by remember(materials) { mutableStateOf(1) }
                        LaunchedEffect(query, selectedType) { materialPage = 1 }
                        val q = query.trim()
                        // 后端分类四类汇聚：电气 / 机械 / 其他 / 未分类（空或未知归未分类），固定顺序
                        val categoryCounts = remember(materials) {
                            linkedMapOf("电气" to 0, "机械" to 0, "其他" to 0, "未分类" to 0).also { counts ->
                                materials.forEach { m -> counts[materialCategoryKey(m)] = (counts[materialCategoryKey(m)] ?: 0) + 1 }
                            }
                        }
                        val filtered = materials.filter { m ->
                            (selectedType == null || materialCategoryKey(m) == selectedType) &&
                                (q.isBlank() || m.materialCode.contains(q, true) ||
                                    m.name.contains(q, true) ||
                                    m.specification?.contains(q, true) == true)
                        }
                        val pageSize = 20
                        val pageCount = if (filtered.isEmpty()) 1 else (filtered.size + pageSize - 1) / pageSize
                        val safePage = materialPage.coerceIn(1, pageCount)
                        val paged = filtered.drop((safePage - 1) * pageSize).take(pageSize)
                        OutlinedTextField(
                            value = query,
                            onValueChange = { query = it },
                            placeholder = { Text("搜索编码 / 名称 / 规格", fontSize = 13.sp) },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        VSpace(Spacing.sm)
                        // 分类标签：点击筛选该类，再点取消；数量为 0 的类别不显示
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            MaterialTypeChip(
                                text = "全部 ${materials.size}",
                                selected = selectedType == null,
                                onClick = { selectedType = null },
                            )
                            categoryCounts.forEach { (cat, count) ->
                                if (count > 0) {
                                    MaterialTypeChip(
                                        text = "$cat $count",
                                        selected = selectedType == cat,
                                        onClick = { selectedType = if (selectedType == cat) null else cat },
                                    )
                                }
                            }
                        }
                        VSpace(Spacing.sm)
                        // 批量操作栏前置：无需滑到列表底部即可全选 / 提交
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            val allChecked = filtered.isNotEmpty() &&
                                checkedMaterialIds.containsAll(filtered.map { it.materialId })
                            TextButton(
                                onClick = {
                                    checkedMaterialIds =
                                        if (allChecked) emptySet()
                                        else filtered.map { it.materialId }.toSet()
                                },
                                enabled = filtered.isNotEmpty(),
                            ) {
                                Text(if (allChecked) "取消全选" else "全选", fontSize = 12.sp)
                            }
                            Text(
                                "已选 ${checkedMaterialIds.size} 项",
                                modifier = Modifier.weight(1f),
                                fontSize = 12.sp,
                                color = LogisticsTheme.colors.textSecondary,
                            )
                            TextButton(
                                onClick = { showBatchTransferDialog = true },
                                enabled = checkedMaterialIds.isNotEmpty(),
                            ) {
                                Text(
                                    "提交流转申请",
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = LogisticsColors.PrimaryDark,
                                )
                            }
                        }
                        if (filtered.isEmpty()) {
                            Text(
                                "没有匹配的物料",
                                fontSize = 13.sp,
                                color = LogisticsTheme.colors.textSecondary,
                                modifier = Modifier.padding(vertical = 12.dp),
                            )
                        } else {
                            // 表格模式：表头固定不随内容横滑，列宽两侧一致；操作/勾选列按角色裁剪
                            val canOperate = role != UserRole.ASSEMBLER
                            MaterialTableHeader(canOperate = canOperate)
                            Text(
                                "← 左右滑动查看在库状态与操作 →",
                                fontSize = 10.sp,
                                color = LogisticsTheme.colors.textTertiary,
                                modifier = Modifier.padding(vertical = 2.dp),
                            )
                            // 固定高度容器内置纵向滚动：页面下滑时表头仍常驻（只有内容行滚动）
                            val tableVScroll = rememberScrollState()
                            LaunchedEffect(safePage) { tableVScroll.scrollTo(0) }
                            Box(
                                Modifier
                                    .fillMaxWidth()
                                    .height(420.dp)
                                    .verticalScroll(tableVScroll)
                                    .horizontalScroll(rememberScrollState())
                            ) {
                                Column(Modifier.width(materialTableWidth(canOperate).dp)) {
                                    paged.forEach { m ->
                                        androidx.compose.material3.HorizontalDivider(
                                            color = LogisticsTheme.colors.border.copy(alpha = 0.4f),
                                            thickness = 0.5.dp,
                                        )
                                        MaterialTableRow(
                                            item = m,
                                            checked = m.materialId in checkedMaterialIds,
                                            canOperate = canOperate,
                                            onToggle = {
                                                checkedMaterialIds =
                                                    if (m.materialId in checkedMaterialIds) checkedMaterialIds - m.materialId
                                                    else checkedMaterialIds + m.materialId
                                            },
                                            onException = { exceptionMaterial = m },
                                        )
                                    }
                                }
                            }
                            VSpace(Spacing.sm)
                            // 分页控件：避免长列表长距离滚动
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                TextButton(
                                    onClick = { materialPage = safePage - 1 },
                                    enabled = safePage > 1,
                                ) { Text("上一页", fontSize = 12.sp) }
                                Text(
                                    "第 $safePage / $pageCount 页 · 共 ${filtered.size} 项",
                                    fontSize = 12.sp,
                                    color = LogisticsTheme.colors.textSecondary,
                                    textAlign = TextAlign.Center,
                                    modifier = Modifier.weight(1f),
                                )
                                TextButton(
                                    onClick = { materialPage = safePage + 1 },
                                    enabled = safePage < pageCount,
                                ) { Text("下一页", fontSize = 12.sp) }
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
                                        listOfNotNull(
                                            transferTypeLabel(t.type),
                                            formatTransferTime(t.createdAt).ifBlank { null },
                                        ).joinToString(" · "),
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
                // 按账号 ID 去重（同人跨任务只显示一次）；同名不同账号各占一行。
                // 此前按名字 distinct，两个 display_name 相同的账号会被错误合并成一人。
                val assemblerEntries = remember(assemblyTasks) {
                    assemblyTasks.flatMap { task ->
                        listOfNotNull(
                            task.assignedAssemblerId?.takeIf { it.isNotBlank() }?.let { id ->
                                id to (task.assignedAssemblerName?.takeIf { it.isNotBlank() } ?: id)
                            }
                        ) + task.members.mapNotNull { m ->
                            val id = m.assemblerId.takeIf { it.isNotBlank() } ?: return@mapNotNull null
                            id to (m.assemblerName?.takeIf { it.isNotBlank() } ?: id)
                        }
                    }.distinctBy { it.first }
                }
                AppCard {
                    SectionBarTitle("人员（${assemblerEntries.size}）")
                    VSpace(Spacing.sm)
                    if (assemblerEntries.isEmpty()) {
                        Text(
                            "该机台暂无指派人员",
                            fontSize = 13.sp,
                            color = LogisticsTheme.colors.textSecondary,
                        )
                    } else {
                        assemblerEntries.forEachIndexed { index, (_, name) ->
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

    // ---- 提交异常对话框：机台、物料固定，订单从关联订单下拉选 ----
    val em = exceptionMaterial
    val d = detail
    if (em != null && d != null) {
        DeviceExceptionDialog(
            deviceNo = d.deviceNo,
            materialName = em.name,
            materialCode = em.materialCode,
            orders = d.orders,
            onDismiss = { exceptionMaterial = null },
            onConfirm = { orderNo, type, actualQty, desc ->
                // 物料行 deviceId 是 order_devices.id（后端异常校验契约），优先于机台详情的 devices.id
                onSubmitException(em.deviceId?.takeIf { it.isNotBlank() } ?: d.deviceId, em.materialId, em.materialCode, orderNo, type, em.inStockQuantity, actualQty, desc)
                exceptionMaterial = null
            },
        )
    }

    // ---- 批量流转申请对话框：勾选多条物料一次提交（每项按需求数量出库） ----
    if (showBatchTransferDialog && detail != null && checkedMaterialIds.isNotEmpty()) {
        val batchItems = materials.filter { it.materialId in checkedMaterialIds }
        DeviceBatchTransferDialog(
            count = batchItems.size,
            orders = detail.orders,
            submitting = loading,
            onDismiss = { showBatchTransferDialog = false },
            onConfirm = { orderNo, remark ->
                showBatchTransferDialog = false
                onSubmitMaterialRequestBatch(batchItems, orderNo, remark)
                checkedMaterialIds = emptySet()
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
private fun DeviceKv(label: String, value: String, mono: Boolean = false, modifier: Modifier = Modifier) {    Column(modifier = modifier.fillMaxWidth().padding(vertical = 5.dp)) {
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
        properties = DialogProperties(decorFitsSystemWindows = false),
        // 键盘弹出时对话框整体上移，提交申请按钮始终可见
        modifier = Modifier.imePadding(),
        title = { Text("申请物料", fontSize = 16.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())) {
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
                    singleLine = true,
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

/**
 * 机台详情提交异常对话框。
 * 机台、物料固定；订单从机台关联订单下拉选择（单个时自动选中）；填写实际数量与说明。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeviceExceptionDialog(
    deviceNo: String,
    materialName: String,
    materialCode: String,
    orders: List<com.company.logistics.model.DeviceOrder>,
    onDismiss: () -> Unit,
    onConfirm: (orderNo: String, type: String, actualQuantity: Int, description: String) -> Unit,
) {
    var selectedOrder by remember(orders) { mutableStateOf(orders.singleOrNull()?.orderNo ?: "") }
    var orderExpanded by remember { mutableStateOf(false) }
    var selectedType by remember { mutableStateOf(EXCEPTION_TYPE_OPTIONS.first().first) }
    var typeExpanded by remember { mutableStateOf(false) }
    var actualQtyText by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    val canConfirm = selectedOrder.isNotBlank() && (actualQtyText.toIntOrNull() ?: -1) >= 0 && description.isNotBlank()

    AlertDialog(
        onDismissRequest = onDismiss,
        // decorFitsSystemWindows=false 使 IME insets 进入 Compose 树，配合 imePadding 实现键盘弹出时按钮上移
        properties = DialogProperties(decorFitsSystemWindows = false),
        // 键盘弹出时对话框整体上移，提交/取消按钮始终贴在键盘上方，无需手动收起键盘
        modifier = Modifier.imePadding(),
        title = { Text("提交异常", fontSize = 16.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())) {
                Text("机台：$deviceNo", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                Text("物料：$materialName（$materialCode）", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                Spacer(Modifier.height(12.dp))
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
                ExposedDropdownMenuBox(expanded = typeExpanded, onExpandedChange = { typeExpanded = it }) {
                    OutlinedTextField(
                        value = EXCEPTION_TYPE_OPTIONS.first { it.first == selectedType }.second,
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("异常类型") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(typeExpanded) },
                        modifier = Modifier.menuAnchor().fillMaxWidth(),
                    )
                    ExposedDropdownMenu(expanded = typeExpanded, onDismissRequest = { typeExpanded = false }) {
                        EXCEPTION_TYPE_OPTIONS.forEach { (code, label) ->
                            DropdownMenuItem(
                                text = { Text(label, fontSize = 13.sp) },
                                onClick = { selectedType = code; typeExpanded = false },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = actualQtyText,
                    onValueChange = { actualQtyText = it.filter { c -> c.isDigit() } },
                    label = { Text("实际数量") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = description,
                    onValueChange = { description = it },
                    label = { Text("异常说明") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(selectedOrder, selectedType, actualQtyText.toIntOrNull() ?: 0, description) }, enabled = canConfirm) {
                Text("提交")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

/**
 * 后端物料分类归一：materials.category ∈ {电气, 机械, 其他} 原样归入；
 * 空白或未知值统一归「未分类」，保证标签恒定四类不爆炸。
 */
private fun materialCategoryKey(item: OrderMaterialItem): String {
    val c = item.materialCategory?.trim().orEmpty()
    return if (c == "电气" || c == "机械" || c == "其他") c else "未分类"
}

/** 流转申请类型中文显示 */
private fun transferTypeLabel(type: String): String = when (type.trim().uppercase(java.util.Locale.ROOT)) {
    "OUTBOUND" -> "出库"
    "INBOUND" -> "入库"
    "TRANSFER" -> "调拨"
    else -> type.ifBlank { "—" }
}

/** 机台状态中文显示（devices.status 枚举 + 合成详情的 BOUND），未知值原样兜底 */
private fun deviceStatusLabel(status: String?): String = when (status?.trim()?.uppercase(java.util.Locale.ROOT)) {
    "ACTIVE" -> "运行中"
    "BOUND" -> "已绑定订单"
    "MAINTENANCE" -> "维护中"
    "DISABLED" -> "已停用"
    null, "" -> "—"
    else -> status
}

/** ISO 时间精简为 "MM-dd HH:mm"（服务端时间戳实为东八区本地时间，+00:00 后缀不可信，不做时区换算） */
private fun formatTransferTime(iso: String?): String {
    val s = iso?.trim().orEmpty()
    if (s.isBlank()) return ""
    return try {
        java.time.LocalDateTime.parse(s.take(19))
            .format(java.time.format.DateTimeFormatter.ofPattern("MM-dd HH:mm"))
    } catch (_: Exception) {
        s.take(16).replace('T', ' ')
    }
}

/** 表格总宽（dp）：勾选24 + 料号72 + 名称88 + 规格64 + 需求/已到/在库各40 + 状态56 + 操作40（只读角色无勾选/操作列）。 */
private fun materialTableWidth(canOperate: Boolean): Int =
    24 + 72 + 88 + 64 + 40 + 40 + 40 + 56 + (if (canOperate) 40 else 0)

/** 物料表格表头：勾选 | 料号 | 名称 | 规格 | 需求 | 已到 | 在库 | 状态 | 操作。固定不随内容横滑。 */
@Composable
private fun MaterialTableHeader(canOperate: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (canOperate) Spacer(Modifier.width(24.dp))
        MaterialHeadCell("料号", Modifier.width(72.dp))
        MaterialHeadCell("名称", Modifier.width(88.dp))
        MaterialHeadCell("规格", Modifier.width(64.dp))
        MaterialHeadCell("需求", Modifier.width(40.dp), end = true)
        MaterialHeadCell("已到", Modifier.width(40.dp), end = true)
        MaterialHeadCell("在库", Modifier.width(40.dp), end = true)
        MaterialHeadCell("状态", Modifier.width(56.dp))
        if (canOperate) MaterialHeadCell("操作", Modifier.width(40.dp))
    }
}

@Composable
private fun MaterialHeadCell(text: String, modifier: Modifier = Modifier, end: Boolean = false) {
    Text(
        text,
        modifier = modifier,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        color = LogisticsTheme.colors.textSecondary,
        textAlign = if (end) TextAlign.End else TextAlign.Start,
        maxLines = 1,
    )
}

/**
 * 物料表格数据行（表模式，不用卡片）：
 * 勾选框 | 编码(mono) | 名称+规格 | 需求/已到/在库（右对齐） | 状态圆点 | 异常按钮。
 * 紧凑密度：自绘 16dp 勾选框替代 M3 Checkbox（避免 48dp 触摸目标撑高行），行 padding 4dp。
 */
@Composable
private fun MaterialTableRow(
    item: OrderMaterialItem,
    checked: Boolean,
    canOperate: Boolean,
    onToggle: () -> Unit,
    onException: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onToggle)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (canOperate) MaterialCheckIndicator(checked)
        Text(
            item.materialCode,
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = LogisticsType.MonoFamily,
            color = LogisticsTheme.colors.textPrimary,
            maxLines = 2,
            modifier = Modifier.width(72.dp),
        )
        Text(
            item.name,
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textPrimary,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.width(88.dp),
        )
        Text(
            item.specification?.takeIf { it.isNotBlank() } ?: "—",
            fontSize = 10.sp,
            color = LogisticsTheme.colors.textTertiary,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.width(64.dp),
        )
        MaterialNumCell(item.requiredQuantity, Modifier.width(40.dp))
        MaterialNumCell(item.arrivedQuantity, Modifier.width(40.dp))
        MaterialNumCell(item.inStockQuantity, Modifier.width(40.dp))
        Row(
            modifier = Modifier.width(56.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(6.dp)
                    .background(item.statusCode.color, CircleShape)
            )
            Spacer(Modifier.width(4.dp))
            Text(
                item.label,
                fontSize = 10.sp,
                color = item.statusCode.tagTextColor(),
                maxLines = 1,
            )
        }
        if (canOperate) {
            Text(
                "异常",
                fontSize = 11.sp,
                color = LogisticsColors.PrimaryDark,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .width(40.dp)
                    .clickable(onClick = onException),
            )
        }
    }
}

/** 紧凑勾选指示器：16dp 圆角方块，选中实心打勾；整行 clickable 负责点击，此处仅展示。 */
@Composable
private fun MaterialCheckIndicator(checked: Boolean) {
    val shape = RoundedCornerShape(3.dp)
    Box(
        modifier = Modifier
            .padding(end = 8.dp)
            .size(16.dp)
            .clip(shape)
            .then(
                if (checked) {
                    Modifier.background(MaterialTheme.colorScheme.primary, shape)
                } else {
                    Modifier.border(1.5.dp, LogisticsTheme.colors.border, shape)
                }
            ),
        contentAlignment = Alignment.Center,
    ) {
        if (checked) {
            Image(
                imageVector = LogisticsIcons.Check,
                contentDescription = null,
                colorFilter = ColorFilter.tint(Color.White),
                modifier = Modifier.size(11.dp),
            )
        }
    }
}

@Composable
private fun MaterialNumCell(value: Int, modifier: Modifier = Modifier) {
    Text(
        "$value",
        modifier = modifier,
        fontSize = 11.sp,
        fontFamily = LogisticsType.MonoFamily,
        color = LogisticsTheme.colors.textSecondary,
        textAlign = TextAlign.End,
        maxLines = 1,
    )
}

/** 物料类型汇聚标签：选中反色高亮，横向滚动，点击筛选 / 再点取消。 */
@Composable
private fun MaterialTypeChip(text: String, selected: Boolean, onClick: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(16.dp),
        color = if (selected) LogisticsColors.Primary else LogisticsTheme.colors.cardBackground,
        border = androidx.compose.foundation.BorderStroke(
            1.dp,
            if (selected) LogisticsColors.Primary else LogisticsTheme.colors.border,
        ),
        modifier = Modifier.clickable(onClick = onClick),
    ) {
        Text(
            text,
            fontSize = 12.sp,
            fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
            color = if (selected) Color.White else LogisticsTheme.colors.textSecondary,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
        )
    }
}

/**
 * 批量流转申请对话框：勾选多条物料后一次提交。
 * 每项按需求数量创建出库（OUTBOUND）流转申请，逐条独立提交，部分失败不影响已成功条目。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeviceBatchTransferDialog(
    count: Int,
    orders: List<com.company.logistics.model.DeviceOrder>,
    submitting: Boolean,
    onDismiss: () -> Unit,
    onConfirm: (orderNo: String, remark: String) -> Unit,
) {
    var selectedOrder by remember(orders) { mutableStateOf(orders.singleOrNull()?.orderNo ?: "") }
    var orderExpanded by remember { mutableStateOf(false) }
    var remark by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(decorFitsSystemWindows = false),
        // 键盘弹出时对话框整体上移，提交申请按钮始终可见
        modifier = Modifier.imePadding(),
        title = { Text("批量流转申请", fontSize = 16.sp, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState())) {
                Text(
                    "已勾选 $count 项物料，将按各项需求数量分别创建出库流转申请。",
                    fontSize = 13.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
                Spacer(Modifier.height(12.dp))
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
                    value = remark,
                    onValueChange = { remark = it },
                    label = { Text("备注（可选）") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(selectedOrder, remark) },
                enabled = selectedOrder.isNotBlank() && count > 0 && !submitting,
            ) { Text("提交申请") }
        },
        dismissButton = { TextButton(onClick = onDismiss, enabled = !submitting) { Text("取消") } },
    )
}
