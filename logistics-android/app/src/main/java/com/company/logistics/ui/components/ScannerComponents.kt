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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
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

/**
 * 扫码作业相关的项目自有矢量图标。
 *
 * 为什么不用 emoji：emoji 在不同厂商 ROM 上字形差异极大、无法跟随主题着色，
 * 也无法保证描边粗细一致，作为功能图标不合格。此处按 Material 官方
 * FlashOn / FlashOff 的 24dp 网格重建为 ImageVector，不引入新的图标依赖
 * （当前仅依赖 material-icons-core，其中不含手电筒图标）。
 *
 * 使用规范：统一 24dp 视口，调用处按 16 / 20 / 24px 三档取用。
 */
object ScannerIcons {

    /** 手电筒开：灯身 + 灯头 + 光锥。 */
    val FlashOn: ImageVector by lazy {
        ImageVector.Builder(
            name = "FlashOn",
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            path(fill = SolidColor(Color.Black)) {
                moveTo(7f, 2f)
                horizontalLineToRelative(10f)
                verticalLineToRelative(3f)
                horizontalLineToRelative(-10f)
                close()
                moveTo(8f, 6f)
                horizontalLineToRelative(8f)
                lineToRelative(-1f, 4f)
                horizontalLineToRelative(-6f)
                close()
                moveTo(9f, 11f)
                horizontalLineToRelative(6f)
                lineToRelative(3f, 11f)
                horizontalLineToRelative(-12f)
                close()
            }
        }.build()
    }

    /** 手电筒关：仅灯身与灯头，无光锥。 */
    val FlashOff: ImageVector by lazy {
        ImageVector.Builder(
            name = "FlashOff",
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            path(fill = SolidColor(Color.Black)) {
                moveTo(7f, 2f)
                horizontalLineToRelative(10f)
                verticalLineToRelative(3f)
                horizontalLineToRelative(-10f)
                close()
                moveTo(8f, 6f)
                horizontalLineToRelative(8f)
                lineToRelative(-1f, 4f)
                horizontalLineToRelative(-6f)
                close()
                moveTo(9f, 11f)
                horizontalLineToRelative(6f)
                verticalLineToRelative(4f)
                horizontalLineToRelative(-6f)
                close()
            }
        }.build()
    }
}

/**
 * 业务状态与导航用的项目自有矢量图标族。
 *
 * 与 [ScannerIcons] 同构：24dp 视口、单条 [path] 填充、无外部图标依赖。
 * 存在的意义是替掉历史上散落在各屏幕里的 emoji 与符号字符
 * （`✓` `⚠` `☰` `○` `▶` 等）——emoji 字形随厂商 ROM 漂移、无法稳定跟随
 * 主题着色、描边粗细不可控，作为功能图标不合格。
 *
 * 使用规范：
 *  - 尺寸按 16px（行内）/ 20px（按钮内）/ 24px（独立）三档取用；
 *  - 需要跟随语义色时用 `colorFilter = ColorFilter.tint(color)`，
 *    图标本体以 [Color.Black] 绘制，tint 会整体替换颜色。
 */
object LogisticsIcons {

    private fun icon(name: String, block: PathBuilder.() -> Unit): ImageVector =
        ImageVector.Builder(
            name = name,
            defaultWidth = 24.dp,
            defaultHeight = 24.dp,
            viewportWidth = 24f,
            viewportHeight = 24f,
        ).apply {
            path(fill = SolidColor(Color.Black)) { block() }
        }.build()

    /** 勾选：完成 / 已执行。 */
    val Check: ImageVector by lazy {
        icon("Check") {
            moveTo(9.55f, 17.6f)
            lineTo(4f, 12.05f)
            lineTo(5.4f, 10.65f)
            lineTo(9.55f, 14.8f)
            lineTo(18.6f, 5.75f)
            lineTo(20f, 7.15f)
            close()
        }
    }

