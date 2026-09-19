package com.company.logistics.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.company.logistics.model.UserRole

@Composable
fun ProductionManagementScreen(role: UserRole, loading: Boolean, error: String?, onCreateOrder: (String,String,Int,String,String,String,Int,String) -> Unit, onCreateDevice: (String,String,String,String?) -> Unit, onAssignDevice: (String,String,String,Int) -> Unit, onBack: () -> Unit) {
    var orderNo by remember { mutableStateOf("") }; var product by remember { mutableStateOf("") }; var model by remember { mutableStateOf("") }; var bom by remember { mutableStateOf("") }
    var deviceId by remember { mutableStateOf("") }; var deviceNo by remember { mutableStateOf("") }; var deviceName by remember { mutableStateOf("") }; var workshop by remember { mutableStateOf("") }
    Column(Modifier.padding(16.dp)) {
        Text("订单 / 机台管理")
        if (role == UserRole.ADMIN || role == UserRole.PLANNER) {
            OutlinedTextField(orderNo, { orderNo = it }, Modifier.fillMaxWidth(), label = { Text("订单号") }); OutlinedTextField(product, { product = it }, Modifier.fillMaxWidth(), label = { Text("产品名称") }); OutlinedTextField(model, { model = it }, Modifier.fillMaxWidth(), label = { Text("机型编码") }); OutlinedTextField(bom, { bom = it }, Modifier.fillMaxWidth(), label = { Text("已发布 BOM ID") })
            Button({ onCreateOrder(orderNo, product, 1, "2099-12-31", model, model, 1, bom) }, enabled = !loading && orderNo.isNotBlank() && model.isNotBlank()) { Text("创建订单") }
        }
        if (role == UserRole.ADMIN || role == UserRole.WORKSHOP_SUPERVISOR) {
            OutlinedTextField(deviceNo, { deviceNo = it }, Modifier.fillMaxWidth(), label = { Text("机台编号") }); OutlinedTextField(deviceName, { deviceName = it }, Modifier.fillMaxWidth(), label = { Text("机台名称") }); OutlinedTextField(workshop, { workshop = it }, Modifier.fillMaxWidth(), label = { Text("车间") }); OutlinedTextField(deviceId, { deviceId = it }, Modifier.fillMaxWidth(), label = { Text("机台 ID（绑定）") })
            Button({ onCreateDevice(deviceNo, deviceName, workshop, model.ifBlank { null }) }, enabled = !loading && deviceNo.isNotBlank()) { Text("创建机台") }
            Button({ onAssignDevice(orderNo, model, deviceId, 1) }, enabled = !loading && orderNo.isNotBlank() && model.isNotBlank() && deviceId.isNotBlank()) { Text("绑定机台") }
        }
        error?.let { Text(it) }; Button(onBack) { Text("返回") }
    }
}
