package com.company.logistics.drawing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DrawingsTest {

    private val json = """{"deviceId":"dev_1","drawings":[
        {"drawingId":"drw_a","title":"总装图","drawingNo":"DWG-01","revision":"A","pageCount":2,
         "thumbUrl":"/api/v1/drawings/drw_a/pages/1/thumb",
         "pages":[{"page":2,"widthPt":1190.6,"heightPt":841.9,"sizeBytes":2048,"sha256":"b","url":"/api/v1/drawings/drw_a/pages/2","thumbUrl":"/t2"},
                  {"page":1,"widthPt":1190.6,"heightPt":841.9,"sizeBytes":1024,"sha256":"a","url":"/api/v1/drawings/drw_a/pages/1","thumbUrl":"/t1"}]},
        {"drawingId":"drw_b","title":"","pages":[]}
    ]}"""

    @Test
    fun parsesDrawingsSortsPagesAndSkipsEmpty() {
        val list = DrawingParser.parseList(json)
        assertEquals(1, list.size)
        val d = list[0]
        assertEquals(listOf(1, 2), d.pages.map { it.page })
        assertEquals("DWG-01 · 版本 A · 2 页", d.subtitle)
        assertEquals(d.pages, DrawingParser.pagesFromJson(DrawingParser.pagesToJson(d.pages)))
    }

    @Test
    fun fitCentersLandscapePageInPortraitView() {
        val t = PageTransform.fit(1190.6f, 841.9f, 1080f, 1920f)
        assertEquals(1080f / 1190.6f, t.scale, 1e-4f)
        assertEquals(0f, t.tx, 1e-3f)
        assertTrue(t.ty > 0f)
    }

    @Test
    fun zoomKeepsCentroidFixedAndRespectsLimits() {
        val t = PageTransform(1f, 0f, 0f)
        val z = t.zoom(100f, 100f, 2f, 0f, 0f, 0.5f, 8f)
        assertEquals(2f, z.scale, 1e-5f)
        // 页面点 (100,100) 缩放前后都在屏幕 (100,100)
        assertEquals(100f, 100f * z.scale + z.tx, 1e-3f)
        assertEquals(8f, t.zoom(0f, 0f, 100f, 0f, 0f, 0.5f, 8f).scale, 1e-5f)
    }

    @Test
    fun clampPreventsPanningPastEdges() {
        val c = PageTransform(2f, 500f, -5000f).clamp(1000f, 1000f, 1000f, 1000f)
        assertEquals(0f, c.tx, 1e-3f)
        assertEquals(-1000f, c.ty, 1e-3f)
    }

    @Test
    fun baseScaleCapsLongEdge() {
        val s = PageTransform.baseScale(3370f, 2384f, fitScale = 1f) // A0 横向
        assertEquals(4096f / 3370f, s, 1e-4f)
    }
}
