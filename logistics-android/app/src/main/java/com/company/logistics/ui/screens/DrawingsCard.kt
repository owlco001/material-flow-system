package com.company.logistics.ui.screens

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.DrawingActivity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.drawing.Drawing
import com.company.logistics.drawing.DrawingTarget
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** 缩略图内存缓存（约 50 张 480px WebP 解码后的位图）。 */
private object DrawingThumbCache {
    val cache = object : LruCache<String, ImageBitmap>(24 * 1024 * 1024) {
        override fun sizeOf(key: String, value: ImageBitmap) = value.width * value.height * 4
    }
}

/**
 * 机台/物料详情页里的「图纸」卡片：列出绑定图纸，点开进入按页查看器。
 * 只加载列表与缩略图（几十 KB），PDF 页在查看器里按需下载。
 */
@Composable
fun DrawingsCard(api: MaterialFlowApi, target: DrawingTarget) {
    var drawings by remember(target) { mutableStateOf<List<Drawing>?>(null) }
    var error by remember(target) { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    val context = LocalContext.current

    LaunchedEffect(target, reload) {
        error = null
        runCatching {
            when (target) {
                is DrawingTarget.Device -> api.deviceDrawings(target.deviceId)
                is DrawingTarget.Material -> api.materialDrawings(target.code)
            }
        }.onSuccess { drawings = it }
            .onFailure { e ->
                if (e is kotlinx.coroutines.CancellationException) throw e
                error = e.message ?: "图纸加载失败"
            }
    }

    AppCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(width = 4.dp, height = 16.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(LogisticsColors.Primary),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "图纸" + (drawings?.let { "（${it.size}）" } ?: ""),
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary,
                modifier = Modifier.weight(1f),
            )
            if (error != null) {
                TextButton(onClick = { reload++ }) { Text("重试", fontSize = 12.sp) }
            }
        }
        VSpace(Spacing.sm)
        val list = drawings
        when {
            error != null -> Text("图纸加载失败：$error", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            list == null -> Text("正在加载图纸…", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            list.isEmpty() -> Text("暂无绑定图纸", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            else -> list.forEach { d ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .clickable { context.startActivity(DrawingActivity.intent(context, d)) }
                        .padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    DrawingThumb(api, d.thumbUrl)
                    Spacer(Modifier.width(Spacing.md))
                    Column(Modifier.weight(1f)) {
                        Text(
                            d.title,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = LogisticsTheme.colors.textPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        VSpace(2.dp)
                        Text(d.subtitle, fontSize = 12.sp, color = LogisticsTheme.colors.textSecondary, maxLines = 1)
                    }
                    Text("查看 ›", fontSize = 13.sp, color = LogisticsColors.Primary, fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
private fun DrawingThumb(api: MaterialFlowApi, url: String?) {
    var bitmap by remember(url) { mutableStateOf(url?.let { DrawingThumbCache.cache.get(it) }) }
    LaunchedEffect(url) {
        if (url == null || bitmap != null) return@LaunchedEffect
        val bytes = runCatching { api.fetchAuthorizedBytes(url) }.getOrNull() ?: return@LaunchedEffect
        val decoded = withContext(Dispatchers.Default) {
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
        } ?: return@LaunchedEffect
        DrawingThumbCache.cache.put(url, decoded)
        bitmap = decoded
    }
    val shape = RoundedCornerShape(6.dp)
    Box(
        modifier = Modifier
            .size(width = 72.dp, height = 52.dp)
            .clip(shape)
            .background(Color.White)
            .border(1.dp, LogisticsTheme.colors.textTertiary.copy(alpha = 0.3f), shape),
        contentAlignment = Alignment.Center,
    ) {
        val b = bitmap
        if (b != null) {
            Image(b, contentDescription = null, contentScale = ContentScale.Fit, modifier = Modifier.size(width = 72.dp, height = 52.dp))
        } else {
            Text("PDF", fontSize = 11.sp, color = LogisticsTheme.colors.textTertiary)
        }
    }
}
