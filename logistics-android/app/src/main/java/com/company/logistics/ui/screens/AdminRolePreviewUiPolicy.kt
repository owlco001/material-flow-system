package com.company.logistics.ui.screens

import com.company.logistics.model.UserRole

/** Small pure policy surface shared by Compose and JVM regression tests. */
object AdminRolePreviewUiPolicy {
    const val BANNER_TEXT = "测试预览，只读，不代表当前账号权限"

    fun canShowEntry(authenticatedRole: UserRole): Boolean = authenticatedRole == UserRole.ADMIN

    fun actionsEnabled(readOnly: Boolean): Boolean = !readOnly
}
