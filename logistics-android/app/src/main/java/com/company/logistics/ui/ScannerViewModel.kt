package com.company.logistics.ui

import androidx.camera.view.PreviewView
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.company.logistics.domain.ScannerRepository
import com.company.logistics.domain.ScannerUiState
import com.company.logistics.data.remote.ApiException
import com.company.logistics.data.remote.safeMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/**
 * 相机启动状态机。
 *
 * 存在的理由：此前 UI 只有一个 `else` 分支，把「正在异步初始化」与
 * 「启动失败」渲染成同一句话「正在启动摄像头…」，且失败后没有任何恢复路径
 * —— 用户分不清是在转圈还是已经死了，只能退出重进。
 *
 * 拆成显式状态后，UI 可以对三种情况给出不同且可操作的反馈：
 *
 *   Idle ──startScanning()──► Starting ──成功──► Ready
 *                                │
 *                                └──超时/异常──► Failed(reason) ──retry()──► Starting
 */
sealed interface CameraStatus {
    /** 尚未开始（权限未授予或页面未就绪） */
    data object Idle : CameraStatus

    /** 正在异步绑定相机；UI 应显示进度，超过 [CAMERA_START_TIMEOUT_MS] 转 Failed */
    data object Starting : CameraStatus

    /** 预览已出图，可扫码 */
    data object Ready : CameraStatus

    /** 启动失败；[reason] 是给现场人员看的具体原因，不是堆栈 */
    data class Failed(val reason: String) : CameraStatus
}

/**
 * 扫码页 ViewModel。
 *
 * 对齐《Android摄像头扫码实现规格》第 4.2 节接口定义，
 * 并补充相机绑定状态与闪光灯状态，供 UI 渲染。
 */
