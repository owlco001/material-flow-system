package com.company.logistics.data.remote

import java.io.OutputStream
import java.security.MessageDigest
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AssemblyModelRepositoryTest {
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
}
