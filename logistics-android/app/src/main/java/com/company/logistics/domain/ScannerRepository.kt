package com.company.logistics.domain

import android.content.Context
import androidx.camera.view.PreviewView
import androidx.lifecycle.LifecycleOwner
import com.company.logistics.model.ScanResult
import kotlinx.coroutines.flow.Flow

/**
 * 扫码领域接口。
 *
 * 对齐《Android摄像头扫码实现规格》第 4.1 节：
 * 相机、工业扫码枪、手动输入三种来源统一收敛到本接口，
 * 业务层不依赖任何具体硬件实现。
 */
interface ScannerRepository {

    /** 扫码事件流：相机识别与硬件扫码枪的原文都从这里流出 */
    fun observeScanEvents(): Flow<ScanEvent>

    /** 绑定生命周期并启动预览与分析 */
    suspend fun startCamera(owner: LifecycleOwner, preview: PreviewView): Result<Unit>

    /** 停止分析并释放 CameraX use case */
    suspend fun stopCamera(): Result<Unit>

    /** 切换闪光灯；设备不支持时返回失败原因 */
    fun toggleTorch(): Boolean

    /** 当前闪光灯状态 */
    val isTorchAvailable: Boolean
    val isTorchOn: Boolean

    /** 手势/服务端业务解析：把条码原文交给 /scan/resolve */
    suspend fun resolve(rawValue: String): Result<ScanResult>

    companion object {
        /** 同一 rawValue + format 在 1500ms 内只解析一次（规格第 6 节） */
        const val DEDUP_WINDOW_MS = 1500L

        /** 原文长度约束（规格第 6 节） */
        const val MIN_RAW_LENGTH = 1
        const val MAX_RAW_LENGTH = 128
    }
}

/** 扫码事件（规格第 4.1 节） */
data class ScanEvent(
    val rawValue: String,
    val format: BarcodeFormat,
    val capturedAt: Long,
)

/**
 * 条码格式。
 * 规格第 5 节：默认仅识别 QR / Code128 / Code39 / EAN-13 / EAN-8 / DataMatrix。
 */
enum class BarcodeFormat(val label: String) {
    QR_CODE("QR 码"),
    CODE_128("Code 128"),
    CODE_39("Code 39"),
    EAN_13("EAN-13"),
    EAN_8("EAN-8"),
    DATA_MATRIX("Data Matrix"),
    UNKNOWN("未知格式");

    val isSupported: Boolean get() = this != UNKNOWN
}

/**
 * 工业扫码枪适配器（规格第 4.1 节）。
 * 键盘楔入 / 广播模式统一走这里，禁止把扫码枪逻辑复制进业务页面。
 */
interface HardwareScannerAdapter {
    fun connect(): Flow<HardwareScannerState>
    fun disconnect()
    fun observeRawValues(): Flow<String>
}

enum class HardwareScannerState { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

/** 扫码 UI 状态（规格第 4.2 节） */
sealed interface ScannerUiState {
    /** 尚未授权，需要请求 CAMERA 权限 */
    data object PermissionRequired : ScannerUiState

    /** 用户拒绝授权；permanentlyDenied 为 true 时应引导去系统设置 */
    data class PermissionDenied(val permanentlyDenied: Boolean) : ScannerUiState

    /** 相机就绪，等待扫码 */
    data object Ready : ScannerUiState

    /** 已识别到条码，正在请求服务端解析 */
    data object Processing : ScannerUiState

    /** 解析完成 */
    data class Resolved(val resolution: ScanResult) : ScannerUiState

    /** 出错；retryable 决定是否展示重试按钮 */
    data class Error(
        val code: String,
        val message: String,
        val retryable: Boolean,
    ) : ScannerUiState
}

/** 相机不可用的原因，用于给出可操作提示（规格第 8 节异常项） */
enum class CameraUnavailableReason(val message: String) {
    NO_CAMERA("本机没有可用摄像头"),
    IN_USE("摄像头被其他应用占用"),
    INIT_FAILED("摄像头初始化失败"),
    UNKNOWN("摄像头暂不可用"),
}
