package com.company.logistics.ui.screens

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.company.logistics.model.RoleWorkspaceSummary
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceViewRole
import com.company.logistics.ui.Screen
import com.company.logistics.ui.WorkspaceLoadState
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class WorkspaceScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun ordinaryRoleBackendSettingsEntryRoutesToEndpointConfig() {
        var destination: Screen? = null
        composeRule.setContent {
            WorkspaceScreen(
                role = UserRole.OPERATOR,
                authenticatedRole = UserRole.OPERATOR,
                previewRole = null,
                summary = RoleWorkspaceSummary.empty(UserRole.OPERATOR),
                items = emptyList(),
                summaryState = WorkspaceLoadState.IDLE,
                summaryError = null,
                summaryUnavailable = false,
                itemsState = WorkspaceLoadState.EMPTY,
                itemsError = null,
                itemsUnavailable = false,
                page = 1,
                pageSize = 20,
                total = 0,
                totalPages = 0,
                serverTime = null,
                onRefresh = {},
                onPageSizeChange = {},
                onNextPage = {},
                onPreviousPage = {},
                onOpenApproval = {},
                onOpenAudit = {},
                currentUserId = null,
                timelineItemId = null,
                timeline = null,
                timelineState = WorkspaceLoadState.IDLE,
                timelineError = null,
                handoverSubmittingId = null,
                onOpenTimeline = {},
                onRetryTimeline = {},
                onHandoverAction = { _, _, _ -> },
                onCreateHandover = { _, _, _, _ -> },
                onEnterPreview = {},
                onExitPreview = {},
                onOpenEndpointConfig = { destination = Screen.ENDPOINT_CONFIG },
            )
        }

        composeRule.onNodeWithText("后端设置").performClick()
        assertEquals(Screen.ENDPOINT_CONFIG, destination)
    }

    @Test
    fun adminSeesPreviewEntryAndPreviewBannerIsExplicitlyReadOnly() {
        composeRule.setContent {
            WorkspaceScreen(
                role = UserRole.MATERIAL,
                authenticatedRole = UserRole.ADMIN,
                previewRole = WorkspaceViewRole.MATERIAL,
                summary = RoleWorkspaceSummary.empty(UserRole.MATERIAL),
                items = emptyList(),
                summaryState = WorkspaceLoadState.IDLE,
                summaryError = null,
                summaryUnavailable = false,
                itemsState = WorkspaceLoadState.EMPTY,
                itemsError = null,
                itemsUnavailable = false,
                page = 1,
                pageSize = 20,
                total = 0,
                totalPages = 0,
                serverTime = null,
                onRefresh = {},
                onPageSizeChange = {},
                onNextPage = {},
                onPreviousPage = {},
                onOpenApproval = {},
                onOpenAudit = {},
                currentUserId = "admin-1",
                timelineItemId = null,
                timeline = null,
                timelineState = WorkspaceLoadState.IDLE,
                timelineError = null,
                handoverSubmittingId = null,
                onOpenTimeline = {},
                onRetryTimeline = {},
                onHandoverAction = { _, _, _ -> },
                onCreateHandover = { _, _, _, _ -> },
                onEnterPreview = {},
                onExitPreview = {},
            )
        }

        composeRule.onNodeWithText("测试预览，只读，不代表当前账号权限").assertIsDisplayed()
        composeRule.onNodeWithText("恢复 ADMIN").assertIsDisplayed()
    }

    @Test
    fun nonAdminDoesNotSeePreviewEntry() {
        composeRule.setContent {
            WorkspaceScreen(
                role = UserRole.OPERATOR,
                authenticatedRole = UserRole.OPERATOR,
                previewRole = null,
                summary = RoleWorkspaceSummary.empty(UserRole.OPERATOR),
                items = emptyList(),
                summaryState = WorkspaceLoadState.IDLE,
                summaryError = null,
                summaryUnavailable = false,
                itemsState = WorkspaceLoadState.EMPTY,
                itemsError = null,
                itemsUnavailable = false,
                page = 1,
                pageSize = 20,
                total = 0,
                totalPages = 0,
                serverTime = null,
                onRefresh = {},
                onPageSizeChange = {},
                onNextPage = {},
                onPreviousPage = {},
                onOpenApproval = {},
                onOpenAudit = {},
                currentUserId = "operator-1",
                timelineItemId = null,
                timeline = null,
                timelineState = WorkspaceLoadState.IDLE,
                timelineError = null,
                handoverSubmittingId = null,
                onOpenTimeline = {},
                onRetryTimeline = {},
                onHandoverAction = { _, _, _ -> },
                onCreateHandover = { _, _, _, _ -> },
                onEnterPreview = {},
                onExitPreview = {},
            )
        }

        composeRule.onAllNodesWithText("测试角色视图").assertCountEquals(0)
    }
}
