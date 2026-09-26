package com.company.logistics

import android.content.Context
import android.content.Intent
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
import com.company.logistics.ui.screens.Debug3dErrorPolicy
import com.company.logistics.ui.screens.Model3dViewerScreen
import com.company.logistics.ui.theme.LogisticsTheme
import java.io.File
import kotlinx.coroutines.launch

/**
 * 3D 装配模型查看（正式入口）。
 *
 * 由机台页面（订单物料状态的机台卡片 / 订单与机台页的 3D 查看区）进入，
 * 按 modelCode 下载服务端已发布模型并渲染。离线无缓存时需要网络。
 */
class Model3dActivity : ComponentActivity() {
    private lateinit var repository: AssemblyModelRepository

    private var glbFile by mutableStateOf<File?>(null)
    private var loading by mutableStateOf(true)
    private var error by mutableStateOf<String?>(null)
    private var modelCode by mutableStateOf("")
    private var modelName by mutableStateOf("")
    private var deviceNo by mutableStateOf("")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        modelCode = intent.getStringExtra(EXTRA_MODEL_CODE).orEmpty()
        modelName = intent.getStringExtra(EXTRA_MODEL_NAME).orEmpty()
        deviceNo = intent.getStringExtra(EXTRA_DEVICE_NO).orEmpty()

        ApiConfig.baseUrl = EndpointStore.get(applicationContext).effectiveUrl
        val api = MaterialFlowApi()
        val session = SessionStore.get(applicationContext)
        api.restoreSession(session.accessToken(), session.refreshToken(), session.deviceIdOrCreate())
        repository = AssemblyModelRepository.from(applicationContext, api)

        setContent {
            LogisticsTheme {
                Model3dViewerScreen(
                    title = modelName.ifBlank { "3D 模型" },
                    subtitle = if (deviceNo.isNotBlank()) "机台 $deviceNo · $modelCode" else modelCode,
                    glbFile = glbFile,
                    loading = loading,
                    error = error,
                    onRetry = ::loadModel,
                    onBack = ::finish,
                )
            }
        }

        if (modelCode.isBlank()) {
            loading = false
            error = "缺少模型编号，请返回重试"
        } else {
            loadModel()
        }
    }

    private fun loadModel() {
        loading = true
        error = null
        glbFile = null
        lifecycleScope.launch {
            repository.loadPublishedModel(modelCode)
                .onSuccess { cached ->
                    glbFile = cached.file
                    if (modelName.isBlank()) modelName = cached.meta.modelName
                }
                .onFailure { error = Debug3dErrorPolicy.load(it) }
            loading = false
        }
    }

    companion object {
        const val EXTRA_MODEL_CODE = "model_code"
        const val EXTRA_MODEL_NAME = "model_name"
        const val EXTRA_DEVICE_NO = "device_no"

        fun intent(context: Context, modelCode: String, modelName: String, deviceNo: String): Intent =
            Intent(context, Model3dActivity::class.java).apply {
                putExtra(EXTRA_MODEL_CODE, modelCode)
                putExtra(EXTRA_MODEL_NAME, modelName)
                putExtra(EXTRA_DEVICE_NO, deviceNo)
            }
    }
}
