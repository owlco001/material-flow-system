package com.company.logistics.ui.screens

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.company.logistics.model.ManagedUser
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class AccountAdminScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun backButtonCallsOnBack() {
        var wentBack = false
        composeRule.setContent {
            UserManagementScreen(
                users = emptyList<ManagedUser>(),
                loading = false,
                error = null,
                onRefresh = {},
                onBack = { wentBack = true },
                onAdd = { _, _, _, _, _ -> },
            )
        }

        composeRule.onNodeWithText("返回管理员工作台").assertIsDisplayed().performClick()
        assertTrue(wentBack)
    }
}
