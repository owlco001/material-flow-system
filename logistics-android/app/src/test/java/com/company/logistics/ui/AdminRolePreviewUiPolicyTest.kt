package com.company.logistics.ui

import com.company.logistics.model.UserRole
import com.company.logistics.ui.screens.AdminRolePreviewUiPolicy
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminRolePreviewUiPolicyTest {

    @Test
    fun previewEntryIsAdminOnlyAndActionsAreReadOnly() {
        assertTrue(AdminRolePreviewUiPolicy.canShowEntry(UserRole.ADMIN))
        assertFalse(AdminRolePreviewUiPolicy.canShowEntry(UserRole.OPERATOR))
        assertFalse(AdminRolePreviewUiPolicy.actionsEnabled(readOnly = true))
        assertTrue(AdminRolePreviewUiPolicy.actionsEnabled(readOnly = false))
    }
}
