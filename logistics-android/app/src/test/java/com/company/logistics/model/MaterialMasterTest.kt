package com.company.logistics.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class MaterialMasterTest {

    @Test
    fun parsesAndBuildsRowsSkippingBlanks() {
        val m = MaterialMaster.parse(
            """{"code":"30101001-00002","itemForm":"制造件","storageLocation":"外购件库","drawingNo":"BY1001-100001",
               "t6Code":null,"deviceNo":"Z99","projectNo":"","brand":null,"unitWeight":1.314,"weightUnitName":"千克",
               "effectiveUntil":"9999-12-31","effective":true,"refCost":12.5}"""
        )
        assertEquals("30101001-00002", m.code)
        assertNull(m.t6Code)
        assertNull(m.projectNo)
        assertEquals(
            listOf("形态" to "制造件", "存储地点" to "外购件库", "U9图号" to "BY1001-100001",
                "设备编号" to "Z99", "单位重量" to "1.314 千克"),
            m.rows,
        )
    }

    @Test
    fun showsExpiryOnlyWhenNotForever() {
        val m = MaterialMaster.parse("""{"code":"A","effectiveUntil":"2026-09-18","effective":false,"unitWeight":0}""")
        assertFalse(m.effective)
        assertEquals(listOf("失效日期" to "2026-09-18"), m.rows)
    }
}
