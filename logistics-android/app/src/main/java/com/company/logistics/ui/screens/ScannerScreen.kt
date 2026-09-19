package com.company.logistics.ui.screens

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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.background
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.UserRole
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.ScannerToolBar
import com.company.logistics.ui.components.ScannerViewfinder
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 扫码作业页 —— 现场核心界面。
 *
 * 布局原则：
 *  - 取景框居中，占据视觉焦点；
 *  - 高频操作（手电筒 / 相册 / 手动输入）下沉到拇指可达区；
 *  - 提供「手动输入测试」入口，便于无相机环境与联调验证；
 *  - 底部展示今日待办，减少切换成本。
 *
 * 说明：V1 暂未接入相机预览（需 CameraX + 运行时权限），
 *      取景框为可点击的模拟入口，便于联调与演示；
 *      接入相机时替换 [ScannerViewfinder] 的 onTap 为真实扫码回调即可。
 */
@Composable
fun ScannerScreen(
    role: UserRole,
    pendingCount: Int,
    syncing: Boolean,
    loading: Boolean,
    lastCode: String?,
    onScanned: (String) -> Unit,
    onOpenQueue: () -> Unit,
    modifier: Modifier = Modifier
) {
    var manualInput by remember { mutableStateOf("") }
    var showManual by remember { mutableStateOf(false) }
    var torchOn by remember { mutableStateOf(false) }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        ScannerViewfinder(
            scanTypeLabel = "支持 生产订单号 / 料号 / 库位码 / 流转单号",
            onTap = {
                // 联调用：无相机时以示例条码触发解析链路
                onScanned("MTR-001")
            }
        )

        Spacer(Modifier.height(Spacing.md))

        ScannerToolBar(
            onTorch = { torchOn = !torchOn },
            onAlbum = { onScanned("MTR-001") },
            onManual = { showManual = !showManual },
            torchEnabled = torchOn
        )

        if (showManual) {
            Spacer(Modifier.height(Spacing.md))
            SectionTitle("手动输入条码")
            Spacer(Modifier.height(Spacing.sm))
            OutlinedCodeField(
                value = manualInput,
                onValueChange = { manualInput = it },
                onConfirm = {
                    if (manualInput.isNotBlank()) {
                        onScanned(manualInput.trim())
                        manualInput = ""
                    }
                },
                loading = loading
            )
        }

        if (lastCode != null) {
            Spacer(Modifier.height(Spacing.md))
            AppCard(accentColor = LogisticsTheme.colors.success) {
                Text(
                    "最近识别",
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    lastCode,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = LogisticsType.MonoFamily,
                    letterSpacing = 0.5.sp,
                    color = LogisticsTheme.colors.textPrimary
                )
            }
        }

        Spacer(Modifier.height(Spacing.lg))

        // 待办 / 离线入口
        AppCard(
            modifier = Modifier.clickable { onOpenQueue() }
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(30.dp)
                        .background(LogisticsTheme.colors.warning, CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        if (pendingCount > 0) "$pendingCount" else "0",
                        color = Color.White,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                }
                Spacer(Modifier.width(Spacing.md))
                Column(Modifier.weight(1f)) {
                    Text(
                        if (pendingCount > 0) "待同步记录" else "离线队列",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        if (syncing) "正在同步…"
                        else if (pendingCount > 0) "$pendingCount 条记录等待上传，点击查看"
                        else "当前无待同步记录",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary
                    )
                }
                Text("›", fontSize = 18.sp, color = LogisticsTheme.colors.textTertiary)
            }
        }

        Spacer(Modifier.height(Spacing.md))

        // 角色能力提示
        AppCard {
            Text(
                "当前角色",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
            Spacer(Modifier.height(4.dp))
            Text(
                role.label,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary
            )
            Spacer(Modifier.height(6.dp))
            Text(
                buildString {
                    append("可提交入库/出库申请")
                    if (role.canApprove) append(" · 可审批")
                    if (role.canAdmin) append(" · 可管理用户与审计")
                },
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary
            )
        }

        if (loading) {
            Spacer(Modifier.height(Spacing.md))
            Text(
                "解析中…",
                modifier = Modifier.fillMaxWidth(),
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.SemiBold
            )
        }

        Spacer(Modifier.height(Spacing.xxl))
    }
}

@Composable
private fun OutlinedCodeField(
    value: String,
    onValueChange: (String) -> Unit,
    onConfirm: () -> Unit,
    loading: Boolean
) {
    Column {
        androidx.compose.material3.OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = !loading,
            shape = MaterialTheme.shapes.small,
            placeholder = {
                Text(
                    "如 MTR-001 / SO202609120001 / A-01-03",
                    fontSize = 14.sp,
                    color = LogisticsTheme.colors.textTertiary
                )
            },
            textStyle = androidx.compose.ui.text.TextStyle(
                fontFamily = LogisticsType.MonoFamily,
                fontSize = 15.sp
            )
        )
        Spacer(Modifier.height(Spacing.sm))
        PrimaryButton(
            text = "解析条码",
            onClick = onConfirm,
            enabled = value.isNotBlank(),
            loading = loading
        )
    }
}
