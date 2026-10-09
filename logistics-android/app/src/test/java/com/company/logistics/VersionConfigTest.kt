package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(43, BuildConfig.VERSION_CODE)
        assertEquals("0.5.36", BuildConfig.VERSION_NAME)
    }
}
