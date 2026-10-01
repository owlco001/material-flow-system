package com.company.logistics.ui.screens

import android.graphics.SurfaceTexture
import android.view.Surface
import android.view.TextureView
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Badge
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.rememberCoroutineScope
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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.company.logistics.rendering.FilamentModelRenderer
import com.company.logistics.rendering.PartInfo
import com.company.logistics.rendering.PartNameCn
import com.company.logistics.ui.theme.LogisticsColors
import kotlinx.coroutines.launch
import java.io.File

/**
 * 3D 模型查看页（正式）。
 *
 * 机台页的「3D 模型」入口进入。模型由调用方（Model3dActivity）按机台
 * 映射的 modelCode 从服务端下载并缓存好后传入。
 *
 * 视觉：深色沉浸式，与开机动画的藏青（#0B2E6F → #08214F）保持一致。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Model3dViewerScreen(
    title: String,
    subtitle: String,
    glbFile: File?,
    loading: Boolean,
    loadingStage: String = "",
    error: String?,
    onRetry: () -> Unit,
    onBack: () -> Unit,
    onStageChange: (String) -> Unit = {},
) {
    val context = LocalContext.current
    val renderer = remember {
        onStageChange("正在初始化 3D 引擎…")
        // 画布背景 = App 页面背景（深藏青 BrandNavyDark #08214F），与整页视觉融为一体；
        // 不再对齐浏览器的 #0b1020（用户指定：背景色采用 app 的背景色）。
        FilamentModelRenderer(backgroundArgb = LogisticsColors.BrandNavyDark.toArgb()).also {
            onStageChange("3D 引擎初始化完成，等待模型文件…")
        }
    }
    var renderStatus by remember { mutableStateOf("") }
    // 零件装配指引状态
    var parts by remember { mutableStateOf<List<PartInfo>>(emptyList()) }
    var selectedPart by remember { mutableStateOf<PartInfo?>(null) }
    var showPartsSheet by remember { mutableStateOf(false) }
    // 悬浮球控制区：右下角透明悬浮球，点击展开半透明面板（零件 / 重置视角 / 爆炸图）
    var controlsExpanded by remember { mutableStateOf(false) }
    var explode by remember { mutableStateOf(0f) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()

    fun selectPart(part: PartInfo?, focusCamera: Boolean) {
        selectedPart = part
        if (part == null) {
            renderer.clearIsolation()
        } else {
            renderer.setIsolatedPart(part.name, part.entity)
            if (focusCamera) renderer.focusPart(part.entity)
        }
    }

    DisposableEffect(renderer) {
        onDispose { renderer.release() }
    }
    // 半透明隔离 / 剖面依赖的 ghost 材质，提前加载一次
    LaunchedEffect(renderer) {
        renderer.ensureGhostMaterial(context.assets)
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(LogisticsColors.BrandNavyDark),
    ) {
        TopBar(title, onBack)
        if (subtitle.isNotBlank()) {
            Text(
                subtitle,
                fontSize = 12.sp,
                color = Color.White.copy(alpha = 0.6f),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 6.dp),
            )
        }

        when {
            loading -> LoadingState(Modifier.weight(1f), loadingStage)
            error != null -> ErrorState(error, onRetry, Modifier.weight(1f))
            glbFile == null -> EmptyState(Modifier.weight(1f))
            else -> {
                Box(Modifier.weight(1f)) {
                    AndroidView(
                        modifier = Modifier
                            .fillMaxSize()
                            .pointerInput(renderer) {
                                // 单指拖动 = 旋转；双指拖动 = 平移 + 捏合缩放。
                                // 旧 detectTransformGestures 把每次拖动（含单指）都同时旋转+平移，
                                // 单指转视角时相机被悄悄平移、视角容易跑飞（用户指定：双指才平移）。
                                awaitEachGesture {
                                    awaitFirstDown(requireUnconsumed = false)
                                    do {
                                        val event = awaitPointerEvent()
                                        val pressed = event.changes.filter { it.pressed }
                                        when {
                                            pressed.size >= 2 -> {
                                                renderer.onScale(event.calculateZoom())
                                                val pan = event.calculatePan()
                                                renderer.onPan(pan.x * .01f, pan.y * .01f)
                                                pressed.forEach { it.consume() }
                                            }
                                            pressed.size == 1 -> {
                                                val change = pressed[0]
                                                val drag = change.positionChange()
                                                renderer.onRotate(drag.x * .2f, drag.y * .2f)
                                                change.consume()
                                            }
                                        }
                                    } while (event.changes.any { it.pressed })
                                }
                            }
                            .pointerInput(renderer) {
                                detectTapGestures(
                                    onTap = { offset ->
                                        renderer.pickPart(offset.x, offset.y) { name, entity ->
                                            if (name == null) {
                                                // 点空处：取消隔离
                                                selectPart(null, focusCamera = false)
                                            } else {
                                                // 点选零件：高亮但不挪相机（避免误触时视角乱跳）
                                                selectPart(PartInfo(name, entity), focusCamera = false)
                                            }
                                        }
                                    }
                                )
                            },
                        factory = { ctx ->
                            TextureView(ctx).also { view ->
                                view.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                                    override fun onSurfaceTextureAvailable(
                                        surfaceTexture: SurfaceTexture, width: Int, height: Int,
                                    ) {
                                        onStageChange("正在创建渲染表面…")
                                        renderer.attach(Surface(surfaceTexture)).fold(
                                            {
                                                onStageChange("正在加载模型文件…")
                                                loadGlbInto(renderer, glbFile) { renderStatus = it }
                                                onStageChange("模型加载完成，正在渲染…")
                                                // 模型加载后提取零件清单
                                                parts = renderer.getParts()
                                            },
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
                    // 点选零件信息 + 隔离状态
                    selectedPart?.let { part ->
                        Row(
                            modifier = Modifier
                                .align(Alignment.TopCenter)
                                .padding(top = 8.dp)
                                .background(Color(0xCC1A1D21), RoundedCornerShape(12.dp))
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(PartNameCn.displayName(part.name), color = Color.White, fontSize = 13.sp)
                                if (PartNameCn.displayName(part.name) != part.name) {
                                    // 英文节点名小字（对齐浏览器「机械臂 大臂 2（arm_upper_2）」）
                                    Text(
                                        "（${part.name}）",
                                        color = Color.White.copy(alpha = 0.45f),
                                        fontSize = 10.sp,
                                    )
                                }
                                Text(
                                    "已定位 · 其余零件半透明",
                                    color = Color.White.copy(alpha = 0.55f),
                                    fontSize = 11.sp,
                                )
                            }
                            TextButton(onClick = { selectPart(null, focusCamera = false) }) {
                                Text("取消", color = Color(0xFF8AB4FF), fontSize = 13.sp)
                            }
                        }
                    }

                    // 底部控制区：透明悬浮球模式（画布右下角，点击展开/收起，不遮挡画布主体）
                    Box(
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(20.dp)
                            .size(52.dp)
                            .background(Color.White.copy(alpha = 0.16f), CircleShape)
                            .border(1.dp, Color.White.copy(alpha = 0.35f), CircleShape)
                            .clickable { controlsExpanded = !controlsExpanded },
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            if (controlsExpanded) "×" else "☰",
                            fontSize = 20.sp,
                            color = Color.White,
                        )
                    }
                    // 展开的半透明控制面板：零件 / 重置视角 / 爆炸图
                    if (controlsExpanded) {
                        Column(
                            modifier = Modifier
                                .align(Alignment.BottomEnd)
                                .padding(end = 20.dp, bottom = 84.dp)
                                .width(248.dp)
                                .background(Color(0xB31A1D21), RoundedCornerShape(16.dp))
                                .padding(horizontal = 14.dp, vertical = 12.dp),
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.End,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                // 零件清单：工人查"哪个部件装哪"
                                BadgedBox(
                                    badge = {
                                        if (parts.isNotEmpty()) {
                                            Badge(
                                                containerColor = LogisticsColors.Info,
                                                contentColor = Color.White,
                                            ) { Text("${parts.size}", fontSize = 10.sp) }
                                        }
                                    },
                                ) {
                                    OutlinedButton(
                                        onClick = { showPartsSheet = true },
                                        border = BorderStroke(1.dp, Color.White.copy(alpha = 0.35f)),
                                    ) {
                                        Text("零件", fontSize = 13.sp, color = Color.White)
                                    }
                                }
                                Spacer(Modifier.width(8.dp))
                                OutlinedButton(
                                    onClick = renderer::resetCamera,
                                    border = BorderStroke(1.dp, Color.White.copy(alpha = 0.35f)),
                                ) {
                                    Text("重置视角", fontSize = 13.sp, color = Color.White)
                                }
                            }
                            // 爆炸图滑杆
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(top = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text("爆炸图", fontSize = 12.sp, color = Color.White.copy(alpha = 0.75f))
                                Slider(
                                    value = explode,
                                    onValueChange = {
                                        explode = it
                                        renderer.setExploded(it)
                                    },
                                    modifier = Modifier
                                        .weight(1f)
                                        .padding(horizontal = 8.dp),
                                    colors = SliderDefaults.colors(
                                        thumbColor = Color.White,
                                        activeTrackColor = LogisticsColors.Info,
                                        inactiveTrackColor = Color.White.copy(alpha = 0.22f),
                                    ),
                                )
                                Text(
                                    "${(explode * 100).toInt()}%",
                                    fontSize = 12.sp,
                                    color = Color.White.copy(alpha = 0.75f),
                                    modifier = Modifier.width(40.dp),
                                    textAlign = TextAlign.End,
                                )
                            }
                        }
                    }
                }
            }
        }

        // 零件清单弹窗：工人按名查找零件，点选后 3D 定位高亮
        if (showPartsSheet) {
            ModalBottomSheet(
                onDismissRequest = { showPartsSheet = false },
                sheetState = sheetState,
                containerColor = Color(0xFF101A30),
                contentColor = Color.White,
            ) {
                PartsSheetContent(
                    parts = parts,
                    selected = selectedPart,
                    onSelect = { part ->
                        selectPart(part, focusCamera = true)
                        scope.launch { sheetState.hide() }.invokeOnCompletion {
                            if (!sheetState.isVisible) showPartsSheet = false
                        }
                    },
                )
            }
        }
    }
}

@Composable
private fun PartsSheetContent(
    parts: List<PartInfo>,
    selected: PartInfo?,
    onSelect: (PartInfo) -> Unit,
) {
    var query by remember { mutableStateOf("") }
    val filtered by remember(parts, query) {
        derivedStateOf {
            val q = query.trim().lowercase()
            if (q.isEmpty()) parts
            else parts.filter {
                it.name.lowercase().contains(q) ||
                    PartNameCn.displayName(it.name).contains(query.trim())
            }
        }
    }
    val listState = rememberLazyListState()

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp)
            .padding(bottom = 24.dp),
    ) {
        Text(
            "零件清单（${parts.size}）",
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
            color = Color.White,
            modifier = Modifier.padding(vertical = 8.dp),
        )
        if (parts.isEmpty()) {
            Text(
                "该模型未拆分独立零件，可直接在 3D 视图中点选查看。",
                fontSize = 13.sp,
                color = Color.White.copy(alpha = 0.6f),
                modifier = Modifier.padding(vertical = 16.dp),
            )
        } else {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("搜索零件名", fontSize = 13.sp) },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 8.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedTextColor = Color.White,
                    unfocusedTextColor = Color.White,
                    focusedBorderColor = LogisticsColors.Info,
                    unfocusedBorderColor = Color.White.copy(alpha = 0.25f),
                    cursorColor = Color.White,
                ),
            )
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                items(filtered, key = { it.name }) { part ->
                    val isSelected = selected?.name == part.name
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { onSelect(part) }
                            .background(
                                if (isSelected) LogisticsColors.Info.copy(alpha = 0.25f)
                                else Color.White.copy(alpha = 0.05f),
                                RoundedCornerShape(10.dp),
                            )
                            .padding(horizontal = 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                PartNameCn.displayName(part.name),
                                fontSize = 14.sp,
                                color = Color.White,
                                fontWeight = if (isSelected) FontWeight.SemiBold else FontWeight.Normal,
                            )
                            if (PartNameCn.displayName(part.name) != part.name) {
                                Text(
                                    part.name,
                                    fontSize = 11.sp,
                                    color = Color.White.copy(alpha = 0.45f),
                                )
                            }
                        }
                        if (isSelected) {
                            Text("已定位", fontSize = 12.sp, color = LogisticsColors.Info)
                        }
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
            .background(
                Brush.linearGradient(
                    colors = listOf(LogisticsColors.BrandNavy, LogisticsColors.BrandNavyDark),
                ),
            )
            .padding(horizontal = 8.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack) { Text("返回", fontSize = 14.sp, color = Color.White) }
        Text(
            title,
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
            color = Color.White,
            modifier = Modifier.weight(1f),
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.width(64.dp))
    }
}

@Composable
private fun LoadingState(modifier: Modifier = Modifier, stage: String = "") {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(Modifier.size(40.dp), color = Color.White)
            Text(
                if (stage.isNotBlank()) stage else "模型加载中…",
                fontSize = 14.sp,
                color = Color.White.copy(alpha = 0.6f),
                modifier = Modifier.padding(top = 12.dp),
            )
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
            Text(message, fontSize = 14.sp, color = Color.White.copy(alpha = 0.75f), textAlign = TextAlign.Center)
            OutlinedButton(
                onClick = onRetry,
                modifier = Modifier.padding(top = 16.dp),
                border = BorderStroke(1.dp, Color.White.copy(alpha = 0.35f)),
            ) {
                Text("重试", fontSize = 14.sp, color = Color.White)
            }
        }
    }
}

@Composable
private fun EmptyState(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text("暂无模型", fontSize = 14.sp, color = Color.White.copy(alpha = 0.6f))
    }
}
