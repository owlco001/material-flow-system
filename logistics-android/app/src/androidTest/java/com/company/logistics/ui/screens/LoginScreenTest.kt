package com.company.logistics.ui.screens

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.platform.LocalContext
import com.company.logistics.data.EndpointStore
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class LoginScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun unauthenticatedLoginShowsEndpointSetupEntry() {
        var opened = false
        composeRule.setContent {
            LoginScreen(
                loading = false,
                errorMessage = null,
                deviceId = "device-1",
                onLogin = { _, _, _, _ -> },
                endpointConfigured = false,
                onOpenEndpointConfig = { opened = true },
            )
        }

        composeRule.onNodeWithText("博阳智造").assertIsDisplayed()
        composeRule.onNodeWithText("尚未配置后端").assertIsDisplayed()
        composeRule.onNodeWithText("立即设置后端").performClick()
        assertEquals(true, opened)
    }

    @Test
    fun configuredLoginShowsBackendSettingsEntry() {
        var opened = false
        composeRule.setContent {
            LoginScreen(
                loading = false,
                errorMessage = null,
                deviceId = "device-1",
                onLogin = { _, _, _, _ -> },
                endpointConfigured = true,
                onOpenEndpointConfig = { opened = true },
            )
        }

        composeRule.onNodeWithText("后端设置").assertIsDisplayed().performClick()
        assertEquals(true, opened)
    }

    @Test
    fun endpointConfigBackReturnsToUnauthenticatedLoginRoute() {
        var returned = false
        composeRule.setContent {
            EndpointConfigScreen(
                store = EndpointStore.get(LocalContext.current),
                onEndpointChanged = {},
                onBack = { returned = true },
            )
        }

        composeRule.onNodeWithText("返回").performClick()
        assertEquals(true, returned)
    }
}
