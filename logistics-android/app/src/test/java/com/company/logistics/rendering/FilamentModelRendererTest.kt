package com.company.logistics.rendering

import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FilamentModelRendererTest {
    @Test
    fun filamentIsInitializedBeforeEngineCreation() {
        val initialized = AtomicBoolean(false)
        val renderer = FilamentModelRenderer(
            filamentInitializer = { initialized.set(true) },
            engineFactory = {
                assertTrue("Filament.init must run first", initialized.get())
                throw IllegalStateException("stop after ordering assertion")
            },
        )

        assertEquals("stop after ordering assertion", renderer.initializationFailureMessage())
        renderer.release()
    }

    @Test
    fun initializationFailureIsContainedAndReportedAsFilamentUnavailable() {
        val renderer = FilamentModelRenderer(
            filamentInitializer = { error("native init unavailable") },
            engineFactory = { error("Engine.create must not run") },
        )

        val failure = renderer.loadGlb(File("/definitely-missing/debug-model.glb"))

        assertNotNull(failure.exceptionOrNull())
        assertTrue(failure.exceptionOrNull() is FilamentUnavailableException)
        renderer.release()
    }

    @Test
    fun rendererConstructionAndReleaseDoNotThrowWhenFilamentIsUnavailable() {
        val renderer = FilamentModelRenderer()

        assertNotNull(renderer.loadGlb(File("/definitely-missing/debug-model.glb")).exceptionOrNull())
        renderer.release()
        renderer.release()
    }
}
