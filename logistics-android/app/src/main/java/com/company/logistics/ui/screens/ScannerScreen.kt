package com.company.logistics.ui.screens

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.view.PreviewView
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.company.logistics.domain.ScannerUiState
import com.company.logistics.model.ScanResult
import com.company.logistics.model.ScanType
import com.company.logistics.model.UserRole
import com.company.logistics.ui.ScannerViewModel
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.ScannerViewfinder
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 扫码作业页 —— 现场核心界面。
 *
 * 对齐《Android摄像头扫码实现规格》第 7 节页面交互要求：
 *  - 摄像头预览 + 固定取景框（FILL_CENTER，不因状态变化跳动）
 *  - 闪光灯开关（不支持时禁用并说明原因）
 *  - 手动输入入口（相机不可用时的降级路径）
 *  - 四态状态：准备扫码 / 识别中 / 查询中 / 识别失败
 *  - 权限拒绝后的设置入口
 *  - 识别成功后的结果确认，禁止连续误触发
 *
 * 安全：本页不显示 access token、完整请求头或服务端堆栈。
 */
@Composable
fun ScannerScreen(
    viewModel: ScannerViewModel,
    role: UserRole,
    pendingCount: Int,
    syncing: Boolean,
    onResolved: (ScanResult) -> Unit,
    onOpenQueue: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    val uiState by viewModel.uiState.collectAsState()
    val cameraBound by viewModel.cameraBound.collectAsState()
    val torchOn by viewModel.torchOn.collectAsState()
    val torchAvailable by viewModel.torchAvailable.collectAsState()
    val cameraError by viewModel.cameraError.collectAsState()
    val manualVisible by viewModel.manualInputVisible.collectAsState()

    var manualInput by remember { mutableStateOf("") }
    var hasPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
        )
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        hasPermission = granted
        val permanentlyDenied = !granted && !shouldShowCameraRationale(context)
        viewModel.onPermissionResult(granted, permanentlyDenied)
    }

    // 首次进入即请求权限；已授权则直接启动
    LaunchedEffect(hasPermission) {
        if (hasPermission) {
            viewModel.onPermissionResult(granted = true, permanentlyDenied = false)
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    // 页面离开时释放相机（onStop / 切页都不应继续占用）
    DisposableEffect(lifecycleOwner) {
        onDispose { viewModel.stopScanning() }
    }

    // 解析成功后回传结果，由外层决定跳转
    LaunchedEffect(uiState) {
        val s = uiState
        if (s is ScannerUiState.Resolved) {
            onResolved(s.resolution)
            viewModel.onScanConsumed()
        }
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        Spacer(Modifier.height(Spacing.sm))

        // ---------- 取景区 ----------
        val state = uiState
        when {
            state is ScannerUiState.PermissionDenied -> {
                PermissionDeniedPanel(
                    permanentlyDenied = state.permanentlyDenied,
                    onRetry = { permissionLauncher.launch(Manifest.permission.CAMERA) },
                    onOpenSettings = { openAppSettings(context) },
                )
            }

            hasPermission && cameraBound -> {
                CameraPreviewPanel(
                    lifecycleOwner = lifecycleOwner,
                    previewFactory = { previewView ->
                        viewModel.startScanning(lifecycleOwner, previewView)
                        previewView
                    },
                    torchOn = torchOn,
                    torchAvailable = torchAvailable,
                    onToggleTorch = { viewModel.toggleTorch() },
                    statusText = statusTextOf(state),
                    statusColor = rememberStatusColorOf(state),
                )
            }

            else -> {
                // 权限已授予但相机尚未就绪 / 相机不可用
                ScannerViewfinder(
                    scanTypeLabel = if (cameraError != null) cameraError!! else "正在启动摄像头…",
                    onTap = { viewModel.toggleManualInput() },
                )
            }
        }

        Spacer(Modifier.height(Spacing.md))

        // ---------- 状态条 ----------
        ScanStatusBar(state)

        Spacer(Modifier.height(Spacing.md))

        // ---------- 操作区 ----------
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            SecondaryButton(
                text = if (manualVisible) "收起手动输入" else "手动输入",
                onClick = { viewModel.toggleManualInput() },
                modifier = Modifier.weight(1f),
            )
            if (state is ScannerUiState.Error && state.retryable) {
                SecondaryButton(
                    text = "重试",
                    onClick = { viewModel.onScanConsumed() },
                    modifier = Modifier.weight(1f),
                )
            }
        }

        if (manualVisible) {
            Spacer(Modifier.height(Spacing.md))
            SectionTitle("手动输入条码")
            Spacer(Modifier.height(Spacing.sm))
            // 出错时保留已输入内容，只高亮提示，绝不清空（规格第 7 节）
            ManualInputField(
                value = manualInput,
                onValueChange = { manualInput = it },
                onConfirm = {
                    if (manualInput.isNotBlank()) {
                        viewModel.onManualInput(manualInput.trim())
                    }
                },
                loading = state is ScannerUiState.Processing,
                isError = state is ScannerUiState.Error,
            )
        }

        Spacer(Modifier.height(Spacing.lg))

        // ---------- 离线队列入口 ----------
        AppCard(modifier = Modifier.clickable { onOpenQueue() }) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(30.dp)
                        .background(LogisticsTheme.colors.warning, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        if (pendingCount > 0) "$pendingCount" else "0",
                        color = Color.White,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Spacer(Modifier.width(Spacing.md))
                Column(Modifier.weight(1f)) {
                    Text(
                        if (pendingCount > 0) "待同步记录" else "离线队列",
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        color = LogisticsTheme.colors.textPrimary,
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        when {
                            syncing -> "正在同步…"
                            pendingCount > 0 -> "$pendingCount 条记录等待上传，点击查看"
                            else -> "当前无待同步记录"
                        },
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary,
                    )
                }
                Text("›", fontSize = 18.sp, color = LogisticsTheme.colors.textTertiary)
            }
        }

        Spacer(Modifier.height(Spacing.md))

        AppCard {
            Text("当前角色", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
            Spacer(Modifier.height(4.dp))
            Text(
                role.label,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                buildString {
                    append("可提交入库/出库申请")
                    if (role.canApprove) append(" · 可审批")
                    if (role.canAdmin) append(" · 可管理用户与审计")
                },
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary,
            )
        }

        Spacer(Modifier.height(Spacing.xxl))
    }
}

