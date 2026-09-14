package com.company.logistics.ui

import com.company.logistics.model.OrderDetail
import com.company.logistics.model.OrderDetailLaborSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MultiOrderDetailStateTest {
    @Test
    fun evictsLeastRecentlyUsedOrderAndKeepsCapacityAtFive() {
        val state = MultiOrderDetailState()
        repeat(5) { state.select("O${it + 1}") }
        state.select("O1")
        state.select("O6")

        assertEquals(listOf("O3", "O4", "O5", "O1", "O6"), state.orderNos)
        assertEquals(5, state.size)
        assertNull(state.state("O2"))
    }

    @Test
    fun switchingOrdersKeepsIndependentLoadingAndContent() {
        val state = MultiOrderDetailState()
        state.select("A")
        val aRequest = state.beginRefresh("A")
        state.select("B")
        val bRequest = state.beginRefresh("B")
        state.applySuccess("A", aRequest, detail("A"))

        assertEquals("B", state.currentOrderNo)
        assertTrue(state.state("B")!!.isLoading)
        assertEquals("A", state.state("A")!!.content!!.orderNo)
    }

    @Test
    fun errorsAreIsolatedPerOrder() {
        val state = MultiOrderDetailState()
        val aRequest = state.beginRefresh("A")
        val bRequest = state.beginRefresh("B")
        state.applyError("A", aRequest, "network")
        state.applySuccess("B", bRequest, detail("B"))

        assertEquals("network", state.state("A")!!.error)
        assertNull(state.state("B")!!.error)
        assertEquals("B", state.state("B")!!.content!!.orderNo)
    }

    @Test
    fun staleRequestCannotOverwriteNewerRefreshOrDifferentOrder() {
        val state = MultiOrderDetailState()
        val oldRequest = state.beginRefresh("A")
        val newRequest = state.beginRefresh("A")
        state.select("B")

        assertFalse(state.applySuccess("A", oldRequest, detail("stale")))
        assertTrue(state.applySuccess("A", newRequest, detail("fresh")))
        assertNull(state.state("B")!!.content)
        assertEquals("fresh", state.state("A")!!.content!!.orderNo)
    }

    private fun detail(orderNo: String) = OrderDetail(
        orderId = orderNo,
        orderNo = orderNo,
        productName = null,
        orderStatus = null,
        materials = emptyList(),
        assemblyTasks = emptyList(),
        laborSummary = OrderDetailLaborSummary(0, 0, 0),
        timeline = emptyList(),
        page = 1,
        pageSize = 20,
        total = 0,
    )
}


class MultiOrderDetailStateBoundedTest {
    @Test
    fun keepsAtMostFiveOrdersAndProtectsStaleResponses() {
        val state = MultiOrderDetailState()
        val ids = (1..6).map { "ORD-$it" }
        ids.forEach { state.select(it) }
        assertEquals(5, state.size)
        assertFalse(state.orderNos.contains("ORD-1"))
        val request = state.beginRefresh("ORD-6")
        assertFalse(state.applySuccess("ORD-6", request - 1, detail("ORD-6")))
        assertTrue(state.applySuccess("ORD-6", request, detail("ORD-6")))
        assertEquals("ORD-6", state.snapshot().selectedOrderNo)
        assertEquals("ORD-6", state.snapshot().entries.single { it.orderNo == "ORD-6" }.state.content?.orderNo)
    }

    private fun detail(orderNo: String) = OrderDetail(
        orderId = "id-$orderNo", orderNo = orderNo, productName = null, orderStatus = "OPEN",
        materials = emptyList(), assemblyTasks = emptyList(),
        laborSummary = com.company.logistics.model.OrderDetailLaborSummary(0, 0, 0),
        timeline = emptyList(), page = 1, pageSize = 20, total = 0,
    )
}
