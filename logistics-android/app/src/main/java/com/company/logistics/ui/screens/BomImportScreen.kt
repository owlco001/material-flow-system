package com.company.logistics.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.ui.LogisticsUiState

@Composable
fun BomImportScreen(state: LogisticsUiState, onPick: (String, ByteArray, String) -> Unit, onReadError: (String) -> Unit, onCommit: () -> Unit) {
    val context = LocalContext.current
    var modelCode by rememberSaveable { mutableStateOf(state.bomPreview?.modelCode.orEmpty()) }
    LaunchedEffect(state.bomPreview?.modelCode) {
        state.bomPreview?.modelCode?.takeIf { it.isNotBlank() }?.let { modelCode = it }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        uri ?: return@rememberLauncherForActivityResult
        val name = uri.lastPathSegment ?: "bom.csv"
        try {
            val bytes = context.contentResolver.openInputStream(uri)?.use {
                BomFileReader.readAtMost(it, MaterialFlowApi.MAX_BOM_FILE_BYTES)
            } ?: return@rememberLauncherForActivityResult
            onPick(name, bytes, modelCode.trim())
        } catch (error: BomFileTooLargeException) {
            onReadError(error.message ?: "BOM 文件超过大小限制")
        } catch (_: Exception) {
            onReadError("BOM 文件读取失败")
        }
    }
    Column(Modifier.padding(24.dp)) {
        Text("BOM CSV 导入")
        OutlinedTextField(
            value = modelCode,
            onValueChange = { modelCode = it },
            label = { Text("机型编码") },
            singleLine = true,
        )
        Button(enabled = modelCode.isNotBlank(), onClick = { picker.launch(arrayOf("text/csv", "text/*")) }) { Text("选择 CSV 文件") }
        state.bomPreview?.let { p ->
            Text("总行 ${p.totalRows} · 有效 ${p.validRows} · 错误 ${p.invalidRows}")
            p.errors.take(20).forEach { Text("第${it.lineNo}行 ${it.field}: ${it.message}") }
            Button(enabled = p.canCommit && state.bomImportState != com.company.logistics.ui.WorkspaceLoadState.LOADING, onClick = onCommit) { Text("确认导入") }
        }
        state.bomImportError?.let { Text(it) }
    }
}
