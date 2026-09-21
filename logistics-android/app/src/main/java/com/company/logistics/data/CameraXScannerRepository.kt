package com.company.logistics.data

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.domain.BarcodeFormat
import com.company.logistics.domain.HardwareScannerAdapter
import com.company.logistics.domain.HardwareScannerState
import com.company.logistics.domain.ScanEvent
import com.company.logistics.domain.ScannerRepository
import com.company.logistics.model.ScanResult
import com.google.mlkit.vision.barcode.BarcodeScanner
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.guava.await
import kotlinx.coroutines.withContext
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * CameraX + ML Kit 扫码实现。
 *
 * 实现约束（规格第 5 节）：
 *  - STRATEGY_KEEP_ONLY_LATEST，避免中端设备堆积帧
 *  - 任何路径都必须调用 imageProxy.close()
 *  - 分析在单线程执行器，不在主线程跑识别/网络
 *  - 页面进入才启动，离开立即解绑；不保存帧、不截图、不写文件
 *  - 默认只识别 QR / Code128 / Code39 / EAN-13 / EAN-8 / DataMatrix
 */
class CameraXScannerRepository(
    private val context: Context,
    private val api: MaterialFlowApi,
) : ScannerRepository {

    private val _scanEvents = MutableSharedFlow<ScanEvent>(
        replay = 0,
        extraBufferCapacity = 1,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    override fun observeScanEvents(): Flow<ScanEvent> = _scanEvents.asSharedFlow()

    private var cameraProvider: ProcessCameraProvider? = null
    private var analysisExecutor: ExecutorService? = null
    private var analyzer: BarcodeAnalyzer? = null
    private var camera: androidx.camera.core.Camera? = null
    private var torchEnabled = false
    private var torchSupported = false
    private var cameraBound = false
    private var lifecycleGeneration = 0L

    /** 去重表：key = rawValue|format，value = 上次处理时间戳 */
    private val recentScans = mutableMapOf<String, Long>()

    override val isTorchAvailable: Boolean get() = torchSupported
    override val isTorchOn: Boolean get() = torchEnabled

    @SuppressLint("UnsafeOptInUsageError")
    override suspend fun startCamera(owner: LifecycleOwner, preview: PreviewView): Result<Unit> =
        withContext(Dispatchers.Main.immediate) {
            val generation = ++lifecycleGeneration
            try {
                if (!hasCameraPermission()) {
                    // 带 CAMERA 关键字，便于上层翻译成「缺少摄像头权限」这类人能读的原因
                    throw IllegalStateException("CAMERA_PERMISSION_MISSING")
                }
                // PreviewView / LifecycleOwner may have been recreated. Rebind instead of
                // claiming success for a stale binding that points at an old surface.
                if (cameraBound) releaseCamera()

                val provider = ProcessCameraProvider.getInstance(context).await()
                if (generation != lifecycleGeneration) return@withContext Result.success(Unit)
                cameraProvider = provider

                val previewUseCase = Preview.Builder().build().also {
                    it.setSurfaceProvider(preview.surfaceProvider)
                }

                val executor = Executors.newSingleThreadExecutor()
                analysisExecutor = executor

                val scanner = buildBarcodeScanner()
                val imageAnalysis = ImageAnalysis.Builder()
                    // 只保留最新帧，避免内存增长
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                    .also { analysis ->
                        analyzer = BarcodeAnalyzer(scanner, executor) { barcode ->
                            onBarcodeDetected(barcode)
                        }
                        analysis.setAnalyzer(executor, analyzer!!)
                    }

                provider.unbindAll()
                if (generation != lifecycleGeneration) {
                    releaseCamera()
                    return@withContext Result.success(Unit)
                }
                // 相机可能被其他应用独占，bindToLifecycle 会抛 CamcorderProfileProvider
                // 或 IllegalArgumentException；原样上抛给 ViewModel 翻译成可读原因
                val camera = provider.bindToLifecycle(
                    owner,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    previewUseCase,
                    imageAnalysis,
                )

                torchSupported = camera.cameraInfo.hasFlashUnit()
                torchEnabled = false
                this@CameraXScannerRepository.camera = camera
                cameraBound = true
                Result.success(Unit)
            } catch (cancelled: CancellationException) {
                releaseCamera()
                throw cancelled
            } catch (e: Throwable) {
                // 失败必须彻底释放，否则下次重试会带着半绑定状态再次失败
                releaseCamera()
                Result.failure(CameraStartException(describe(e), e))
            }
        }

    /** 把底层异常转成简短可读的原因，避免把整段堆栈透给 UI */
    private fun describe(e: Throwable): String = when {
        e is CameraStartException -> e.message.orEmpty()
        e.message?.contains("CAMERA_PERMISSION_MISSING") == true -> "缺少摄像头权限"
        e is IllegalArgumentException && e.message?.contains("does not have a camera") == true ->
            "设备没有可用摄像头"
        e.message?.contains("in use", ignoreCase = true) == true ||
            e.message?.contains("disconnected", ignoreCase = true) == true ->
            "相机被其他应用占用"
        else -> "摄像头初始化失败，请重试"
    }

    override suspend fun stopCamera(): Result<Unit> = withContext(Dispatchers.Main.immediate) {
        lifecycleGeneration++
        runCatching { releaseCamera() }
    }

    override fun releaseCameraResources() {
        lifecycleGeneration++
        if (Looper.myLooper() == Looper.getMainLooper()) {
            releaseCamera()
        } else {
            Handler(Looper.getMainLooper()).post { releaseCamera() }
        }
    }

    override fun toggleTorch(): Boolean {
        val control = camera?.cameraControl
        if (!torchSupported || control == null) return false
        torchEnabled = !torchEnabled
        control.enableTorch(torchEnabled)
        return torchEnabled
    }

    private fun releaseCamera() {
        runCatching {
            analyzer?.close()
            analyzer = null
            cameraProvider?.unbindAll()
            cameraBound = false
        }
        analysisExecutor?.shutdown()
        analysisExecutor = null
        cameraProvider = null
        camera = null
        torchEnabled = false
        torchSupported = false
    }

    override suspend fun resolve(rawValue: String): Result<ScanResult> {
        val trimmed = rawValue.trim()
        if (trimmed.length !in ScannerRepository.MIN_RAW_LENGTH..ScannerRepository.MAX_RAW_LENGTH) {
            return Result.failure(IllegalArgumentException("条码长度必须在 1-128 之间"))
        }
        // api.resolveScan 失败时抛 ApiException，由仓库层统一转成 Result
        return runCatching { api.resolveScan(trimmed) }
    }

    private fun buildBarcodeScanner(): BarcodeScanner {
        val options = BarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_QR_CODE,
                Barcode.FORMAT_CODE_128,
                Barcode.FORMAT_CODE_39,
                Barcode.FORMAT_EAN_13,
                Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_DATA_MATRIX,
            )
            .build()
        return BarcodeScanning.getClient(options)
    }

    private fun onBarcodeDetected(barcode: Barcode) {
        val raw = barcode.rawValue ?: return
        val format = barcode.mapFormat()
        if (!format.isSupported) return

        val trimmed = raw.trim()
        if (trimmed.length !in ScannerRepository.MIN_RAW_LENGTH..ScannerRepository.MAX_RAW_LENGTH) return

        // 去重键 = rawValue + format（规格第 6 节，不是全局时间屏蔽）
        val key = "$trimmed|$format"
        val now = System.currentTimeMillis()
        val last = recentScans[key]
        if (last != null && now - last < ScannerRepository.DEDUP_WINDOW_MS) return
        recentScans[key] = now

        // 清理过期条目，避免长时间运行后 map 无限增长
        if (recentScans.size > 64) {
            val threshold = now - ScannerRepository.DEDUP_WINDOW_MS
            recentScans.entries.removeAll { it.value < threshold }
        }

        _scanEvents.tryEmit(ScanEvent(rawValue = trimmed, format = format, capturedAt = now))
    }

    private fun Barcode.mapFormat(): BarcodeFormat = when (format) {
        Barcode.FORMAT_QR_CODE -> BarcodeFormat.QR_CODE
        Barcode.FORMAT_CODE_128 -> BarcodeFormat.CODE_128
        Barcode.FORMAT_CODE_39 -> BarcodeFormat.CODE_39
        Barcode.FORMAT_EAN_13 -> BarcodeFormat.EAN_13
        Barcode.FORMAT_EAN_8 -> BarcodeFormat.EAN_8
        Barcode.FORMAT_DATA_MATRIX -> BarcodeFormat.DATA_MATRIX
        else -> BarcodeFormat.UNKNOWN
    }

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
}

