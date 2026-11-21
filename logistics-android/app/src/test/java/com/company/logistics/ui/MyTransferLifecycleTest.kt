package com.company.logistics.ui

import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineOperationDao
import com.company.logistics.data.OfflineOperationEntity
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import org.junit.Assert.*
import org.junit.Test
import kotlin.coroutines.Continuation
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

/** Uncancellable continuations exercise ownership checks independently of cancellation. */
@OptIn(ExperimentalCoroutinesApi::class)
class MyTransferLifecycleTest {
    @Test fun oldSuccessAfterLogoutAndReloginCannotFinishNewUsersLoad() = fixture { vm, repo ->
        vm.loadMyTransferRequests()
        val old = repo.requests.single()
        vm.logout()
        vm.login("B", "password", "device", false)
        vm.loadMyTransferRequests()
        val current = repo.requests.last()

        old.complete(Result.success(page(row("old-A", "A"))))
        assertTrue(vm.state.value.myTransferRequestsLoading)
        assertTrue(vm.state.value.myTransferRequests.isEmpty())
        current.complete(Result.success(page(row("new-B", "B"))))
        assertEquals(listOf("new-B"), vm.state.value.myTransferRequests.map { it.id })
        assertFalse(vm.state.value.myTransferRequestsLoading)
    }

    @Test fun oldFailureCannotSetErrorOrReleaseNewUsersLoading() = fixture { vm, repo ->
        vm.loadMyTransferRequests()
        val old = repo.requests.single()
        vm.logout()
        vm.login("B", "password", "device", false)
        vm.loadMyTransferRequests()
        old.complete(Result.failure(IllegalStateException("old failure")))
        assertNull(vm.state.value.myTransferRequestsError)
        assertTrue(vm.state.value.myTransferRequestsLoading)
        repo.requests.last().complete(Result.success(page(row("B", "B"))))
        assertEquals(listOf("B"), vm.state.value.myTransferRequests.map { it.id })
    }

    @Test fun logoutAloneRejectsLateSuccessAndFailure() = fixture { vm, repo ->
        for (response in listOf(Result.success(page(row("A", "A"))), Result.failure(IllegalStateException("late")))) {
            vm.loadMyTransferRequests()
            val old = repo.requests.last()
            vm.logout()
            old.complete(response)
            assertFalse(vm.state.value.loggedIn)
            assertTrue(vm.state.value.myTransferRequests.isEmpty())
            assertFalse(vm.state.value.myTransferRequestsLoading)
            assertNull(vm.state.value.myTransferRequestsError)
            vm.login("A", "password", "device", false)
        }
    }

    @Test fun newLoginWithoutLogoutInvalidatesOldPersonalLoad() = fixture { vm, repo ->
        vm.loadMyTransferRequests()
        val old = repo.requests.single()
        vm.login("B", "password", "device", false)
        assertFalse(vm.state.value.myTransferRequestsLoading)
        vm.loadMyTransferRequests()
        assertEquals(2, repo.requests.size)
        old.complete(Result.success(page(row("old", "A"))))
        assertTrue(vm.state.value.myTransferRequestsLoading)
        repo.requests.last().complete(Result.success(page(row("current", "B"))))
        assertEquals(listOf("current"), vm.state.value.myTransferRequests.map { it.id })
    }

    @Test fun previewBoundariesClearPreviousProjectionButAllowCurrentPersonalReads() = fixture { vm, repo ->
        vm.loadMyTransferRequests()
        repo.requests.single().complete(Result.success(page(row("cached", "A"))))
        vm.loadMyTransferRequests()
        val beforePreview = repo.requests.last()
        assertTrue(vm.enterRolePreview(WorkspaceViewRole.ASSEMBLER).isSuccess)
        assertTrue(vm.state.value.myTransferRequests.isEmpty())
        assertFalse(vm.state.value.myTransferRequestsLoading)
        vm.loadMyTransferRequests()
        assertEquals(3, repo.requests.size)
        beforePreview.complete(Result.failure(IllegalStateException("before preview stale")))
        assertTrue(vm.state.value.myTransferRequestsLoading)
        assertNull(vm.state.value.myTransferRequestsError)
        repo.requests.last().complete(Result.success(page(row("preview-current", "A"), row("other", "B"))))
        assertEquals(listOf("preview-current"), vm.state.value.myTransferRequests.map { it.id })

        vm.loadMyTransferRequests()
        val duringPreview = repo.requests.last()
        assertTrue(vm.exitRolePreview().isSuccess)
        assertTrue(vm.state.value.myTransferRequests.isEmpty())
        assertFalse(vm.state.value.myTransferRequestsLoading)
        vm.loadMyTransferRequests()
        assertEquals(5, repo.requests.size)
        duringPreview.complete(Result.success(page(row("stale-preview", "A"))))
        assertTrue(vm.state.value.myTransferRequestsLoading)
        assertTrue(vm.state.value.myTransferRequests.isEmpty())
        repo.requests.last().complete(Result.success(page(row("fresh", "A"))))
        assertEquals(listOf("fresh"), vm.state.value.myTransferRequests.map { it.id })
    }

    @Test fun loadingDeduplicatesAndFailureAllowsRetryWithOnlyMineSortedDescending() = fixture { vm, repo ->
        vm.loadMyTransferRequests()
        vm.loadMyTransferRequests()
        assertEquals(1, repo.requests.size)
        repo.requests.single().complete(Result.failure(IllegalStateException("offline")))
        assertFalse(vm.state.value.myTransferRequestsLoading)
        assertNotNull(vm.state.value.myTransferRequestsError)
        vm.loadMyTransferRequests()
        assertEquals(2, repo.requests.size)
        assertNull(vm.state.value.myTransferRequestsError)
        repo.requests.last().complete(Result.success(page(
            row("old", "A", "2026-01-01"), row("other", "B", "2026-12-01"),
            row("new", "A", "2026-02-01"), row("unknown", null), row("undated", "A", null),
        )))
        assertEquals(listOf("new", "old", "undated"), vm.state.value.myTransferRequests.map { it.id })
        assertFalse(vm.state.value.myTransferRequestsLoading)
    }

