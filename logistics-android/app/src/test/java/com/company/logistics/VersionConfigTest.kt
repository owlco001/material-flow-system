package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(15, BuildConfig.VERSION_CODE)
        assertEquals("0.5.1", BuildConfig.VERSION_NAME)
    }
}
