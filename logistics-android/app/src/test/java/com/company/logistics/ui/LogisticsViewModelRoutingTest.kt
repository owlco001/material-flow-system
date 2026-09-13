package com.company.logistics.ui

import com.company.logistics.model.UserRole
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LogisticsViewModelRoutingTest {

    @Test
    fun everyRoleStartsAtWorkspaceAndKeepsOnlyAllowedTabs() {
        assertEquals(Screen.WORKSPACE, LogisticsViewModel.defaultScreenFor(UserRole.OPERATOR))
        assertEquals(Screen.WORKSPACE, LogisticsViewModel.defaultScreenFor(UserRole.MATERIAL))
        assertEquals(Screen.WORKSPACE, LogisticsViewModel.defaultScreenFor(UserRole.WAREHOUSE_ADMIN))
        assertEquals(Screen.WORKSPACE, LogisticsViewModel.defaultScreenFor(UserRole.ADMIN))

        assertFalse(LogisticsViewModel.tabsFor(UserRole.OPERATOR).contains(NavTab.APPROVAL))
        assertFalse(LogisticsViewModel.tabsFor(UserRole.MATERIAL).contains(NavTab.APPROVAL))
        assertTrue(LogisticsViewModel.tabsFor(UserRole.WAREHOUSE_ADMIN).contains(NavTab.APPROVAL))
        assertTrue(LogisticsViewModel.tabsFor(UserRole.ADMIN).contains(NavTab.APPROVAL))
        assertTrue(LogisticsViewModel.tabsFor(UserRole.ADMIN).contains(NavTab.WORKSPACE))
        assertFalse(LogisticsViewModel.canNavigate(UserRole.OPERATOR, Screen.APPROVAL))
        assertFalse(LogisticsViewModel.canNavigate(UserRole.WAREHOUSE_ADMIN, Screen.AUDIT))
        assertTrue(LogisticsViewModel.canNavigate(UserRole.ADMIN, Screen.AUDIT))
    }

    @Test
    fun initialUiStateIsExplicitlyRestoring() {
        assertTrue(LogisticsUiState().authState is AuthState.Restoring)
        assertFalse(LogisticsUiState().loggedIn)
    }
}
