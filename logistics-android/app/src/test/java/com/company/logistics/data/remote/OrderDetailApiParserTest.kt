package com.company.logistics.data.remote

import org.junit.Assert.assertEquals
import org.junit.Test

class OrderDetailApiParserTest {
    @Test fun parsesServerAggregateFactsWithoutDerivingFields() {
        val detail = ApiParser.parseOrderDetail("""{"orderId":"o1","orderNo":"PO-1","orderStatus":"IN_PROGRESS","materials":[{"materialId":"m1","materialCode":"M-1","materialName":"螺栓","requiredQuantity":2,"arrivedQuantity":1,"inStockQuantity":0,"statusCode":"OUT_OF_STOCK"}],"assemblyTasks":[{"taskId":"t1","deviceId":"d1","deviceNo":"D-1","status":"IN_PROGRESS","progressStage":2,"taskVersion":7,"assignedAssemblerId":"u1"}],"laborSummary":{"assemblyLaborMinutes":10,"temporaryTransferLaborMinutes":3,"totalLaborMinutes":13},"timeline":[{"type":"ASSEMBLY_STARTED","entityId":"t1","status":"IN_PROGRESS","serverTime":"2026-01-01T00:00:00Z","actorId":"u1"}],"page":1,"pageSize":20,"total":1}""")
        assertEquals("PO-1", detail.orderNo)
        assertEquals(7, detail.assemblyTasks.single().taskVersion)
        assertEquals(13, detail.laborSummary.totalLaborMinutes)
        assertEquals("ASSEMBLY_STARTED", detail.timeline.single().type)
    }
}