    @Test fun leavingProfileKeepsSameSessionLoadAndReentryDeduplicates() = fixture { vm, repo ->
        vm.navigate(Screen.PROFILE)
        assertEquals(1, repo.requests.size)
        vm.navigate(Screen.WORKSPACE)
        vm.navigate(Screen.PROFILE)
        assertEquals(1, repo.requests.size)
        repo.requests.single().complete(Result.success(page(row("mine", "A"))))
        assertEquals(listOf("mine"), vm.state.value.myTransferRequests.map { it.id })
        assertFalse(vm.state.value.myTransferRequestsLoading)
    }

    @Test fun logoutCancelsCooperativePersonalRequest() = fixture { vm, repo ->
        val response = CompletableDeferred<Result<TransferRequestPage>>()
        var cancelled = false
        repo.cooperative = {
            try { response.await() } finally { cancelled = !currentCoroutineContext().isActive }
        }
        vm.loadMyTransferRequests()
        assertTrue(vm.state.value.myTransferRequestsLoading)
        vm.logout()
        assertTrue("logout must cancel the personal-list child job", cancelled)
        assertFalse(vm.state.value.myTransferRequestsLoading)
    }

    @Test fun sessionExpiryCancelsPersonalLoadAndAllowsNewSessionRetry() = fixture { vm, repo ->
        val pending = CompletableDeferred<Result<TransferRequestPage>>()
        var cancelled = false
        repo.cooperative = {
            try { pending.await() } finally { cancelled = !currentCoroutineContext().isActive }
        }
        vm.loadMyTransferRequests()
        checkNotNull(repo.onSessionExpired).invoke()
        assertTrue(cancelled)
        assertFalse(vm.state.value.loggedIn)
        assertFalse(vm.state.value.myTransferRequestsLoading)
        assertTrue(vm.state.value.myTransferRequests.isEmpty())
        repo.cooperative = null
        vm.login("B", "password", "device", false)
        vm.loadMyTransferRequests()
        repo.requests.single().complete(Result.success(page(row("fresh-B", "B"))))
        assertEquals(listOf("fresh-B"), vm.state.value.myTransferRequests.map { it.id })
    }

    private fun fixture(test: (LogisticsViewModel, PersonalRepository) -> Unit) {
        val scope = CoroutineScope(SupervisorJob() + UnconfinedTestDispatcher())
        val repo = PersonalRepository()
        try {
            val vm = LogisticsViewModel(repo, scope)
            vm.login("A", "password", "device", false)
            test(vm, repo)
        } finally {
            scope.cancel()
            repo.requests.filterNot { it.completed }.forEach {
                it.complete(Result.failure(CancellationException("test cleanup")))
            }
        }
    }

    private class Pending {
        private lateinit var continuation: Continuation<Result<TransferRequestPage>>
        var completed = false
            private set
        suspend fun await(): Result<TransferRequestPage> = suspendCoroutine { continuation = it }
        fun complete(result: Result<TransferRequestPage>) {
            check(!completed)
            completed = true
            continuation.resume(result)
        }
    }

    private class PersonalRepository : LogisticsRepository(MaterialFlowApi(), NoOpDao()) {
        val requests = mutableListOf<Pending>()
        var cooperative: (suspend () -> Result<TransferRequestPage>)? = null
        override suspend fun login(username: String, password: String, deviceId: String, remember: Boolean) =
            LoginResult("synthetic", null, null, false, User(username, username, username, UserRole.ADMIN))
        override suspend fun workspaceSummary() = Result.success(WorkspaceSummary())
        override suspend fun workspaceMaterialItems(status: String?, orderNo: String?, page: Int, pageSize: Int) =
            Result.success(WorkspaceMaterialItemsPage(emptyList(), page, pageSize, 0, 0, null, null))
        override suspend fun assemblyTaskPage(page: Int, pageSize: Int, deviceNo: String?, deviceId: String?) =
            Result.success(AssemblyTaskPage(emptyList(), page, pageSize, 0, 0))
        override suspend fun transferRequests(status: String?): Result<TransferRequestPage> {
            cooperative?.let { return it() }
            val request = Pending()
            requests += request
            return request.await()
        }
    }

    private class NoOpDao : OfflineOperationDao {
        override fun observeAll() = emptyFlow<List<OfflineOperationEntity>>()
        override suspend fun pending(): List<OfflineOperationEntity> = emptyList()
        override fun observePendingCount() = emptyFlow<Int>()
        override fun observeAttentionCount() = emptyFlow<Int>()
        override suspend fun insert(operation: OfflineOperationEntity) = 1L
        override suspend fun updateStatus(id: String, status: String, error: String?) = Unit
        override suspend fun markSynced(id: String, status: String, serverTime: String?) = Unit
        override suspend fun clearSynced() = Unit
        override suspend fun findById(id: String): OfflineOperationEntity? = null
        override suspend fun resetStuckSyncing() = 0
        override suspend fun resetToPending(id: String) = Unit
        override suspend fun deleteById(id: String) = Unit
    }

    companion object {
        private fun page(vararg rows: TransferRequest) = TransferRequestPage(rows.toList())
        private fun row(id: String, owner: String?, created: String? = "2026-01-01") =
            TransferRequest(id, type = "INBOUND", status = "PENDING", createdBy = owner, createdAt = created)
    }
}
