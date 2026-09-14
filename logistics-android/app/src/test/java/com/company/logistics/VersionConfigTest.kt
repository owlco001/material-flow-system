package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(8, BuildConfig.VERSION_CODE)
        assertEquals("0.3.7", BuildConfig.VERSION_NAME)
    }
}
