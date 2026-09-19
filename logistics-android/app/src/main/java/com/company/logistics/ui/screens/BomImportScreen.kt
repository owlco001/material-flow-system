package com.company.logistics.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.LocalContext
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.ui.LogisticsUiState

@Composable
fun BomImportScreen(state: LogisticsUiState, onPick: (String, ByteArray, String) -> Unit, onCommit: () -> Unit) {
    val context = LocalContext.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        uri ?: return@rememberLauncherForActivityResult
        val name = uri.lastPathSegment ?: "bom.csv"
        val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return@rememberLauncherForActivityResult
        onPick(name, bytes, "")
    }
    Column(Modifier.padding(24.dp)) {
        Text("BOM CSV 导入")
        Button(onClick = { picker.launch(arrayOf("text/csv", "text/*")) }) { Text("选择 CSV 文件") }
        state.bomPreview?.let { p ->
            Text("总行 ${p.totalRows} · 有效 ${p.validRows} · 错误 ${p.invalidRows}")
            p.errors.take(20).forEach { Text("第${it.lineNo}行 ${it.field}: ${it.message}") }
            Button(enabled = p.canCommit && state.bomImportState != com.company.logistics.ui.WorkspaceLoadState.LOADING, onClick = onCommit) { Text("确认导入") }
        }
        state.bomImportError?.let { Text(it) }
    }
}
