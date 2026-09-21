package com.company.logistics.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing

/**
 * 组件库 —— 统一的状态化组件，四态齐全（默认/按压/禁用/加载）。
 *
 * 现场作业约束：
 *  - 主操作按钮 56dp，扫码主按钮 72dp；
 *  - 最小触控热区 48dp；
 *  - 状态标签一律「色 + 图标/符号 + 文字」三重表达。
 */

// ==================== 按钮 ====================

/**
 * 主操作按钮（56dp）。
 * Material3 的 Button 自带 按压/禁用 态；[loading] 时展示进度并禁点。
 */
@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
    icon: ImageVector? = null
) {
    Button(
        onClick = onClick,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = Dimens.ButtonHeight),
        enabled = enabled && !loading,
        shape = MaterialTheme.shapes.medium,
        colors = ButtonDefaults.buttonColors(
            containerColor = MaterialTheme.colorScheme.primary,
            contentColor = Color.White,
            disabledContainerColor = MaterialTheme.colorScheme.outline,
            disabledContentColor = LogisticsTheme.colors.textTertiary
        )
    ) {
        if (loading) {
            CircularProgressIndicator(
                modifier = Modifier.size(20.dp),
                color = Color.White,
                strokeWidth = 2.5.dp
            )
            Spacer(Modifier.width(Spacing.sm))
            Text("处理中…", fontSize = 16.sp, fontWeight = FontWeight.Bold)
        } else {
            icon?.let {
                Icon(it, contentDescription = null, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(Spacing.sm))
            }
            Text(text, fontSize = 16.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.5.sp)
        }
    }
}

/** 扫码主按钮（72dp）—— 现场最高频操作，热区最大 */
@Composable
fun ScanActionButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    containerColor: Color? = null
) {
    Button(
        onClick = onClick,
        modifier = modifier
            .fillMaxWidth()
            .height(Dimens.ScanButtonHeight),
        enabled = enabled,
        shape = MaterialTheme.shapes.medium,
        colors = ButtonDefaults.buttonColors(
            containerColor = containerColor ?: MaterialTheme.colorScheme.primary,
            contentColor = Color.White,
            disabledContainerColor = MaterialTheme.colorScheme.outline,
            disabledContentColor = LogisticsTheme.colors.textTertiary
        )
    ) {
        Text(text, fontSize = 18.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.5.sp)
    }
}

/** 次按钮（描边） */
@Composable
fun SecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true
) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = Dimens.ButtonHeight),
        enabled = enabled,
        shape = MaterialTheme.shapes.medium,
        border = BorderStroke(
            1.5.dp,
            if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline
        ),
        colors = ButtonDefaults.outlinedButtonColors(
            contentColor = MaterialTheme.colorScheme.primary,
            disabledContentColor = LogisticsTheme.colors.textTertiary
        )
    ) {
        Text(text, fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}

/** 危险操作按钮（如驳回、删除） */
@Composable
fun DangerButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true
) {
    Button(
        onClick = onClick,
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = Dimens.ButtonHeight),
        enabled = enabled,
        shape = MaterialTheme.shapes.medium,
        colors = ButtonDefaults.buttonColors(
            containerColor = MaterialTheme.colorScheme.error,
            contentColor = Color.White
        )
    ) {
        Text(text, fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}

// ==================== 状态标签 ====================

/**
 * 状态标签。
 * 契约要求：状态不得只靠颜色区分，必须同时给出符号（图标）与文字。
 *
 * [symbol] 保持 [String] 类型是**刻意的**：数据层（`Models.kt` /
 * `WorkspaceScreen.kt` 的状态映射）以字符串承载符号语义，改类型会连带
 * 破坏数据契约与既有测试断言。因此这里在渲染层做一次翻译——
 * 命中 [LogisticsIcons.fromSymbol] 的符号渲染为矢量图标，
 * 未命中的合规字符（如几何字符 `●`、标点 `!`）回退为文本渲染。
 */
@Composable
fun StatusTag(
    label: String,
    color: Color,
    containerColor: Color,
    modifier: Modifier = Modifier,
    symbol: String? = null
) {
    Surface(
        modifier = modifier.height(Dimens.TagHeight),
        shape = MaterialTheme.shapes.small,
        color = containerColor
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center
        ) {
            val icon = LogisticsIcons.fromSymbol(symbol)
            if (icon != null) {
                Image(
                    imageVector = icon,
                    contentDescription = null,
                    colorFilter = ColorFilter.tint(color),
                    modifier = Modifier.size(12.dp)
                )
                Spacer(Modifier.width(4.dp))
            } else if (!symbol.isNullOrBlank()) {
                Text(
                    text = symbol,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = color
                )
                Spacer(Modifier.width(4.dp))
            }
            Text(
                text = label,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = color
            )
        }
    }
}

