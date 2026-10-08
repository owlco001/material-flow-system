package com.company.logistics.ui.components

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * 扫码结果的触觉 + 声音反馈。
 *
 * 车间场景：戴手套、环境嘈杂、视线常不在屏幕上，单靠状态文字无法确认「扫到了没有」。
 * 成功：短震 + 一声短「嘀」；失败：两段长震 + 低沉错误音。
 * 声音走通知音量通道，跟随设备音量/静音设置；任何硬件不可用都静默降级，不影响扫码。
 */
class ScanFeedback(context: Context) {
    private val appContext = context.applicationContext

    private val vibrator: Vibrator? = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (appContext.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            appContext.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
    } catch (_: Throwable) {
        null
    }

    private var tone: ToneGenerator? = try {
        ToneGenerator(AudioManager.STREAM_NOTIFICATION, TONE_VOLUME)
    } catch (_: Throwable) {
        null // 部分设备音频资源紧张时构造会抛 RuntimeException
    }

    fun success() {
        vibrate(longArrayOf(0, 40))
        playTone(ToneGenerator.TONE_PROP_BEEP, 120)
    }

    fun error() {
        vibrate(longArrayOf(0, 180, 100, 180))
        playTone(ToneGenerator.TONE_SUP_ERROR, 350)
    }

    fun release() {
        try {
            tone?.release()
        } catch (_: Throwable) {
        }
        tone = null
    }

    private fun vibrate(pattern: LongArray) {
        val v = vibrator ?: return
        try {
            if (!v.hasVibrator()) return
            v.vibrate(VibrationEffect.createWaveform(pattern, -1))
        } catch (_: Throwable) {
            // 缺 VIBRATE 权限或厂商限制时静默忽略
        }
    }

    private fun playTone(type: Int, durationMs: Int) {
        try {
            tone?.startTone(type, durationMs)
        } catch (_: Throwable) {
        }
    }

    private companion object {
        const val TONE_VOLUME = 80 // 0..100，相对通知音量
    }
}
