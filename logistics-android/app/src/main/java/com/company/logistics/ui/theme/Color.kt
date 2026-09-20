package com.company.logistics.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * 色彩系统 —— 现场作业场景优化（强光 / 戴手套 / 单手）
 *
 * 设计原则：
 *  1. 状态识别优先靠「颜色 + 图标 + 文字」三重通道，不做纯色区分（色盲友好）。
 *  2. 正文对比度 >= 7:1（AAA），次要文字 >= 4.5:1（AA）。
 *  3. 现场强光下避免大面积深色，主色采用高饱和工业蓝。
 *
 * 说明：MaterialStatusColors 中的色值对齐接口契约中约定的语义
 *      （缺货=红 / 到货=黄 / 在库=绿），仅对色相做可读性微调，
 *      客户端不得只依赖颜色判断状态。
 */
object LogisticsColors {

    // ========== 品牌主色 ==========
    /** 工业蓝：主按钮 / 选中态 / 品牌标识。白底对比度 5.9:1（AA） */
    val Primary = Color(0xFF0E5FD8)
    /** 按压态 / 深色背景。白底对比度 8.4:1（AAA） */
    val PrimaryDark = Color(0xFF0A47A3)
    /** 选中背景 / 浅色填充 */
    val PrimaryLight = Color(0xFFE8F0FE)

    // ========== 语义色 ==========
    val Success = Color(0xFF00A870)
    val Danger = Color(0xFFE64545)
    val Warning = Color(0xFFF5A623)
    val Info = Color(0xFF4A90E2)
    val Purple = Color(0xFF7B61FF)
    val Neutral = Color(0xFF6B7785)

    // ========== 中性色阶（浅色主题） ==========
    val BgPage = Color(0xFFF4F6F9)
    val BgCard = Color(0xFFFFFFFF)
    val Border = Color(0xFFDFE3E8)
    /** 主文字，对比度 16.1:1 */
    val TextPrimary = Color(0xFF1A1F26)
    /** 次要文字，对比度 6.4:1 */
    val TextSecondary = Color(0xFF5A6472)
    /** 占位符，仅用于非关键信息 */
    val TextTertiary = Color(0xFF8A94A3)

    // ========== 深色主题（夜间作业） ==========
    val DarkBgPage = Color(0xFF0F1621)
    val DarkBgCard = Color(0xFF17202E)
    val DarkBorder = Color(0xFF2A3644)
    val DarkTextPrimary = Color(0xFFF0F3F7)
    val DarkTextSecondary = Color(0xFFA8B3C1)
    val DarkTextTertiary = Color(0xFF6F7B8A)
}

/**
 * 物料状态色 —— 严格对齐接口契约 2.1 节。
 *
 * 契约规定状态色为红 #D92D20 / 黄 #F2A900 / 绿 #1F9D55。
 * 此处保留契约语义映射，同时提供可读性微调后的展示色
 * （微调仅提升强光下的辨识度，不改变状态语义）。
 *
 * 接口返回 statusCode / label / colorToken，客户端必须展示图标与文字。
 */
object MaterialStatusColors {
    /** 缺货 OUT_OF_STOCK —— 契约色 #D92D20 */
    val Shortage = Color(0xFFD92D20)
    /** 到货 ARRIVED —— 契约色 #F2A900 */
    val Arrived = Color(0xFFF2A900)
    /** 在库 IN_STOCK —— 契约色 #1F9D55 */
    val InStock = Color(0xFF1F9D55)

    /** 缺货 / 到货 / 在库 对应的浅色填充背景 */
    val ShortageContainer = Color(0x1FD92D20)
    val ArrivedContainer = Color(0x24F2A900)
    val InStockContainer = Color(0x1F1F9D55)
}