// ==================== 相机预览 ====================

@Composable
private fun CameraPreviewPanel(
    lifecycleOwner: androidx.lifecycle.LifecycleOwner,
    previewFactory: (PreviewView) -> PreviewView,
    torchOn: Boolean,
    torchAvailable: Boolean,
    onToggleTorch: () -> Unit,
    statusText: String,
    statusColor: Color,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(1f)
            .clip(RoundedCornerShape(Dimens.CardCorner))
            .background(Color.Black),
    ) {
        AndroidView(
            factory = { ctx ->
                PreviewView(ctx).apply {
                    // FILL_CENTER：取景区域固定，不因文字或状态变化跳动（规格 5 节）
                    scaleType = PreviewView.ScaleType.FILL_CENTER
                    implementationMode = PreviewView.ImplementationMode.COMPATIBLE
                    previewFactory(this)
                }
            },
            modifier = Modifier.fillMaxSize(),
        )

        // 固定取景框
        ScanFrameOverlay()

        // 顶部：状态 + 闪光灯
        Row(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .fillMaxWidth()
                .padding(Spacing.md),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(Dimens.PillCorner))
                    .background(statusColor.copy(alpha = 0.92f))
                    .padding(horizontal = 10.dp, vertical = 5.dp),
            ) {
                Text(
                    statusText,
                    color = Color.White,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Spacer(Modifier.weight(1f))
            TorchButton(
                enabled = torchAvailable,
                on = torchOn,
                onClick = onToggleTorch,
            )
        }
    }
}

/** 固定取景框：四角高亮 + 扫描线动效 */
@Composable
private fun ScanFrameOverlay() {
    val accent = LogisticsTheme.colors.primary
    val reduceMotion = LocalAccessibilityManager.current
    val transition = rememberInfiniteTransition(label = "scanline")
    val rawProgress by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 2200),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "scanlineProgress",
    )
    // 尊重系统「减少动画」：关闭扫描线位移，仅保留静态取景框
    val progress = if (reduceMotion) 0.5f else rawProgress

    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Box(
            modifier = Modifier
                .fillMaxSize(0.68f)
                .border(2.dp, Color.White.copy(alpha = 0.85f), RoundedCornerShape(18.dp)),
        ) {
            // 扫描线
            Box(
                modifier = Modifier
                    .fillMaxWidth(0.94f)
                    .height(2.dp)
                    .align(Alignment.Center)
                    .padding(top = (140 * progress).dp)
                    .background(accent.copy(alpha = 0.9f), RoundedCornerShape(1.dp)),
            )
        }
    }
}

/** 系统是否开启了「减少动画」（无障碍设置） */
private val LocalAccessibilityManager: androidx.compose.runtime.ProvidableCompositionLocal<Boolean> =
    androidx.compose.runtime.staticCompositionLocalOf { false }

@Composable
private fun TorchButton(enabled: Boolean, on: Boolean, onClick: () -> Unit) {
    val colors = LogisticsTheme.colors
    val bg = when {
        !enabled -> Color.White.copy(alpha = 0.25f)
        on -> colors.primary
        else -> Color.White.copy(alpha = 0.85f)
    }
    Box(
        modifier = Modifier
            .size(Dimens.MinTouchTarget)
            .clip(CircleShape)
            .background(bg)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = if (on) "🔦" else "💡",
            fontSize = 18.sp,
        )
    }
}

// ==================== 权限拒绝面板 ====================

