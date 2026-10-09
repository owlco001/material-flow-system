package com.company.logistics.update

import org.json.JSONObject

/** 服务端 /api/app/latest 返回的发布信息。 */
data class AppRelease(
    val versionCode: Int,
    val versionName: String,
    val notes: String,
    val force: Boolean,
    val sizeBytes: Long,
    val downloadUrl: String,
)

/** 检查更新的结果。 */
sealed interface UpdateCheckResult {
    data class Available(val release: AppRelease) : UpdateCheckResult
    data object UpToDate : UpdateCheckResult
    /** 服务端尚未发布任何版本（HTTP 404）。 */
    data object NoRelease : UpdateCheckResult
    data class Failed(val message: String) : UpdateCheckResult
}

/** 纯逻辑，便于单测：解析、比较版本、拼下载地址。 */
object AppUpdatePolicy {

    fun parse(json: String, baseUrl: String): AppRelease? = runCatching {
        val o = JSONObject(json)
        val code = o.getInt("versionCode")
        val name = o.optString("versionName", "")
        if (code <= 0 || name.isBlank()) return null
        AppRelease(
            versionCode = code,
            versionName = name,
            notes = o.optString("notes", ""),
            force = o.optBoolean("force", false),
            sizeBytes = o.optLong("size", 0L),
            downloadUrl = resolveUrl(baseUrl, o.optString("downloadUrl", "/api/app/download")),
        )
    }.getOrNull()

    fun decide(currentVersionCode: Int, release: AppRelease?): UpdateCheckResult = when {
        release == null -> UpdateCheckResult.Failed("服务端返回的版本信息无法解析")
        release.versionCode > currentVersionCode -> UpdateCheckResult.Available(release)
        else -> UpdateCheckResult.UpToDate
    }

    /** 相对地址拼到服务端地址上；绝对 http(s) 地址原样使用。 */
    fun resolveUrl(baseUrl: String, url: String): String {
        if (url.startsWith("https://") || url.startsWith("http://")) return url
        return baseUrl.trimEnd('/') + "/" + url.trimStart('/')
    }

    fun formatSize(bytes: Long): String =
        if (bytes <= 0) "" else String.format(java.util.Locale.ROOT, "%.1f MB", bytes / 1048576.0)
}

/** 网络层：只打公开接口 /api/app/latest，不需要登录。 */
object AppUpdateChecker {
    suspend fun check(baseUrl: String, currentVersionCode: Int): UpdateCheckResult =
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            try {
                val url = java.net.URL(baseUrl.trimEnd('/') + "/api/app/latest")
                val conn = (url.openConnection() as java.net.HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 8000
                    readTimeout = 8000
                }
                try {
                    when (val code = conn.responseCode) {
                        in 200..299 -> {
                            val body = conn.inputStream.bufferedReader().use { it.readText() }
                            AppUpdatePolicy.decide(currentVersionCode, AppUpdatePolicy.parse(body, baseUrl))
                        }
                        404 -> UpdateCheckResult.NoRelease
                        else -> UpdateCheckResult.Failed("服务端返回 HTTP $code")
                    }
                } finally {
                    conn.disconnect()
                }
            } catch (e: java.net.SocketTimeoutException) {
                UpdateCheckResult.Failed("连接超时，请检查网络或服务端地址")
            } catch (e: java.io.IOException) {
                UpdateCheckResult.Failed("网络异常：${e.message.orEmpty()}")
            }
        }
}
