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
import androidx.compose.material3.MaterialTheme
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
import com.company.logistics.rendering.FilamentModelRenderer
import java.io.File

@Composable
fun FilamentDebug3dPanel(glbFile: File?, loadStatus: String = "") {
    val renderer = remember { FilamentModelRenderer() }
    var status by remember(loadStatus, glbFile) { mutableStateOf(loadStatus.ifBlank { if (glbFile == null) "暂无模型" else "等待 Surface" }) }

    DisposableEffect(renderer) {
        onDispose { renderer.release() }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("3D 装配图测试", style = MaterialTheme.typography.headlineSmall)
        Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant)
        AndroidView(
            modifier = Modifier.fillMaxWidth().height(300.dp).pointerInput(Unit) {
                detectTransformGestures { _, pan, zoom, _ ->
                    renderer.onRotate(pan.x * 0.2f, pan.y * 0.2f)
                    renderer.onPan(pan.x * 0.01f, pan.y * 0.01f)
                    renderer.onScale(zoom)
                }
            },
            factory = { context ->
                SurfaceView(context).also { surfaceView ->
                    surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
                        override fun surfaceCreated(holder: SurfaceHolder) {
                            renderer.attach(holder.surface)
                            glbFile?.let { file ->
                                status = renderer.loadGlb(file).fold(
                                    onSuccess = { "GLB 已加载：${file.name}" },
                                    onFailure = { "GLB 加载失败：${it.message}" },
                                )
                            }
                        }

                        override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
                            renderer.onViewportChanged(width, height)
                        }

                        override fun surfaceDestroyed(holder: SurfaceHolder) = Unit
                    })
                }
            },
        )
        Button(onClick = renderer::resetCamera) { Text("重置相机") }
        Text("Yaw ${renderer.camera.yawDegrees.toInt()}° · Pitch ${renderer.camera.pitchDegrees.toInt()}° · 距离 %.2f".format(renderer.camera.distance))
    }
}
