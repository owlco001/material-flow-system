package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.model.AuditLog
import com.company.logistics.model.UserRole
import com.company.logistics.ui.WorkspaceLoadState
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.StatusTag
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/** 管理员只读审计工作台；列表由服务端分页，客户端不写入、不聚合、不展示敏感 JSON。 */
@Composable
fun AuditScreen(
    role: UserRole,
    logs: List<AuditLog>,
    state: WorkspaceLoadState,
    error: String?,
    page: Int,
    pageSize: Int,
    hasNext: Boolean,
    serverTime: String?,
    onRefresh: () -> Unit,
    onRetry: () -> Unit,
    onNextPage: () -> Unit,
    onPreviousPage: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        Spacer(Modifier.height(Spacing.sm))
        SectionTitle("审计记录", trailing = "管理员只读")
        VSpace(Spacing.sm)

        if (role != UserRole.ADMIN) {
            EmptyState(
                title = "无审计权限",
                description = "只有管理员可以查看服务端审计记录。",
            )
            SecondaryButton(text = "返回工作台", onClick = onBack)
            return@Column
        }

        AppCard(accentColor = MaterialTheme.colorScheme.primary) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "服务端审计",
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary,
                    )
                    VSpace(4.dp)
                    Text(
                        "仅查看服务端时间、操作者和结果；不支持在客户端修改记录",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary,
                    )
                }
                SecondaryButton(
                    text = "刷新",
                    onClick = onRefresh,
                    enabled = state != WorkspaceLoadState.LOADING,
                    modifier = Modifier.width(88.dp),
                )
            }
            serverTime?.let {
                VSpace(Spacing.sm)
                Text("服务端时间 $it", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
            }
        }

        when (state) {
            WorkspaceLoadState.IDLE -> Unit
            WorkspaceLoadState.LOADING -> {
                VSpace(Spacing.xl)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(modifier = Modifier.width(22.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(Spacing.sm))
                    Text("正在读取审计记录…", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
                }
            }
            WorkspaceLoadState.ERROR -> {
                EmptyState(
                    title = "审计记录加载失败",
                    description = error ?: "请稍后重试",
                    action = { SecondaryButton(text = "重试", onClick = onRetry) },
                )
            }
            WorkspaceLoadState.EMPTY -> EmptyState(
                title = "暂无审计记录",
                description = "服务端当前没有返回可见审计记录。",
            )
            WorkspaceLoadState.CONTENT -> {
                VSpace(Spacing.lg)
                logs.forEach { log -> AuditLogCard(log) }
            }
        }

        if (state != WorkspaceLoadState.ERROR && state != WorkspaceLoadState.IDLE) {
            VSpace(Spacing.md)
            Row(verticalAlignment = Alignment.CenterVertically) {
                SecondaryButton(
                    text = "上一页",
                    onClick = onPreviousPage,
                    enabled = state != WorkspaceLoadState.LOADING && page > 1,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(Spacing.sm))
                Text(
                    "第 $page 页 · 每页 $pageSize 条",
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
                Spacer(Modifier.width(Spacing.sm))
                SecondaryButton(
                    text = "下一页",
                    onClick = onNextPage,
                    enabled = state != WorkspaceLoadState.LOADING && hasNext,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        VSpace(Spacing.md)
        SecondaryButton(text = "返回工作台", onClick = onBack)
        VSpace(Spacing.xxl)
    }
}

@Composable
private fun AuditLogCard(log: AuditLog) {
    val successful = log.result.equals("SUCCESS", ignoreCase = true)
    AppCard(accentColor = if (successful) LogisticsTheme.colors.success else LogisticsTheme.colors.danger) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "${log.action.ifBlank { "未知动作" }} · ${log.resourceType.ifBlank { "未知资源" }}",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                VSpace(4.dp)
                Text(
                    listOfNotNull(
                        log.operatorId?.takeIf { it.isNotBlank() }?.let { "操作者 $it" },
                        log.role?.takeIf { it.isNotBlank() }?.let { "角色 $it" },
                        log.resourceId?.takeIf { it.isNotBlank() }?.let { "资源 $it" },
                    ).joinToString(" · ").ifBlank { "服务端未返回操作者摘要" },
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            StatusTag(
                label = log.result.ifBlank { "未知结果" },
                color = if (successful) LogisticsTheme.colors.success else LogisticsTheme.colors.danger,
                containerColor = if (successful) LogisticsTheme.colors.success.copy(alpha = 0.14f)
                else LogisticsTheme.colors.danger.copy(alpha = 0.14f),
                symbol = if (successful) "✓" else "!",
            )
        }
        VSpace(Spacing.sm)
        Text(
            listOfNotNull(
                log.occurredAt?.takeIf { it.isNotBlank() }?.let { "服务端时间 $it" },
                log.requestId?.takeIf { it.isNotBlank() }?.let { "请求 $it" },
            ).joinToString("\n").ifBlank { "服务端未返回时间或请求标识" },
            fontSize = 11.sp,
            fontFamily = LogisticsType.MonoFamily,
            color = LogisticsTheme.colors.textTertiary,
        )
    }
    VSpace(Spacing.sm)
}
