package com.company.logistics.data.remote

import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceViewRole
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminRolePreviewParserTest {

    @Test
    fun parsesPreviewMetadataForSummaryAndItems() {
        val summary = ApiParser.parseWorkspaceSummary(
            """
            {"role":"MATERIAL","preview":true,"authenticatedRole":"ADMIN"}
            """.trimIndent()
        )
        val page = ApiParser.parseWorkspaceMaterialItems(
            """
            {"items":[],"preview":true,"authenticatedRole":"ADMIN","page":1,"pageSize":20}
            """.trimIndent()
        )

        assertEquals(UserRole.MATERIAL, summary.role)
        assertTrue(summary.preview)
        assertEquals(UserRole.ADMIN, summary.authenticatedRole)
        assertTrue(page.preview)
        assertEquals(UserRole.ADMIN, page.authenticatedRole)
        assertEquals(WorkspaceViewRole.MATERIAL, WorkspaceViewRole.from(summary.role))
    }

    @Test
    fun oldWorkspaceResponsesRemainNonPreview() {
        val summary = ApiParser.parseWorkspaceSummary("{\"role\":\"ADMIN\"}")
        val page = ApiParser.parseWorkspaceMaterialItems("{\"items\":[]}")

        assertFalse(summary.preview)
        assertFalse(page.preview)
    }
}
