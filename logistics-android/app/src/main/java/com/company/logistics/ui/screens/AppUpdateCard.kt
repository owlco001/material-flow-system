package com.company.logistics.ui.screens

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.BuildConfig
import com.company.logistics.data.remote.AppUpdateChecker
import com.company.logistics.data.remote.AppUpdateInfo
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing
import kotlinx.coroutines.launch

/**
 * APP 更新卡片（放在「我的」页）。
 * 更新源 = 本 APP 配置的后端地址，调用 {baseUrl}/api/v1/app/updates/latest。
 */
@Composable
fun AppUpdateCard(
    checker: AppUpdateChecker,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var state by remember { mutableStateOf<UpdateUiState>(UpdateUiState.Idle) }
    var pendingInfo by remember { mutableStateOf<AppUpdateInfo?>(null) }
    var downloadId by remember { mutableStateOf<Long?>(null) }

    // 下载完成 → 触发安装
    DisposableEffect(downloadId) {
        val id = downloadId ?: return@DisposableEffect onDispose {}
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                val doneId = intent.getLongExtra(
                    android.app.DownloadManager.EXTRA_DOWNLOAD_ID, -1
                )
                if (doneId == id) {
                    checker.downloadedFile(id)?.let { file ->
                        state = UpdateUiState.Downloaded
                        checker.install(file)
                    } ?: run {
                        state = UpdateUiState.Error("下载完成但找不到文件，请到下载目录手动安装")
                    }
                }
            }
        }
        val filter = IntentFilter(android.app.DownloadManager.ACTION_DOWNLOAD_COMPLETE)
        if (Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(receiver, filter)
        }
        onDispose { context.unregisterReceiver(receiver) }
    }

    // 新版本确认弹窗
    pendingInfo?.let { info ->
        AlertDialog(
            onDismissRequest = { pendingInfo = null },
            title = { Text("发现新版本 v${info.versionName}") },
            text = {
                Column {
                    Text("当前版本：v${BuildConfig.VERSION_NAME}（${BuildConfig.VERSION_CODE}）")
                    VSpace(4.dp)
                    Text("最新版本：v${info.versionName}（${info.versionCode}）")
                    if (info.changelog.isNotBlank()) {
                        VSpace(4.dp)
                        Text("更新内容：${info.changelog}")
                    }
                    VSpace(4.dp)
                    Text(
                        "大小：${"%.1f".format(info.fileSize / 1048576.0)} MB",
                        color = LogisticsTheme.colors.textTertiary,
                        fontSize = 12.sp,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    pendingInfo = null
                    state = UpdateUiState.Downloading
                    try {
                        downloadId = checker.download(info)
                    } catch (e: Exception) {
                        state = UpdateUiState.Error("下载启动失败：${e.message}")
                    }
                }) { Text("立即更新") }
            },
            dismissButton = {
                TextButton(onClick = { pendingInfo = null }) { Text("稍后") }
            },
        )
    }

    AppCard(modifier = modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "APP 更新",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary,
                )
                VSpace(3.dp)
                val statusText = when (val s = state) {
                    is UpdateUiState.Idle ->
                        "当前版本 v${BuildConfig.VERSION_NAME}"
                    is UpdateUiState.Checking -> "正在检查更新…"
                    is UpdateUiState.UpToDate -> "已是最新版本（v${BuildConfig.VERSION_NAME}）"
                    is UpdateUiState.Downloading -> "正在下载更新包…"
                    is UpdateUiState.Downloaded -> "下载完成，正在安装…"
                    is UpdateUiState.Error -> s.message
                }
                Text(
                    statusText,
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                )
            }
            SecondaryButton(
                text = "检查更新",
                onClick = {
                    if (state is UpdateUiState.Checking || state is UpdateUiState.Downloading) return@SecondaryButton
                    state = UpdateUiState.Checking
                    scope.launch {
                        val info = checker.check()
                        if (info != null) {
                            state = UpdateUiState.Idle
                            pendingInfo = info
                        } else {
                            state = UpdateUiState.UpToDate
                        }
                    }
                },
                modifier = Modifier.width(96.dp),
            )
        }
    }
}

private sealed interface UpdateUiState {
    data object Idle : UpdateUiState
    data object Checking : UpdateUiState
    data object UpToDate : UpdateUiState
    data object Downloading : UpdateUiState
    data object Downloaded : UpdateUiState
    data class Error(val message: String) : UpdateUiState
}
