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

    @Test fun parsesMaterialSummaryAndPreservesServerStatus() {
        val detail = ApiParser.parseOrderDetail("""{"orderId":"o1","orderNo":"PO-1","materials":[],"materialSummary":{"items":[{"materialId":"m1","materialCode":"M-1","materialName":"螺栓","unit":"件","requiredQuantity":4,"arrivedQuantity":3,"inStockQuantity":2,"shortageQuantity":2,"statusCode":"CUSTOM","statusLabel":"服务端状态"}],"totalMaterialTypes":1,"totalRequiredQuantity":4,"totalArrivedQuantity":3,"totalInStockQuantity":2,"totalShortageQuantity":2}}""")
        val summary = detail.materialSummary
        assertEquals(1, summary.totalMaterialTypes)
        assertEquals(2, summary.totalShortageQuantity)
        assertEquals("CUSTOM", summary.items.single().statusCode)
        assertEquals("服务端状态", summary.items.single().statusLabel)
    }

    @Test fun missingMaterialSummaryDefaultsToEmptySummary() {
        val detail = ApiParser.parseOrderDetail("""{"orderId":"o1","orderNo":"PO-1","materials":[]}""")
        assertEquals(emptyList<Any>(), detail.materialSummary.items)
        assertEquals(0, detail.materialSummary.totalRequiredQuantity)
    }

    @Test fun parsesMachineOrderMaterialStatusAndPreservesUnknownServerStatus() {
        val status = ApiParser.parseOrderMaterialStatus("""{"documentNo":"PO-9","documentType":"PRODUCTION_ORDER","items":[{"deviceId":"device-7","materialId":"m1","materialCode":"M-1","materialName":"螺栓","requiredQuantity":4,"arrivedQuantity":3,"inStockQuantity":2,"statusCode":"CUSTOM","statusLabel":"服务端自定义"}]}""")
        val item = status.items.single()
        assertEquals("PO-9", status.documentNo)
        assertEquals("device-7", item.deviceId)
        assertEquals("CUSTOM", item.effectiveStatusCode)
        assertEquals("服务端自定义", item.effectiveStatusLabel)
        assertEquals(2, item.shortageQuantity)
    }
}