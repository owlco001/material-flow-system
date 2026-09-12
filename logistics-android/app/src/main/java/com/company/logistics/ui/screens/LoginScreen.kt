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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing

/**
 * 登录页。
 *
 * 契约约束：
 *  - 初始管理员 owlco 首次登录必须改密（mustChangePassword）；
 *  - 密码提交后不写入本地日志；
 *  - 设备 ID 随请求上报，用于审计（契约 1 节审计字段含 deviceId）。
 *
 * 角色不再由用户在登录时手选 —— 角色由服务端返回，避免客户端伪造权限视图。
 */
@Composable
fun LoginScreen(
    loading: Boolean,
    errorMessage: String?,
    deviceId: String,
    onLogin: (username: String, password: String, deviceId: String) -> Unit,
    modifier: Modifier = Modifier
) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(LogisticsTheme.colors.pageBackground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Spacer(Modifier.height(72.dp))

        // 品牌标识
        Box(
            modifier = Modifier
                .size(72.dp)
                .clip(RoundedCornerShape(20.dp))
                .background(
                    Brush.linearGradient(
                        listOf(MaterialTheme.colorScheme.primary, com.company.logistics.ui.theme.LogisticsColors.PrimaryDark)
                    )
                ),
            contentAlignment = Alignment.Center
        ) {
            Text("⇅", color = Color.White, fontSize = 32.sp, fontWeight = FontWeight.Bold)
        }

        Spacer(Modifier.height(Spacing.lg))
        Text(
            "物料流转系统",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "厂内物料流转 · 扫码作业平台",
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textTertiary
        )

        Spacer(Modifier.height(36.dp))

        Text(
            "账号",
            modifier = Modifier.fillMaxWidth(),
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = LogisticsTheme.colors.textSecondary
        )
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = username,
            onValueChange = { username = it.trim() },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            placeholder = { Text("请输入工号 / 账号", color = LogisticsTheme.colors.textTertiary) },
            enabled = !loading,
            shape = MaterialTheme.shapes.small,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next)
        )

        Spacer(Modifier.height(Spacing.lg))

        Text(
            "密码",
            modifier = Modifier.fillMaxWidth(),
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = LogisticsTheme.colors.textSecondary
        )
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            placeholder = { Text("请输入密码", color = LogisticsTheme.colors.textTertiary) },
            visualTransformation = PasswordVisualTransformation(),
            enabled = !loading,
            shape = MaterialTheme.shapes.small,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done)
        )

        if (errorMessage != null) {
            Spacer(Modifier.height(Spacing.md))
            Text(
                text = "⚠ $errorMessage",
                modifier = Modifier.fillMaxWidth(),
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.error,
                fontWeight = FontWeight.SemiBold
            )
        }

        Spacer(Modifier.height(Spacing.xl))
        PrimaryButton(
            text = "登 录",
            onClick = { onLogin(username, password, deviceId) },
            loading = loading,
            enabled = username.isNotBlank() && password.isNotBlank()
        )

        Spacer(Modifier.height(Spacing.lg))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "设备标识 ",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary
            )
            Text(
                deviceId.take(16) + "…",
                fontSize = 11.sp,
                color = LogisticsTheme.colors.textTertiary,
                fontWeight = FontWeight.SemiBold
            )
        }

        Spacer(Modifier.height(Spacing.sm))
        Text(
            "v${com.company.logistics.data.LogisticsRepository.CLIENT_VERSION} · 仅限厂内网络使用",
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(40.dp))
    }
}
