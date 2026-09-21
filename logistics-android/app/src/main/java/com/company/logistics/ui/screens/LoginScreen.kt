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
import androidx.compose.foundation.Image
import androidx.compose.material3.Checkbox
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
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.R
import com.company.logistics.ui.components.LogisticsIcons
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
    onLogin: (username: String, password: String, deviceId: String, remember: Boolean) -> Unit,
    endpointConfigured: Boolean = false,
    onOpenEndpointConfig: () -> Unit = {},
    modifier: Modifier = Modifier
) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var rememberLogin by remember { mutableStateOf(true) }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(LogisticsTheme.colors.pageBackground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Spacer(Modifier.height(72.dp))

        // 品牌标识：复用现有公司齿轮图标，不新增或修改图标资源。
        Image(
            painter = painterResource(R.mipmap.ic_launcher_foreground),
            contentDescription = stringResource(R.string.app_name) + " Logo",
            modifier = Modifier.size(72.dp),
        )

        Spacer(Modifier.height(Spacing.lg))
        Text(
            stringResource(R.string.app_name),
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary
        )
        Spacer(Modifier.height(6.dp))
        Text(
            stringResource(R.string.brand_subtitle),
            fontSize = 13.sp,
            color = LogisticsTheme.colors.textTertiary
        )

        Spacer(Modifier.height(36.dp))

        Text(
            "账号/工号",
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

        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = rememberLogin, onCheckedChange = { rememberLogin = it }, enabled = !loading)
            Text("保持登录", color = LogisticsTheme.colors.textSecondary, fontSize = 13.sp)
        }

        if (errorMessage != null) {
            Spacer(Modifier.height(Spacing.md))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Image(
                    imageVector = LogisticsIcons.Alert,
                    contentDescription = null,
                    colorFilter = ColorFilter.tint(MaterialTheme.colorScheme.error),
                    modifier = Modifier.size(16.dp)
                )
                Spacer(Modifier.width(Spacing.xs))
                Text(
                    text = errorMessage,
                    modifier = Modifier.weight(1f),
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }

        Spacer(Modifier.height(Spacing.xl))
        PrimaryButton(
            text = "登 录",
            onClick = { onLogin(username, password, deviceId, rememberLogin) },
            loading = loading,
            enabled = username.isNotBlank() && password.isNotBlank()
        )

        Spacer(Modifier.height(Spacing.md))
        PrimaryButton(
            text = if (endpointConfigured) "后端设置" else "立即设置后端",
            onClick = onOpenEndpointConfig,
            enabled = !loading,
        )
        Spacer(Modifier.height(Spacing.xs))
        Text(
            if (endpointConfigured) "后端已配置，可随时修改服务地址" else "尚未配置后端",
            fontSize = 12.sp,
            color = LogisticsTheme.colors.textTertiary,
            textAlign = TextAlign.Center,
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
