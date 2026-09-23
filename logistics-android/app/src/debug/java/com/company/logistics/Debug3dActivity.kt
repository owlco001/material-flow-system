package com.company.logistics

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import com.company.logistics.data.EndpointStore
import com.company.logistics.data.SessionStore
import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.AssemblyModelFileInfo
import com.company.logistics.data.remote.AssemblyModelMeta
import com.company.logistics.data.remote.AssemblyModelRepository
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.ui.screens.Debug3dUploadPolicy
import com.company.logistics.ui.theme.LogisticsTheme
import java.io.File
import java.util.UUID
import kotlinx.coroutines.launch

/** Debug-only activity; it is absent from release source and manifest. */
class Debug3dActivity : ComponentActivity() {
    private lateinit var repository: AssemblyModelRepository
    private var selectedUri: Uri? = null
    private var selectedInfo by mutableStateOf<AssemblyModelFileInfo?>(null)
    private var status by mutableStateOf("请选择 GLB 文件")
    private var progress by mutableStateOf(0L)
    private var uploading by mutableStateOf(false)
    private var glbFile by mutableStateOf<File?>(null)
    private var resultMeta by mutableStateOf<AssemblyModelMeta?>(null)
    private var modelCode by mutableStateOf(Debug3dUploadPolicy.DEFAULT_MODEL_CODE)
    private var modelName by mutableStateOf(Debug3dUploadPolicy.DEFAULT_MODEL_NAME)
    private val picker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri ?: return@registerForActivityResult
        selectedUri = uri
        lifecycleScope.launch {
            runCatching { repository.inspect(contentResolver, uri) }
                .onSuccess { selectedInfo = it; status = "已校验，可上传" }
                .onFailure { selectedInfo = null; status = it.message ?: "文件校验失败" }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ApiConfig.baseUrl = EndpointStore.get(applicationContext).effectiveUrl
        val api = MaterialFlowApi()
        val session = SessionStore.get(applicationContext)
        api.restoreSession(session.accessToken(), session.refreshToken(), session.deviceIdOrCreate())
        repository = AssemblyModelRepository.from(applicationContext, api)
        setContent {
            LogisticsTheme {
                FilamentDebug3dPanel(
                    glbFile = glbFile, selectedInfo = selectedInfo, modelCode = modelCode,
                    modelName = modelName, status = status, progress = progress,
                    uploading = uploading, resultMeta = resultMeta,
                    onModelCodeChanged = { modelCode = it }, onModelNameChanged = { modelName = it },
                    onPick = { picker.launch(arrayOf("model/gltf-binary", "application/octet-stream")) },
                    onUpload = ::upload,
                )
            }
        }
    }

    private fun upload() {
        Debug3dUploadPolicy.validate(modelCode, modelName)?.let {
            status = it
            return
        }
        val uri = selectedUri ?: run { status = "请先选择 GLB 文件"; return }
        if (uploading) return
        uploading = true; progress = 0L; resultMeta = null
        val operationId = UUID.randomUUID()
        lifecycleScope.launch {
            repository.upload(contentResolver, uri, modelCode, modelName, UUID.randomUUID(), operationId) { sent, total ->
                runOnUiThread { progress = sent; status = "上传中 ${sent * 100 / total}%" }
            }.onSuccess { meta ->
                glbFile = null; resultMeta = meta; status = "上传成功：v${meta.version} ${meta.format} ${meta.byteSize}B"; uploading = false
            }.onFailure { status = "上传失败：${it.message ?: "网络错误"}"; uploading = false }
        }
    }

    companion object { const val EXTRA_GLB_PATH = "glb_path"; const val EXTRA_MODEL_CODE = "model_code" }
}
