package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.company.logistics.model.DeviceDetail
import com.company.logistics.model.DeviceModelMap
import com.company.logistics.Model3dActivity

@Composable
fun DeviceDetailScreen(
    detail: DeviceDetail?,
    loading: Boolean,
    error: String?,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onBack) { Text("返回") }
            Text("机台详情", style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.width(48.dp))
        }
        Spacer(Modifier.height(8.dp))
        when {
            loading -> CircularProgressIndicator()
            error != null -> Text(error, color = MaterialTheme.colorScheme.error)
            detail == null -> Text("暂无数据")
            else -> {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Text("编号：${detail.deviceNo}", style = MaterialTheme.typography.titleMedium)
                        Text("名称：${detail.deviceName}")
                        detail.workshop?.let { Text("车间：$it") }
                        detail.modelCapability?.let { Text("机型：$it") }
                        Text("状态：${detail.status}")
                    }
                }
                Spacer(Modifier.height(12.dp))
                val modelCode = DeviceModelMap.modelCodeFor("", detail.deviceNo)
                if (modelCode != null) {
                    Button(
                        onClick = {
                            context.startActivity(
                                Model3dActivity.intent(context, modelCode, "机台 ${detail.deviceNo}", detail.deviceNo)
                            )
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("查看 3D 模型") }
                } else {
                    Text("该机型暂无 3D 模型", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Spacer(Modifier.height(12.dp))
                Text("关联订单", style = MaterialTheme.typography.titleMedium)
                if (detail.orders.isEmpty()) {
                    Text("未绑定任何生产订单", color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    detail.orders.forEach { o ->
                        Card(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                            Column(Modifier.padding(12.dp)) {
                                Text(o.orderNo, style = MaterialTheme.typography.titleSmall)
                                o.productName?.let { Text("产品：$it") }
                                o.assignStatus?.let { Text("状态：$it") }
                            }
                        }
                    }
                }
            }
        }
    }
}