class ScannerViewModel(
    private val scannerRepository: ScannerRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow<ScannerUiState>(ScannerUiState.PermissionRequired)
    val uiState: StateFlow<ScannerUiState> = _uiState.asStateFlow()

    private val _cameraStatus = MutableStateFlow<CameraStatus>(CameraStatus.Idle)
    val cameraStatus: StateFlow<CameraStatus> = _cameraStatus.asStateFlow()

    /** 仍在绑定中（Starting）时为 true —— UI 据此显示进度指示 */
    val cameraBound: StateFlow<Boolean> get() = _cameraBoundMirror

    private val _cameraBoundMirror = MutableStateFlow(false)

    private val _torchOn = MutableStateFlow(false)
    val torchOn: StateFlow<Boolean> = _torchOn.asStateFlow()

    private val _torchAvailable = MutableStateFlow(false)
    val torchAvailable: StateFlow<Boolean> = _torchAvailable.asStateFlow()

    /** 相机不可用的原因；非空时 UI 应展示手动输入降级入口 */
    private val _cameraError = MutableStateFlow<String?>(null)
    val cameraError: StateFlow<String?> = _cameraError.asStateFlow()

    /** 手动输入切换 */
    private val _manualInputVisible = MutableStateFlow(false)
    val manualInputVisible: StateFlow<Boolean> = _manualInputVisible.asStateFlow()

    private var cameraStartJob: Job? = null
    private var scanEventsJob: Job? = null
    private var cameraGeneration = 0L

    /**
     * 绑定预览并开始识别。
     * 仅当权限已被授予时调用；权限流程由 UI 层负责请求。
     *
     * 重入保护说明：原实现用 `if (_cameraBound.value) return`，
     * 而 `_cameraBound` 只在成功时置位 —— 失败后该判断恒为 false，
     * 但 Compose 也不会再重跑 `AndroidView` 的 factory，于是永远不会重试。
     * 改为按 [CameraStatus] 判断：仅 Ready / Starting 时拒绝重入，
     * Failed 状态允许（也仅允许通过 [retryCamera]）再次尝试。
     */
    fun startScanning(owner: LifecycleOwner, preview: PreviewView) {
        when (_cameraStatus.value) {
            is CameraStatus.Ready, is CameraStatus.Starting -> return
            else -> Unit // Idle / Failed 允许继续
        }
        val generation = ++cameraGeneration
        _cameraStatus.value = CameraStatus.Starting
        _cameraError.value = null

        cameraStartJob?.cancel()
        cameraStartJob = viewModelScope.launch {
            // 超时兜底：CameraX 在某些 ROM 上会静默卡住不回调，
            // 没有超时用户就会无限期停在「正在启动摄像头…」。
            val result = try {
                withTimeout(CAMERA_START_TIMEOUT_MS) {
                    scannerRepository.startCamera(owner, preview).getOrThrow()
                }
                Result.success(Unit)
            } catch (timeout: TimeoutCancellationException) {
                withContext(NonCancellable) { scannerRepository.stopCamera() }
                Result.failure(timeout)
            } catch (cancelled: CancellationException) {
                // A start can be cancelled after CameraX has bound the use cases. Always
                // release on a non-cancellable context so a leaving screen cannot leak it.
                withContext(NonCancellable) { scannerRepository.stopCamera() }
                throw cancelled
            } catch (error: Throwable) {
                Result.failure(error)
            }

            if (!isActiveGeneration(generation)) {
                if (result.isSuccess) scannerRepository.stopCamera()
                return@launch
            }

            result
                .onSuccess {
                    _cameraBoundMirror.value = true
                    _torchAvailable.value = scannerRepository.isTorchAvailable
                    _cameraError.value = null
                    _cameraStatus.value = CameraStatus.Ready
                    _uiState.value = ScannerUiState.Ready
                }
                .onFailure { e ->
                    _cameraBoundMirror.value = false
                    _torchAvailable.value = false
                    _torchOn.value = false
                    val reason = describeCameraFailure(e)
                    _cameraError.value = reason
                    _cameraStatus.value = CameraStatus.Failed(reason)
                    // 相机不可用时直接展开手动输入，现场不至于完全没法作业
                    _manualInputVisible.value = true
                    _uiState.value = ScannerUiState.Ready
                }
        }
        // Subscribe once per ViewModel instance. A process-wide flag would leave a newly
        // created ViewModel without a collector after Activity recreation.
        if (scanEventsJob == null) {
            scanEventsJob = viewModelScope.launch {
                scannerRepository.observeScanEvents().collect { event -> resolve(event.rawValue) }
            }
        }
    }

    /**
     * 重试前把状态推回可重入的 Idle 并清干净仓库侧残留绑定。
     *
     * 单独拆出（而不是在 retryCamera 里直接重建预览）的原因是：
     * 预览视图只在 Ready 态渲染，失败态下它已被移除，
     * 必须由 UI 先回到 Idle 触发重组、重建 AndroidView，
     * 才能重新拿到 PreviewView 去绑定 —— 否则无视图可绑。
     */
    fun resetForRetry() {
        val generation = ++cameraGeneration
        cameraStartJob?.cancel()
        cameraStartJob = null
        viewModelScope.launch {
            runCatching { scannerRepository.stopCamera() }
            if (generation != cameraGeneration) return@launch
            _cameraBoundMirror.value = false
            _torchOn.value = false
            _torchAvailable.value = false
            _cameraError.value = null
            _cameraStatus.value = CameraStatus.Idle
            if (_uiState.value !is ScannerUiState.PermissionDenied) {
                _uiState.value = ScannerUiState.Ready
            }
        }
    }

    fun stopScanning() {
        cameraGeneration++
        cameraStartJob?.cancel()
        cameraStartJob = null
        _cameraBoundMirror.value = false
        _torchOn.value = false
        _torchAvailable.value = false
        _cameraError.value = null
        _cameraStatus.value = CameraStatus.Idle
        viewModelScope.launch {
            runCatching { scannerRepository.stopCamera() }
        }
    }

    override fun onCleared() {
        cameraGeneration++
        cameraStartJob?.cancel()
        scannerRepository.releaseCameraResources()
        super.onCleared()
    }

    /** 把异常翻译成现场能看懂、且能据此行动的原因（不外泄堆栈） */
    private fun describeCameraFailure(e: Throwable): String = when {
        e is kotlinx.coroutines.TimeoutCancellationException ->
            "摄像头启动超时（${CAMERA_START_TIMEOUT_MS / 1000} 秒无响应）"
        // CameraStartException 已由仓库层翻译过，直接采用其文案
        e is com.company.logistics.data.CameraStartException ->
            e.message?.takeIf { it.isNotBlank() } ?: "摄像头初始化失败"
        e.message?.contains("CAMERA", ignoreCase = true) == true ->
            "缺少摄像头权限"
        e is IllegalStateException && e.message?.contains("Lifecycle", ignoreCase = true) == true ->
            "相机与页面生命周期绑定失败"
        else -> "摄像头初始化失败，请重试"
    }

    fun onPermissionResult(granted: Boolean, permanentlyDenied: Boolean) {
        if (!granted) stopScanning()
        _uiState.value = if (granted) {
            ScannerUiState.Ready
        } else {
            _manualInputVisible.value = true
            _cameraStatus.value = CameraStatus.Idle
            ScannerUiState.PermissionDenied(permanentlyDenied)
        }
    }

    fun onRetryPermission() {
        _uiState.value = ScannerUiState.PermissionRequired
    }

    fun onManualInput(rawValue: String) {
        resolve(rawValue)
    }

    fun toggleManualInput() {
        _manualInputVisible.value = !_manualInputVisible.value
    }

    fun toggleTorch() {
        if (!_torchAvailable.value) return
        _torchOn.value = scannerRepository.toggleTorch()
    }

    /** 解析结束后由页面调用，回到待扫码状态 */
    fun onScanConsumed() {
        if (_uiState.value !is ScannerUiState.PermissionDenied) {
            _uiState.value = ScannerUiState.Ready
        }
    }

    private companion object {
        /** 相机启动超时；超过则判定失败并给用户重试入口 */
        const val CAMERA_START_TIMEOUT_MS = 3_000L
    }

    private fun isActiveGeneration(generation: Long): Boolean =
        generation == cameraGeneration

    private fun resolve(rawValue: String) {
        if (_uiState.value is ScannerUiState.Processing) return
        val trimmed = rawValue.trim()
        if (trimmed.isEmpty()) {
            _uiState.value = ScannerUiState.Error("EMPTY_SCAN", "条码内容为空", retryable = false)
            return
        }
        if (trimmed.length > ScannerRepository.MAX_RAW_LENGTH) {
            _uiState.value = ScannerUiState.Error("SCAN_TOO_LONG", "条码超长，无法识别", retryable = false)
            return
        }

        _uiState.value = ScannerUiState.Processing
        viewModelScope.launch {
            scannerRepository.resolve(trimmed)
                .onSuccess { _uiState.value = ScannerUiState.Resolved(it) }
                .onFailure { e ->
                    val (code, retryable) = when (e) {
                        is ApiException -> e.code to (e.retryable || e.isUnauthorized)
                        else -> "NETWORK_ERROR" to true
                    }
                    _uiState.value = ScannerUiState.Error(
                        code = code,
                        message = when (e) {
                            is ApiException -> e.safeMessage("条码解析失败，请重试")
                            else -> "网络不可用，请检查连接后重试"
                        },
                        retryable = retryable,
                    )
                }
        }
    }
}
