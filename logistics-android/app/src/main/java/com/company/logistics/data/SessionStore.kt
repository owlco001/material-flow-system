package com.company.logistics.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * 会话持久化。
 *
 * 为什要持久化：refresh token 有效期 30 天，若只存内存则 App 一重启就丢，
 * 用户每天开机都要重新登录 —— 长时效设计失去意义。
 *
 * 安全考量：
 *  - 使用 [EncryptedSharedPreferences]（AES-256-GCM），密钥由 Android Keystore
 *    托管，不落磁盘；root 设备上也无法直接读取明文。
 *  - 若设备不支持加密存储（极端老机型 / Keystore 异常），
 *    降级为普通 SharedPreferences 但记录降级标志，
 *    避免 App 直接崩溃导致现场无法作业。
 *  - 登出 / 刷新令牌失效时必须显式 [clear]，不留残影。
 */
class SessionStore private constructor(
    private val prefs: SharedPreferences,
    /** 是否成功启用了加密存储；false 表示走了降级路径 */
    val encrypted: Boolean
) {

    fun save(accessToken: String?, refreshToken: String?, deviceId: String) {
        prefs.edit()
            .putString(KEY_ACCESS, accessToken)
            .putString(KEY_REFRESH, refreshToken)
            .putString(KEY_DEVICE, deviceId)
            .apply()
    }

    /** 仅更新 access token（刷新成功后调用，refresh token 一并轮转） */
    fun updateTokens(accessToken: String?, refreshToken: String?) {
        prefs.edit()
            .putString(KEY_ACCESS, accessToken)
            .putString(KEY_REFRESH, refreshToken)
            .apply()
    }

    fun accessToken(): String? = prefs.getString(KEY_ACCESS, null)

    fun refreshToken(): String? = prefs.getString(KEY_REFRESH, null)

    fun deviceId(): String? = prefs.getString(KEY_DEVICE, null)

    fun clear() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val FILE_NAME = "logistics_session"
        private const val KEY_ACCESS = "access_token"
        private const val KEY_REFRESH = "refresh_token"
        private const val KEY_DEVICE = "device_id"

        @Volatile
        private var instance: SessionStore? = null

        fun get(context: Context): SessionStore = instance ?: synchronized(this) {
            instance ?: create(context.applicationContext).also { instance = it }
        }

        private fun create(context: Context): SessionStore = try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            val prefs = EncryptedSharedPreferences.create(
                context,
                FILE_NAME,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
            SessionStore(prefs, encrypted = true)
        } catch (_: Exception) {
            // Keystore 不可用（部分定制 ROM / 越狱环境）→ 降级但保持可用。
            // 此处不静默：由上层读取 encrypted 标志，可在 UI 上提示风险。
            SessionStore(
                context.getSharedPreferences("${FILE_NAME}_fallback", Context.MODE_PRIVATE),
                encrypted = false
            )
        }
    }
}
