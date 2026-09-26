package com.company.logistics.ui.screens

import android.graphics.SurfaceTexture
import android.view.Surface
import android.view.TextureView
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.company.logistics.rendering.FilamentModelRenderer
import java.io.File

/**
 * 3D 模型查看页（正式）。
 *
 * 机台页的「3D 模型」入口进入。模型由调用方（Model3dActivity）按机台
 * 映射的 modelCode 从服务端下载并缓存好后传入。
 */
@Composable
fun Model3dViewerScreen(
    title: String,
    subtitle: String,
    glbFile: File?,
    loading: Boolean,
    error: String?,
    onRetry: () -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val renderer = remember { FilamentModelRenderer(context) }
    var renderStatus by remember { mutableStateOf("") }

    DisposableEffect(renderer) {
        onDispose { renderer.release() }
    }

    Column(Modifier.fillMaxSize()) {
        TopBar(title, onBack)
        if (subtitle.isNotBlank()) {
            Text(
                subtitle,
                fontSize = 12.sp,
                color = Color(0xFF8A8F98),
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xFFF3F4F6))
                    .padding(horizontal = 16.dp, vertical = 6.dp),
            )
        }

        when {
            loading -> LoadingState(Modifier.weight(1f))
            error != null -> ErrorState(error, onRetry, Modifier.weight(1f))
            glbFile == null -> EmptyState(Modifier.weight(1f))
            else -> {
                Box(Modifier.weight(1f)) {
                    AndroidView(
                        modifier = Modifier
                            .fillMaxSize()
                            .pointerInput(renderer) {
                                detectTransformGestures { _, pan, zoom, _ ->
                                    renderer.onRotate(pan.x * .2f, pan.y * .2f)
                                    renderer.onPan(pan.x * .01f, pan.y * .01f)
                                    renderer.onScale(zoom)
                                }
                            },
                        factory = { ctx ->
                            TextureView(ctx).also { view ->
                                view.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                                    override fun onSurfaceTextureAvailable(
                                        surfaceTexture: SurfaceTexture, width: Int, height: Int,
                                    ) {
                                        renderer.attach(Surface(surfaceTexture)).fold(
                                            { loadGlbInto(renderer, glbFile) { renderStatus = it } },
                                            { renderStatus = "3D 不可用：${it.message ?: "设备不支持或初始化失败"}" },
                                        )
                                        renderer.onViewportChanged(width, height)
                                    }

                                    override fun onSurfaceTextureSizeChanged(
                                        surfaceTexture: SurfaceTexture, width: Int, height: Int,
                                    ) {
                                        renderer.onViewportChanged(width, height)
                                    }

                                    override fun onSurfaceTextureDestroyed(surfaceTexture: SurfaceTexture): Boolean {
                                        renderer.detachSurface()
                                        return true
                                    }

                                    override fun onSurfaceTextureUpdated(surfaceTexture: SurfaceTexture) = Unit
                                }
                            }
                        },
                    )
                    if (renderStatus.isNotBlank()) {
                        Text(
                            renderStatus,
                            color = Color.White,
                            fontSize = 12.sp,
                            textAlign = TextAlign.Center,
                            modifier = Modifier
                                .align(Alignment.BottomCenter)
                                .fillMaxWidth()
                                .background(Color(0x99000000))
                                .padding(8.dp),
                        )
                    }
                }
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "单指旋转 · 双指缩放/平移",
                        fontSize = 12.sp,
                        color = Color(0xFF8A8F98),
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedButton(onClick = renderer::resetCamera) {
                        Text("重置视角", fontSize = 13.sp)
                    }
                }
            }
        }
    }
}

private fun loadGlbInto(renderer: FilamentModelRenderer, file: File, onStatus: (String) -> Unit) {
    renderer.loadGlb(file).fold(
        { onStatus("") },
        { onStatus("模型渲染失败：${it.message ?: "文件无效或设备不支持"}") },
    )
}

@Composable
private fun TopBar(title: String, onBack: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(Color.White)
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack) { Text("返回", fontSize = 14.sp) }
        Text(
            title,
            fontSize = 16.sp,
            color = Color(0xFF1A1D21),
            modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.width(64.dp))
    }
}

@Composable
private fun LoadingState(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(Modifier.size(40.dp), color = MaterialTheme.colorScheme.primary)
            Text("模型加载中…", fontSize = 14.sp, color = Color(0xFF8A8F98), modifier = Modifier.padding(top = 12.dp))
        }
    }
}

@Composable
private fun ErrorState(message: String, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(horizontal = 32.dp),
        ) {
            Text(message, fontSize = 14.sp, color = Color(0xFF5A5F66), textAlign = TextAlign.Center)
            OutlinedButton(onClick = onRetry, modifier = Modifier.padding(top = 16.dp)) {
                Text("重试", fontSize = 14.sp)
            }
        }
    }
}

@Composable
private fun EmptyState(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text("暂无模型", fontSize = 14.sp, color = Color(0xFF8A8F98))
    }
}
