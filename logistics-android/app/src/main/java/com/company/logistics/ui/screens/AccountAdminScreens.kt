package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.company.logistics.model.ManagedUser
import com.company.logistics.model.UserRole

private val assignableUserRoles = listOf(
    UserRole.OPERATOR,
    UserRole.MATERIAL,
    UserRole.WAREHOUSE_ADMIN,
    UserRole.WORKSHOP_SUPERVISOR,
    UserRole.ASSEMBLER,
)

internal object AddUserFormPolicy {
    val allowedRoles: List<UserRole> = assignableUserRoles

    fun canSubmit(employeeNo: String, displayName: String, password: String, role: UserRole?): Boolean =
        employeeNo.isNotBlank() && displayName.isNotBlank() && password.isNotBlank() && role in allowedRoles
}

@Composable
fun ChangePasswordScreen(loading: Boolean, error: String?, onSubmit: (String, String, String) -> Unit) {
    var old by remember { mutableStateOf("") }
    var next by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("首次登录必须修改密码", style = MaterialTheme.typography.headlineSmall)
        Text("修改成功后需要重新登录。")
        OutlinedTextField(old, { old = it }, Modifier.fillMaxWidth(), label = { Text("旧密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        OutlinedTextField(next, { next = it }, Modifier.fillMaxWidth(), label = { Text("新密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        OutlinedTextField(confirm, { confirm = it }, Modifier.fillMaxWidth(), label = { Text("确认新密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(onClick = { onSubmit(old, next, confirm) }, enabled = !loading && old.isNotBlank() && next.isNotBlank() && confirm.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text(if (loading) "提交中…" else "提交并重新登录") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UserManagementScreen(
    users: List<ManagedUser>,
    loading: Boolean,
    error: String?,
    onRefresh: () -> Unit,
    onBack: () -> Unit,
    deletingUserId: String? = null,
    success: String? = null,
    onDelete: (String) -> Unit = {},
    onAdd: (String, String, String, String, String?) -> Unit,
) {
    var employeeNo by remember { mutableStateOf("") }
    var displayName by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var managerId by remember { mutableStateOf("") }
    var selectedRole by remember { mutableStateOf<UserRole?>(null) }
    var roleMenuExpanded by remember { mutableStateOf(false) }
    var pendingDelete by remember { mutableStateOf<ManagedUser?>(null) }
    val canSubmit = AddUserFormPolicy.canSubmit(employeeNo, displayName, password, selectedRole)

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onBack) { Text("返回管理员工作台") }
            Text("用户管理", style = MaterialTheme.typography.headlineSmall)
            TextButton(onClick = onRefresh) { Text("刷新") }
        }
        Text("添加员工（带 * 为必填项）", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(employeeNo, { employeeNo = it }, Modifier.fillMaxWidth(), label = { Text("员工工号 *") }, singleLine = true)
        OutlinedTextField(displayName, { displayName = it }, Modifier.fillMaxWidth(), label = { Text("姓名 *") }, singleLine = true)
        ExposedDropdownMenuBox(
            expanded = roleMenuExpanded,
            onExpandedChange = { roleMenuExpanded = !roleMenuExpanded },
            modifier = Modifier.fillMaxWidth(),
        ) {
            OutlinedTextField(
                value = selectedRole?.label.orEmpty(),
                onValueChange = {},
                modifier = Modifier.menuAnchor().fillMaxWidth(),
                readOnly = true,
                label = { Text("角色 *") },
                placeholder = { Text("请选择角色") },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = roleMenuExpanded) },
                singleLine = true,
            )
            ExposedDropdownMenu(expanded = roleMenuExpanded, onDismissRequest = { roleMenuExpanded = false }) {
                assignableUserRoles.forEach { option ->
                    DropdownMenuItem(
                        text = { Text("${option.code} ${option.label}") },
                        onClick = {
                            selectedRole = option
                            roleMenuExpanded = false
                        },
                    )
                }
            }
        }
        OutlinedTextField(password, { password = it }, Modifier.fillMaxWidth(), label = { Text("临时密码 *") }, supportingText = { Text("必填，密码要求由后端校验") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        OutlinedTextField(managerId, { managerId = it }, Modifier.fillMaxWidth(), label = { Text("直属领导 ID（可选）") }, singleLine = true)
        Button(onClick = { onAdd(employeeNo, displayName, selectedRole!!.code, password, managerId.ifBlank { null }) }, enabled = !loading && canSubmit, modifier = Modifier.fillMaxWidth()) { Text("添加员工") }
        if (loading) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            Text("正在加载用户…", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        success?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(users) { u ->
                Card(Modifier.fillMaxWidth()) {
                    Row(
                        Modifier.fillMaxWidth().padding(12.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("${u.displayName} (${u.username})")
                            Text("角色：${u.role.label} · 首次改密：${if (u.mustChangePassword) "是" else "否"}")
                            Text(if (u.active) "启用" else "停用")
                        }
                        if (u.active) {
                            OutlinedButton(
                                onClick = { pendingDelete = u },
                                enabled = deletingUserId == null && !loading,
                            ) {
                                Text(if (deletingUserId == u.id) "停用中…" else "停用")
                            }
                        }
                    }
                }
            }
        }
    }

    pendingDelete?.let { target ->
        AlertDialog(
            onDismissRequest = { if (deletingUserId == null) pendingDelete = null },
            title = { Text("确认停用用户") },
            text = { Text("确定停用 ${target.displayName}（${target.username}）？停用后该账号不能继续登录。") },
            confirmButton = {
                Button(
                    onClick = {
                        pendingDelete = null
                        onDelete(target.id)
                    },
                    enabled = deletingUserId == null,
                ) { Text("确认停用") }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }, enabled = deletingUserId == null) { Text("取消") }
            },
        )
    }
}
