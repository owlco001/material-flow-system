package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(60, BuildConfig.VERSION_CODE)
        assertEquals("0.5.53", BuildConfig.VERSION_NAME)
    }
}
