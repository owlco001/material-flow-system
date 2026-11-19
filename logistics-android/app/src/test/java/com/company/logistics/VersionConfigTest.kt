package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(59, BuildConfig.VERSION_CODE)
        assertEquals("0.5.52", BuildConfig.VERSION_NAME)
    }
}
