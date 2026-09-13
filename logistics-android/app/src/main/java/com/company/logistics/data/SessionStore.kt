package com.company.logistics.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.company.logistics.model.UserRole
import java.util.UUID

/** Encrypted persistence for the session summary, tokens, and stable device id. */
class SessionStore private constructor(
    private val prefs: SharedPreferences,
    val encrypted: Boolean
) {
    data class UserSummary(
        val id: String,
        val username: String,
        val displayName: String,
        val role: UserRole,
        val mustChangePassword: Boolean
    )

    fun save(accessToken: String?, refreshToken: String?, deviceId: String) {
        prefs.edit().putString(KEY_ACCESS, accessToken).putString(KEY_REFRESH, refreshToken)
            .putString(KEY_DEVICE, deviceId).apply()
    }

    fun save(accessToken: String?, refreshToken: String?, deviceId: String, user: UserSummary) {
        prefs.edit().putString(KEY_ACCESS, accessToken).putString(KEY_REFRESH, refreshToken)
            .putString(KEY_DEVICE, deviceId).putString(KEY_USER_ID, user.id)
            .putString(KEY_USERNAME, user.username).putString(KEY_DISPLAY_NAME, user.displayName)
            .putString(KEY_ROLE, user.role.name)
            .putBoolean(KEY_MUST_CHANGE_PASSWORD, user.mustChangePassword).apply()
    }

    fun updateTokens(accessToken: String?, refreshToken: String?) {
        prefs.edit().putString(KEY_ACCESS, accessToken).putString(KEY_REFRESH, refreshToken).apply()
    }

    fun accessToken(): String? = prefs.getString(KEY_ACCESS, null)
    fun refreshToken(): String? = prefs.getString(KEY_REFRESH, null)
    fun deviceId(): String? = prefs.getString(KEY_DEVICE, null)
    fun deviceIdOrCreate(): String = deviceId() ?: ("android-" + UUID.randomUUID().toString().take(16)).also {
        prefs.edit().putString(KEY_DEVICE, it).apply()
    }

    fun userSummary(): UserSummary? {
        val id = prefs.getString(KEY_USER_ID, null) ?: return null
        val username = prefs.getString(KEY_USERNAME, null) ?: return null
        val displayName = prefs.getString(KEY_DISPLAY_NAME, null) ?: username
        val role = prefs.getString(KEY_ROLE, null)?.let { runCatching { UserRole.valueOf(it) }.getOrNull() }
            ?: return null
        return UserSummary(id, username, displayName, role, prefs.getBoolean(KEY_MUST_CHANGE_PASSWORD, false))
    }

    fun clear() { prefs.edit().clear().apply() }

    companion object {
        private const val FILE_NAME = "logistics_session"
        private const val KEY_ACCESS = "access_token"
        private const val KEY_REFRESH = "refresh_token"
        private const val KEY_DEVICE = "device_id"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_USERNAME = "username"
        private const val KEY_DISPLAY_NAME = "display_name"
        private const val KEY_ROLE = "role"
        private const val KEY_MUST_CHANGE_PASSWORD = "must_change_password"
        @Volatile private var instance: SessionStore? = null

        fun get(context: Context): SessionStore = instance ?: synchronized(this) {
            instance ?: create(context.applicationContext).also { instance = it }
        }

        private fun create(context: Context): SessionStore = try {
            val masterKey = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
            val prefs = EncryptedSharedPreferences.create(context, FILE_NAME, masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
            SessionStore(prefs, encrypted = true)
        } catch (_: Exception) {
            SessionStore(context.getSharedPreferences("${FILE_NAME}_fallback", Context.MODE_PRIVATE), encrypted = false)
        }
    }
}
