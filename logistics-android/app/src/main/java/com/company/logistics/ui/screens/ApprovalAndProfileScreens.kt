package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.background
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.OfflineOperation
import com.company.logistics.model.UserRole
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.KeyValueCell
import com.company.logistics.ui.components.PlaceholderScreen
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 审批页 —— 契约 2.2 审批状态机。
 *
 * 审批权限：仓库管理员与管理员（V1 无「主管」角色）。
 * 契约约束：审批拒绝必须填写原因；同一用户默认不得既审批又执行同一单据。
 */
@Composable
fun ApprovalScreen(
    role: UserRole,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))

        if (!role.canApprove) {
            EmptyState(
                title = "无审批权限",
                description = "当前角色为「${role.label}」。入库、出库与异常调整需由仓库管理员或管理员审批。"
            )
            return@Column
        }

        AppCard(accentColor = LogisticsTheme.colors.warning) {
            Text(
                "待我审批",
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary
            )
            VSpace(6.dp)
            Text(
                "审批通过后才允许实际变更库存；拒绝时必须填写原因",
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary
            )
        }

        VSpace(Spacing.lg)
        SectionTitle("审批状态说明")
        VSpace(Spacing.sm)

        AppCard {
            val flow = listOf(
                "DRAFT" to "草稿",
                "SUBMITTED" to "已提交",
                "PENDING_APPROVAL" to "待审批",
                "APPROVED" to "已批准",
                "EXECUTED" to "已执行"
            )
            flow.forEachIndexed { index, (_, label) ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    androidx.compose.foundation.layout.Box(
                        modifier = Modifier
                            .size(22.dp)
                            .background(MaterialTheme.colorScheme.primaryLight(), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "${index + 1}",
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary
                        )
                    }
                    Spacer(Modifier.width(Spacing.sm))
                    Text(
                        label,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                }
                if (index < flow.size - 1) VSpace(Spacing.sm)
            }
            VSpace(Spacing.sm)
            Text(
                "驳回分支：待审批 → 已驳回（需填写原因）",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.error
            )
        }

        VSpace(Spacing.lg)
        SectionTitle("权限边界")
        VSpace(Spacing.sm)
        AppCard {
            Text(
                "同一用户默认不得既审批又执行同一单据，以形成相互制约。",
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary
            )
        }

        VSpace(Spacing.xxl)
    }
}

/** 库存查询页（列表入口，扫码进入详情） */
@Composable
fun InventoryScreen(
    onGoScan: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Dimens.PagePadding)
    ) {
        Spacer(Modifier.height(Spacing.sm))
        EmptyState(
            title = "扫码查询库存",
            description = "扫描料号条码查看实时库存与库位分布；支持绑定库位与提交流转申请",
            action = {
                PrimaryButton(text = "去扫码", onClick = onGoScan)
            }
        )
    }
}

/** 我的页 —— 展示角色、权限、服务端配置与离线队列摘要 */
@Composable
fun ProfileScreen(
    userName: String,
    role: UserRole,
    queue: List<OfflineOperation>,
    endpointUrl: String,
    endpointConfigured: Boolean,
    onOpenQueue: () -> Unit,
    onOpenEndpointConfig: () -> Unit,
    onLogout: () -> Unit,
    modifier: Modifier = Modifier
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
                androidx.compose.foundation.layout.Box(
                    modifier = Modifier
                        .size(48.dp)
                        .background(MaterialTheme.colorScheme.primaryLight(), CircleShape),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        userName.take(1).ifBlank { "?" },
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary
                    )
                }
                Spacer(Modifier.width(Spacing.md))
                Column(Modifier.weight(1f)) {
                    Text(
                        userName.ifBlank { "未登录" },
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(4.dp)
                    StatusTag(
                        label = role.label,
                        color = MaterialTheme.colorScheme.primary,
                        containerColor = MaterialTheme.colorScheme.primaryLight()
                    )
                }
            }
        }

        VSpace(Spacing.md)

        AppCard {
            Text("权限范围", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
            VSpace(Spacing.sm)
            PermissionRow("扫码查看物料", true)
            PermissionRow("提交流转申请", true)
            PermissionRow("绑定库位", true)
            PermissionRow("审批入库/出库", role.canApprove)
            PermissionRow("执行库存变更", role.canExecute)
            PermissionRow("用户与审计管理", role.canAdmin)
        }

        VSpace(Spacing.md)

        // 服务端配置入口 —— 现场换环境时无需重新打包
        AppCard(
            accentColor = if (endpointConfigured)
                LogisticsTheme.colors.success else LogisticsTheme.colors.warning
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "服务端地址",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(3.dp)
                    Text(
                        endpointUrl,
                        fontSize = 11.sp,
                        fontFamily = LogisticsType.MonoFamily,
                        color = LogisticsTheme.colors.textTertiary,
                        maxLines = 1,
                        overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis
                    )
                }
                SecondaryButton(
                    text = "配置",
                    onClick = onOpenEndpointConfig,
                    modifier = Modifier.width(96.dp)
                )
            }
        }

        VSpace(Spacing.md)

        AppCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "离线队列",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary
                    )
                    VSpace(3.dp)
                    Text(
                        "共 ${queue.size} 条记录",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary
                    )
                }
                SecondaryButton(
                    text = "查看",
                    onClick = onOpenQueue,
                    modifier = Modifier.width(96.dp)
                )
            }
        }

        VSpace(Spacing.lg)
        SecondaryButton(text = "退出登录", onClick = onLogout)

        VSpace(Spacing.xxl)
    }
}

@Composable
private fun PermissionRow(label: String, allowed: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            if (allowed) "✓" else "—",
            fontSize = 13.sp,
            fontWeight = FontWeight.Bold,
            color = if (allowed) LogisticsTheme.colors.success else LogisticsTheme.colors.textTertiary
        )
        Spacer(Modifier.width(Spacing.sm))
        Text(
            label,
            fontSize = 13.sp,
            color = if (allowed) LogisticsTheme.colors.textPrimary else LogisticsTheme.colors.textTertiary
        )
    }
}

/** ColorScheme 扩展：浅色主色容器 */
@Composable
private fun androidx.compose.material3.ColorScheme.primaryLight(): Color =
    com.company.logistics.ui.theme.LogisticsColors.PrimaryLight
