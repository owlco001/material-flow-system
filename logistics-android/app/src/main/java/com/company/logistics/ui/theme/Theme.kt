package com.company.logistics.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * 物流流转系统主题。
 *
 * 设计目标：仓库 / 车间现场作业场景 —— 强光、戴手套、单手操作。
 * 因此：
 *  - 浅色为主（避免大面积深色在强光下反光），同时提供深色模式支持夜间作业；
 *  - 主操作尺寸放大、对比度拉高；
 *  - 状态一律「颜色 + 图标 + 文字」三重表达。
 */
private val LightColors = lightColorScheme(
    primary = LogisticsColors.Primary,
    onPrimary = Color.White,
    primaryContainer = LogisticsColors.PrimaryLight,
    onPrimaryContainer = LogisticsColors.PrimaryDark,

    secondary = LogisticsColors.Neutral,
    onSecondary = Color.White,

    background = LogisticsColors.BgPage,
    onBackground = LogisticsColors.TextPrimary,

    surface = LogisticsColors.BgCard,
    onSurface = LogisticsColors.TextPrimary,
    surfaceVariant = LogisticsColors.BgPage,
    onSurfaceVariant = LogisticsColors.TextSecondary,

    outline = LogisticsColors.Border,
    outlineVariant = LogisticsColors.Border,

    error = LogisticsColors.Danger,
    onError = Color.White
)

private val DarkColors = darkColorScheme(
    primary = LogisticsColors.Info,
    onPrimary = Color.White,
    primaryContainer = LogisticsColors.PrimaryDark,
    onPrimaryContainer = Color.White,

    secondary = LogisticsColors.Neutral,
    onSecondary = Color.White,

    background = LogisticsColors.DarkBgPage,
    onBackground = LogisticsColors.DarkTextPrimary,

    surface = LogisticsColors.DarkBgCard,
    onSurface = LogisticsColors.DarkTextPrimary,
    surfaceVariant = LogisticsColors.DarkBgPage,
    onSurfaceVariant = LogisticsColors.DarkTextSecondary,

    outline = LogisticsColors.DarkBorder,
    outlineVariant = LogisticsColors.DarkBorder,

    error = LogisticsColors.Danger,
    onError = Color.White
)

/**
 * 扩展色彩槽位：承载 Material3 ColorScheme 未覆盖的语义色。
 * 通过 [LocalLogisticsColors] 提供，业务组件用 [LogisticsTheme.colors] 读取。
 */
data class LogisticsColorTokens(
    val primary: Color,
    val primaryContainer: Color,
    val danger: Color,
    val success: Color,
    val warning: Color,
    val info: Color,
    val purple: Color,
    val textPrimary: Color,
    val textSecondary: Color,
    val textTertiary: Color,
    val textOnDark: Color,
    val border: Color,
    val cardBackground: Color,
    val pageBackground: Color
)

private val LightTokens = LogisticsColorTokens(
    primary = LogisticsColors.Primary,
    primaryContainer = LogisticsColors.PrimaryLight,
    danger = LogisticsColors.Danger,
    success = LogisticsColors.Success,
    warning = LogisticsColors.Warning,
    info = LogisticsColors.Info,
    purple = LogisticsColors.Purple,
    textPrimary = LogisticsColors.TextPrimary,
    textSecondary = LogisticsColors.TextSecondary,
    textTertiary = LogisticsColors.TextTertiary,
    textOnDark = Color.White,
    border = LogisticsColors.Border,
    cardBackground = LogisticsColors.BgCard,
    pageBackground = LogisticsColors.BgPage
)

private val DarkTokens = LogisticsColorTokens(
    primary = LogisticsColors.Info,
    primaryContainer = LogisticsColors.PrimaryDark,
    danger = LogisticsColors.Danger,
    success = LogisticsColors.Success,
    warning = LogisticsColors.Warning,
    info = LogisticsColors.Info,
    purple = LogisticsColors.Purple,
    textPrimary = LogisticsColors.DarkTextPrimary,
    textSecondary = LogisticsColors.DarkTextSecondary,
    textTertiary = LogisticsColors.DarkTextTertiary,
    textOnDark = Color.White,
    border = LogisticsColors.DarkBorder,
    cardBackground = LogisticsColors.DarkBgCard,
    pageBackground = LogisticsColors.DarkBgPage
)

private val LocalLogisticsColors = staticCompositionLocalOf { LightTokens }

object LogisticsTheme {
    /** 扩展语义色访问入口：LogisticsTheme.colors.success */
    val colors: LogisticsColorTokens
        @Composable @ReadOnlyComposable get() = LocalLogisticsColors.current
}

@Composable
fun LogisticsTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val scheme = if (darkTheme) DarkColors else LightColors
    val tokens = if (darkTheme) DarkTokens else LightTokens

    CompositionLocalProvider(LocalLogisticsColors provides tokens) {
        MaterialTheme(
            colorScheme = scheme,
            typography = LogisticsTypography,
            shapes = LogisticsShapes,
            content = content
        )
    }
}
