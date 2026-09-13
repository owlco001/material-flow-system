package com.company.logistics.model

/** Roles that an authenticated ADMIN may inspect in the workspace projection. */
enum class WorkspaceViewRole(val code: String, val label: String) {
    OPERATOR("OPERATOR", "操作员"),
    MATERIAL("MATERIAL", "物料员"),
    WAREHOUSE_ADMIN("WAREHOUSE_ADMIN", "仓库管理员"),
    ADMIN("ADMIN", "管理员");

    fun toUserRole(): UserRole = UserRole.valueOf(name)

    companion object {
        fun from(role: UserRole): WorkspaceViewRole = valueOf(role.name)

        fun from(code: String?): WorkspaceViewRole? =
            entries.firstOrNull { it.code.equals(code, ignoreCase = true) }
    }
}

/** Read-only query context. It never replaces the authenticated session role. */
data class WorkspaceQueryContext(
    val authenticatedRole: UserRole,
    val viewRole: WorkspaceViewRole,
    val preview: Boolean,
)

interface AdminRolePreviewController {
    fun enterPreview(role: WorkspaceViewRole): Result<WorkspaceQueryContext>
    fun exitPreview(): Result<WorkspaceQueryContext>
    fun currentContext(): WorkspaceQueryContext
}

/** In-memory controller; process death therefore cannot preserve a preview role. */
class InMemoryAdminRolePreviewController(
    private val authenticatedRole: UserRole,
) : AdminRolePreviewController {
    private var context = WorkspaceQueryContext(
        authenticatedRole = authenticatedRole,
        viewRole = WorkspaceViewRole.from(authenticatedRole),
        preview = false,
    )

    override fun enterPreview(role: WorkspaceViewRole): Result<WorkspaceQueryContext> {
        if (authenticatedRole != UserRole.ADMIN) {
            return Result.failure(IllegalStateException("只有 ADMIN 可以进入测试角色视图"))
        }
        context = WorkspaceQueryContext(
            authenticatedRole = UserRole.ADMIN,
            viewRole = role,
            preview = true,
        )
        return Result.success(context)
    }

    override fun exitPreview(): Result<WorkspaceQueryContext> {
        context = WorkspaceQueryContext(
            authenticatedRole = authenticatedRole,
            viewRole = WorkspaceViewRole.from(authenticatedRole),
            preview = false,
        )
        return Result.success(context)
    }

    override fun currentContext(): WorkspaceQueryContext = context
}

/** Workspace projection boundary used by the ViewModel and repository tests. */
interface RoleWorkspaceRepository {
    suspend fun loadRoleSummary(
        viewRole: WorkspaceViewRole? = null,
        requestId: String,
    ): Result<RoleSummary>

    suspend fun listWorkItems(
        viewRole: WorkspaceViewRole? = null,
        status: String?,
        orderNo: String?,
        page: Int,
        pageSize: Int,
        requestId: String,
    ): Result<PagedMaterialWorkItems>
}

typealias RoleSummary = WorkspaceSummary
typealias PagedMaterialWorkItems = WorkspaceMaterialItemsPage
