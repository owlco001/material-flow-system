package com.company.logistics.ui.components

import android.graphics.Bitmap
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.graphicsLayer
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter

/**
 * 流转码展示 —— 审核通过后，相关角色在 APP 里展示此二维码，交接方扫码交接。
 * 二维码内容为流转单 document_no，与服务端 /api/v1/barcodes/transfer/{no} 一致，本地生成无需联网。
 *
 * 视觉：渐变头图 + 弹性入场 + 取景框角标，现场强光下易识别、易扫码。
 */
@Composable
fun FlowQrCodeDialog(
    documentNo: String,
    onDismiss: () -> Unit,
) {
    val bitmap = remember(documentNo) { generateQrBitmap(documentNo, 640) }

    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { shown = true }
    val scale by animateFloatAsState(
        targetValue = if (shown) 1f else 0.82f,
        animationSpec = spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = 320f),
        label = "qrDialogScale",
    )
    val alpha by animateFloatAsState(
        targetValue = if (shown) 1f else 0f,
        animationSpec = tween(180),
        label = "qrDialogAlpha",
    )

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            modifier = Modifier
                .padding(horizontal = 28.dp)
                .graphicsLayer(scaleX = scale, scaleY = scale, alpha = alpha),
            shape = RoundedCornerShape(28.dp),
            color = LogisticsTheme.colors.cardBackground,
            shadowElevation = 12.dp,
        ) {
            Column {
                // ---- 渐变头图 ----
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(
                            Brush.linearGradient(
                                colors = listOf(LogisticsColors.BrandNavy, LogisticsColors.BrandNavyDark),
                            ),
                        )
                        .padding(horizontal = 20.dp, vertical = 18.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        // 迷你二维码装饰
                        MiniQrGlyph(modifier = Modifier.size(44.dp))
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                "流转码",
                                fontSize = 19.sp,
                                fontWeight = FontWeight.Bold,
                                color = Color.White,
                            )
                            Spacer(Modifier.height(3.dp))
                            Text(
                                "审核已通过 · 出示给交接方扫码",
                                fontSize = 12.sp,
                                color = Color.White.copy(alpha = 0.78f),
                            )
                        }
                        TextButton(onClick = onDismiss) {
                            Text("关闭", fontSize = 14.sp, color = Color.White)
                        }
                    }
                }

                // ---- 二维码区 ----
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 24.dp, vertical = 22.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Surface(
                            shape = RoundedCornerShape(20.dp),
                            color = Color.White,
                            shadowElevation = 6.dp,
                        ) {
                            if (bitmap != null) {
                                Image(
                                    bitmap = bitmap.asImageBitmap(),
                                    contentDescription = "流转码二维码",
                                    modifier = Modifier
                                        .padding(18.dp)
                                        .size(216.dp),
                                )
                            } else {
                                Text(
                                    "二维码生成失败",
                                    fontSize = 13.sp,
                                    color = MaterialTheme.colorScheme.error,
                                    modifier = Modifier.padding(48.dp),
                                )
                            }
                        }
                        if (bitmap != null) {
                            ScanCorners(modifier = Modifier.matchParentSize())
                        }
                    }

                    Spacer(Modifier.height(18.dp))

                    // 单号胶囊
                    Surface(
                        shape = RoundedCornerShape(12.dp),
                        color = LogisticsTheme.colors.primaryContainer,
                    ) {
                        Text(
                            documentNo,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.SemiBold,
                            fontFamily = LogisticsType.MonoFamily,
                            color = LogisticsColors.PrimaryDark,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        )
                    }

                    Spacer(Modifier.height(12.dp))
                    Text(
                        "交接方用 APP 扫码，勾选物料后确认交接",
                        fontSize = 12.sp,
                        color = LogisticsTheme.colors.textSecondary,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        "交接记录自动留痕，可在流转单详情查看",
                        fontSize = 11.sp,
                        color = LogisticsTheme.colors.textTertiary,
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }
    }
}

/** 头图上的迷你二维码装饰：三个定位点 + 若干数据模块。 */
@Composable
private fun MiniQrGlyph(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val white = Color.White
        val n = 7
        val cell = size.minDimension / n
        // 半透明底
        drawRoundRect(
            white.copy(alpha = 0.16f),
            cornerRadius = CornerRadius(10.dp.toPx()),
        )
        fun module(cx: Int, cy: Int) {
            drawRect(white, Offset(cx * cell, cy * cell), Size(cell, cell))
        }
        fun finder(fx: Int, fy: Int) {
            // 定位点：外圈描边 + 实心内芯
            drawRect(
                white,
                Offset(fx * cell, fy * cell),
                Size(cell * 3, cell * 3),
                style = Stroke(width = cell * 0.55f),
            )
            drawRect(white, Offset((fx + 1) * cell, (fy + 1) * cell), Size(cell, cell))
        }
        finder(0, 0)
        finder(4, 0)
        finder(0, 4)
        module(4, 4)
        module(5, 5)
        module(3, 5)
        module(5, 3)
        module(3, 4)
    }
}

/** 二维码四角取景框角标。 */
@Composable
private fun ScanCorners(modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
        val c = LogisticsColors.Primary
        val len = 30.dp.toPx()
        val stroke = 5.dp.toPx()
        val pad = 6.dp.toPx()
        val w = size.width
        val h = size.height
        fun corner(x0: Float, y0: Float, dx: Float, dy: Float) {
            drawLine(c, Offset(x0, y0), Offset(x0 + dx * len, y0), strokeWidth = stroke)
            drawLine(c, Offset(x0, y0), Offset(x0, y0 + dy * len), strokeWidth = stroke)
        }
        corner(pad, pad, 1f, 1f)
        corner(w - pad, pad, -1f, 1f)
        corner(pad, h - pad, 1f, -1f)
        corner(w - pad, h - pad, -1f, -1f)
    }
}

private fun generateQrBitmap(content: String, sizePx: Int): Bitmap? {
    return try {
        val matrix = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, sizePx, sizePx)
        val bitmap = Bitmap.createBitmap(sizePx, sizePx, Bitmap.Config.RGB_565)
        for (x in 0 until sizePx) {
            for (y in 0 until sizePx) {
                bitmap.setPixel(x, y, if (matrix.get(x, y)) 0xFF000000.toInt() else 0xFFFFFFFF.toInt())
            }
        }
        bitmap
    } catch (_: Exception) {
        null
    }
}
