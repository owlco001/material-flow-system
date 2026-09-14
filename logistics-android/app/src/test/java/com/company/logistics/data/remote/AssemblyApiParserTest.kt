package com.company.logistics.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AssemblyApiParserTest {

    @Test
    fun parsesBackendAssemblyTaskPageAndServerTaskVersion() {
        val page = ApiParser.parseAssemblyTaskPage(
            """
            {
              "items":[{
                "id":"task-1",
                "orderNo":"WO-1",
                "deviceId":"machine-1",
                "deviceNo":"M-01",
                "assignedAssemblerId":"assembler-1",
                "status":"IN_PROGRESS",
                "progressStage":2,
                "taskVersion":5,
                "serverTime":"2026-09-14T03:04:05+00:00"
              }],
              "page":1,
              "pageSize":20,
              "total":1,
              "totalPages":1
            }
            """.trimIndent()
        )

        assertEquals(1, page.items.size)
        assertEquals("task-1", page.items.single().id)
        assertEquals(2, page.items.single().progressStage)
        assertEquals(5, page.items.single().taskVersion)
        assertEquals("2026-09-14T03:04:05+00:00", page.items.single().serverTime)
        assertEquals(20, page.pageSize)
        assertEquals(1, page.totalPages)
    }

    @Test
    fun parsesBackendAssemblyStartAndTemporaryTransferSummariesWithoutInventingDuration() {
        val assembly = ApiParser.parseLaborRecord(
            """
            {
              "id":"lr-1",
              "laborRecordId":"lr-1",
              "type":"ASSEMBLY",
              "status":"ACTIVE",
              "startedAt":"2026-09-14T03:04:05+00:00",
              "taskVersion":3,
              "serverTime":"2026-09-14T03:04:05+00:00"
            }
            """.trimIndent()
        )
        val transfer = ApiParser.parseTemporaryTransfer(
            """
            {
              "temporaryTransferId":"tt-1",
              "status":"COMPLETED",
              "serverTime":"2026-09-14T03:10:05+00:00"
            }
            """.trimIndent()
        )

        assertEquals("lr-1", assembly.laborRecordId)
        assertEquals(3, assembly.taskVersion)
        assertEquals("ACTIVE", assembly.status)
        assertNull(assembly.durationMinutes)
        assertEquals("tt-1", transfer.temporaryTransferId)
        assertEquals("COMPLETED", transfer.status)
        assertEquals("2026-09-14T03:10:05+00:00", transfer.serverTime)
        assertNull(transfer.durationMinutes)
    }

    @Test
    fun parsesBackendMachineProgressPage() {
        val page = ApiParser.parseMachineProgressPage(
            """
            {
              "items":[{
                "device_id":"machine-1",
                "device_no":"M-01",
                "taskCount":4,
                "completedTaskCount":1,
                "progressPercent":25
              }],
              "page":2,
              "pageSize":20
            }
            """.trimIndent()
        )

        assertEquals(2, page.page)
        assertEquals("machine-1", page.items.single().deviceId)
        assertEquals(25, page.items.single().progressPercent)
        assertNull(page.items.single().laborMinutes)
    }
}
