package com.company.logistics.ui.screens

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.OrderMaterialStatus
import com.company.logistics.model.RoleWorkspaceSummary
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMetric
import com.company.logistics.model.WorkspaceMetricKey
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing

private data class WorkspaceEntry(
    val key: WorkspaceMetricKey,
    val title: String,
    val hint: String,
    val actionLabel: String,
    val actionIsPrimary: Boolean = false
)

@Composable
fun WorkspaceScreen(
    role: UserRole,
    summary: RoleWorkspaceSummary,
    orderStatus: OrderMaterialStatus?,
    onOpenScanner: () -> Unit,
    onOpenOrder: () -> Unit,
    onOpenApproval: () -> Unit,
    onOpenAudit: () -> Unit,
    modifier: Modifier = Modifier
) {
    val entries = entriesFor(role)

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        Spacer(Modifier.height(Spacing.sm))

        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Text(
                text = titleFor(role),
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary,
            )
            VSpace(6.dp)
            Text(
                text = subtitleFor(role),
                fontSize = 13.sp,
                color = LogisticsTheme.colors.textSecondary,
            )
            VSpace(Spacing.md)
            StatusTag(
                label = role.label,
                color = MaterialTheme.colorScheme.primary,
                containerColor = LogisticsTheme.colors.primaryContainer,
                symbol = "●",
            )
        }

        VSpace(Spacing.md)
        DataSourceNotice(summary = summary, orderStatus = orderStatus, onOpenScanner = onOpenScanner)
        VSpace(Spacing.md)

        entries.forEach { entry ->
            WorkspaceMetricCard(
                entry = entry,
                metric = summary.metric(entry.key),
                onClick = when (entry.key) {
                    WorkspaceMetricKey.PENDING_APPROVAL -> onOpenApproval
                    WorkspaceMetricKey.AUDIT -> onOpenAudit
                    else -> onOpenOrder
                },
            )
            VSpace(Spacing.sm)
        }

        if (orderStatus == null) {
            EmptyState(
                title = "等待订单数据",
                description = "工作台不会创建演示计数。扫描生产订单后，这里会展示服务端返回的订单物料摘要。",
                action = { PrimaryButton(text = "扫描生产订单", onClick = onOpenScanner) },
            )
        }

        VSpace(Spacing.xxl)
    }
}

@Composable
private fun DataSourceNotice(
    summary: RoleWorkspaceSummary,
    orderStatus: OrderMaterialStatus?,
    onOpenScanner: () -> Unit,
) {
    AppCard(
        accentColor = if (orderStatus == null) LogisticsTheme.colors.warning else LogisticsTheme.colors.success,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = if (summary.sourceOrderNo == null) "未载入生产订单" else "当前订单 ${summary.sourceOrderNo}",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                VSpace(4.dp)
                Text(
                    text = if (summary.serverTime == null) {
                        "摘要只使用现有订单接口数据"
                    } else {
                        "服务端时间：${summary.serverTime}"
                    },
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary,
                )
            }
            SecondaryButton(
                text = if (orderStatus == null) "去扫码" else "刷新订单",
                onClick = onOpenScanner,
                modifier = Modifier.width(104.dp),
            )
        }
    }
}

@Composable
private fun WorkspaceMetricCard(
    entry: WorkspaceEntry,
    metric: WorkspaceMetric,
    onClick: () -> Unit,
) {
    AppCard(
        accentColor = if (metric.available) MaterialTheme.colorScheme.primary else LogisticsTheme.colors.border,
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = entry.title,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                VSpace(4.dp)
                Text(
                    text = if (metric.available) entry.hint else "当前订单接口未提供该状态域数据",
                    fontSize = 11.sp,
                    color = LogisticsTheme.colors.textTertiary,
                )
            }
            Text(
                text = metric.count?.toString() ?: "—",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = if (metric.available) MaterialTheme.colorScheme.primary else LogisticsTheme.colors.textTertiary,
            )
            Spacer(Modifier.width(Spacing.sm))
            if (entry.actionIsPrimary) {
                PrimaryButton(
                    text = entry.actionLabel,
                    onClick = onClick,
                    modifier = Modifier.width(96.dp),
                )
            } else {
                SecondaryButton(
                    text = entry.actionLabel,
                    onClick = onClick,
                    modifier = Modifier.width(96.dp),
                )
            }
        }
        if (!metric.available) {
            VSpace(Spacing.sm)
            Text(
                text = "— 表示未从服务端获得数据，不代表数量为 0",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
            )
        }
    }
}

private fun titleFor(role: UserRole): String = when (role) {
    UserRole.OPERATOR -> "操作员工作台"
    UserRole.MATERIAL -> "物料员工作台"
    UserRole.WAREHOUSE_ADMIN -> "仓库管理工作台"
    UserRole.ADMIN -> "管理员工作台"
}

private fun subtitleFor(role: UserRole): String = when (role) {
    UserRole.OPERATOR -> "优先关注已领取与已到机台，按订单查看本人负责范围"
    UserRole.MATERIAL -> "优先处理待出库与已出库，出库动作沿用现有申请流程"
    UserRole.WAREHOUSE_ADMIN -> "优先查看待审批与待交接，实际权限仍由服务端校验"
    UserRole.ADMIN -> "查看全量与异常入口，并保留审计查询入口"
}

private fun entriesFor(role: UserRole): List<WorkspaceEntry> = when (role) {
    UserRole.OPERATOR -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.CLAIMED, "已领取", "服务端确认的领取记录", "查看订单", true),
        WorkspaceEntry(WorkspaceMetricKey.AT_STATION, "已到机台", "服务端确认目标机台的交接", "查看订单"),
    )
    UserRole.MATERIAL -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.OUTBOUND_PENDING, "待出库", "服务端返回的待出库状态", "查看订单", true),
        WorkspaceEntry(WorkspaceMetricKey.OUTBOUND_CONFIRMED, "已出库", "服务端返回的已出库状态", "查看订单"),
    )
    UserRole.WAREHOUSE_ADMIN -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.PENDING_APPROVAL, "待审批", "服务端返回的审批状态", "去审批", true),
        WorkspaceEntry(WorkspaceMetricKey.PENDING_HANDOVER, "待交接", "服务端返回的交接状态", "查看订单"),
    )
    UserRole.ADMIN -> listOf(
        WorkspaceEntry(WorkspaceMetricKey.ALL, "全量物料", "当前订单接口返回的全部物料项", "查看全量", true),
        WorkspaceEntry(WorkspaceMetricKey.EXCEPTION, "异常", "服务端明确标记的异常项", "查看异常"),
        WorkspaceEntry(WorkspaceMetricKey.AUDIT, "审计入口", "服务端审计接口返回的记录", "打开审计"),
    )
}
