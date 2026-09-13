package com.company.logistics.ui

import androidx.camera.view.PreviewView
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.company.logistics.domain.ScannerRepository
import com.company.logistics.domain.ScannerUiState
import com.company.logistics.data.remote.ApiException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

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

    private val _cameraBound = MutableStateFlow(false)
    val cameraBound: StateFlow<Boolean> = _cameraBound.asStateFlow()

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

    /**
     * 绑定预览并开始识别。
     * 仅当权限已被授予时调用；权限流程由 UI 层负责请求。
     */
    fun startScanning(owner: LifecycleOwner, preview: PreviewView) {
        if (_cameraBound.value) return
        viewModelScope.launch {
            scannerRepository.startCamera(owner, preview)
                .onSuccess {
                    _cameraBound.value = true
                    _torchAvailable.value = scannerRepository.isTorchAvailable
                    _cameraError.value = null
                    _uiState.value = ScannerUiState.Ready
                }
                .onFailure { e ->
                    _cameraBound.value = false
                    _cameraError.value = e.message ?: "摄像头初始化失败"
                    _manualInputVisible.value = true
                    _uiState.value = ScannerUiState.Ready
                }
        }
        // 订阅识别事件
        viewModelScope.launch {
            scannerRepository.observeScanEvents().collect { event ->
                resolve(event.rawValue)
            }
        }
    }

    fun stopScanning() {
        viewModelScope.launch {
            scannerRepository.stopCamera()
            _cameraBound.value = false
            _torchOn.value = false
        }
    }

    fun onPermissionResult(granted: Boolean, permanentlyDenied: Boolean) {
        _uiState.value = if (granted) {
            ScannerUiState.Ready
        } else {
            _manualInputVisible.value = true
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
                        is ApiException -> e.code to e.retryable
                        else -> "NETWORK_ERROR" to true
                    }
                    _uiState.value = ScannerUiState.Error(
                        code = code,
                        message = e.message ?: "解析失败",
                        retryable = retryable,
                    )
                }
        }
    }
}
