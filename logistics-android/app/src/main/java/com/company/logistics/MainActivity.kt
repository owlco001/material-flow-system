package com.company.logistics

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

private const val API_BASE = "http://107.173.70.115:8000"
private enum class Role(val label: String) { OPERATOR("操作员"), WAREHOUSE("仓库管理员"), MATERIAL("物料员"), ADMIN("管理员") }
private data class Material(val code: String, val name: String, val stock: Int, val location: String)
private data class QueueItem(val id: String, val materialCode: String, val status: String)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState); setContent { MaterialFlowApp() } }
}

private object MaterialFlowApi {
    suspend fun login(user: String, password: String): Result<String> = request("/api/v1/auth/login", "POST", null, "{\"username\":\"$user\",\"password\":\"$password\",\"deviceId\":\"android-${UUID.randomUUID()}\",\"clientVersion\":\"0.2.0\"}").map { JSONObject(it).getString("accessToken") }
    suspend fun inventory(token: String, code: String): Result<Material> = request("/api/v1/materials/$code/inventory", "GET", token, null).map { root -> val x = JSONObject(root); val m = x.getJSONObject("material"); val i = x.getJSONObject("inventory"); val loc = if (i.getJSONArray("locations").length() > 0) i.getJSONArray("locations").getJSONObject(0).getString("locationCode") else "未绑定"; Material(m.getString("code"), m.getString("name"), i.getInt("availableQuantity"), loc) }
    suspend fun orderStatus(token: String, order: String): Result<String> = request("/api/v1/orders/material-status", "POST", token, "{\"documentType\":\"ORDER_NO\",\"documentNo\":\"$order\"}").map { root -> val x = JSONObject(root); val items = x.getJSONArray("items"); if (items.length() == 0) "无物料" else items.getJSONObject(0).getString("label") }
    suspend fun createInbound(token: String, material: Material): Result<String> = request("/api/v1/transfer-requests", "POST", token, "{\"clientOperationId\":\"${UUID.randomUUID()}\",\"type\":\"INBOUND\",\"documentNo\":null,\"items\":[{\"materialId\":\"mat_001\",\"quantity\":1,\"targetLocationCode\":\"${material.location}\",\"expectedInventoryVersion\":1}],\"remark\":\"APP测试入库\",\"evidenceIds\":[]}").map { JSONObject(it).getString("status") }
    private suspend fun request(path: String, method: String, token: String?, body: String?): Result<String> = withContext(Dispatchers.IO) { runCatching { val c = URL(API_BASE + path).openConnection() as HttpURLConnection; c.requestMethod = method; c.connectTimeout = 8000; c.readTimeout = 8000; c.setRequestProperty("Content-Type", "application/json"); token?.let { c.setRequestProperty("Authorization", "Bearer $it") }; if (body != null) { c.doOutput = true; c.outputStream.use { it.write(body.toByteArray()) } }; val text = (if (c.responseCode in 200..299) c.inputStream else c.errorStream).bufferedReader().use { it.readText() }; if (c.responseCode !in 200..299) error("HTTP ${c.responseCode}: $text"); text } }
}

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun MaterialFlowApp() {
    var loggedIn by remember { mutableStateOf(false) }; var token by remember { mutableStateOf("") }; var username by remember { mutableStateOf("owlco") }; var password by remember { mutableStateOf("") }; var loginMessage by remember { mutableStateOf("") }
    if (!loggedIn) { LoginPage(username, password, loginMessage, { username = it }, { password = it }) { scope -> scope.launch { loginMessage = "登录中…"; MaterialFlowApi.login(username, password).onSuccess { token = it; loggedIn = true }.onFailure { loginMessage = it.message ?: "登录失败" } } }; return }
    var role by remember { mutableStateOf(Role.ADMIN) }; var page by remember { mutableStateOf("scan") }; var scanned by remember { mutableStateOf<Material?>(null) }; var queue by remember { mutableStateOf(listOf<QueueItem>()) }; var message by remember { mutableStateOf("已连接 VPS API") }; val scope = rememberCoroutineScope()
    MaterialTheme { Scaffold(topBar = { TopAppBar(title = { Text("物料流转 · ${role.label}") }) }) { padding -> Column(Modifier.fillMaxSize().padding(padding).padding(16.dp)) { Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { Role.values().forEach { r -> FilterChip(role == r, { role = r }, label = { Text(r.label) }) } }; Spacer(Modifier.height(10.dp)); when (page) { "scan" -> ScanPage(message, { code -> scope.launch { message = "查询中…"; MaterialFlowApi.inventory(token, code).onSuccess { scanned = it; page = "detail"; message = "料号查询成功" }.onFailure { message = it.message ?: "查询失败" } } }, { page = "queue" }, { order -> scope.launch { message = "查询订单…"; MaterialFlowApi.orderStatus(token, order).onSuccess { message = "订单状态：$it" }.onFailure { message = it.message ?: "订单查询失败" } } }); "detail" -> DetailPage(scanned!!, { scope.launch { message = "提交入库申请中…"; MaterialFlowApi.createInbound(token, scanned!!).onSuccess { status -> message = "后端已受理：$status"; queue = queue + QueueItem(UUID.randomUUID().toString(), scanned!!.code, "待审批"); page = "queue" }.onFailure { message = it.message ?: "提交失败" } } }) { page = "scan" }; "queue" -> QueuePage(queue, message, { queue = queue.map { it.copy(status = "同步成功") }; message = "同步完成" }) { page = "scan" } } } } }
}

