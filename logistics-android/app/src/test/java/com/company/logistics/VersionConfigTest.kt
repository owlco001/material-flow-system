package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(65, BuildConfig.VERSION_CODE)
        assertEquals("0.5.58", BuildConfig.VERSION_NAME)
    }
}
