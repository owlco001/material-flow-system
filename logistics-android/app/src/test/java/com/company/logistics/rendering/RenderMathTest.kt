package com.company.logistics.rendering

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RenderMathTest {
    @Test
    fun boomBoxScaleModelStaysInsideClipPlanes() {
        val (near, far) = RenderMath.clipPlanes(distance = 0.0472f, radius = 0.017f)
        assertTrue("near must sit in front of the model", near < 0.0472 - 0.017)
        assertTrue("far must sit behind the model", far > 0.0472 + 0.017)
    }

    @Test
    fun gearboxScaleModelStaysInsideClipPlanes() {
        val (near, far) = RenderMath.clipPlanes(distance = 94f, radius = 15.6f)
        assertTrue(near < 94.0 - 15.6)
        assertTrue(far > 94.0 + 15.6)
    }

    @Test
    fun degenerateDistanceStillProducesValidPlanes() {
        val (near, far) = RenderMath.clipPlanes(distance = 0f, radius = 0.017f)
        assertTrue(near > 0.0)
        assertTrue(far > near)
    }

    @Test
    fun srgbToLinearMatchesKnownValues() {
        assertEquals(0f, RenderMath.srgbToLinear(0f), 1e-6f)
        assertEquals(1f, RenderMath.srgbToLinear(1f), 1e-6f)
        assertEquals(0.2140f, RenderMath.srgbToLinear(0.5f), 0.0005f)
    }
}
