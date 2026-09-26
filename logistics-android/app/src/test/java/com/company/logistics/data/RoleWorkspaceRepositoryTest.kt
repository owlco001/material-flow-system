package com.company.logistics.data

import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.LoginResult
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
import com.company.logistics.model.WorkspaceViewRole
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RoleWorkspaceRepositoryTest {

    @Test
    fun roleProjectionForwardsViewRoleAndRequestContextWithoutChangingSessionRole() = runBlocking {
        val api = CapturingApi(loginRole = UserRole.ADMIN)
        val repository = LogisticsRepository(api, NoOpDao())
        repository.login("admin", "password", "device", remember = false)

        val summary = repository.loadRoleSummary(WorkspaceViewRole.MATERIAL, "request-summary").getOrThrow()
        val page = repository.listWorkItems(
            viewRole = WorkspaceViewRole.MATERIAL,
            status = null,
            orderNo = null,
            page = 1,
            pageSize = 20,
            requestId = "request-items",
        ).getOrThrow()

        assertEquals(UserRole.ADMIN, api.authenticatedUserRole)
        assertEquals(WorkspaceViewRole.MATERIAL, api.summaryRole)
        assertEquals("request-summary", api.summaryRequestId)
        assertEquals(WorkspaceViewRole.MATERIAL, api.itemsRole)
        assertEquals("request-items", api.itemsRequestId)
        assertEquals(UserRole.MATERIAL, summary.role)
        assertTrue(page.preview)
    }

    @Test
    fun nonAdminProjectionIsRejectedBeforeApiCall() = runBlocking {
        val api = CapturingApi(loginRole = UserRole.OPERATOR)
        val repository = LogisticsRepository(api, NoOpDao())
        repository.login("operator", "password", "device", remember = false)

        val result = repository.loadRoleSummary(WorkspaceViewRole.ADMIN, "request")

        assertTrue(result.isFailure)
        assertEquals(0, api.summaryCalls)
        assertEquals("ROLE_PREVIEW_ADMIN_ONLY", (result.exceptionOrNull() as com.company.logistics.data.remote.ApiException).code)
    }

    private class CapturingApi(
        private val loginRole: UserRole,
    ) : MaterialFlowApi() {
        var authenticatedUserRole: UserRole? = null
        var summaryRole: WorkspaceViewRole? = null
        var summaryRequestId: String? = null
        var itemsRole: WorkspaceViewRole? = null
        var itemsRequestId: String? = null
        var summaryCalls = 0

        override suspend fun login(
            username: String,
            password: String,
            deviceId: String,
            clientVersion: String,
        ): LoginResult = LoginResult(
            accessToken = "access",
            refreshToken = null,
            expiresAt = null,
            mustChangePassword = false,
            user = User("u-1", username, "测试用户", loginRole),
        ).also { authenticatedUserRole = it.user.role }

        override suspend fun workspaceSummary(
            viewRole: WorkspaceViewRole?,
            requestId: String?,
            clientOperationId: String?,
        ): WorkspaceSummary {
            summaryCalls++
            summaryRole = viewRole
            summaryRequestId = requestId
            return WorkspaceSummary(
                role = viewRole?.toUserRole() ?: UserRole.ADMIN,
                preview = viewRole != null,
                authenticatedRole = UserRole.ADMIN,
            )
        }

        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
            viewRole: WorkspaceViewRole?,
            requestId: String?,
            clientOperationId: String?,
        ): WorkspaceMaterialItemsPage {
            itemsRole = viewRole
            itemsRequestId = requestId
            return WorkspaceMaterialItemsPage(
                items = emptyList(),
                page = page,
                pageSize = pageSize,
                total = 0,
                totalPages = 0,
                serverTime = null,
                traceId = null,
                preview = viewRole != null,
                authenticatedRole = UserRole.ADMIN,
            )
        }
    }

    private class NoOpDao : OfflineOperationDao {
        override fun observeAll(): Flow<List<OfflineOperationEntity>> = emptyFlow()
        override suspend fun pending(): List<OfflineOperationEntity> = emptyList()
        override fun observePendingCount(): Flow<Int> = emptyFlow()
        override suspend fun insert(operation: OfflineOperationEntity): Long = 1L
        override suspend fun updateStatus(id: String, status: String, error: String?) = Unit
        override suspend fun markSynced(id: String, status: String, serverTime: String?) = Unit
        override suspend fun clearSynced() = Unit
        override suspend fun findById(id: String): OfflineOperationEntity? = null
        override suspend fun resetStuckSyncing(): Int = 0
        override suspend fun resetToPending(id: String) = Unit
        override suspend fun deleteById(id: String) = Unit
    }
}
