package com.company.logistics

import android.os.Bundle
import android.content.ComponentName
import android.content.Intent
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import com.company.logistics.data.CameraXScannerRepository
import com.company.logistics.data.EndpointStore
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.data.OfflineSyncScheduler
import com.company.logistics.data.SessionStore
import com.company.logistics.data.remote.ApiConfig
import com.company.logistics.ui.LogisticsApp
import com.company.logistics.ui.LogisticsViewModel
import com.company.logistics.ui.ScannerViewModel
import com.company.logistics.ui.screens.BootSplashScreen

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
        // 进程启动时调度一次离线同步（唯一任务，重复调用无副作用）。
        // Repository.get() 内已恢复卡死的 SYNCING 并调度，这里再保底一次，
        // 覆盖 Activity 重建但 Repository 单例已存在的场景。
        OfflineSyncScheduler.enqueue(applicationContext)
        com.company.logistics.ui.DeviceId.value = SessionStore.get(applicationContext).deviceIdOrCreate()
        // 系统「减少动画」开关：为 0 表示用户关闭了动画，开机动画应直接呈现终态
        val animatorScale = Settings.Global.getFloat(
            contentResolver,
            Settings.Global.ANIMATOR_DURATION_SCALE,
            1f,
        )

        // 运行时地址覆盖：用户保存的地址优先于构建期注入值。
        // 必须在任何网络调用之前设置，否则首个请求会打到默认地址。
        val endpointStore = EndpointStore.get(applicationContext)
        ApiConfig.baseUrl = endpointStore.effectiveUrl

        // 会话恢复由 LogisticsViewModel 驱动，并在 UI 中显式展示 Restoring 状态。
        // 这里先完成地址与设备标识装配，避免恢复过程早于运行时配置。
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

            // 开机动画：只在整个进程首次创建时播放。
            // rememberSaveable 保证屏幕旋转/Activity 重建后不会重播，
            // 否则用户每次转屏都要再看一次 logo，非常烦人。
            var showSplash by rememberSaveable { mutableStateOf(true) }
            val reduceMotion = animatorScale == 0f

            if (showSplash) {
                BootSplashScreen(
                    onFinished = { showSplash = false },
                    reduceMotion = reduceMotion,
                )
            } else {
                LogisticsApp(
                    viewModel = vm,
                    scannerViewModel = scannerVm,
                    endpointStore = endpointStore,
                    onEndpointChanged = { ApiConfig.baseUrl = it },
                    onOpenDebug3d = if (BuildConfig.DEBUG) {
                        {
                            startActivity(
                                Intent().setComponent(
                                    ComponentName(packageName, "com.company.logistics.Debug3dActivity")
                                )
                            )
                        }
                    } else {
                        null
                    },
                )
            }
        }
    }
}