/** 键值对展示（用于详情页的数据网格） */
@Composable
fun KeyValueCell(
    key: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = LogisticsTheme.colors.textPrimary,
    mono: Boolean = false
) {
    Column(modifier = modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
        Text(
            text = key,
            fontSize = 11.sp,
            color = LogisticsTheme.colors.textTertiary
        )
        Spacer(Modifier.height(5.dp))
        Text(
            text = value,
            fontSize = if (mono) 17.sp else 16.sp,
            fontWeight = FontWeight.Bold,
            color = valueColor,
            style = if (mono) LogisticsType.MonoFamily.let {
                androidx.compose.ui.text.TextStyle(fontFamily = it)
            } else androidx.compose.ui.text.TextStyle.Default,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/** 区块标题 */
@Composable
fun SectionTitle(
    text: String,
    modifier: Modifier = Modifier,
    trailing: String? = null
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = text,
            fontSize = 14.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary
        )
        if (trailing != null) {
            Spacer(Modifier.weight(1f))
            Text(
                text = trailing,
                fontSize = 12.sp,
                color = LogisticsTheme.colors.textSecondary
            )
        }
    }
}

/** 空状态占位 */
@Composable
fun EmptyState(
    title: String,
    description: String,
    modifier: Modifier = Modifier,
    action: (@Composable () -> Unit)? = null
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            modifier = Modifier
                .size(64.dp)
                .background(LogisticsTheme.colors.pageBackground, CircleShape)
        )
        Spacer(Modifier.height(Spacing.lg))
        Text(
            text = title,
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            color = LogisticsTheme.colors.textPrimary
        )
        Spacer(Modifier.height(Spacing.sm))
        Text(
            text = description,
            fontSize = 14.sp,
            color = LogisticsTheme.colors.textSecondary,
            textAlign = TextAlign.Center,
            lineHeight = 22.sp
        )
        if (action != null) {
            Spacer(Modifier.height(Spacing.xl))
            Box(Modifier.widthIn(max = 240.dp)) { action() }
        }
    }
}

/** 区块占位（脚手架） */
@Composable
fun PlaceholderScreen(
    title: String,
    description: String,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(Spacing.xl)
        ) {
            Text(title, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(Spacing.sm))
            Text(description, fontSize = 14.sp, color = LogisticsTheme.colors.textSecondary)
        }
    }
}

/** 文字操作按钮 */
@Composable
fun LinkTextButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.primary
) {
    TextButton(onClick = onClick, modifier = modifier) {
        Text(text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = color)
    }
}

/** 圆角卡片容器 */
@Composable
fun AppCard(
    modifier: Modifier = Modifier,
    borderColor: Color? = null,
    accentColor: Color? = null,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.surface,
        border = borderColor?.let { BorderStroke(1.5.dp, it) },
        tonalElevation = 0.dp,
        shadowElevation = 1.dp
    ) {
        Row {
            // 左侧状态色条（现场远距离扫视可辨）
            if (accentColor != null) {
                Box(
                    modifier = Modifier
                        .width(4.dp)
                        .heightIn(min = Dimens.MinTouchTarget)
                        .background(accentColor)
                )
            }
            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(Dimens.CardPadding),
                content = content
            )
        }
    }
}

/** 间距辅助 */
@Composable
fun VSpace(height: androidx.compose.ui.unit.Dp) = Spacer(Modifier.height(height))

@Composable
fun HSpace(width: androidx.compose.ui.unit.Dp) = Spacer(Modifier.width(width))

/** 圆角形状快捷方式 */
val PillShape = RoundedCornerShape(999.dp)
