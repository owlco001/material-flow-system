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

@Composable
fun UserManagementScreen(users: List<ManagedUser>, loading: Boolean, error: String?, onRefresh: () -> Unit, onAdd: (String, String, String, String, String?) -> Unit) {
    var employeeNo by remember { mutableStateOf("") }; var displayName by remember { mutableStateOf("") }; var password by remember { mutableStateOf("") }; var managerId by remember { mutableStateOf("") }
    var role by remember { mutableStateOf(UserRole.OPERATOR.code) }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("用户管理", style = MaterialTheme.typography.headlineSmall); TextButton(onClick = onRefresh) { Text("刷新") } }
        OutlinedTextField(employeeNo, { employeeNo = it }, Modifier.fillMaxWidth(), label = { Text("员工工号") }, singleLine = true)
        OutlinedTextField(displayName, { displayName = it }, Modifier.fillMaxWidth(), label = { Text("姓名") }, singleLine = true)
        OutlinedTextField(role, { role = it.uppercase() }, Modifier.fillMaxWidth(), label = { Text("角色（非 ADMIN）") }, singleLine = true)
        OutlinedTextField(password, { password = it }, Modifier.fillMaxWidth(), label = { Text("临时密码") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        OutlinedTextField(managerId, { managerId = it }, Modifier.fillMaxWidth(), label = { Text("直属领导 ID（可选）") }, singleLine = true)
        Button(onClick = { onAdd(employeeNo, displayName, role, password, managerId.ifBlank { null }) }, enabled = !loading && employeeNo.isNotBlank() && displayName.isNotBlank() && password.isNotBlank()) { Text("添加员工") }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) { items(users) { u -> Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(12.dp)) { Text("${u.displayName} (${u.username})"); Text("角色：${u.role.label} · 首次改密：${if (u.mustChangePassword) "是" else "否"}"); Text(if (u.active) "启用" else "停用") } } } }
    }
}
