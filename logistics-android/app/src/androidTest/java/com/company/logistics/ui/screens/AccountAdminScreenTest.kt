package com.company.logistics.ui.screens

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.company.logistics.model.ManagedUser
import com.company.logistics.model.UserRole
import com.company.logistics.ui.Screen
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class AccountAdminScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun backButtonNavigatesToAdminWorkspace() {
        var destination: Screen? = null
        composeRule.setContent {
            UserManagementScreen(
                users = emptyList<ManagedUser>(),
                loading = false,
                error = null,
                onRefresh = {},
                onBack = { destination = Screen.WORKSPACE },
                onAdd = { _, _, _, _, _ -> },
            )
        }

        composeRule.onNodeWithText("返回管理员工作台").assertIsDisplayed().performClick()
        assertEquals(Screen.WORKSPACE, destination)
    }

    @Test
    fun activeUserRequiresConfirmationBeforeDeleteCallback() {
        var deletedUserId: String? = null
        composeRule.setContent {
            UserManagementScreen(
                users = listOf(
                    ManagedUser("u-1", "E-1", "员工一", UserRole.OPERATOR, true, false, null)
                ),
                loading = false,
                error = null,
                onRefresh = {},
                onBack = {},
                onDelete = { deletedUserId = it },
                onAdd = { _, _, _, _, _ -> },
            )
        }

        composeRule.onNodeWithText("停用").assertIsDisplayed().performClick()
        composeRule.onNodeWithText("确认停用").assertIsDisplayed().performClick()
        assertEquals("u-1", deletedUserId)
    }
}
