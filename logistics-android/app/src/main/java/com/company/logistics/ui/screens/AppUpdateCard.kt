package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.BuildConfig
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.ConfirmDialog
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.update.AppUpdateChecker
import com.company.logistics.update.AppUpdatePolicy
import com.company.logistics.update.UpdateCheckResult
import kotlinx.coroutines.launch

/**
 * 「我的」页的检查更新卡片：进入页面自动检查一次，也可手动点。
 * 发现新版本时弹窗，确认后用系统浏览器下载 APK，下载完成后点通知安装。
 */
@Composable
fun AppUpdateCard(endpointUrl: String, endpointConfigured: Boolean) {
    val scope = rememberCoroutineScope()
    val uriHandler = LocalUriHandler.current
    var checking by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<UpdateCheckResult?>(null) }
    var showDialog by remember { mutableStateOf(false) }

    fun runCheck() {
        if (checking) return
        checking = true
        scope.launch {
            val r = AppUpdateChecker.check(endpointUrl, BuildConfig.VERSION_CODE)
            result = r
            checking = false
            if (r is UpdateCheckResult.Available) showDialog = true
        }
    }

    LaunchedEffect(endpointUrl, endpointConfigured) {
        if (endpointConfigured) runCheck()
    }

    val available = result as? UpdateCheckResult.Available
    if (showDialog && available != null) {
        val rel = available.release
        val size = AppUpdatePolicy.formatSize(rel.sizeBytes)
        ConfirmDialog(
            title = "发现新版本 ${rel.versionName}",
            message = buildString {
                append("当前版本 ${BuildConfig.VERSION_NAME}")
                if (size.isNotEmpty()) append("，安装包 $size")
                append("。")
                if (rel.notes.isNotBlank()) append("\n\n").append(rel.notes)
                append("\n\n下载完成后，点通知栏里的安装包完成更新。")
            },
            confirmText = "下载更新",
            dismissText = if (rel.force) "稍后（此版本为必需更新）" else "稍后",
            onConfirm = {
                showDialog = false
                runCatching { uriHandler.openUri(rel.downloadUrl) }
            },
            onDismiss = { showDialog = false },
        )
    }

    val status = when (val r = result) {
        null -> if (checking) "正在检查…" else "当前版本 ${BuildConfig.VERSION_NAME}"
        is UpdateCheckResult.Available -> "有新版本 ${r.release.versionName} 可更新"
        UpdateCheckResult.UpToDate -> "已是最新版本 ${BuildConfig.VERSION_NAME}"
        UpdateCheckResult.NoRelease -> "当前版本 ${BuildConfig.VERSION_NAME} · 服务端暂无发布"
        is UpdateCheckResult.Failed -> "检查失败：${r.message}"
    }
    val accent = when (result) {
        is UpdateCheckResult.Available -> LogisticsTheme.colors.warning
        is UpdateCheckResult.Failed -> LogisticsTheme.colors.danger
        else -> null
    }

    AppCard(accentColor = accent) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "检查更新",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold,
                    color = LogisticsTheme.colors.textPrimary
                )
                VSpace(3.dp)
                Text(status, fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary)
            }
            SecondaryButton(
                text = when {
                    checking -> "检查中"
                    available != null -> "更新"
                    else -> "检查"
                },
                enabled = endpointConfigured && !checking,
                onClick = { if (available != null) showDialog = true else runCheck() },
                modifier = Modifier.width(96.dp)
            )
        }
    }
}