@Composable
private fun PermissionDeniedPanel(
    permanentlyDenied: Boolean,
    onRetry: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    val colors = LogisticsTheme.colors
    AppCard(accentColor = colors.warning) {
        Text(
            if (permanentlyDenied) "摄像头权限已被永久拒绝" else "需要摄像头权限",
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            color = colors.textPrimary,
        )
        Spacer(Modifier.height(Spacing.sm))
        Text(
            if (permanentlyDenied) {
                "请到系统设置中开启「相机」权限，或直接使用下方的手动输入继续作业。"
            } else {
                "扫码需要访问摄像头。你也可以手动输入条码继续作业。"
            },
            fontSize = 13.sp,
            color = colors.textSecondary,
            lineHeight = 20.sp,
        )
        Spacer(Modifier.height(Spacing.md))
        if (permanentlyDenied) {
            PrimaryButton(text = "前往系统设置", onClick = onOpenSettings)
            Spacer(Modifier.height(Spacing.sm))
        } else {
            PrimaryButton(text = "重新授权", onClick = onRetry)
            Spacer(Modifier.height(Spacing.sm))
        }
        TextButton(onClick = onRetry, modifier = Modifier.fillMaxWidth()) {
            Text("我再试一次", fontSize = 13.sp, color = colors.primary)
        }
    }
}

// ==================== 状态条 ====================

/** 四态展示：准备扫码 / 识别中 / 查询中 / 识别失败（规格第 7 节） */
@Composable
private fun ScanStatusBar(state: ScannerUiState) {
    val colors = LogisticsTheme.colors
    when (state) {
        is ScannerUiState.Ready -> StatusLine("准备扫码", colors.textTertiary, loading = false)
        is ScannerUiState.Processing -> StatusLine("查询中…", colors.primary, loading = true)
        is ScannerUiState.Resolved -> StatusLine("识别成功", colors.success, loading = false)
        is ScannerUiState.Error -> StatusLine("识别失败：${state.message}", colors.danger, loading = false)
        is ScannerUiState.PermissionDenied -> StatusLine("未授权摄像头", colors.warning, loading = false)
        is ScannerUiState.PermissionRequired -> StatusLine("等待摄像头权限…", colors.textTertiary, loading = true)
    }
}

@Composable
private fun StatusLine(text: String, color: Color, loading: Boolean) {
    AppCard(accentColor = color) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 语义色 + 文字双通道表达，避免只靠颜色传达状态（无障碍）
            Box(
                modifier = Modifier
                    .size(if (loading) 10.dp else 8.dp)
                    .background(color, CircleShape),
            )
            Spacer(Modifier.width(Spacing.sm))
            Text(
                text,
                modifier = Modifier.weight(1f),
                fontSize = 14.sp,
                fontWeight = FontWeight.Medium,
                color = LogisticsTheme.colors.textPrimary,
            )
            if (loading) {
                Text("···", fontSize = 14.sp, color = color, fontWeight = FontWeight.Bold)
            }
        }
    }
}

private fun statusTextOf(state: ScannerUiState): String = when (state) {
    is ScannerUiState.Processing -> "查询中"
    is ScannerUiState.Resolved -> "识别成功"
    is ScannerUiState.Error -> "识别失败"
    else -> "对准条码"
}

@Composable
private fun rememberStatusColorOf(state: ScannerUiState): Color {
    val colors = LogisticsTheme.colors
    return when (state) {
        is ScannerUiState.Processing -> colors.primary
        is ScannerUiState.Resolved -> colors.success
        is ScannerUiState.Error -> colors.danger
        else -> colors.textSecondary
    }
}

// ==================== 手动输入 ====================

@Composable
private fun ManualInputField(
    value: String,
    onValueChange: (String) -> Unit,
    onConfirm: () -> Unit,
    loading: Boolean,
    isError: Boolean = false,
) {
    Column {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            enabled = !loading,
            isError = isError,
            shape = MaterialTheme.shapes.small,
            supportingText = if (isError) {
                { Text("未匹配到该条码，请核对后重试", fontSize = 12.sp) }
            } else null,
            placeholder = {
                Text(
                    "如 MTR-001 / SO202609120001 / A-01-03",
                    fontSize = 14.sp,
                    color = LogisticsTheme.colors.textTertiary,
                )
            },
            textStyle = androidx.compose.ui.text.TextStyle(
                fontFamily = LogisticsType.MonoFamily,
                fontSize = 15.sp,
            ),
        )
        Spacer(Modifier.height(Spacing.sm))
        PrimaryButton(
            text = "解析条码",
            onClick = onConfirm,
            enabled = value.isNotBlank(),
            loading = loading,
        )
    }
}

// ==================== 工具 ====================

private fun shouldShowCameraRationale(context: android.content.Context): Boolean {
    val activity = context as? android.app.Activity ?: return false
    return androidx.core.app.ActivityCompat.shouldShowRequestPermissionRationale(
        activity, Manifest.permission.CAMERA,
    )
}

private fun openAppSettings(context: android.content.Context) {
    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
        data = Uri.fromParts("package", context.packageName, null)
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    context.startActivity(intent)
}
