package com.company.logistics.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing

/**
 * 扫码取景框 —— 现场核心交互组件。
 *
 * 设计要点：
 *  - 取景框 260dp 居中，四角高亮角标 + 扫描线动画，远距离也能快速对准；
 *  - 底部三个操作热区（手电筒 / 相册 / 手动输入）等分，热区 >= 64dp 便于戴手套操作；
 *  - 结果反馈采用「震动 + 音效 + 闪屏」多通道，噪音环境下视觉为主。
 */
@Composable
fun ScannerViewfinder(
    modifier: Modifier = Modifier,
    scanTypeLabel: String? = null,
    onTap: (() -> Unit)? = null,
    viewfinderSize: androidx.compose.ui.unit.Dp = 260.dp
) {
    val accent = MaterialTheme.colorScheme.primary
    val transition = rememberInfiniteTransition(label = "scanline")
    val progress by transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(2100, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "scanlineProgress"
    )

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = Color(0xFF0F1621)
    ) {
        Column(
            modifier = Modifier.padding(Spacing.xl),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                text = "对准条码，自动识别",
                color = Color.White,
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(Spacing.md))

            Box(
                modifier = Modifier
                    .size(viewfinderSize)
                    .clip(RoundedCornerShape(14.dp))
                    .background(Color(0x73000000))
                    .then(if (onTap != null) Modifier.clickable { onTap() } else Modifier),
                contentAlignment = Alignment.Center
            ) {
                // 四角角标
                Canvas(Modifier.matchParentSize()) {
                    val armLen = 34.dp.toPx()
                    val inset = 4.dp.toPx()
                    val stroke = 4.dp.toPx()
                    val w = size.width
                    val h = size.height

                    fun corner(x: Float, y: Float, dx: Float, dy: Float) {
                        drawLine(accent, Offset(x, y), Offset(x + dx * armLen, y), stroke, StrokeCap.Round)
                        drawLine(accent, Offset(x, y), Offset(x, y + dy * armLen), stroke, StrokeCap.Round)
                    }
                    corner(inset, inset, 1f, 1f)
                    corner(w - inset, inset, -1f, 1f)
                    corner(inset, h - inset, 1f, -1f)
                    corner(w - inset, h - inset, -1f, -1f)
                }

                // 条码示意（占位纹理，实际由相机预览填充）
                BarcodePlaceholder()

                // 扫描线
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 8.dp)
                ) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(3.dp)
                            .background(
                                color = accent,
                                shape = RoundedCornerShape(2.dp)
                            )
                    )
                }
                // 通过 Canvas 精确定位扫描线位置
                Canvas(Modifier.matchParentSize()) {
                    val y = 20.dp.toPx() + (size.height - 40.dp.toPx()) * progress
                    drawLine(
                        color = accent.copy(alpha = 0.85f),
                        start = Offset(10.dp.toPx(), y),
                        end = Offset(size.width - 10.dp.toPx(), y),
                        strokeWidth = 3.dp.toPx(),
                        cap = StrokeCap.Round
                    )
                    drawRect(
                        brush = androidx.compose.ui.graphics.Brush.verticalGradient(
                            listOf(accent.copy(alpha = 0.22f), Color.Transparent)
                        ),
                        topLeft = Offset(0f, (y - 22.dp.toPx()).coerceAtLeast(0f)),
                        size = androidx.compose.ui.geometry.Size(size.width, 22.dp.toPx())
                    )
                }
            }

            if (scanTypeLabel != null) {
                Spacer(Modifier.height(Spacing.md))
                Surface(
                    shape = PillShape,
                    color = Color(0x33F5A623)
                ) {
                    Text(
                        text = scanTypeLabel,
                        color = Color(0xFFF5C462),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 5.dp)
                    )
                }
            }
        }
    }
}

/** 条码纹理占位 */
@Composable
private fun BarcodePlaceholder() {
    Canvas(Modifier.size(width = 150.dp, height = 52.dp)) {
        val pattern = listOf(4f, 2f, 5f, 3f, 2f, 6f, 2f, 4f, 6f, 2f, 3f, 5f, 2f, 4f, 2f, 6f, 3f, 2f)
        var x = 0f
        var i = 0
        while (x < size.width) {
            val w = pattern[i % pattern.size].dp.toPx() * 0.25f
            drawRect(
                color = Color.White.copy(alpha = 0.88f),
                topLeft = Offset(x, if (i % 3 == 0) 0f else size.height * 0.12f),
                size = androidx.compose.ui.geometry.Size(w, size.height * (if (i % 3 == 0) 1f else 0.76f))
            )
            x += w + 2.5.dp.toPx()
            i++
        }
    }
}

/**
 * 扫码工具条：手电筒 / 相册识别 / 手动输入。
 * 三个热区等分，高度 64dp（戴手套可点）。
 */
@Composable
fun ScannerToolBar(
    onTorch: () -> Unit,
    onAlbum: () -> Unit,
    onManual: () -> Unit,
    modifier: Modifier = Modifier,
    torchEnabled: Boolean = false
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(Spacing.md)
    ) {
        ScannerToolButton("手电筒", if (torchEnabled) "开" else "关", onTorch, Modifier.weight(1f))
        ScannerToolButton("相册识别", "▣", onAlbum, Modifier.weight(1f))
        ScannerToolButton("手动输入", "⌨", onManual, Modifier.weight(1f))
    }
}

@Composable
private fun ScannerToolButton(
    label: String,
    symbol: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Surface(
        modifier = modifier
            .height(64.dp)
            .clip(RoundedCornerShape(14.dp))
            .clickable { onClick() },
        shape = RoundedCornerShape(14.dp),
        color = Color(0x1FFFFFFF)
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            Text(symbol, color = Color.White, fontSize = 19.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(3.dp))
            Text(label, color = Color.White.copy(alpha = 0.85f), fontSize = 11.sp)
        }
    }
}

/**
 * 离线状态条 —— 常驻顶部，提示待同步数量。
 * 契约强调服务端时间为审计唯一依据，离线操作需明确标识为「暂存」。
 */
@Composable
fun OfflineBanner(
    pendingCount: Int,
    syncing: Boolean,
    onTap: () -> Unit,
    modifier: Modifier = Modifier
) {
    val warning = LogisticsTheme.colors.warning
    Surface(
        modifier = modifier
            .fillMaxWidth()
            .clickable { onTap() },
        color = warning.copy(alpha = 0.16f)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("!", color = Color(0xFFB8791A), fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.width(Spacing.sm))
            Text(
                text = if (syncing) "同步中 · 剩余 $pendingCount 条"
                else "离线中 · $pendingCount 条待同步",
                color = Color(0xFFB8791A),
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.weight(1f))
            Text("查看 ›", color = Color(0xFFB8791A), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}
