package com.company.logistics.ui.screens

import android.graphics.Bitmap
import android.graphics.Matrix
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.drawing.DrawingPage
import com.company.logistics.drawing.DrawingPageCache
import com.company.logistics.drawing.PageTransform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.math.roundToInt

private val ViewerBg = Color(0xFF1F2933)
private val BarBg = Color(0xFF111820)

@Composable
fun DrawingViewerScreen(
    title: String,
    subtitle: String,
    pages: List<DrawingPage>,
    cache: DrawingPageCache,
    onBack: () -> Unit,
) {
    var index by rememberSaveable { mutableIntStateOf(0) }
    val page = pages.getOrNull(index)
    var file by remember(page) { mutableStateOf(page?.let { cache.cached(it) }) }
    var progress by remember(page) { mutableFloatStateOf(0f) }
    var error by remember(page) { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) }

    LaunchedEffect(page, retry) {
        if (page == null || file != null) return@LaunchedEffect
        error = null
        runCatching { cache.load(page) { progress = it } }
            .onSuccess { file = it }
            .onFailure { e ->
                if (e is kotlinx.coroutines.CancellationException) throw e
                error = (e as? com.company.logistics.data.remote.ApiException)?.let {
                    if (it.isUnauthorized) "登录已过期，请返回后重新进入" else it.message
                } ?: e.message ?: "下载失败"
            }
    }

    Column(Modifier.fillMaxSize().background(ViewerBg)) {
        Row(
            Modifier.fillMaxWidth().background(BarBg).statusBarsPadding().padding(horizontal = 4.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onBack) { Text("‹ 返回", color = Color.White, fontSize = 14.sp) }
            Column(Modifier.weight(1f)) {
                Text(title, color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (subtitle.isNotBlank()) {
                    Text(subtitle, color = Color.White.copy(alpha = 0.65f), fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            if (pages.size > 1) {
                Text("${index + 1} / ${pages.size}", color = Color.White, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 8.dp))
            }
        }

        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            val f = file
            when {
                page == null -> Text("没有可显示的页面", color = Color.White)
                error != null -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(error!!, color = Color.White, fontSize = 14.sp)
                    TextButton(onClick = { retry++ }) { Text("重试", color = Color.White) }
                }
                f == null -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    if (progress > 0f) {
                        LinearProgressIndicator(progress = { progress }, modifier = Modifier.width(180.dp))
                    } else {
                        CircularProgressIndicator(color = Color.White)
                    }
                    Spacer(Modifier.padding(6.dp))
                    val mb = if (page.sizeBytes > 0) String.format(java.util.Locale.ROOT, "（%.1f MB）", page.sizeBytes / 1048576.0) else ""
                    Text("正在下载第 ${page.page} 页$mb", color = Color.White.copy(alpha = 0.8f), fontSize = 13.sp)
                }
                else -> PdfPageView(f)
            }
        }

        if (pages.size > 1) {
            Row(
                Modifier.fillMaxWidth().background(BarBg).navigationBarsPadding().padding(horizontal = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { index-- }, enabled = index > 0) { Text("‹ 上一页", color = if (index > 0) Color.White else Color.Gray) }
                Spacer(Modifier.weight(1f))
                Text("双指缩放 · 双击放大", color = Color.White.copy(alpha = 0.5f), fontSize = 11.sp)
                Spacer(Modifier.weight(1f))
                TextButton(onClick = { index++ }, enabled = index < pages.size - 1) {
                    Text("下一页 ›", color = if (index < pages.size - 1) Color.White else Color.Gray)
                }
            }
        }
    }
}

/** PdfRenderer 非线程安全：所有渲染串行执行。 */
private class PdfPageHandle(file: File) {
    private val fd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    private val renderer = PdfRenderer(fd)
    private val lock = Mutex()
    private val sizePt: Pair<Float, Float> = renderer.openPage(0).use { p -> p.width.toFloat() to p.height.toFloat() }
    val widthPt: Float get() = sizePt.first
    val heightPt: Float get() = sizePt.second