    /** 告警：三角外框 + 感叹号。用于错误态与设备异常。 */
    val Alert: ImageVector by lazy {
        icon("Alert") {
            // 三角外框
            moveTo(12f, 2.5f)
            lineTo(1f, 21f)
            horizontalLineToRelative(22f)
            close()
            // 挖空内三角
            moveTo(12f, 7.2f)
            lineTo(5.6f, 18.4f)
            horizontalLineToRelative(12.8f)
            close()
            // 感叹号竖条
            moveTo(11f, 10.2f)
            horizontalLineToRelative(2f)
            verticalLineToRelative(4.6f)
            horizontalLineToRelative(-2f)
            close()
            // 感叹号圆点
            moveTo(11f, 16.2f)
            horizontalLineToRelative(2f)
            verticalLineToRelative(1.6f)
            horizontalLineToRelative(-2f)
            close()
        }
    }

    /** 空心圆：未开始 / 未达成。 */
    val CircleOutline: ImageVector by lazy {
        icon("CircleOutline") {
            // 外圆
            moveTo(12f, 2f)
            curveTo(6.48f, 2f, 2f, 6.48f, 2f, 12f)
            curveToRelative(0f, 5.52f, 4.48f, 10f, 10f, 10f)
            curveToRelative(5.52f, 0f, 10f, -4.48f, 10f, -10f)
            curveTo(22f, 6.48f, 17.52f, 2f, 12f, 2f)
            close()
            // 挖空内圆（形成 2px 描边）
            moveTo(12f, 4f)
            curveToRelative(4.42f, 0f, 8f, 3.58f, 8f, 8f)
            curveToRelative(0f, 4.42f, -3.58f, 8f, -8f, 8f)
            curveToRelative(-4.42f, 0f, -8f, -3.58f, -8f, -8f)
            curveTo(4f, 7.58f, 7.58f, 4f, 12f, 4f)
            close()
        }
    }

    /** 实心三角：进行中 / 播放。 */
    val Play: ImageVector by lazy {
        icon("Play") {
            moveTo(7f, 4f)
            lineTo(20f, 12f)
            lineTo(7f, 20f)
            close()
        }
    }

    /** 列表：订单 / 明细入口。 */
    val List: ImageVector by lazy {
        icon("List") {
            moveTo(3f, 5.5f)
            horizontalLineToRelative(3f)
            verticalLineToRelative(3f)
            horizontalLineToRelative(-3f)
            close()
            moveTo(8f, 6f)
            horizontalLineToRelative(13f)
            verticalLineToRelative(2f)
            horizontalLineToRelative(-13f)
            close()
            moveTo(3f, 10.5f)
            horizontalLineToRelative(3f)
            verticalLineToRelative(3f)
            horizontalLineToRelative(-3f)
            close()
            moveTo(8f, 11f)
            horizontalLineToRelative(13f)
            verticalLineToRelative(2f)
            horizontalLineToRelative(-13f)
            close()
            moveTo(3f, 15.5f)
            horizontalLineToRelative(3f)
            verticalLineToRelative(3f)
            horizontalLineToRelative(-3f)
            close()
            moveTo(8f, 16f)
            horizontalLineToRelative(13f)
            verticalLineToRelative(2f)
            horizontalLineToRelative(-13f)
            close()
        }
    }

    /** 感叹号：单点异常标记（非三角告警）。 */
    val Exclamation: ImageVector by lazy {
        icon("Exclamation") {
            moveTo(10.8f, 3f)
            horizontalLineToRelative(2.4f)
            verticalLineToRelative(11f)
            horizontalLineToRelative(-2.4f)
            close()
            moveTo(10.8f, 16.5f)
            horizontalLineToRelative(2.4f)
            verticalLineToRelative(2.5f)
            horizontalLineToRelative(-2.4f)
            close()
        }
    }

    /**
     * 把历史遗留的符号字符串翻译成矢量图标。
     *
     * 存在的理由：数据层（`Models.kt` / `WorkspaceScreen.kt` 的状态映射）
     * 把状态映射成了 `String` 符号，直接当图标渲染。为了不破坏数据契约
     * （`WorkspaceApiParserTest` 等断言依赖这些字符串），这里在**渲染层**
     * 做一次翻译，数据层原样不动。
     *
     * 返回 `null` 表示该字符串不属于图标语义（例如合规的几何字符 `●`、
     * 普通标点 `!`），调用方应回退到文本渲染。
     */
    fun fromSymbol(symbol: String?): ImageVector? = when (symbol) {
        "✓" -> Check
        "⚠" -> Alert
        "○" -> CircleOutline
        "▶" -> Play
        "☰" -> List
        else -> null
    }
}
