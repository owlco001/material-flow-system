package com.company.logistics.rendering

import kotlin.math.atan
import kotlin.math.min
import kotlin.math.tan

/** Engine-neutral camera state for a future 3D model surface. */
class OrbitCameraState(
    private val minDistance: Float = 1f,
    private val maxDistance: Float = 20f,
    private val maxPan: Float = 10f,
    initialYawDegrees: Float = 0f,
    initialPitchDegrees: Float = 0f,
    initialDistance: Float = 5f,
) {
    init {
        require(minDistance > 0f) { "minDistance must be positive" }
        require(maxDistance >= minDistance) { "maxDistance must not be below minDistance" }
        require(maxPan >= 0f) { "maxPan must not be negative" }
        require(initialDistance in minDistance..maxDistance) { "initialDistance must be within distance bounds" }
    }

    var yawDegrees: Float = normalizeYaw(initialYawDegrees)
        private set
    var pitchDegrees: Float = initialPitchDegrees.coerceIn(MIN_PITCH, MAX_PITCH)
        private set
    var distance: Float = initialDistance
        private set
    var panX: Float = 0f
        private set
    var panY: Float = 0f
        private set

    private val initialYaw = yawDegrees
    private val initialPitch = pitchDegrees
    private val initialZoom = initialDistance

    fun rotate(deltaYaw: Float, deltaPitch: Float) {
        yawDegrees = normalizeYaw(yawDegrees + deltaYaw)
        pitchDegrees = (pitchDegrees + deltaPitch).coerceIn(MIN_PITCH, MAX_PITCH)
    }

    /** A scale below one zooms out; a scale above one zooms in. */
    fun zoom(scale: Float) {
        require(scale > 0f) { "scale must be positive" }
        distance = (distance / scale).coerceIn(minDistance, maxDistance)
    }

    fun pan(deltaX: Float, deltaY: Float) {
        panX = (panX + deltaX).coerceIn(-maxPan, maxPan)
        panY = (panY + deltaY).coerceIn(-maxPan, maxPan)
    }

    fun reset() {
        yawDegrees = initialYaw
        pitchDegrees = initialPitch
        distance = initialZoom
        panX = 0f
        panY = 0f
    }

    /** Frames a model of the given bounding radius at screen center; keeps orbit angles. */
    fun fit(radius: Float, aspect: Float = 1f) {
        require(radius > 0f) { "radius must be positive" }
        require(aspect > 0f) { "aspect must be positive" }
        val halfFovX = atan(tan(HALF_FOV_Y_RADIANS) * aspect)
        val halfFov = min(halfFovX, HALF_FOV_Y_RADIANS)
        distance = (radius / tan(halfFov) * 1.15f).coerceIn(minDistance, maxDistance)
        panX = 0f
        panY = 0f
    }

    private fun normalizeYaw(value: Float): Float {
        return ((value + 180f) % 360f + 360f) % 360f - 180f
    }

    private companion object {
        const val MIN_PITCH = -89f
        const val MAX_PITCH = 89f
        const val HALF_FOV_Y_RADIANS = 0.3926991f // 22.5 degrees, matching the renderer's 45deg FOV
    }
}
