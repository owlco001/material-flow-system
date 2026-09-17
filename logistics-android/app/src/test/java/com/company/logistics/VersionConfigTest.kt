package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(13, BuildConfig.VERSION_CODE)
        assertEquals("0.4.2", BuildConfig.VERSION_NAME)
    }
}
