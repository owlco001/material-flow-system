package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.company.logistics.model.UserRole
import com.company.logistics.ui.components.EmptyState
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme

/**
 * 管理员审计入口。
 *
 * 本阶段不新增后端接口，因此这里明确显示未接入，而不是伪造审计数量或事件。
 */
@Composable
fun AuditScreen(
    role: UserRole,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Dimens.PagePadding),
    ) {
        SectionTitle("审计记录")
        if (role != UserRole.ADMIN) {
            EmptyState(
                title = "无审计权限",
                description = "只有管理员可以查看全量审计记录。",
            )
        } else {
            EmptyState(
                title = "审计查询入口已保留",
                description = "当前订单接口没有返回审计事件，本阶段不创建演示记录。接入审计 API 后将在此展示服务端时间、操作者和结果。",
            )
        }
        androidx.compose.material3.TextButton(onClick = onBack) {
            Text(
                text = "返回工作台",
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                color = LogisticsTheme.colors.textSecondary,
            )
        }
    }
}
