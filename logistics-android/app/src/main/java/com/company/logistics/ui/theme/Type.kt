package com.company.logistics.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/**
 * 字号阶梯（sp）—— 现场作业可读性优先。
 *
 * 硬约束：
 *  - 正文不小于 16sp；
 *  - 标题类不小于 14sp；
 *  - 12sp 为下限，仅用于角标等非关键辅助信息。
 *
 * 数字 / 料号必须使用等宽字体 [MonoFamily]，避免数字认读错误。
 * 注：使用系统等宽字族，避免额外引入字体资源增加包体积。
 */
object LogisticsType {
    val MonoFamily: FontFamily = FontFamily.Monospace
}

val LogisticsTypography = Typography(
    // display 32 / 700 —— 料号放大展示、扫码结果主标题
    displaySmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Bold,
        fontSize = 32.sp,
        lineHeight = 40.sp
    ),
    // h1 24 / 700 —— 页面标题
    headlineMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Bold,
        fontSize = 24.sp,
        lineHeight = 32.sp
    ),
    // h2 20 / 600 —— 卡片标题
    headlineSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 20.sp,
        lineHeight = 28.sp
    ),
    // 卡片标题替代尺寸 18 / 600
    titleLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 26.sp
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 24.sp
    ),
    // body 16 / 400 —— 正文最小尺寸
    bodyLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        lineHeight = 24.sp
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 22.sp
    ),
    // caption 14 / 400 —— 辅助说明
    bodySmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp
    ),
    // tiny 12 / 400 —— 角标 / 时间戳（下限）
    labelSmall = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 14.sp,
        lineHeight = 20.sp
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.Default,
        fontWeight = FontWeight.SemiBold,
        fontSize = 15.sp,
        lineHeight = 22.sp
    )
)

/** 料号专用文本样式：等宽 + 字间距，防止数字误读 */
val MaterialCodeStyle = TextStyle(
    fontFamily = LogisticsType.MonoFamily,
    fontWeight = FontWeight.Bold,
    fontSize = 20.sp,
    lineHeight = 28.sp,
    letterSpacing = 0.5.sp
)

/** 料号放大样式：扫码结果页主展示 */
val MaterialCodeDisplayStyle = TextStyle(
    fontFamily = LogisticsType.MonoFamily,
    fontWeight = FontWeight.Bold,
    fontSize = 26.sp,
    lineHeight = 34.sp,
    letterSpacing = 0.5.sp
)

/** 数字展示样式：数量等关键数值 */
val NumericStyle = TextStyle(
    fontFamily = LogisticsType.MonoFamily,
    fontWeight = FontWeight.Bold,
    fontSize = 19.sp,
    lineHeight = 26.sp
)
