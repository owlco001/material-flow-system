package com.company.logistics.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RoleWorkspaceSummaryFactoryTest {

    @Test
    fun legacyOrderResponseDoesNotInventWorkflowCounts() {
        val summary = RoleWorkspaceSummaryFactory.from(
            UserRole.MATERIAL,
            order(
                item(statusCode = MaterialStatusCode.IN_STOCK),
                item(statusCode = MaterialStatusCode.ARRIVED),
            ),
        )

        assertFalse(summary.metric(WorkspaceMetricKey.OUTBOUND_PENDING).available)
        assertNull(summary.metric(WorkspaceMetricKey.OUTBOUND_PENDING).count)
        assertFalse(summary.metric(WorkspaceMetricKey.OUTBOUND_CONFIRMED).available)
        assertEquals(2, summary.metric(WorkspaceMetricKey.ALL).count)
        assertTrue(summary.metric(WorkspaceMetricKey.ALL).available)
    }

    @Test
    fun inventoryStatusCodeCannotBeMistakenForOperatorWorkflowStatus() {
        val summary = RoleWorkspaceSummaryFactory.from(
            UserRole.OPERATOR,
            order(item(statusCode = MaterialStatusCode.IN_STOCK)),
        )

        assertFalse(summary.metric(WorkspaceMetricKey.CLAIMED).available)
        assertFalse(summary.metric(WorkspaceMetricKey.AT_STATION).available)
    }

    @Test
    fun explicitServerWorkflowFieldsAreCountedByTheirDomain() {
        val summary = RoleWorkspaceSummaryFactory.from(
            UserRole.WAREHOUSE_ADMIN,
            order(
                item(workflowStatusCode = "OUTBOUND_PENDING"),
                item(approvalStatusCode = "PENDING_APPROVAL"),
                item(handoverStatusCode = "PENDING"),
                item(workflowStatusCode = "EXCEPTION"),
            ),
        )

        assertEquals(1, summary.metric(WorkspaceMetricKey.OUTBOUND_PENDING).count)
        assertEquals(1, summary.metric(WorkspaceMetricKey.EXCEPTION).count)
        assertEquals(1, summary.metric(WorkspaceMetricKey.PENDING_APPROVAL).count)
        assertEquals(1, summary.metric(WorkspaceMetricKey.PENDING_HANDOVER).count)
    }

    @Test
    fun workflowStatusTakesPrecedenceWithoutChangingInventoryStatus() {
        val item = item(
            statusCode = MaterialStatusCode.IN_STOCK,
            workflowStatusCode = "PICKED_UP",
        )
        val summary = RoleWorkspaceSummaryFactory.from(
            UserRole.OPERATOR,
            order(item),
        )

        assertEquals(MaterialStatusCode.IN_STOCK, item.statusCode)
        assertEquals("PICKED_UP", item.effectiveStatusCode)
        assertEquals(1, summary.metric(WorkspaceMetricKey.CLAIMED).count)
        assertEquals(0, summary.metric(WorkspaceMetricKey.AT_STATION).count)
    }

    @Test
    fun unknownServerStatusKeepsSafeLabelAndNeverBecomesSuccess() {
        val item = item().copy(label = "", serverStatusCode = "FUTURE_STATUS")

        assertEquals("未知状态", item.effectiveStatusLabel)
        assertEquals(MaterialStatusCode.IN_STOCK, item.statusCode)
        assertFalse(
            RoleWorkspaceSummaryFactory.from(UserRole.OPERATOR, order(item))
                .metric(WorkspaceMetricKey.AT_STATION).available
        )
    }

    @Test
    fun operatorAndAdminUseDifferentSummaryEntrances() {
        val operator = RoleWorkspaceSummaryFactory.from(UserRole.OPERATOR, order(item()))
        val admin = RoleWorkspaceSummaryFactory.from(UserRole.ADMIN, order(item()))

        assertTrue(operator.metrics.keys.containsAll(setOf(WorkspaceMetricKey.CLAIMED, WorkspaceMetricKey.AT_STATION)))
        assertTrue(admin.metrics.keys.containsAll(setOf(WorkspaceMetricKey.ALL, WorkspaceMetricKey.EXCEPTION, WorkspaceMetricKey.AUDIT)))
        assertFalse(admin.metric(WorkspaceMetricKey.AUDIT).available)
    }

    private fun order(vararg items: OrderMaterialItem) = OrderMaterialStatus(
        documentNo = "PO-TEST",
        documentType = "PRODUCTION_ORDER",
        items = items.toList(),
        serverTime = "2026-09-13T00:00:00Z",
    )

    private fun item(
        statusCode: MaterialStatusCode = MaterialStatusCode.IN_STOCK,
        workflowStatusCode: String? = null,
        approvalStatusCode: String? = null,
        handoverStatusCode: String? = null,
    ) = OrderMaterialItem(
        deviceId = "device-1",
        deviceType = "测试机台",
        deviceNo = "D-01",
        materialId = "material-1",
        materialCode = "MAT-001",
        name = "测试物料",
        specification = null,
        requiredQuantity = 1,
        arrivedQuantity = 1,
        inStockQuantity = 1,
        statusCode = statusCode,
        label = statusCode.label,
        colorToken = "IN_STOCK",
        workflowStatusCode = workflowStatusCode,
        approvalStatusCode = approvalStatusCode,
        handoverStatusCode = handoverStatusCode,
    )
}
