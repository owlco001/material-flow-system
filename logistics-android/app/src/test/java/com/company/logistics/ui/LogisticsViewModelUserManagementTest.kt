package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.DeleteUserResult
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.LoginResult
import com.company.logistics.model.ManagedUser
import com.company.logistics.model.User
import com.company.logistics.model.UserRole
import com.company.logistics.model.WorkspaceMaterialItemsPage
import com.company.logistics.model.WorkspaceSummary
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LogisticsViewModelUserManagementTest {

    @Test
    fun deleteUserExposesLoadingThenSuccessAndRefreshesList() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeUserRepository()
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("admin", "password", "device", remember = false)
        viewModel.loadManagedUsers()
        viewModel.deleteManagedUser("u-1")

        assertEquals("u-1", viewModel.state.value.managedUserDeletingId)
        assertTrue(repository.deleteOperationId != null)

        repository.deleteResult.complete(
            Result.success(DeleteUserResult("u-1", "DELETED", false, "server", "trace"))
        )

        assertNull(viewModel.state.value.managedUserDeletingId)
        assertEquals("用户已停用", viewModel.state.value.managedUsersSuccess)
        assertEquals(2, repository.listUsersCalls)
        scope.cancel()
    }

    @Test
    fun deleteUserMapsConflictToSafeFailureState() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val repository = FakeUserRepository()
        repository.deleteResult.complete(
            Result.failure(ApiException(409, "USER_HAS_ACTIVE_BUSINESS", "server detail"))
        )
        val viewModel = LogisticsViewModel(repository, scope)

        viewModel.login("admin", "password", "device", remember = false)
        viewModel.deleteManagedUser("u-1")

        assertNull(viewModel.state.value.managedUserDeletingId)
        assertEquals("用户存在未完成业务，无法停用", viewModel.state.value.managedUsersError)
        scope.cancel()
    }

    private class FakeUserRepository : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val deleteResult = CompletableDeferred<Result<DeleteUserResult>>()
        var deleteOperationId: String? = null
        var listUsersCalls = 0

        override suspend fun login(
            username: String,
            password: String,
            deviceId: String,
            remember: Boolean,
        ) = LoginResult(
            accessToken = "access",
            refreshToken = null,
            expiresAt = null,
            mustChangePassword = false,
            user = User("admin-1", username, "管理员", UserRole.ADMIN),
        )

        override suspend fun listUsers(): Result<List<ManagedUser>> {
            listUsersCalls += 1
            return Result.success(listOf(ManagedUser("u-1", "E-1", "员工一", UserRole.OPERATOR, true, false, null)))
        }

        override suspend fun deleteUser(userId: String, clientOperationId: String): Result<DeleteUserResult> {
            deleteOperationId = clientOperationId
            return deleteResult.await()
        }

        override suspend fun workspaceSummary(): Result<WorkspaceSummary> = Result.success(WorkspaceSummary(role = UserRole.ADMIN))

        // 必须打桩：否则 login() 后的工作台加载会回落到真实 MaterialFlowApi 网络调用，
        // 失败时触发 expireSession() 把 authState 异步重置为 Unauthenticated，
        // 角色守卫随即短路，导致 managedUserDeletingId 断言间歇失败。
        override suspend fun workspaceMaterialItems(
            status: String?,
            orderNo: String?,
            page: Int,
            pageSize: Int,
        ): Result<WorkspaceMaterialItemsPage> = Result.success(
            WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, "server", "trace")
        )
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
    }
}