/**
 * 相机启动失败。
 *
 * 单独定义（而非直接上抛底层异常）是为了让上层能区分
 * 「相机没起来」与「条码解析失败」两类完全不同的错误，
 * 前者需要重试入口，后者需要重新对准条码。
 */
class CameraStartException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

/**
 * 单帧分析器。
 * 约束：任何分支都必须 close() ImageProxy，否则 CameraX 会停止出帧。
 */
private class BarcodeAnalyzer(
    private val scanner: BarcodeScanner,
    private val executor: ExecutorService,
    private val onDetected: (Barcode) -> Unit,
) : ImageAnalysis.Analyzer {

    private val closed = AtomicBoolean(false)

    @SuppressLint("UnsafeOptInUsageError")
    override fun analyze(imageProxy: ImageProxy) {
        if (closed.get()) {
            imageProxy.close()
            return
        }
        val mediaImage = imageProxy.image ?: run {
            imageProxy.close()
            return
        }
        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        try {
            scanner.process(image)
                .addOnSuccessListener { barcodes ->
                    if (!closed.get()) barcodes.firstOrNull()?.let(onDetected)
                }
                .addOnCompleteListener { imageProxy.close() }
        } catch (_: Throwable) {
            // ML Kit may reject a frame synchronously while the scanner is closing.
            imageProxy.close()
        }
    }

    fun close() {
        closed.set(true)
        runCatching { scanner.close() }
    }
}

/** 空实现的键盘楔入适配器，供后续接工业扫码枪 */
class NoopHardwareScannerAdapter : HardwareScannerAdapter {
    override fun connect(): Flow<HardwareScannerState> =
        kotlinx.coroutines.flow.flowOf(HardwareScannerState.DISCONNECTED)

    override fun disconnect() = Unit

    override fun observeRawValues(): Flow<String> = kotlinx.coroutines.flow.emptyFlow()
}
