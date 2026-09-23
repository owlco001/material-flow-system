package com.company.logistics

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.company.logistics.ui.theme.LogisticsTheme
import java.io.File

/** Debug-only activity; it is absent from release source and manifest. */
class Debug3dActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val glbFile = intent.getStringExtra(EXTRA_GLB_PATH)?.let(::File)
        setContent { LogisticsTheme { FilamentDebug3dPanel(glbFile) } }
    }

    companion object {
        const val EXTRA_GLB_PATH = "glb_path"
    }
}