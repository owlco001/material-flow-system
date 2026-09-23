package com.company.logistics

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import com.company.logistics.data.EndpointStore
import com.company.logistics.data.SessionStore
import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.data.remote.AssemblyModelRepository
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.ui.theme.LogisticsTheme
import java.io.File
import kotlinx.coroutines.launch

/** Debug-only activity; it is absent from release source and manifest. */
class Debug3dActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val suppliedFile = intent.getStringExtra(EXTRA_GLB_PATH)?.let(::File)
        val modelCode = intent.getStringExtra(EXTRA_MODEL_CODE)?.trim().orEmpty()
        var glbFile by mutableStateOf(suppliedFile)
        var status by mutableStateOf(if (suppliedFile != null) "等待 Surface" else "正在读取模型元数据…")
        setContent { LogisticsTheme { FilamentDebug3dPanel(glbFile, status) } }
        if (suppliedFile == null) {
            if (modelCode.isBlank()) { status = "暂无模型：未提供 modelCode"; return }
            ApiConfig.baseUrl = EndpointStore.get(applicationContext).effectiveUrl
            val api = MaterialFlowApi()
            val session = SessionStore.get(applicationContext)
            api.restoreSession(session.accessToken(), session.refreshToken(), session.deviceIdOrCreate())
            lifecycleScope.launch {
                AssemblyModelRepository.from(applicationContext, api).loadPublishedModel(modelCode)
                    .onSuccess { cached -> glbFile = cached.file; status = "模型已缓存：${cached.meta.modelName}" }
                    .onFailure { status = "模型加载失败：${it.message ?: "暂无可用模型"}" }
            }
        }
    }

    companion object {
        const val EXTRA_GLB_PATH = "glb_path"
        const val EXTRA_MODEL_CODE = "model_code"
    }
}