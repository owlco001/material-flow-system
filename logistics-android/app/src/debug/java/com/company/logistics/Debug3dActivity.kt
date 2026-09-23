package com.company.logistics

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.company.logistics.ui.screens.Debug3dPanel
import com.company.logistics.ui.theme.LogisticsTheme

/** Debug-only activity; it is absent from release source and manifest. */
class Debug3dActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { LogisticsTheme { Debug3dPanel() } }
    }
}