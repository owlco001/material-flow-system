package com.company.logistics.data.remote

import java.io.ByteArrayInputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AssemblyModelRepositoryTest {
    @Test
    fun cachedSelectionIsUploadedFromLocalFileInputStream() = runBlocking {
        val bytes = "cached-glb-content".toByteArray()
        val dir = createTempDir(prefix = "assembly-model-test-")
        try {
            val transport = CapturingTransport()
            val repository = AssemblyModelRepository(MaterialFlowApi(), dir, transport)
            val cached = repository.cacheSelectedFile("model.glb", ByteArrayInputStream(bytes))
            assertTrue(cached.file.isFile)
            assertEquals("model.glb", cached.info.displayName)
            assertEquals(bytes.size.toLong(), cached.info.byteSize)

            val result = repository.upload(
                cached.file, cached.info.displayName, "GearboxAssy", "Gearbox",
                UUID.randomUUID(), UUID.randomUUID(),
            ) { _, _ -> }

            assertEquals("GearboxAssy", result.getOrThrow().modelCode)
            assertArrayEquals(bytes, transport.received)
            assertEquals(bytes.size.toLong(), transport.contentLength)
            assertEquals("model.glb", transport.fileName)
            assertEquals("model/gltf-binary", transport.contentType)
            assertTrue(transport.content is LocalFileInputStream)
        } finally { dir.deleteRecursively() }
    }

    @Test
    fun cacheCopyStreamsToTempFileBeforeSourceIsExhausted() {
        val dir = createTempDir(prefix = "assembly-model-test-")
        try {
            val payload = ByteArray(300 * 1024)
            var delivered = 0
            var partialWriteObserved = false
            val source = object : InputStream() {
                override fun read(): Int =
                    if (delivered >= payload.size) -1 else payload[delivered++].toInt() and 0xFF

                override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                    if (delivered in 1 until payload.size && dir.listFiles().orEmpty().any { it.length() > 0 }) {
                        partialWriteObserved = true
                    }
                    if (delivered >= payload.size) return -1
                    val count = minOf(length, payload.size - delivered)
                    System.arraycopy(payload, delivered, buffer, offset, count)
                    delivered += count
                    return count
                }
            }
            val repository = AssemblyModelRepository(MaterialFlowApi(), dir, CapturingTransport())
            val cached = repository.cacheSelectedFile("model.glb", source)

            assertTrue(partialWriteObserved)
            assertEquals(payload.size.toLong(), cached.file.length())
            assertEquals(payload.size.toLong(), cached.info.byteSize)
            assertEquals(sha256Of(payload), cached.info.sha256)
            assertEquals(1, dir.listFiles().orEmpty().size)
        } finally { dir.deleteRecursively() }
    }

    @Test
    fun uploadFromMissingCachedFileFailsAsLocalReadFailure() = runBlocking {
        val dir = createTempDir(prefix = "assembly-model-test-")
        try {
            val repository = AssemblyModelRepository(MaterialFlowApi(), dir, CapturingTransport())
            val result = repository.upload(
                File(dir, "absent.glb"), "absent.glb", "GearboxAssy", "Gearbox",
                UUID.randomUUID(), UUID.randomUUID(),
            ) { _, _ -> }

            assertTrue(result.exceptionOrNull() is LocalFileReadException)
        } finally { dir.deleteRecursively() }
    }

    @Test
    fun downloadsVerifiesAndThenHitsCache() = runBlocking {
        val bytes = "glb-content".toByteArray()
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        val meta = AssemblyModelMeta("id", "GearboxAssy", "Gearbox", 2, "glb", bytes.size.toLong(), digest, "/content")
        val api = FakeModelApi(meta, bytes)
        val dir = createTempDir(prefix = "assembly-model-test-")
        try {
            val repository = AssemblyModelRepository(api, dir)
            val first = repository.loadPublishedModel("GearboxAssy").getOrThrow()
            val second = repository.loadPublishedModel("GearboxAssy").getOrThrow()
            assertEquals(first.file, second.file)
            assertEquals(1, api.downloads)
            assertTrue(first.file.isFile)
            assertEquals(bytes.size.toLong(), first.file.length())
        } finally { dir.deleteRecursively() }
    }

    private class FakeModelApi(private val meta: AssemblyModelMeta, private val bytes: ByteArray) : MaterialFlowApi() {
        var downloads = 0
        override suspend fun publishedAssemblyModel(modelCode: String) = meta
        override suspend fun downloadAssemblyModelContent(meta: AssemblyModelMeta, output: OutputStream, onProgress: (Long) -> Unit): Long {
            downloads++
            output.write(bytes)
            onProgress(bytes.size.toLong())
            return bytes.size.toLong()
        }
    }

    private class CapturingTransport : AssemblyModelTransport {
        var received = ByteArray(0)
        var contentLength = 0L
        var fileName = ""
        var contentType = ""
        lateinit var content: InputStream

        override suspend fun upload(
            modelCode: String, modelName: String, fileName: String, contentType: String,
            contentLength: Long, content: InputStream, requestId: UUID, operationId: UUID,
            onProgress: (Long) -> Unit,
        ): AssemblyModelMeta {
            this.content = content
            this.fileName = fileName
            this.contentType = contentType
            this.contentLength = contentLength
            received = content.readBytes()
            onProgress(received.size.toLong())
            return AssemblyModelMeta("id", modelCode, modelName, 1, "glb", contentLength, "a".repeat(64), "/content")
        }
    }

    private fun sha256Of(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
