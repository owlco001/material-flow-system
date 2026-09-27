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
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import com.company.logistics.model.DeviceDetail
import com.company.logistics.model.DeviceModelMap
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 机台详情页。
 *
 * 视觉：渐变横幅头图（机台编号 + 名称 + 状态徽章）+ 分区卡片 + 关联订单卡片。
 */
@Composable
fun DeviceDetailScreen(
    detail: DeviceDetail?,
    loading: Boolean,
    error: String?,
    onBack: () -> Unit
) {
    val context = LocalContext.current
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
                // ---- 渐变横幅头图 ----
                Surface(
                    modifier = Modifier.fillMaxWidth(),
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
                            Surface(
                                shape = RoundedCornerShape(10.dp),
                                color = Color.White,
                            ) {
                                Text(
                                    detail.status,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = LogisticsColors.PrimaryDark,
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                                )
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

                // ---- 3D 模型 ----
                val modelCode = DeviceModelMap.modelCodeFor("", detail.deviceNo)
                if (modelCode != null) {
                    PrimaryButton(
                        text = "查看 3D 模型",
                        onClick = {
                            context.startActivity(
                                Model3dActivity.intent(context, modelCode, "机台 ${detail.deviceNo}", detail.deviceNo)
                            )
                        },
                    )
                } else {
                    Text(
                        "该机型暂无 3D 模型",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textTertiary,
                    )
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
