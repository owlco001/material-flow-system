package com.company.logistics.ui.screens

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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.background
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.MaterialInventory
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 物料详情页 —— 契约 4.4 料号库存。
 *
 * 展示原则：
 *  - 料号以等宽字体放大，防止数字认读错误（现场关键防错手段）；
 *  - 在库数量与当前库位并列展示，是现场最关心的两个字段；
 *  - 展示库存版本号 version，让用户理解写操作存在乐观锁校验。
 */
@Composable
fun MaterialDetailScreen(
    inventory: MaterialInventory,
    loading: Boolean,
    readOnly: Boolean = false,
    onBack: () -> Unit,
    onInbound: () -> Unit,
    onBindLocation: () -> Unit,
    modifier: Modifier = Modifier
) {
    val inv = inventory.inventory
    val m = inventory.material

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        // 识别成功提示
        AppCard(
            accentColor = LogisticsTheme.colors.success
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(22.dp)
                        .background(LogisticsTheme.colors.success, CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text("✓", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                }
                Spacer(Modifier.width(Spacing.sm))
                Text(
                    "扫码成功 · 已获取库存",
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = LogisticsTheme.colors.success
                )
            }
        }

        VSpace(Spacing.md)

        // 料号主卡
        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Text(
                "料号 PART NO.",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
                letterSpacing = 0.5.sp
            )
            VSpace(4.dp)
            Text(
                text = m.code,
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                letterSpacing = 0.5.sp,
                color = LogisticsTheme.colors.textPrimary
            )
            VSpace(Spacing.sm)
            Text(
                m.name,
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                color = LogisticsTheme.colors.textPrimary
            )
            if (!m.specification.isNullOrBlank()) {
                VSpace(4.dp)
                Text(
                    "规格：${m.specification}",
                    fontSize = 13.sp,
                    color = LogisticsTheme.colors.textSecondary
                )
            }
        }

        VSpace(Spacing.md)

        // 数量与库位（两列关键数据）
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
            InfoCell(
                label = "总库存",
                value = "${inv.totalQuantity}",
                unit = m.unit,
                modifier = Modifier.weight(1f)
            )
            InfoCell(
                label = "可用库存",
                value = "${inv.availableQuantity}",
                unit = m.unit,
                valueColor = MaterialTheme.colorScheme.primary,
                modifier = Modifier.weight(1f)
            )
        }

        VSpace(Spacing.md)

        AppCard {
            Text(
                "库位分布",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
            VSpace(Spacing.sm)
            if (inv.locations.isEmpty()) {
                Text(
                    "暂未绑定库位",
                    fontSize = 14.sp,
                    color = LogisticsTheme.colors.textSecondary
                )
            } else {
                inv.locations.forEach { loc ->
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .size(6.dp)
                                .background(LogisticsTheme.colors.success, CircleShape)
                        )
                        Spacer(Modifier.width(Spacing.sm))
                        Text(
                            loc.locationCode,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = LogisticsType.MonoFamily,
                            color = MaterialTheme.colorScheme.primary
                        )
                        Spacer(Modifier.weight(1f))
                        Text(
                            "${loc.quantity} ${m.unit}",
                            fontSize = 14.sp,
                            fontFamily = LogisticsType.MonoFamily,
                            fontWeight = FontWeight.SemiBold,
                            color = LogisticsTheme.colors.textPrimary
                        )
                    }
                }
            }
            VSpace(Spacing.sm)
            Text(
                "库存版本 v${inventory.version} · 提交时服务端将做版本校验",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
        }

        if (!m.batchNo.isNullOrBlank() || !m.expiryDate.isNullOrBlank()) {
            VSpace(Spacing.md)
            AppCard {
                Row {
                    if (!m.batchNo.isNullOrBlank()) {
                        Column(Modifier.weight(1f)) {
                            Text("批次号", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
                            VSpace(4.dp)
                            Text(
                                m.batchNo!!,
                                fontSize = 14.sp,
                                fontFamily = LogisticsType.MonoFamily,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                    if (!m.expiryDate.isNullOrBlank()) {
                        Column(Modifier.weight(1f)) {
                            Text("有效期", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
                            VSpace(4.dp)
                            Text(
                                m.expiryDate!!,
                                fontSize = 14.sp,
                                fontFamily = LogisticsType.MonoFamily,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            }
        }

        VSpace(Spacing.lg)

        // 主操作
        PrimaryButton(
            text = "提交入库申请",
            onClick = onInbound,
            loading = loading,
            enabled = !readOnly && (inv.availableQuantity > 0 || inv.totalQuantity >= 0)
        )
        VSpace(Spacing.sm)
        SecondaryButton(
            text = "绑定库位",
            onClick = onBindLocation,
            enabled = !readOnly && !loading
        )

        if (readOnly) {
            VSpace(Spacing.sm)
            Text(
                "测试预览只读，不能提交入库或绑定库位",
                modifier = Modifier.fillMaxWidth(),
                fontSize = 12.sp,
                color = LogisticsTheme.colors.warning,
                fontWeight = FontWeight.SemiBold,
            )
        }

        VSpace(Spacing.sm)
        Text(
            "入库申请提交后需仓库管理员审批，审批通过才实际变更库存",
            modifier = Modifier.fillMaxWidth(),
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textTertiary
        )

        VSpace(Spacing.xxl)
    }
}

@Composable
private fun InfoCell(
    label: String,
    value: String,
    unit: String,
    modifier: Modifier = Modifier,
    valueColor: Color = LogisticsTheme.colors.textPrimary
) {
    AppCard(modifier = modifier) {
        Text(label, fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        VSpace(6.dp)
        Row(verticalAlignment = Alignment.Bottom) {
            Text(
                value,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = LogisticsType.MonoFamily,
                color = valueColor
            )
            Spacer(Modifier.width(4.dp))
            Text(
                unit,
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary,
                modifier = Modifier.padding(bottom = 3.dp)
            )
        }
    }
}
