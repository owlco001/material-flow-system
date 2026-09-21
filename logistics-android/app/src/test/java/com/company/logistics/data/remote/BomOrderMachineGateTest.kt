package com.company.logistics.data.remote

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BomOrderMachineGateTest {
    @Test
    fun unauthorizedAndConflictAreTypedAndNonRetryableConflictIsPreserved() {
        val unauthorized = ApiParser.parseError(401, "{\"code\":\"TOKEN_EXPIRED\",\"message\":\"expired\"}")
        assertEquals(401, unauthorized.statusCode)
        assertEquals("TOKEN_EXPIRED", unauthorized.code)
        val conflict = ApiParser.parseError(409, "{\"code\":\"CONFLICT\",\"message\":\"version conflict\",\"retryable\":false}")
        assertEquals(409, conflict.statusCode)
        assertEquals("CONFLICT", conflict.code)
        assertTrue(!conflict.retryable)
    }

    @Test
    fun mutatingEntryPointsRejectNonUuidBeforeNetwork() = runBlocking {
        val invalid = listOf(
            runCatching { MaterialFlowApi().commitBomImport("preview", "fixed-operation", true) },
            runCatching { MaterialFlowApi().createProductionOrder("fixed-operation", "WO-1", "产品", 1, "2099-12-31", org.json.JSONArray()) },
            runCatching { MaterialFlowApi().createDevice("fixed-operation", "D-1", "机台", "车间", null) },
            runCatching { MaterialFlowApi().assignDevice("WO-1", "M-1", "fixed-operation", "d1", 1) },
        )
        invalid.forEach { result -> assertTrue(result.exceptionOrNull() is IllegalArgumentException) }
    }
}
