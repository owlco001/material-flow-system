package com.company.logistics.data.remote

import android.content.ContentResolver
import android.database.Cursor
import android.net.Uri
import java.io.File
import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID

const val MAX_ASSEMBLY_MODEL_BYTES = 15L * 1024L * 1024L

/** Local cache/source read failure; kept distinct from transport errors for safe UI copy. */
class LocalFileReadException(message: String, cause: Throwable? = null) : IOException(message, cause)

data class AssemblyModelMeta(
    val modelId: String, val modelCode: String, val modelName: String, val version: Int,
    val format: String, val byteSize: Long, val sha256: String, val downloadPath: String,
    val status: String = "",
)
data class CachedAssemblyModel(val meta: AssemblyModelMeta, val file: File)
data class AssemblyModelFileInfo(val displayName: String, val byteSize: Long, val sha256: String)
data class CachedAssemblyModelFile(val info: AssemblyModelFileInfo, val file: File)

/** Multipart upload seam so the cached-file input path is provable without HTTP. */
interface AssemblyModelTransport {
    suspend fun upload(
        modelCode: String, modelName: String, fileName: String, contentType: String,
        contentLength: Long, content: InputStream, requestId: UUID, operationId: UUID,
        onProgress: (Long) -> Unit,
    ): AssemblyModelMeta
}

class ApiAssemblyModelTransport(private val api: MaterialFlowApi) : AssemblyModelTransport {
    override suspend fun upload(
        modelCode: String, modelName: String, fileName: String, contentType: String,
        contentLength: Long, content: InputStream, requestId: UUID, operationId: UUID,
        onProgress: (Long) -> Unit,
    ): AssemblyModelMeta = api.uploadAssemblyModel(
        modelCode, modelName, fileName, contentType, contentLength, content, requestId, operationId, onProgress,
    )
}

/** SAF-backed streaming validation, upload, and private cache for debug model tooling. */
class AssemblyModelRepository(
    private val api: MaterialFlowApi,
    private val cacheDir: File,
    private val transport: AssemblyModelTransport = ApiAssemblyModelTransport(api),
) {
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

    /** Copies a picked SAF document once into app-private cache storage. */
    fun cacheSelectedModel(resolver: ContentResolver, uri: Uri): CachedAssemblyModelFile {
        val name = displayName(resolver, uri)
        val input = try {
            resolver.openInputStream(uri) ?: throw LocalFileReadException("无法读取本地 GLB 文件")
        } catch (error: LocalFileReadException) {
            throw error
        } catch (error: IOException) {
            throw LocalFileReadException("无法读取本地 GLB 文件", error)
        }
        return cacheSelectedFile(name, input)
    }

    /** Streams one source into a private temp file, then atomically stabilizes it. */
    fun cacheSelectedFile(name: String, input: InputStream): CachedAssemblyModelFile {
        require(name.endsWith(".glb", ignoreCase = true)) { "仅支持 .glb 文件" }
        cacheDir.mkdirs()
        val temp = File.createTempFile(".assembly-upload-", ".tmp", cacheDir)
        try {
            val digest = MessageDigest.getInstance("SHA-256")
            val size = LocalFileInputStream(input).use { source ->
                temp.outputStream().use { output -> copyAndDigest(source, output, digest) }
            }
            require(size > 0) { "GLB 文件不能为空" }
            val target = File(cacheDir, ".assembly-upload-${UUID.randomUUID()}-${safeName(name)}")
            try { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE) }
            catch (_: AtomicMoveNotSupportedException) { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING) }
            return CachedAssemblyModelFile(AssemblyModelFileInfo(name, size, digest.hex()), target)
        } catch (error: LocalFileReadException) {
            throw error
        } catch (error: IOException) {
            throw LocalFileReadException("无法保存本地 GLB 文件", error)
        } finally {
            temp.delete()
        }
    }

    /** Uploads a cached GLB through its local file stream; the SAF Uri is never re-read. */
    suspend fun upload(
        file: File, fileName: String, modelCode: String, modelName: String,
        requestId: UUID, operationId: UUID, onProgress: (sent: Long, total: Long) -> Unit,
    ): Result<AssemblyModelMeta> = runCatching {
        require(modelCode.matches(Regex("[A-Za-z0-9._-]{1,64}"))) { "modelCode 格式无效" }
        require(modelName.trim().length in 1..128) { "modelName 长度无效" }
        require(fileName.isNotBlank()) { "fileName 不能为空" }
        if (!file.isFile) throw LocalFileReadException("上传文件不存在")
        val size = file.length()
        if (size !in 1..MAX_ASSEMBLY_MODEL_BYTES) throw LocalFileReadException("GLB 文件大小无效")
        val stream = try {
            file.inputStream()
        } catch (error: IOException) {
            throw LocalFileReadException("无法读取本地 GLB 文件", error)
        }
        stream.use { raw ->
            transport.upload(
                modelCode, modelName.trim(), fileName, "model/gltf-binary", size,
                LocalFileInputStream(raw), requestId, operationId,
            ) { sent -> onProgress(sent, size) }
        }
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
                val received = temp.outputStream().use { output -> api.downloadAssemblyModelContent(meta, output) }
                check(received == meta.byteSize && temp.length() == meta.byteSize && sha256(temp) == meta.sha256.lowercase()) { "模型文件校验失败" }
                try { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE) }
                catch (_: AtomicMoveNotSupportedException) { Files.move(temp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING) }
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
    private fun copyAndDigest(input: InputStream, output: OutputStream, digest: MessageDigest): Long {
        val buffer = ByteArray(64 * 1024); var size = 0L
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            size += count
            require(size <= MAX_ASSEMBLY_MODEL_BYTES) { "GLB 文件不能超过 15 MiB" }
            output.write(buffer, 0, count)
            digest.update(buffer, 0, count)
        }
        return size
    }
    private fun sha256(file: File): String = file.inputStream().use { input -> MessageDigest.getInstance("SHA-256").let { d -> digestAndCount(input, d); d.hex() } }
    private fun safeName(value: String) = value.replace(Regex("[^A-Za-z0-9._-]"), "_")
    private fun MessageDigest.hex() = digest().joinToString("") { "%02x".format(it) }

    companion object { fun from(context: android.content.Context, api: MaterialFlowApi) = AssemblyModelRepository(api, File(context.cacheDir, "assembly-models")) }
}

/** Wraps local source reads so I/O failures map to local-file UI copy, not network copy. */
internal class LocalFileInputStream(input: InputStream) : FilterInputStream(input) {
    override fun read(): Int = try { super.read() } catch (error: IOException) { throw LocalFileReadException("无法读取本地 GLB 文件", error) }
    override fun read(buffer: ByteArray, offset: Int, length: Int): Int =
        try { super.read(buffer, offset, length) } catch (error: IOException) { throw LocalFileReadException("无法读取本地 GLB 文件", error) }
}

private fun Cursor.getFirstString(index: Int = 0): String? = if (moveToFirst() && !isNull(index)) getString(index) else null
