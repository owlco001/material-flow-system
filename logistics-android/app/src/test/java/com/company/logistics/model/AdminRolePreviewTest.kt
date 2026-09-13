package com.company.logistics.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminRolePreviewTest {

    @Test
    fun controllerOnlyAllowsAdminAndNeverChangesAuthenticatedRole() {
        val controller = InMemoryAdminRolePreviewController(UserRole.ADMIN)
        assertEquals(
            WorkspaceViewRole.entries.toList(),
            listOf(
                WorkspaceViewRole.OPERATOR,
                WorkspaceViewRole.MATERIAL,
                WorkspaceViewRole.WAREHOUSE_ADMIN,
                WorkspaceViewRole.ADMIN,
            ),
        )

        val entered = controller.enterPreview(WorkspaceViewRole.MATERIAL).getOrThrow()

        assertTrue(entered.preview)
        assertEquals(UserRole.ADMIN, entered.authenticatedRole)
        assertEquals(WorkspaceViewRole.MATERIAL, entered.viewRole)
        assertEquals(WorkspaceViewRole.MATERIAL, controller.currentContext().viewRole)

        val exited = controller.exitPreview().getOrThrow()
        assertFalse(exited.preview)
        assertEquals(UserRole.ADMIN, exited.authenticatedRole)
        assertEquals(WorkspaceViewRole.ADMIN, exited.viewRole)
    }

    @Test
    fun nonAdminCannotEnterPreview() {
        val controller = InMemoryAdminRolePreviewController(UserRole.OPERATOR)

        assertTrue(controller.enterPreview(WorkspaceViewRole.ADMIN).isFailure)
        assertEquals(WorkspaceViewRole.OPERATOR, controller.currentContext().viewRole)
        assertFalse(controller.currentContext().preview)
    }
}
