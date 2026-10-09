package com.company.logistics.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdatePolicyTest {

    private val json = """{"versionCode":42,"versionName":"0.5.35","notes":"修复扫码","force":false,
        "sha256":"x","size":10485760,"publishedAt":"2026-10-09T02:00:00Z","downloadUrl":"/api/app/download"}"""

    @Test
    fun parsesAndResolvesRelativeDownloadUrl() {
        val r = AppUpdatePolicy.parse(json, "https://mf.example.com/")!!
        assertEquals(42, r.versionCode)
        assertEquals("0.5.35", r.versionName)
        assertEquals("修复扫码", r.notes)
        assertEquals("https://mf.example.com/api/app/download", r.downloadUrl)
        assertEquals("10.0 MB", AppUpdatePolicy.formatSize(r.sizeBytes))
    }

    @Test
    fun keepsAbsoluteDownloadUrl() {
        assertEquals("https://cdn.example.com/a.apk",
            AppUpdatePolicy.resolveUrl("https://mf.example.com", "https://cdn.example.com/a.apk"))
    }

    @Test
    fun invalidJsonReturnsNull() {
        assertNull(AppUpdatePolicy.parse("not json", "https://x"))
        assertNull(AppUpdatePolicy.parse("""{"versionCode":0,"versionName":"1"}""", "https://x"))
    }

    @Test
    fun newerVersionIsAvailableOtherwiseUpToDate() {
        val r = AppUpdatePolicy.parse(json, "https://x")
        assertTrue(AppUpdatePolicy.decide(41, r) is UpdateCheckResult.Available)
        assertEquals(UpdateCheckResult.UpToDate, AppUpdatePolicy.decide(42, r))
        assertEquals(UpdateCheckResult.UpToDate, AppUpdatePolicy.decide(50, r))
        assertTrue(AppUpdatePolicy.decide(41, null) is UpdateCheckResult.Failed)
    }
}
