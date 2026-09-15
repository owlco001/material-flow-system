package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(11, BuildConfig.VERSION_CODE)
        assertEquals("0.4.0", BuildConfig.VERSION_NAME)
    }
}
