package com.company.logistics.data.remote

import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import androidx.core.content.FileProvider
import com.company.logistics.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/**
 * APP 更新通道。
 *
 * 更新源在后端上：以 APP「设置的后端地址」为基地址，调用
 *   GET {baseUrl}/api/v1/app/updates/latest
 * 检查新版本，后端地址设在哪，更新源就在哪。
 *
 * 流程：检查 → 有新版则弹确认 → DownloadManager 下载 APK → FileProvider 触发安装。
 */
data class AppUpdateInfo(
    val versionCode: Int,
    val versionName: String,
    val downloadUrl: String, // 相对路径，如 /api/v1/app/updates/download
    val fileSize: Long,
    val sha256: String,
    val changelog: String,
    val updatedAt: String,
) {
    /** 相对 downloadUrl 拼出绝对地址 */
    fun absoluteUrl(baseUrl: String): String =
        baseUrl.trimEnd('/') + downloadUrl
}

fun parseAppUpdateInfo(json: String): AppUpdateInfo {
    val root = JSONObject(json)
    return AppUpdateInfo(
        versionCode = root.getInt("versionCode"),
        versionName = root.getString("versionName"),
        downloadUrl = root.getString("downloadUrl"),
        fileSize = root.optLong("fileSize"),
        sha256 = root.optString("sha256"),
        changelog = root.optString("changelog"),
        updatedAt = root.optString("updatedAt"),
    )
}

class AppUpdateChecker(
    private val context: Context,
    private val api: MaterialFlowApi,
    private val baseUrl: () -> String,
) {
    /** 检查更新：返回新版本信息；无更新/无发布/网络异常返回 null */
    suspend fun check(): AppUpdateInfo? = withContext(Dispatchers.IO) {
        try {
            val info = api.checkAppUpdate()
            if (info != null && info.versionCode > BuildConfig.VERSION_CODE) info else null
        } catch (e: Exception) {
            null
        }
    }

    /** 开始下载，返回 DownloadManager 的下载 id */
    fun download(info: AppUpdateInfo): Long {
        val url = info.absoluteUrl(baseUrl())
        val fileName = "smartfactory-${info.versionCode}-${info.versionName}.apk"
        val req = DownloadManager.Request(Uri.parse(url))
            .setTitle("智慧工厂 v${info.versionName}")
            .setDescription("正在下载更新包")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName)
            .setAllowedOverMetered(true)
        val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        return dm.enqueue(req)
    }

    /** 下载完成后触发安装（传入 DownloadManager 完成的文件 Uri 对应的本地文件） */
    fun install(apkFile: File) {
        val uri = FileProvider.getUriForFile(
            context, "${context.packageName}.fileprovider", apkFile
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            context.startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            // 无可用安装器，忽略（用户可手动到下载目录安装）
        }
    }

    /** 从 DownloadManager 下载 id 解析出本地文件 */
    fun downloadedFile(downloadId: Long): File? {
        val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val q = DownloadManager.Query().setFilterById(downloadId)
        dm.query(q)?.use { c ->
            if (c.moveToFirst()) {
                val uriStr = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI))
                val path = Uri.parse(uriStr)?.path
                if (path != null) {
                    val f = File(path)
                    if (f.isFile) return f
                }
            }
        }
        return null
    }
}
