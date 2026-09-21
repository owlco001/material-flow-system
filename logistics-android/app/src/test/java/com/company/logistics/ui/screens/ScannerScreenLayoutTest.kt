package com.company.logistics.ui.screens

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Test

class ScannerScreenLayoutTest {
    @Test
    fun cameraPanelsUseSharedFlatPreviewRatio() {
        val source = File("src/main/java/com/company/logistics/ui/screens/ScannerScreen.kt").readText()
        val ratio = Regex("CAMERA_PREVIEW_ASPECT_RATIO\\s*=\\s*([0-9.]+)f")
            .find(source)
            ?.groupValues
            ?.get(1)

        assertEquals("1.55", ratio)
        assertEquals(3, Regex("\\.aspectRatio\\(CAMERA_PREVIEW_ASPECT_RATIO\\)").findAll(source).count())
    }
}
