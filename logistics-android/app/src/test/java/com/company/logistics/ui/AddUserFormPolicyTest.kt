package com.company.logistics.ui

import com.company.logistics.model.UserRole
import com.company.logistics.ui.screens.AddUserFormPolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AddUserFormPolicyTest {
    @Test
    fun onlyAssignableRolesAreOfferedAndAdminIsExcluded() {
        assertEquals(
            listOf(UserRole.OPERATOR, UserRole.MATERIAL, UserRole.WAREHOUSE_ADMIN, UserRole.WORKSHOP_SUPERVISOR, UserRole.ASSEMBLER),
            AddUserFormPolicy.allowedRoles,
        )
        assertFalse(UserRole.ADMIN in AddUserFormPolicy.allowedRoles)
    }

    @Test
    fun submitRequiresAllRequiredFieldsAndSelectedRole() {
        assertFalse(AddUserFormPolicy.canSubmit("E-1", "张三", "temporary", null))
        assertTrue(AddUserFormPolicy.canSubmit("E-1", "张三", "temporary", UserRole.OPERATOR))
        assertFalse(AddUserFormPolicy.canSubmit("", "张三", "temporary", UserRole.OPERATOR))
        assertFalse(AddUserFormPolicy.canSubmit("E-1", "", "temporary", UserRole.OPERATOR))
        assertFalse(AddUserFormPolicy.canSubmit("E-1", "张三", "", UserRole.OPERATOR))
        assertFalse(AddUserFormPolicy.canSubmit("E-1", "张三", "temporary", UserRole.ADMIN))
    }
}
