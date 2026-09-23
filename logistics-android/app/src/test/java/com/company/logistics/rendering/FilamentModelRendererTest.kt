package com.company.logistics.rendering

import java.io.File
import org.junit.Assert.assertNotNull
import org.junit.Test

class FilamentModelRendererTest {
    @Test
    fun rendererConstructionAndReleaseDoNotThrowWhenFilamentIsUnavailable() {
        val renderer = FilamentModelRenderer()

        assertNotNull(renderer.loadGlb(File("/definitely-missing/debug-model.glb")).exceptionOrNull())
        renderer.release()
        renderer.release()
    }
}