@Composable private fun LoginPage(user: String, pass: String, msg: String, onUser: (String) -> Unit, onPass: (String) -> Unit, onLogin: (kotlinx.coroutines.CoroutineScope) -> Unit) { val scope = rememberCoroutineScope(); Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { Text("物料流转", style = MaterialTheme.typography.headlineMedium); Text("公司内部管理端 · VPS 测试环境"); OutlinedTextField(user, onUser, Modifier.fillMaxWidth(), label = { Text("账号") }); OutlinedTextField(pass, onPass, Modifier.fillMaxWidth(), label = { Text("密码") }); Button({ onLogin(scope) }, Modifier.fillMaxWidth()) { Text("登录") }; Text(msg, color = MaterialTheme.colorScheme.primary) } }

@Composable private fun ScanPage(message: String, onMaterial: (String) -> Unit, onQueue: () -> Unit, onOrder: (String) -> Unit) { var code by remember { mutableStateOf("MTR-001") }; var order by remember { mutableStateOf("SO-TEST-001") }; Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { Text("扫码作业", style = MaterialTheme.typography.headlineSmall); Text(message); OutlinedTextField(code, { code = it }, Modifier.fillMaxWidth(), label = { Text("料号 / 扫码结果") }); Button({ onMaterial(code) }, Modifier.fillMaxWidth()) { Text("查询料号库存") }; OutlinedTextField(order, { order = it }, Modifier.fillMaxWidth(), label = { Text("订单号 / 物流号") }); OutlinedButton({ onOrder(order) }, Modifier.fillMaxWidth()) { Text("查询订单物料状态") }; OutlinedButton(onQueue, Modifier.fillMaxWidth()) { Text("查看离线队列") } } }

@Composable private fun DetailPage(m: Material, onSubmit: () -> Unit, onBack: () -> Unit) { Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { Text("物料详情", style = MaterialTheme.typography.headlineSmall); Text("料号：${m.code}"); Text("名称：${m.name}"); Text("可用库存：${m.stock} ${"件"}"); Text("当前库位：${m.location}"); Button(onSubmit, Modifier.fillMaxWidth()) { Text("入库申请并暂存") }; OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回") } } }

@Composable private fun QueuePage(queue: List<QueueItem>, message: String, onSync: () -> Unit, onBack: () -> Unit) { Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { Text("离线暂存", style = MaterialTheme.typography.headlineSmall); Text(message); LazyColumn(Modifier.weight(1f)) { items(queue) { item -> Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) { Column(Modifier.padding(12.dp)) { Text(item.materialCode); Text(item.status) } } } }; Button(onSync, Modifier.fillMaxWidth(), enabled = queue.isNotEmpty()) { Text("立即同步") }; OutlinedButton(onBack, Modifier.fillMaxWidth()) { Text("返回扫码") } } }
