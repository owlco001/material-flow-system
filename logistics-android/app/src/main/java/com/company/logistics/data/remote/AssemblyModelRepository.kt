package com.company.logistics.data.remote

import android.content.ContentResolver
import android.database.Cursor
import android.net.Uri
import java.io.File
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID

const val MAX_ASSEMBLY_MODEL_BYTES = 15L * 1024L * 1024L

data class AssemblyModelMeta(
    val modelId: String, val modelCode: String, val modelName: String, val version: Int,
    val format: String, val byteSize: Long, val sha256: String, val downloadPath: String,
    val status: String = "",
)
data class CachedAssemblyModel(val meta: AssemblyModelMeta, val file: File)
data class AssemblyModelFileInfo(val displayName: String, val byteSize: Long, val sha256: String)

/** SAF-backed streaming validation, upload, and private cache for debug model tooling. */
class AssemblyModelRepository(private val api: MaterialFlowApi, private val cacheDir: File) {
    fun inspect(resolver: ContentResolver, uri: Uri): AssemblyModelFileInfo {
        val name = displayName(resolver, uri)
        require(name.endsWith(".glb", ignoreCase = true)) { "仅支持 .glb 文件" }
        resolver.openInputStream(uri)?.use { input ->
            val digest = MessageDigest.getInstance("SHA-256")
            val size = digestAndCount(input, digest)
            require(size > 0) { "GLB 文件不能为空" }
            return AssemblyModelFileInfo(name, size, digest.hex())
        } ?: error("无法读取所选文件")
    }

    suspend fun upload(
        resolver: ContentResolver, uri: Uri, modelCode: String, modelName: String,
        requestId: UUID, operationId: UUID, onProgress: (sent: Long, total: Long) -> Unit,
    ): Result<AssemblyModelMeta> = runCatching {
        require(modelCode.matches(Regex("[A-Za-z0-9._-]{1,64}"))) { "modelCode 格式无效" }
        require(modelName.trim().length in 1..128) { "modelName 长度无效" }
        val info = inspect(resolver, uri)
        val stream = resolver.openInputStream(uri) ?: error("无法读取所选文件")
        api.uploadAssemblyModel(modelCode, modelName.trim(), info.displayName, "model/gltf-binary", info.byteSize, stream, requestId, operationId) { sent -> onProgress(sent, info.byteSize) }
    }

    suspend fun loadPublishedModel(modelCode: String): Result<CachedAssemblyModel> = runCatching {
        val meta = api.publishedAssemblyModel(modelCode)
        require(meta.modelCode == modelCode && meta.version > 0 && meta.byteSize in 1..MAX_ASSEMBLY_MODEL_BYTES) { "模型元数据无效" }
        require(meta.sha256.matches(Regex("[0-9a-fA-F]{64}"))) { "模型摘要无效" }
        val target = File(cacheDir, "${safeName(meta.modelCode)}-v${meta.version}-${meta.sha256.lowercase()}.glb")
        if (!target.isFile || target.length() != meta.byteSize || sha256(target) != meta.sha256.lowercase()) {
            target.delete(); cacheDir.mkdirs()
            val temp = File.createTempFile(".assembly-", ".download", cacheDir)
            try {
                temp.outputStream().use { it.write(api.downloadAssemblyModelContent(meta)) }
                check(temp.length() == meta.byteSize && sha256(temp) == meta.sha256.lowercase()) { "模型文件校验失败" }
                try { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE) }
                catch (_: java.nio.file.AtomicMoveNotSupportedException) { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING) }
            } finally { temp.delete() }
        }
        CachedAssemblyModel(meta, target)
    }

    private fun displayName(resolver: ContentResolver, uri: Uri): String {
        val name = resolver.query(uri, arrayOf("_display_name"), null, null, null)?.use(Cursor::getFirstString)
        return name?.takeIf { it.isNotBlank() } ?: uri.lastPathSegment?.substringAfterLast('/') ?: "model.glb"
    }
    private fun digestAndCount(input: InputStream, digest: MessageDigest): Long {
        val buffer = ByteArray(64 * 1024); var size = 0L
        while (true) { val count = input.read(buffer); if (count < 0) break; size += count; require(size <= MAX_ASSEMBLY_MODEL_BYTES) { "GLB 文件不能超过 15 MiB" }; digest.update(buffer, 0, count) }
        return size
    }
    private fun sha256(file: File): String = file.inputStream().use { input -> MessageDigest.getInstance("SHA-256").let { d -> digestAndCount(input, d); d.hex() } }
    private fun safeName(value: String) = value.replace(Regex("[^A-Za-z0-9._-]"), "_")
    private fun MessageDigest.hex() = digest().joinToString("") { "%02x".format(it) }

    companion object { fun from(context: android.content.Context, api: MaterialFlowApi) = AssemblyModelRepository(api, File(context.cacheDir, "assembly-models")) }
}

private fun Cursor.getFirstString(index: Int = 0): String? = if (moveToFirst() && !isNull(index)) getString(index) else null
