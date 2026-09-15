package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(12, BuildConfig.VERSION_CODE)
        assertEquals("0.4.1", BuildConfig.VERSION_NAME)
    }
}
