package com.company.logistics.data.remote

import org.junit.Assert.assertEquals
import org.junit.Test

class AssemblyStageApiParserTest {
    @Test fun parsesIndependentThreeStageStateAndOperationFields() {
        val task = ApiParser.parseAssemblyTask("""{"id":"t1","deviceId":"d1","deviceNo":"M1","status":"IN_PROGRESS","taskVersion":4,"stages":[{"stageNo":1,"status":"COMPLETED","version":2},{"stageNo":2,"status":"REWORK_REQUIRED","version":3,"reworkReason":"尺寸不符"},{"stageNo":3,"status":"NOT_STARTED","version":1}]}""")
        assertEquals(listOf("COMPLETED", "REWORK_REQUIRED", "NOT_STARTED"), task.stages.map { it.status.code })
        assertEquals("尺寸不符", task.stages[1].reworkReason)
        val result = ApiParser.parseAssemblyStageOperation("""{"taskId":"t1","stageNo":2,"status":"IN_PROGRESS","version":4,"serverTime":"now","traceId":"trace"}""")
        assertEquals(2, result.stageNo)
        assertEquals(4, result.version)
    }
}
