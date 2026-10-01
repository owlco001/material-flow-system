package com.company.logistics.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing

/**
 * 统一的二次确认对话框 —— 不可逆操作（放弃/清理/完工/登出/批准/执行）必须经过它。
 *
 * 设计要求：
 *  - message 必须写明操作后果（会发生什么、不会发生什么），不许只写「确定吗？」；
 *  - danger=true 时确认按钮用红色，强调破坏性；
 *  - 确认/取消文案允许自定义（如「放弃记录/再想想」），默认「确认/取消」。
 */
@Composable
fun ConfirmDialog(
    title: String,
    message: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    confirmText: String = "确认",
    dismissText: String = "取消",
    danger: Boolean = false
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                title,
                fontSize = 17.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary
            )
        },
        text = {
            Column {
                Text(
                    message,
                    fontSize = 14.sp,
                    color = LogisticsTheme.colors.textSecondary
                )
                Spacer(Modifier.height(Spacing.sm))
            }
        },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(
                    confirmText,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    color = if (danger) LogisticsTheme.colors.danger else LogisticsTheme.colors.primary
                )
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(
                    dismissText,
                    fontSize = 15.sp,
                    color = LogisticsTheme.colors.textSecondary
                )
            }
        }
    )
}
