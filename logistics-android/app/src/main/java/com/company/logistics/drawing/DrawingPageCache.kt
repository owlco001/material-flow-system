package com.company.logistics.drawing

import com.company.logistics.data.remote.MaterialFlowApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.security.MessageDigest

/**
 * 单页 PDF 本地缓存：文件名用服务端给的 sha256，内容相同只下载一次；
 * 下载到临时文件，校验哈希后再原子改名，避免半截文件被当成缓存。
 * 超过 maxBytes 时按最近访问时间淘汰。
 */
class DrawingPageCache(
    private val api: MaterialFlowApi,
    private val dir: File,
    private val maxBytes: Long = 512L * 1024 * 1024,
) {
    private fun fileFor(page: DrawingPage): File {
        val key = page.sha256.takeIf { it.matches(Regex("[0-9a-f]{64}")) }
            ?: sha256Hex(page.url.toByteArray())
        return File(dir, "$key.pdf")
    }

    fun cached(page: DrawingPage): File? = fileFor(page).takeIf { it.isFile && it.length() > 0 }

    suspend fun load(page: DrawingPage, onProgress: (Float) -> Unit = {}): File = withContext(Dispatchers.IO) {
        cached(page)?.let { it.setLastModified(System.currentTimeMillis()); return@withContext it }
        dir.mkdirs()
        val target = fileFor(page)
        val tmp = File(dir, target.name + ".part")
        try {
            val digest = MessageDigest.getInstance("SHA-256")
            tmp.outputStream().buffered().use { raw ->
                val out = java.security.DigestOutputStream(raw, digest)
                api.downloadAuthorized(page.url, out) { received, total ->
                    val expected = if (total > 0) total else page.sizeBytes
                    if (expected > 0) onProgress((received.toFloat() / expected).coerceIn(0f, 1f))
                }
                out.flush()
            }
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            if (page.sha256.isNotBlank() && !actual.equals(page.sha256, ignoreCase = true)) {
                throw IOException("图纸文件校验失败，请重试")
            }
            if (!tmp.renameTo(target)) throw IOException("图纸缓存写入失败")
            trim()
            target
        } finally {
            tmp.delete()
        }
    }

    private fun trim() {
        val files = dir.listFiles { f -> f.isFile && f.name.endsWith(".pdf") } ?: return
        var total = files.sumOf { it.length() }
        if (total <= maxBytes) return
        files.sortedBy { it.lastModified() }.forEach { f ->
            if (total <= maxBytes) return
            total -= f.length()
            f.delete()
        }
    }

    private fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
