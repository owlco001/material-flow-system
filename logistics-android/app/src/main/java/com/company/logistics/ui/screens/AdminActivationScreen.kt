package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction

import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.SetupStatusDto
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing
import kotlinx.coroutines.launch
import androidx.compose.runtime.rememberCoroutineScope

@Composable
fun AdminActivationScreen(
    repository: LogisticsRepository,
    onBack: () -> Unit,
    onInitialized: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var status by remember { mutableStateOf<SetupStatusDto?>(null) }
    var password by remember { mutableStateOf("") }
    var confirmation by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var success by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        repository.setupStatus()
            .onSuccess { status = it }
            .onFailure { error = it.safeActivationMessage("无法读取初始化状态") }
    }

    Column(
        modifier = modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(Dimens.PagePadding),
        verticalArrangement = Arrangement.Top,
    ) {
        VSpace(Spacing.md)
        SectionTitle("初始管理员激活")
        VSpace(Spacing.sm)
        Text("仅设置一次初始管理员密码。密码不会保存到本机。", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
        VSpace(Spacing.lg)

        status?.let { current ->
            AppCard(accentColor = if (current.initialized) LogisticsTheme.colors.danger else LogisticsTheme.colors.warning) {
                Text("管理员账号", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
                Text(current.adminUsername, fontSize = 17.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.textPrimary)
                VSpace(Spacing.sm)
                if (current.initialized) {
                    Text("管理员已初始化，请使用现有账号登录或联系管理员重置。不可覆盖已有管理员。", color = LogisticsTheme.colors.danger, fontSize = 13.sp)
                } else {
                    Text("当前尚未初始化", color = LogisticsTheme.colors.warning, fontSize = 13.sp)
                }
            }
        }

        if (status?.initialized == false) {
            VSpace(Spacing.lg)
            OutlinedTextField(
                value = password,
                onValueChange = { password = it; error = null },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("初始密码") },
                singleLine = true,
                enabled = !busy,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Next),
            )
            VSpace(Spacing.sm)
            OutlinedTextField(
                value = confirmation,
                onValueChange = { confirmation = it; error = null },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("确认初始密码") },
                singleLine = true,
                enabled = !busy,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            )
            VSpace(Spacing.xs)
            Text("密码长度必须为 8~256 个字符，初始化成功后首次登录必须改密。", fontSize = 12.sp, color = LogisticsTheme.colors.textTertiary)
            error?.let { Text("⚠ $it", modifier = Modifier.fillMaxWidth().padding(top = 12.dp), color = MaterialTheme.colorScheme.error, fontSize = 13.sp) }
            VSpace(Spacing.lg)
            PrimaryButton(
                text = "初始化管理员",
                loading = busy,
                enabled = !busy && password.length in 8..256 && password == confirmation,
                onClick = {
                    if (password.length !in 8..256) { error = "密码长度必须为 8~256 个字符"; return@PrimaryButton }
                    if (password != confirmation) { error = "两次输入的密码不一致"; return@PrimaryButton }
                    busy = true
                    scope.launch {
                        repository.initializeAdmin(password, confirmation)
                            .onSuccess {
                                password = ""; confirmation = ""; success = true; busy = false
                            }
                            .onFailure { e -> busy = false; error = e.safeActivationMessage("初始化失败") }
                    }
                },
            )
            if (success) {
                VSpace(Spacing.sm)
                Text("初始化成功。请返回登录页，首次登录后必须修改密码。", color = LogisticsTheme.colors.success, fontSize = 13.sp)
                VSpace(Spacing.sm)
                PrimaryButton(text = "返回登录", onClick = onInitialized)
            }
        }
        VSpace(Spacing.xl)
        androidx.compose.material3.TextButton(onClick = onBack, enabled = !busy) { Text("返回") }
    }
}

private fun Throwable.safeActivationMessage(fallback: String): String = when (this) {
    is ApiException -> when {
        statusCode == 409 -> "管理员已初始化，请使用现有账号登录或联系管理员重置。"
        statusCode in 400..499 -> message.ifBlank { "请求不符合初始化策略" }
        else -> fallback
    }
    else -> fallback
}
