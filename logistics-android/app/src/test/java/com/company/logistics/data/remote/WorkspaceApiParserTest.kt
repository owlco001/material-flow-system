package com.company.logistics.data.remote

import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMetricKey
import com.company.logistics.model.ServerWorkspaceSummaryFactory
import com.company.logistics.ui.theme.MaterialStatusColors
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkspaceApiParserTest {

    @Test
    fun parsesServerSummaryAndKeepsZeroAvailable() {
        val summary = ApiParser.parseWorkspaceSummary(
            """
            {
              "role":"WAREHOUSE_ADMIN",
              "pendingApprovalCount":0,
              "pendingOutboundCount":2,
              "pendingHandoverCount":null,
              "atStationCount":0,
              "pickedUpCount":0,
              "outOfStockCount":4,
              "generatedAt":"2026-09-14T01:02:03Z",
              "serverTime":"2026-09-14T01:02:03Z",
              "traceId":"trace-summary"
            }
            """.trimIndent()
        )

        assertEquals(UserRole.WAREHOUSE_ADMIN, summary.role)
        assertEquals(0, summary.pendingApprovalCount)
        assertEquals(2, summary.pendingOutboundCount)
        assertNull(summary.pendingHandoverCount)
        assertEquals("trace-summary", summary.traceId)

        val view = ServerWorkspaceSummaryFactory.from(summary)
        assertEquals(0, view.metric(WorkspaceMetricKey.PENDING_APPROVAL).count)
        assertTrue(view.metric(WorkspaceMetricKey.PENDING_APPROVAL).available)
        assertFalse(view.metric(WorkspaceMetricKey.PENDING_HANDOVER).available)
        assertNull(view.metric(WorkspaceMetricKey.PENDING_HANDOVER).count)
    }

    @Test
    fun parsesConfirmedOutboundCountOnlyFromServerStatusCounts() {
        val summary = ApiParser.parseWorkspaceSummary(
            """
            {
              "role":"MATERIAL",
              "pendingOutboundCount":2,
              "statusCounts":{"OUTBOUND_CONFIRMED":3}
            }
            """.trimIndent()
        )

        val view = ServerWorkspaceSummaryFactory.from(summary)
        assertEquals(2, view.metric(WorkspaceMetricKey.OUTBOUND_PENDING).count)
        assertEquals(3, view.metric(WorkspaceMetricKey.OUTBOUND_CONFIRMED).count)
        assertTrue(view.metric(WorkspaceMetricKey.OUTBOUND_CONFIRMED).available)
    }

    @Test
    fun parsesPagedWorkspaceItemWithoutInventingMissingFields() {
        val page = ApiParser.parseWorkspaceMaterialItems(
            """
            {
              "items":[{
                "id":"wi-1",
                "requirementId":"req-1",
                "orderNo":"PO-1",
                "productName":"产品",
                "orderStatus":"RELEASED",
                "deviceId":"dev-1",
                "deviceType":"BUFFER",
                "deviceNo":"HZ01",
                "materialId":"mat-1",
                "materialCode":"MAT-001",
                "materialName":"控制柜",
                "specification":"A",
                "unit":"件",
                "requiredQuantity":20,
                "arrivedQuantity":20,
                "inStockQuantity":0,
                "issuedQuantity":1,
                "pickedQuantity":1,
                "statusCode":"AT_STATION",
                "statusLabel":"已到机台",
                "label":"已到机台",
                "colorToken":"status-green",
                "statusDomain":"WORKSPACE",
                "updatedAt":"2026-09-14T01:02:03Z",
                "assignedUserId":"u-1",
                "assignedUserName":"操作员",
                "currentOwnerUserId":"u-1",
                "currentOwnerName":"操作员",
                "responsibilitySummary":{
                  "assignedUserId":"u-1",
                  "assignedUserName":"操作员",
                  "currentOwnerUserId":"u-1",
                  "currentOwnerName":"操作员"
                },
                "lastHandoverId":"ho-1",
                "lastHandoverStatus":"CONFIRMED",
                "lastHandover":{
                  "id":"ho-1",
                  "status":"CONFIRMED",
                  "quantity":1,
                  "fromLocation":"A-01-03",
                  "deviceId":"dev-1",
                  "transferRequestId":"tr-1",
                  "senderUserId":"u-material",
                  "senderName":"物料员",
                  "receiverUserId":"u-1",
                  "receiverName":"操作员",
                  "initiatedAt":"2026-09-14T01:00:00Z",
                  "confirmedBy":"u-1",
                  "confirmedAt":"2026-09-14T01:02:03Z",
                  "remark":"已送达"
                },
                "handoverSummary":{
                  "lastHandoverId":"ho-1",
                  "lastStatus":"CONFIRMED",
                  "lastInitiatedAt":"2026-09-14T01:00:00Z",
                  "lastConfirmedAt":"2026-09-14T01:02:03Z",
                  "count":1
                },
                "transferRequestId":"tr-1",
                "transferStatus":"EXECUTED"
              }],
              "page":2,
              "pageSize":20,
              "total":21,
              "totalPages":2,
              "serverTime":"2026-09-14T01:02:03Z",
              "traceId":"trace-items"
            }
            """.trimIndent()
        )

        assertEquals(2, page.page)
        assertEquals(20, page.pageSize)
        assertEquals(21, page.total)
        assertEquals(2, page.totalPages)
        val item = page.items.single()
        assertEquals("AT_STATION", item.statusCode)
        assertEquals("已到机台", item.statusLabel)
        assertEquals("status-green", item.colorToken)
        assertEquals("u-1", item.responsibilitySummary?.currentOwnerUserId)
        assertEquals("ho-1", item.lastHandover?.id)
        assertEquals("tr-1", item.transferRequestId)
        assertEquals("✓", item.statusSymbol)
        assertEquals(MaterialStatusColors.InStock, item.statusColor)
    }

    @Test
    fun unknownWorkspaceStatusUsesSafeDisplayWithoutChangingServerText() {
        val page = ApiParser.parseWorkspaceMaterialItems(
            """
            {"items":[{"id":"wi-unknown","orderNo":"PO-1","materialId":"mat-1",
              "materialCode":"MAT-001","materialName":"物料","statusCode":"FUTURE_STATUS",
              "statusLabel":"未来状态","colorToken":"new-color","statusDomain":"FUTURE"}],
             "page":1,"pageSize":20,"total":1,"totalPages":1}
            """.trimIndent()
        )

        val item = page.items.single()
        assertEquals("未来状态", item.statusLabel)
        assertEquals("?", item.statusSymbol)
        assertEquals(MaterialStatusColors.Unknown, item.statusColor)
    }

    @Test
    fun parsesHandoverActionAndSnakeCaseTimelineEventsWithoutExposingSourceIp() {
        val action = ApiParser.parseHandoverAction(
            """
            {"handoverId":"ho-1","status":"CONFIRMED",
             "eventTypes":["HANDOVER_CONFIRMED","MATERIAL_AT_STATION"],
             "traceId":"trace-1","idempotent":true}
            """.trimIndent()
        )
        val timeline = ApiParser.parseHandoverTimeline(
            """
            {"handoverId":"ho-1","workItemId":"wi-1","status":"CONFIRMED",
             "workspaceStatus":"AT_STATION","items":[
               {"id":"event-1","event_type":"HANDOVER_CONFIRMED",
                "actor_user_id":"u-1","actor_role":"OPERATOR",
                "request_id":"request-1","client_operation_id":"op-1",
                "server_time":"2026-09-14T01:02:03Z","source_ip":"not-for-ui"}
             ],"serverTime":"2026-09-14T01:02:03Z"}
            """.trimIndent()
        )

        assertEquals("ho-1", action.handoverId)
        assertTrue(action.idempotent)
        assertEquals(listOf("HANDOVER_CONFIRMED", "MATERIAL_AT_STATION"), action.eventTypes)
        assertEquals("HANDOVER_CONFIRMED", timeline.items.single().eventType)
        assertEquals("u-1", timeline.items.single().actorUserId)
        assertEquals("request-1", timeline.items.single().requestId)
    }
}
