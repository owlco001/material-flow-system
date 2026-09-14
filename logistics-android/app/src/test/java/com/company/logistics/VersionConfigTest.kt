package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(7, BuildConfig.VERSION_CODE)
        assertEquals("0.3.6", BuildConfig.VERSION_NAME)
    }
}
