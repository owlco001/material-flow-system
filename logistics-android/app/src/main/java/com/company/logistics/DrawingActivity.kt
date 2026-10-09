package com.company.logistics

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.company.logistics.data.EndpointStore
import com.company.logistics.data.SessionStore
import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.drawing.Drawing
import com.company.logistics.drawing.DrawingPageCache
import com.company.logistics.drawing.DrawingParser
import com.company.logistics.ui.screens.DrawingViewerScreen
import com.company.logistics.ui.theme.LogisticsTheme

/**
 * 图纸查看（机台详情 / 物料详情的「图纸」卡片进入）。
 *
 * 服务端已把图纸拆成单页 PDF：这里按页下载到本地缓存（按 sha256 去重，离线可看已打开过的页），
 * 用系统 PdfRenderer 渲染。允许横屏，大幅面图纸横过来看更舒服。
 */
class DrawingActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty()
        val subtitle = intent.getStringExtra(EXTRA_SUBTITLE).orEmpty()
        val pages = DrawingParser.pagesFromJson(intent.getStringExtra(EXTRA_PAGES).orEmpty())

        ApiConfig.baseUrl = EndpointStore.get(applicationContext).effectiveUrl
        val api = MaterialFlowApi()
        val session = SessionStore.get(applicationContext)
        api.restoreSession(session.accessToken(), session.refreshToken(), session.deviceIdOrCreate())
        val cache = DrawingPageCache(api, java.io.File(cacheDir, "drawings"))

        setContent {
            LogisticsTheme {
                DrawingViewerScreen(
                    title = title.ifBlank { "图纸" },
                    subtitle = subtitle,
                    pages = pages,
                    cache = cache,
                    onBack = ::finish,
                )
            }
        }
    }

    companion object {
        const val EXTRA_TITLE = "title"
        const val EXTRA_SUBTITLE = "subtitle"
        const val EXTRA_PAGES = "pages"

        fun intent(context: Context, drawing: Drawing): Intent =
            Intent(context, DrawingActivity::class.java).apply {
                putExtra(EXTRA_TITLE, drawing.title)
                putExtra(EXTRA_SUBTITLE, drawing.subtitle)
                putExtra(EXTRA_PAGES, DrawingParser.pagesToJson(drawing.pages))
            }
    }
}
