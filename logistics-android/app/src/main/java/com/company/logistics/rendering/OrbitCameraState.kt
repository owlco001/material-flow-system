package com.company.logistics.rendering

import kotlin.math.atan
import kotlin.math.max
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
    private var homeZoom = initialDistance
    private var zoomMin = minDistance
    private var zoomMax = maxDistance

    fun rotate(deltaYaw: Float, deltaPitch: Float) {
        yawDegrees = normalizeYaw(yawDegrees + deltaYaw)
        pitchDegrees = (pitchDegrees + deltaPitch).coerceIn(MIN_PITCH, MAX_PITCH)
    }

    /** A scale below one zooms out; a scale above one zooms in. */
    fun zoom(scale: Float) {
        require(scale > 0f) { "scale must be positive" }
        distance = (distance / scale).coerceIn(zoomMin, zoomMax)
    }

    fun pan(deltaX: Float, deltaY: Float) {
        panX = (panX + deltaX).coerceIn(-maxPan, maxPan)
        panY = (panY + deltaY).coerceIn(-maxPan, maxPan)
    }

    /** 绝对聚焦：把相机目标直接移到 (x, y)，用于零件定位 */
    fun focusAt(x: Float, y: Float) {
        panX = x.coerceIn(-maxPan, maxPan)
        panY = y.coerceIn(-maxPan, maxPan)
    }

    fun reset() {
        yawDegrees = initialYaw
        pitchDegrees = initialPitch
        distance = homeZoom
        panX = 0f
        panY = 0f
    }

    /** Frames a model of the given bounding radius at screen center; keeps orbit angles. */
    fun fit(radius: Float, aspect: Float = 1f) {
        require(radius > 0f) { "radius must be positive" }
        require(aspect > 0f) { "aspect must be positive" }
        val halfFovX = atan(tan(HALF_FOV_Y_RADIANS) * aspect)
        val halfFov = min(halfFovX, HALF_FOV_Y_RADIANS)
        // Framing scales with the model: clamping to fixed bounds framed 2cm parts at
        // 0.1 units (near-plane slicing) and refused to back off from huge assemblies.
        // 2.2 对齐浏览器 three.js（fitCameraToObject: dist = maxDim * 2.2，模型约占屏一半）
        distance = radius / tan(halfFov) * 2.2f
        homeZoom = distance
        // 相机不许进入模型包围球：旧 zoomMin=fit/50≈0.11r 会让相机钻进模型内部，
        // 配合背面剔除，旋转到某些朝向整个模型"消失"（用户实测）。聚焦零件只移目标
        // 不动距离（focusAt），收紧下限不影响零件聚焦。
        zoomMin = max(distance / ZOOM_RATIO, radius * INSIDE_GUARD)
        zoomMax = distance * ZOOM_RATIO
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
        const val ZOOM_RATIO = 50f

        /** 最近距离 ≥ 模型半径×该系数：保证相机始终在包围球外（1.15 留 15% 余量防 near 贴面） */
        const val INSIDE_GUARD = 1.15f
    }
}