    suspend fun render(widthPx: Int, heightPx: Int, scale: Float, tx: Float, ty: Float): ImageBitmap? =
        withContext(Dispatchers.IO) {
            if (widthPx <= 0 || heightPx <= 0) return@withContext null
            lock.withLock {
                val bmp = try {
                    Bitmap.createBitmap(widthPx, heightPx, Bitmap.Config.ARGB_8888)
                } catch (_: OutOfMemoryError) {
                    return@withLock null
                }
                bmp.eraseColor(android.graphics.Color.WHITE)
                val m = Matrix().apply { setScale(scale, scale); postTranslate(tx, ty) }
                renderer.openPage(0).use { it.render(bmp, null, m, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY) }
                bmp.asImageBitmap()
            }
        }

    fun close() {
        runCatching { renderer.close() }
        runCatching { fd.close() }
    }
}

/**
 * 单页查看：一张适配分辨率的底图 + 放大后按当前视口重渲的清晰图。
 * 手势过程中先拉伸已有位图，停手 200ms 后在视口范围内重新渲染，内存只占约两张屏幕大小的位图。
 */
@Composable
private fun PdfPageView(file: File) {
    val handle = remember(file) { runCatching { PdfPageHandle(file) }.getOrNull() }
    DisposableEffect(handle) { onDispose { handle?.close() } }
    if (handle == null) {
        Text("图纸文件无法打开", color = Color.White)
        return
    }
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val vw = constraints.maxWidth.toFloat()
        val vh = constraints.maxHeight.toFloat()
        val pw = handle.widthPt
        val ph = handle.heightPt
        val fit = remember(vw, vh, pw, ph) { PageTransform.fit(pw, ph, vw, vh) }
        val minScale = fit.scale
        val maxScale = fit.scale * 16f
        var t by remember(fit) { mutableStateOf(fit) }
        var base by remember(handle, fit) { mutableStateOf<ImageBitmap?>(null) }
        var baseScale by remember(handle, fit) { mutableFloatStateOf(0f) }
        var detail by remember(handle, fit) { mutableStateOf<Pair<ImageBitmap, PageTransform>?>(null) }

        LaunchedEffect(handle, fit) {
            val bs = PageTransform.baseScale(pw, ph, fit.scale)
            base = handle.render((pw * bs).roundToInt(), (ph * bs).roundToInt(), bs, 0f, 0f)
            baseScale = bs
        }
        LaunchedEffect(handle, t, baseScale) {
            if (baseScale <= 0f || t.scale <= baseScale * 1.05f) {
                detail = null
                return@LaunchedEffect
            }
            delay(200)
            handle.render(vw.roundToInt(), vh.roundToInt(), t.scale, t.tx, t.ty)?.let { detail = it to t }
        }

        Canvas(
            Modifier
                .fillMaxSize()
                .pointerInput(fit) {
                    detectTransformGestures { centroid, pan, zoom, _ ->
                        t = t.zoom(centroid.x, centroid.y, zoom, pan.x, pan.y, minScale, maxScale).clamp(pw, ph, vw, vh)
                    }
                }
                .pointerInput(fit) {
                    detectTapGestures(onDoubleTap = { o ->
                        t = if (t.scale > fit.scale * 1.5f) fit
                        else t.zoom(o.x, o.y, fit.scale * 3f / t.scale, 0f, 0f, minScale, maxScale).clamp(pw, ph, vw, vh)
                    })
                }
        ) {
            val dstW = pw * t.scale
            val dstH = ph * t.scale
            drawRect(Color.White, topLeft = Offset(t.tx, t.ty), size = Size(dstW, dstH))
            base?.let { b ->
                drawImage(
                    b,
                    srcOffset = IntOffset.Zero,
                    srcSize = IntSize(b.width, b.height),
                    dstOffset = IntOffset(t.tx.roundToInt(), t.ty.roundToInt()),
                    dstSize = IntSize(dstW.roundToInt(), dstH.roundToInt()),
                    filterQuality = FilterQuality.Medium,
                )
            }
            detail?.let { (b, dt) ->
                val r = t.scale / dt.scale
                drawImage(
                    b,
                    srcOffset = IntOffset.Zero,
                    srcSize = IntSize(b.width, b.height),
                    dstOffset = IntOffset((t.tx - dt.tx * r).roundToInt(), (t.ty - dt.ty * r).roundToInt()),
                    dstSize = IntSize((b.width * r).roundToInt(), (b.height * r).roundToInt()),
                    filterQuality = FilterQuality.Low,
                )
            }
        }
        if (base == null) {
            CircularProgressIndicator(color = Color.White, modifier = Modifier.align(Alignment.Center))
        }
    }
}
