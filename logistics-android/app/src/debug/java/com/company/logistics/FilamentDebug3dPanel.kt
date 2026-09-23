package com.company.logistics

import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.unit.dp
import com.company.logistics.data.remote.AssemblyModelFileInfo
import com.company.logistics.data.remote.AssemblyModelMeta
import com.company.logistics.rendering.FilamentModelRenderer
import java.io.File

@Composable
fun FilamentDebug3dPanel(
    glbFile: File?, selectedInfo: AssemblyModelFileInfo? = null, modelCode: String = "", modelName: String = "",
    status: String = "", progress: Long = 0, uploading: Boolean = false, resultMeta: AssemblyModelMeta? = null,
    onModelCodeChanged: (String) -> Unit = {}, onModelNameChanged: (String) -> Unit = {},
    onPick: () -> Unit = {}, onUpload: () -> Unit = {},
) {
    val renderer = remember { FilamentModelRenderer() }
    var renderStatus by remember { mutableStateOf(if (glbFile == null) "暂无模型" else "等待 Surface") }
    DisposableEffect(renderer) { onDispose { renderer.release() } }
    Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("3D 装配图测试")
        OutlinedTextField(modelCode, onModelCodeChanged, label = { Text("modelCode") }, singleLine = true)
        OutlinedTextField(modelName, onModelNameChanged, label = { Text("modelName") }, singleLine = true)
        Button(onClick = onPick, enabled = !uploading) { Text("选择 GLB") }
        selectedInfo?.let { Text("${it.displayName} · ${it.byteSize} B · SHA-256 ${it.sha256}") }
        Button(onClick = onUpload, enabled = !uploading && selectedInfo != null) { Text(if (uploading) "上传中 ${progress}/${selectedInfo?.byteSize}" else "上传") }
        Text(status.ifBlank { renderStatus })
        resultMeta?.let { Text("模型元数据：${it.modelCode} v${it.version} · ${it.format} · ${it.byteSize} B · ${it.sha256}") }
        AndroidView(
            modifier = Modifier.fillMaxWidth().height(300.dp).pointerInput(Unit) { detectTransformGestures { _, pan, zoom, _ -> renderer.onRotate(pan.x * .2f, pan.y * .2f); renderer.onPan(pan.x * .01f, pan.y * .01f); renderer.onScale(zoom) } },
            factory = { context -> SurfaceView(context).also { view -> view.holder.addCallback(object : SurfaceHolder.Callback {
                override fun surfaceCreated(holder: SurfaceHolder) { renderer.attach(holder.surface); glbFile?.let { file -> renderStatus = renderer.loadGlb(file).fold({ "GLB 已加载：${file.name}" }, { "GLB 加载失败：${it.message}" }) } }
                override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) { renderer.onViewportChanged(width, height) }
                override fun surfaceDestroyed(holder: SurfaceHolder) = Unit
            }) } },
        )
        Button(onClick = renderer::resetCamera) { Text("重置相机") }
    }
}
