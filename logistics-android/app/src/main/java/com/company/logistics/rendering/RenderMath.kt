package com.company.logistics.rendering

import kotlin.math.pow

/** Pure render math: unit-testable clip-plane and color-space helpers. */
internal object RenderMath {
    /**
     * Clip planes that always contain the model shell (distance ± radius) with margin.
     * Near used to be fixed at 0.1 while tiny models framed at ~0.05 distance, which
     * sliced the mesh open ("huge" close-up artifacts); far at 100 clipped large rigs.
     */
    fun clipPlanes(distance: Float, radius: Float): Pair<Double, Double> {
        val r = if (radius > 0f) radius else 1f
        val d = distance.toDouble().coerceAtLeast(r * 0.01)
        val near = (d - r * 2.0).coerceAtLeast(r * 0.001).coerceAtLeast(1e-6)
        val far = d + r * 10.0
        return near to far
    }

    /** Skybox colors are linear; UI theme colors are sRGB. */
    fun srgbToLinear(channel: Float): Float =
        if (channel <= 0.04045f) channel / 12.92f else ((channel + 0.055f) / 1.055f).pow(2.4f)
}
