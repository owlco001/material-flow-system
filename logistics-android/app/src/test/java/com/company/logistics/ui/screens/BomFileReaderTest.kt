package com.company.logistics.ui.screens

import com.company.logistics.data.remote.MaterialFlowApi
import java.io.ByteArrayInputStream
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class BomFileReaderTest {
    @Test
    fun readsExactlyTenMiB() {
        val bytes = ByteArray(MaterialFlowApi.MAX_BOM_FILE_BYTES) { 7 }
        assertArrayEquals(bytes, BomFileReader.readAtMost(ByteArrayInputStream(bytes), MaterialFlowApi.MAX_BOM_FILE_BYTES))
    }

    @Test
    fun rejectsTheFirstByteOverTenMiB() {
        val error = runCatching {
            BomFileReader.readAtMost(ByteArrayInputStream(ByteArray(MaterialFlowApi.MAX_BOM_FILE_BYTES + 1)), MaterialFlowApi.MAX_BOM_FILE_BYTES)
        }.exceptionOrNull()
        assertEquals("BOM 文件不能超过 10 MB", error?.message)
    }
}