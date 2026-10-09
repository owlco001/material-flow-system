package com.company.logistics.drawing

import org.json.JSONArray
import org.json.JSONObject

/** 图纸单页：服务端已拆成单页 PDF，按页下载。尺寸单位为 PDF 点（1/72 英寸）。 */
data class DrawingPage(
    val page: Int,
    val widthPt: Float,
    val heightPt: Float,
    val sizeBytes: Long,
    val sha256: String,
    val url: String,
    val thumbUrl: String,
)

data class Drawing(
    val drawingId: String,
    val title: String,
    val drawingNo: String,
    val revision: String,
    val pageCount: Int,
    val thumbUrl: String?,
    val pages: List<DrawingPage>,
) {
    /** 列表副标题：图号 · 版本 · 页数 */
    val subtitle: String
        get() = listOf(drawingNo.ifBlank { "无图号" }, revision.takeIf { it.isNotBlank() }?.let { "版本 $it" }, "$pageCount 页")
            .filterNotNull().joinToString(" · ")
}

/** 机台或物料。 */
sealed interface DrawingTarget {
    data class Device(val deviceId: String) : DrawingTarget
    data class Material(val code: String) : DrawingTarget
}

object DrawingParser {
    fun parseList(json: String): List<Drawing> {
        val arr = JSONObject(json).optJSONArray("drawings") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::parseDrawing) }
    }

    fun parseDrawing(o: JSONObject): Drawing? {
        val id = o.optString("drawingId").takeIf { it.isNotBlank() } ?: return null
        val pages = parsePages(o.optJSONArray("pages"))
        if (pages.isEmpty()) return null
        return Drawing(
            drawingId = id,
            title = o.optString("title").ifBlank { "未命名图纸" },
            drawingNo = o.optString("drawingNo"),
            revision = o.optString("revision"),
            pageCount = o.optInt("pageCount", pages.size),
            thumbUrl = o.optString("thumbUrl").takeIf { it.isNotBlank() && it != "null" },
            pages = pages,
        )
    }

    private fun parsePages(arr: JSONArray?): List<DrawingPage> {
        if (arr == null) return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            val p = arr.optJSONObject(i) ?: return@mapNotNull null
            val url = p.optString("url")
            val w = p.optDouble("widthPt", 0.0).toFloat()
            val h = p.optDouble("heightPt", 0.0).toFloat()
            if (url.isBlank() || w <= 0f || h <= 0f) return@mapNotNull null
            DrawingPage(
                page = p.optInt("page", i + 1),
                widthPt = w,
                heightPt = h,
                sizeBytes = p.optLong("sizeBytes", 0L),
                sha256 = p.optString("sha256"),
                url = url,
                thumbUrl = p.optString("thumbUrl"),
            )
        }.sortedBy { it.page }
    }

    /** Activity 之间传递页列表（Intent extra）。 */
    fun pagesToJson(pages: List<DrawingPage>): String = JSONArray().apply {
        pages.forEach { p ->
            put(JSONObject().apply {
                put("page", p.page); put("widthPt", p.widthPt.toDouble()); put("heightPt", p.heightPt.toDouble())
                put("sizeBytes", p.sizeBytes); put("sha256", p.sha256); put("url", p.url); put("thumbUrl", p.thumbUrl)
            })
        }
    }.toString()

    fun pagesFromJson(json: String): List<DrawingPage> = runCatching { parsePages(JSONArray(json)) }.getOrDefault(emptyList())
}

/**
 * 屏幕坐标 = 页面点坐标 × scale + (tx, ty)。
 * 纯数学，便于单测：适配、双指缩放（以手势中心为锚点）、平移限制。
 */
data class PageTransform(val scale: Float, val tx: Float, val ty: Float) {

    fun zoom(
        centroidX: Float, centroidY: Float, factor: Float, panX: Float, panY: Float,
        minScale: Float, maxScale: Float,
    ): PageTransform {
        val newScale = (scale * factor).coerceIn(minScale, maxScale)
        val r = newScale / scale
        return PageTransform(
            scale = newScale,
            tx = centroidX - (centroidX - tx) * r + panX,
            ty = centroidY - (centroidY - ty) * r + panY,
        )
    }

    /** 页面比视口小时居中；比视口大时不允许拖出空白。 */
    fun clamp(pageW: Float, pageH: Float, viewW: Float, viewH: Float): PageTransform {
        fun axis(t: Float, content: Float, view: Float): Float =
            if (content <= view) (view - content) / 2f else t.coerceIn(view - content, 0f)
        return copy(tx = axis(tx, pageW * scale, viewW), ty = axis(ty, pageH * scale, viewH))
    }

    companion object {
        fun fit(pageW: Float, pageH: Float, viewW: Float, viewH: Float): PageTransform {
            val s = minOf(viewW / pageW, viewH / pageH)
            return PageTransform(s, (viewW - pageW * s) / 2f, (viewH - pageH * s) / 2f)
        }

        /** 底图分辨率：长边最多 maxEdgePx 像素，避免大幅面图纸 OOM。 */
        fun baseScale(pageW: Float, pageH: Float, fitScale: Float, maxEdgePx: Int = 4096): Float =
            minOf(fitScale * 2f, maxEdgePx / maxOf(pageW, pageH))
    }
}
