package com.company.logistics.data.remote

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Test

class MaterialFlowApiContractTest {

    @Test
    fun timelineRejectsPageSizeAboveMemoryBoundBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().handoverTimeline("work-item", pageSize = 50)
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun createHandoverRejectsNonPositiveQuantityBeforeNetworkAccess() = runBlocking {
        val error = runCatching {
            MaterialFlowApi().createHandover(
                clientOperationId = "operation",
                workItemId = "work-item",
                transferRequestId = "transfer",
                quantity = 0,
                fromLocation = "A-01",
                deviceId = null,
                receiverUserId = null,
            )
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

}
