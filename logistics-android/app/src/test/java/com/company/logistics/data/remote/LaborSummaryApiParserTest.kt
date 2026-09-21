package com.company.logistics.data.remote

import org.junit.Assert.assertNull
import org.junit.Assert.assertEquals
import org.junit.Test

class LaborSummaryApiParserTest {
    @Test fun parsesExactServerFieldsAndPreservesMissingMinutes() {
        val page = ApiParser.parseLaborSummary("""{"items":[{"taskId":"t1","orderNo":"WO-1","deviceId":"d1","deviceNo":"D-1","assemblerId":"u1","assemblerName":"张三","assemblyLaborMinutes":17,"temporaryTransferLaborMinutes":13,"totalLaborMinutes":30}],"page":1,"pageSize":20,"total":1}""")
        val item = page.items.single()
        assertEquals("t1", item.taskId)
        assertEquals(17, item.assemblyLaborMinutes)
        assertEquals(13, item.temporaryTransferLaborMinutes)
        assertEquals(30, item.totalLaborMinutes)
        val missing = ApiParser.parseLaborSummary("""{"items":[{"taskId":"t2","totalLaborMinutes":4}]}""").items.single()
        assertNull(missing.assemblyLaborMinutes)
        assertNull(missing.temporaryTransferLaborMinutes)
    }
}
