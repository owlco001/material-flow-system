package com.company.logistics.data.remote

import android.content.Context
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

data class AssemblyModelMeta(
    val modelId: String, val modelCode: String, val modelName: String, val version: Int,
    val format: String, val byteSize: Long, val sha256: String, val downloadPath: String,
)

data class CachedAssemblyModel(val meta: AssemblyModelMeta, val file: File)

/** Downloads published GLBs into a verified, atomically-installed private cache. */
class AssemblyModelRepository(private val api: MaterialFlowApi, private val cacheDir: File) {
    suspend fun loadPublishedModel(modelCode: String): Result<CachedAssemblyModel> = runCatching {
        require(modelCode.isNotBlank()) { "modelCode 不能为空" }
        val meta = api.publishedAssemblyModel(modelCode)
        require(meta.modelCode == modelCode) { "模型元数据编码不匹配" }
        require(meta.version > 0 && meta.byteSize >= 0 && meta.sha256.matches(HEX_SHA256)) { "模型元数据无效" }
        val target = File(cacheDir, "${safeName(meta.modelCode)}-v${meta.version}-${meta.sha256.lowercase()}.${meta.format.lowercase()}")
        if (target.isFile && isValid(target, meta)) return@runCatching CachedAssemblyModel(meta, target)
        target.delete()
        cacheDir.mkdirs()
        val temp = File.createTempFile(".${safeName(meta.modelCode)}-", ".download", cacheDir)
        try {
            temp.outputStream().use { it.write(api.downloadAssemblyModelContent(meta)) }
            check(isValid(temp, meta)) { "模型文件校验失败" }
            try { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE) }
            catch (_: java.nio.file.AtomicMoveNotSupportedException) { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING) }
            CachedAssemblyModel(meta, target)
        } finally { temp.delete() }
    }

    private fun isValid(file: File, meta: AssemblyModelMeta): Boolean = file.length() == meta.byteSize && sha256(file) == meta.sha256.lowercase()
    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) { val count = input.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
    private fun safeName(value: String): String = value.replace(Regex("[^A-Za-z0-9._-]"), "_")

    companion object {
        private val HEX_SHA256 = Regex("[0-9a-fA-F]{64}")
        fun from(context: Context, api: MaterialFlowApi) = AssemblyModelRepository(api, File(context.cacheDir, "assembly-models"))
    }
}
