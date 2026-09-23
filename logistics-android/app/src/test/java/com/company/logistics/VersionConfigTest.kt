package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(19, BuildConfig.VERSION_CODE)
        assertEquals("0.5.5", BuildConfig.VERSION_NAME)
    }
}
