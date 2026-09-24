package com.company.logistics

import org.junit.Assert.assertEquals
import org.junit.Test

class VersionConfigTest {

    @Test
    fun deliveryVersionIsCurrent() {
        assertEquals(26, BuildConfig.VERSION_CODE)
        assertEquals("0.5.12", BuildConfig.VERSION_NAME)
    }
}
