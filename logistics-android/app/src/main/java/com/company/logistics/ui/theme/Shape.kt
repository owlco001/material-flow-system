package com.company.logistics.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * 圆角系统。
 *  - sm 8dp  输入框 / 小标签
 *  - md 12dp 卡片 / 按钮（默认）
 *  - lg 16dp 弹窗 / 底部抽屉
 */
val LogisticsShapes = Shapes(
    extraSmall = RoundedCornerShape(6.dp),
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(24.dp)
)

/**
 * 间距系统（8dp 基准栅格）。
 *
 * 现场约束：触控热区最小 48dp，主操作 >= 56dp，扫码主按钮 >= 72dp。
 */
object Spacing {
    /** 4dp 图标与文字间距 */
    val xs = 4.dp
    /** 8dp 小组件内间距 */
    val sm = 8.dp
    /** 12dp 卡片间距 */
    val md = 12.dp
    /** 16dp 卡片内边距 / 页面左右边距（默认） */
    val lg = 16.dp
    /** 24dp 区块间距 */
    val xl = 24.dp
    /** 32dp 页面顶部 / 大区块分隔 */
    val xxl = 32.dp
}

/**
 * 尺寸规范 —— 现场作业的触控与布局尺寸。
 */
object Dimens {
    /** 最小可点击热区（Material 底线） */
    val MinTouchTarget = 48.dp
    /** 主操作按钮高度 */
    val ButtonHeight = 56.dp
    /** 扫码主按钮高度（现场最高频操作） */
    val ScanButtonHeight = 72.dp
    /** 顶部 AppBar 高度 */
    val AppBarHeight = 56.dp
    /** 底部导航栏高度 */
    val BottomBarHeight = 64.dp
    /** 输入框高度 */
    val InputHeight = 56.dp
    /** 卡片内边距 */
    val CardPadding = 16.dp
    /** 页面左右安全边距 */
    val PagePadding = 16.dp
    /** 扫码取景框尺寸 */
    val ViewfinderSize = 260.dp
    var CardCorner = 12.dp
    /** 胶囊圆角（状态标签、筛选 chip） */
    var PillCorner = 999.dp
    /** 状态标签高度 */
    var TagHeight = 24.dp
}
