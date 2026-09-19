package com.company.logistics.ui.screens

import java.io.ByteArrayOutputStream
import java.io.InputStream

internal object BomFileReader {
    fun readAtMost(input: InputStream, maxBytes: Int): ByteArray {
        require(maxBytes > 0) { "maxBytes must be positive" }
        val output = ByteArrayOutputStream(minOf(maxBytes, 8192))
        val buffer = ByteArray(8192)
        var total = 0
        while (true) {
            val read = input.read(buffer, 0, minOf(buffer.size, maxBytes - total + 1))
            if (read == -1) return output.toByteArray()
            if (read == 0) continue
            if (read > maxBytes - total) throw BomFileTooLargeException(maxBytes)
            output.write(buffer, 0, read)
            total += read
            if (total == maxBytes) {
                if (input.read() != -1) throw BomFileTooLargeException(maxBytes)
                return output.toByteArray()
            }
        }
    }
}

internal class BomFileTooLargeException(maxBytes: Int) :
    IllegalArgumentException("BOM 文件不能超过 ${maxBytes / (1024 * 1024)} MB")
