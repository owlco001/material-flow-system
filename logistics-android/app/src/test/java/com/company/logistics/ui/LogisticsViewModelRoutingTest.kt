package com.company.logistics.ui

import com.company.logistics.model.UserRole
import com.company.logistics.model.ScanResult
import com.company.logistics.model.ScanType
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

    @Test
    fun unknownScanKeepsResourceIdAndNormalizedValueForAssemblyRouting() {
        val route = assemblyDeviceRoute(ScanResult(ScanType.UNKNOWN, " MACHINE-07 ", " device-7 "))

        assertEquals(AssemblyDeviceRoute("device-7", "MACHINE-07"), route)
    }

    @Test
    fun knownScanDoesNotEnterAssemblyRouting() {
        assertEquals(null, assemblyDeviceRoute(ScanResult(ScanType.MATERIAL_CODE, "MAT-1", "device-1")))
    }
}
