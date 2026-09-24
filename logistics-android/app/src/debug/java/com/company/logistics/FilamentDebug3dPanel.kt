package com.company.logistics

import android.graphics.SurfaceTexture
import android.view.Surface
import android.view.TextureView
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.company.logistics.data.remote.AssemblyModelFileInfo
import com.company.logistics.data.remote.AssemblyModelMeta
import com.company.logistics.rendering.FilamentModelRenderer
import com.company.logistics.rendering.FilamentUnavailableException
import java.io.File

@Composable
fun FilamentDebug3dPanel(
    glbFile: File?, selectedInfo: AssemblyModelFileInfo? = null, modelCode: String = "", modelName: String = "",
    status: String = "", progress: Long = 0, uploading: Boolean = false, loadingPublished: Boolean = false,
    retryUpload: Boolean = false, retryLoad: Boolean = false, resultMeta: AssemblyModelMeta? = null,
    onModelCodeChanged: (String) -> Unit = {}, onModelNameChanged: (String) -> Unit = {},
    onPick: () -> Unit = {}, onUpload: () -> Unit = {}, onRetryUpload: () -> Unit = onUpload,
    onLoadPublishedModel: () -> Unit = {}, onRetryLoad: () -> Unit = onLoadPublishedModel,
) {
    val renderer = remember { FilamentModelRenderer() }
    var renderStatus by remember { mutableStateOf(if (glbFile == null) "暂无模型" else "等待 Surface") }
    var fullscreen by remember { mutableStateOf(false) }
    DisposableEffect(renderer) { onDispose { renderer.release() } }

    if (fullscreen) {
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            RenderViewport(
                renderer = renderer, glbFile = glbFile, onStatus = { renderStatus = it },
                modifier = Modifier.fillMaxSize(),
            )
            Row(
                Modifier.fillMaxWidth().background(Color(0x66000000)).padding(8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(onClick = { fullscreen = false }) { Text("退出全屏") }
                Button(onClick = renderer::resetCamera) { Text("重置相机") }
            }
            Text(
                renderStatus, color = Color.White,
                modifier = Modifier.align(Alignment.BottomCenter).background(Color(0x66000000)).padding(8.dp),
            )
        }
    } else {
        Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("3D 装配图测试")
            OutlinedTextField(modelCode, onModelCodeChanged, label = { Text("modelCode") }, singleLine = true)
            OutlinedTextField(modelName, onModelNameChanged, label = { Text("modelName") }, singleLine = true)
            Button(onClick = onPick, enabled = !uploading) { Text("选择 GLB") }
            selectedInfo?.let { Text("${it.displayName} · ${it.byteSize} B · SHA-256 ${it.sha256}") }
            Button(onClick = onUpload, enabled = !uploading && selectedInfo != null) { Text(if (uploading) "上传中 ${progress}/${selectedInfo?.byteSize}" else "上传") }
            if (retryUpload && !uploading) {
                Button(onClick = onRetryUpload) { Text("重试上传") }
            }
            Button(onClick = onLoadPublishedModel, enabled = !uploading && !loadingPublished && modelCode.isNotBlank()) {
                Text(if (loadingPublished) "加载已发布模型中" else "加载已发布模型")
            }
            if (retryLoad && !loadingPublished) {
                Button(onClick = onRetryLoad) { Text("重试加载") }
            }
            Text(status.ifBlank { renderStatus })
            Text("渲染：$renderStatus")
            resultMeta?.let { Text("模型元数据：${it.modelCode} v${it.version} · ${it.format} · ${it.byteSize} B · ${it.sha256}") }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { fullscreen = true }) { Text("全屏预览") }
                Button(onClick = renderer::resetCamera) { Text("重置相机") }
            }
            RenderViewport(
                renderer = renderer, glbFile = glbFile, onStatus = { renderStatus = it },
                modifier = Modifier.fillMaxWidth().weight(1f),
            )
        }
    }
}

/** Shared GLB viewport: SurfaceView + gestures + streamed model load; keeps the renderer across surface swaps. */
@Composable
private fun RenderViewport(
    renderer: FilamentModelRenderer,
    glbFile: File?,
    onStatus: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var surfaceReady by remember { mutableStateOf(false) }
    var diag by remember { mutableStateOf("诊断启动中…") }
    LaunchedEffect(renderer) {
        while (true) {
            val (scheduled, begun, rendered) = renderer.debugFrameCounters()
            diag = "渲染诊断\ninit: ${renderer.initializationFailureChain() ?: "OK"}\n" +
                "surface: ${if (surfaceReady) "ready" else "none"}\n" +
                "frames: sched=$scheduled begun=$begun rendered=$rendered\n" +
                "model: ${glbFile?.name ?: "未加载"}"
            kotlinx.coroutines.delay(500)
        }
    }
    Box(modifier) {
    AndroidView(
        modifier = Modifier.fillMaxSize().pointerInput(renderer) {
            detectTransformGestures { _, pan, zoom, _ ->
                renderer.onRotate(pan.x * .2f, pan.y * .2f)
                renderer.onPan(pan.x * .01f, pan.y * .01f)
                renderer.onScale(zoom)
            }
        },
        factory = { context ->
            TextureView(context).also { view ->
                view.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                    override fun onSurfaceTextureAvailable(surfaceTexture: SurfaceTexture, width: Int, height: Int) {
                        surfaceReady = true
                        renderer.attach(Surface(surfaceTexture)).fold(
                            { if (glbFile == null) onStatus("等待模型") },
                            { error ->
                                onStatus(
                                    (if (error is FilamentUnavailableException) {
                                        "FILAMENT_UNAVAILABLE：Filament 不可用"
                                    } else {
                                        "3D 不可用：${error.message ?: "设备不支持或初始化失败"}"
                                    }) + (renderer.initializationFailureChain()?.let { " / $it" } ?: ""),
                                )
                            },
                        )
                        renderer.onViewportChanged(width, height)
                    }

                    override fun onSurfaceTextureSizeChanged(surfaceTexture: SurfaceTexture, width: Int, height: Int) {
                        renderer.onViewportChanged(width, height)
                    }

                    override fun onSurfaceTextureDestroyed(surfaceTexture: SurfaceTexture): Boolean {
                        surfaceReady = false
                        renderer.detachSurface()
                        onStatus("等待 Surface")
                        return true
                    }

                    override fun onSurfaceTextureUpdated(surfaceTexture: SurfaceTexture) = Unit
                }
            }
        },
    )
    Text(
        diag, color = Color.White, fontSize = 13.sp, lineHeight = 17.sp,
        modifier = Modifier.align(Alignment.TopStart).fillMaxWidth().background(Color(0xCC000000)).padding(10.dp),
    )
    }
    LaunchedEffect(glbFile, surfaceReady) {
        val file = glbFile
        if (surfaceReady && file != null) {
            onStatus("加载模型中")
            onStatus(
                renderer.loadGlb(file).fold(
                    { "GLB 已渲染：${file.name}" },
                    { "GLB 渲染失败：${it.message ?: "文件无效或设备不支持"}" + (renderer.initializationFailureChain()?.let { chain -> " / $chain" } ?: "") },
                ),
            )
        }
    }
}
