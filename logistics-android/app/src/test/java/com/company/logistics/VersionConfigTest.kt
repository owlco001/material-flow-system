package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(20, BuildConfig.VERSION_CODE)
        assertEquals("0.5.6", BuildConfig.VERSION_NAME)
    }
}
