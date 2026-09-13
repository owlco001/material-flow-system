package com.company.logistics

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import com.company.logistics.data.CameraXScannerRepository
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.ui.LogisticsApp
import com.company.logistics.ui.LogisticsViewModel
import com.company.logistics.ui.ScannerViewModel

/**
 * 应用入口。
 *
 * 架构分层：
 *   MainActivity → LogisticsApp（导航/主题）
 *                → LogisticsViewModel（状态/路由）
 *                → ScannerViewModel（扫码状态机）
 *                → LogisticsRepository（网络 + 离线队列）
 *                → CameraXScannerRepository（CameraX + ML Kit 条码识别）
 *                → MaterialFlowApi（契约 API）/ Room（离线持久化）
 *
 * 说明：V1 采用轻量手写依赖装配，未引入 DI 框架以减少包体积与构建复杂度；
 *      若后续模块增多，可平滑替换为 Hilt。
 *      扫码链路与业务链路共用同一个 [LogisticsRepository] 实例，
 *      因此复用同一份 MaterialFlowApi（token / baseUrl 一致）。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val repository = LogisticsRepository.get(applicationContext)
        // 冷启动恢复会话：有 refresh token 则静默续期，用户不必每天重登
        repository.restoreSession()
        val scannerRepository = CameraXScannerRepository(
            context = applicationContext,
            api = repository.apiHandle
        )

        setContent {
            val vm: LogisticsViewModel = viewModel(
                factory = object : ViewModelProvider.Factory {
                    @Suppress("UNCHECKED_CAST")
                    override fun <T : ViewModel> create(modelClass: Class<T>): T =
                        LogisticsViewModel(repository) as T
                }
            )
            val scannerVm: ScannerViewModel = viewModel(
                factory = object : ViewModelProvider.Factory {
                    @Suppress("UNCHECKED_CAST")
                    override fun <T : ViewModel> create(modelClass: Class<T>): T =
                        ScannerViewModel(scannerRepository) as T
                }
            )
            LogisticsApp(vm, scannerVm)
        }
    }
}
