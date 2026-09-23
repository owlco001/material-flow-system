package com.company.logistics.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import com.company.logistics.rendering.ModelRendererAdapter
import com.company.logistics.rendering.OrbitCameraModelRendererAdapter

/** Serializes published-model loads so upload callbacks and manual retries cannot race. */
class Debug3dModelLoadCoordinator {
    private var inFlight = false

    fun request(modelCode: String, load: (String) -> Unit): Boolean {
        if (inFlight || modelCode.isBlank()) return false
        inFlight = true
        load(modelCode)
        return true
    }

    fun complete() {
        inFlight = false
    }
}

/** Keeps the debug entry decision testable without making release navigation depend on it. */
object Debug3dEntryPolicy {
    fun isVisible(isDebugBuild: Boolean): Boolean = isDebugBuild
}

object Debug3dUploadPolicy {
    const val DEFAULT_MODEL_CODE = "GearboxAssy"
    const val DEFAULT_MODEL_NAME = "Gearbox Assembly"

    private val modelCodePattern = Regex("[A-Za-z0-9._-]{1,64}")

    fun validate(modelCode: String, modelName: String): String? {
        if (!modelCodePattern.matches(modelCode)) {
            return "modelCode 格式无效：仅允许 1-64 个 ASCII 字母、数字、.、_、-"
        }
        if (modelName.isBlank()) {
            return "modelName 不能为空"
        }
        return null
    }
}

/** Retains one idempotency key for a failed upload and its user retry. */
class Debug3dUploadOperation {
    private var operationId: java.util.UUID? = null

    fun id(): java.util.UUID = operationId ?: java.util.UUID.randomUUID().also { operationId = it }

    fun complete() {
        operationId = null
    }
}

/** Keeps HTTP failures actionable without coupling the debug UI to transport details. */
object Debug3dErrorPolicy {
    fun upload(error: Throwable): String = "上传失败：${message(error, "上传暂时不可用")}"

    fun load(error: Throwable): String = "加载失败：${message(error, "模型下载或校验失败")}"

    private fun message(error: Throwable, fallback: String): String = when (error) {
        is com.company.logistics.data.remote.ApiException -> when (error.statusCode) {
            401 -> "登录已失效，请重新登录后重试"
            403 -> "当前账号无权执行该操作"
            404 -> "无已发布模型，请确认 modelCode"
            409 -> "模型状态已变化，请重试"
            413 -> "文件超过服务端大小限制"
            415 -> "服务端不支持该文件格式"
            else -> if (error.retryable || error.statusCode >= 500) "网络或服务暂时不可用，请重试" else fallback
        }
        else -> "网络失败，请检查连接后重试"
    }
}

@Composable
fun Debug3dPanel(
    adapter: ModelRendererAdapter = remember { OrbitCameraModelRendererAdapter() },
) {
    var revision by remember { mutableIntStateOf(0) }
    fun changed() {
        revision++
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("3D装配图测试", style = MaterialTheme.typography.headlineSmall)
        Text(
            "仅 Debug 可见。当前未接入 Filament/GLB 渲染引擎；以下是相机状态演示。",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(220.dp)
                .background(Color(0xFFE8EEF2))
                .pointerInput(Unit) {
                    detectTransformGestures { _, pan, zoom, _ ->
                        adapter.onRotate(pan.x * 0.2f, pan.y * 0.2f)
                        adapter.onPan(pan.x * 0.01f, pan.y * 0.01f)
                        adapter.onScale(zoom)
                        changed()
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            Text("渲染引擎未接入\n仅显示相机状态", color = Color(0xFF344955))
        }
        Text("Yaw ${adapter.camera.yawDegrees.toInt()}°  ·  Pitch ${adapter.camera.pitchDegrees.toInt()}°")
        Text("距离 %.2f  ·  平移 (%.2f, %.2f)".format(adapter.camera.distance, adapter.camera.panX, adapter.camera.panY))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { adapter.onRotate(-15f, 0f); changed() }) { Text("旋转") }
            Button(onClick = { adapter.onScale(1.25f); changed() }) { Text("缩放") }
            Button(onClick = { adapter.onPan(0.5f, 0.5f); changed() }) { Text("平移") }
        }
        OutlinedButton(onClick = { adapter.resetCamera(); changed() }) { Text("重置相机") }
        Spacer(Modifier.size(4.dp))
        Text("状态演示不会加载模型，也不会影响业务流程。", color = MaterialTheme.colorScheme.onSurfaceVariant)
        @Suppress("UNUSED_EXPRESSION")
        revision
    }
}