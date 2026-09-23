package com.company.logistics


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
import com.company.logistics.data.remote.CachedAssemblyModelFile
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.ui.screens.Debug3dErrorPolicy
import com.company.logistics.ui.screens.Debug3dModelLoadCoordinator
import com.company.logistics.ui.screens.Debug3dUploadOperation
import com.company.logistics.ui.screens.Debug3dUploadPolicy
import com.company.logistics.ui.theme.LogisticsTheme
import java.io.File
import java.util.UUID
import kotlinx.coroutines.launch

/** Debug-only activity; it is absent from release source and manifest. */
class Debug3dActivity : ComponentActivity() {
    private lateinit var repository: AssemblyModelRepository

    private var selectedInfo by mutableStateOf<AssemblyModelFileInfo?>(null)
    private var status by mutableStateOf("请选择 GLB 文件")
    private var progress by mutableStateOf(0L)
    private var uploading by mutableStateOf(false)
    private var loadingPublished by mutableStateOf(false)
    private var retryUpload by mutableStateOf(false)
    private var retryLoad by mutableStateOf(false)
    private val uploadOperation = Debug3dUploadOperation()
    private var uploadSource by mutableStateOf<CachedAssemblyModelFile?>(null)
    private var glbFile by mutableStateOf<File?>(null)
    private var resultMeta by mutableStateOf<AssemblyModelMeta?>(null)
    private var modelCode by mutableStateOf(Debug3dUploadPolicy.DEFAULT_MODEL_CODE)
    private var modelName by mutableStateOf(Debug3dUploadPolicy.DEFAULT_MODEL_NAME)
    private val modelLoadCoordinator = Debug3dModelLoadCoordinator()
    private val picker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri ?: return@registerForActivityResult

        uploadOperation.complete()
        retryUpload = false
        uploadSource = null
        lifecycleScope.launch {
            runCatching { repository.cacheSelectedModel(contentResolver, uri) }
                .onSuccess { cached ->
                    uploadSource = cached
                    selectedInfo = cached.info
                    status = "已缓存到私有目录并校验，可上传"
                }
                .onFailure { status = Debug3dErrorPolicy.localFile(it) }
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
                    uploading = uploading, loadingPublished = loadingPublished, retryUpload = retryUpload,
                    retryLoad = retryLoad, resultMeta = resultMeta,
                    onModelCodeChanged = { modelCode = it }, onModelNameChanged = { modelName = it },
                    onPick = { picker.launch(arrayOf("model/gltf-binary", "application/octet-stream")) },
                    onUpload = ::upload, onRetryUpload = ::upload,
                    onLoadPublishedModel = { loadPublishedModel() }, onRetryLoad = { loadPublishedModel() }
                )
            }
        }
    }

    private fun upload() {
        Debug3dUploadPolicy.validate(modelCode, modelName)?.let {
            status = it
            return
        }
        val source = uploadSource ?: run { status = "请先选择 GLB 文件"; return }
        if (uploading) return
        uploading = true; retryUpload = false; progress = 0L; resultMeta = null
        val operationId = uploadOperation.id()
        lifecycleScope.launch {
            repository.upload(source.file, source.info.displayName, modelCode, modelName, UUID.randomUUID(), operationId) { sent, total ->
                runOnUiThread { progress = sent; status = "上传中 ${sent * 100 / total}%" }
            }.onSuccess { meta ->
                resultMeta = meta; status = "上传成功：v${meta.version} ${meta.format} ${meta.byteSize}B"; uploading = false
                uploadOperation.complete()
                loadPublishedModel(meta.modelCode)
            }.onFailure { error ->
                status = Debug3dErrorPolicy.upload(error); retryUpload = true; uploading = false
            }
        }
    }

    private fun loadPublishedModel(requestedCode: String = modelCode) {
        if (!modelLoadCoordinator.request(requestedCode) { code ->
                loadingPublished = true
                retryLoad = false
                status = "正在加载已发布模型：$code"
                lifecycleScope.launch {
                    repository.loadPublishedModel(code)
                        .onSuccess { cached ->
                            glbFile = cached.file
                            resultMeta = cached.meta
                            status = "已下载已发布模型：v${cached.meta.version}，等待渲染"
                        }
                        .onFailure { error -> status = Debug3dErrorPolicy.load(error); retryLoad = true }
                        .also {
                            loadingPublished = false
                            modelLoadCoordinator.complete()
                        }
                }
            }) {
            if (requestedCode.isBlank()) status = "请输入 modelCode"
        }
    }

    companion object { const val EXTRA_GLB_PATH = "glb_path"; const val EXTRA_MODEL_CODE = "model_code" }
}
