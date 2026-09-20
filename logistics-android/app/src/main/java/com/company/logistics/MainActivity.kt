package com.company.logistics

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import com.company.logistics.data.LogisticsRepository
import com.company.logistics.ui.LogisticsApp
import com.company.logistics.ui.LogisticsViewModel

/**
 * 应用入口。
 *
 * 架构分层：
 *   MainActivity → LogisticsApp（导航/主题）
 *                → LogisticsViewModel（状态/路由）
 *                → LogisticsRepository（网络 + 离线队列）
 *                → MaterialFlowApi（契约 API）/ Room（离线持久化）
 *
 * 说明：V1 采用轻量手写依赖装配，未引入 DI 框架以减少包体积与构建复杂度；
 *      若后续模块增多，可平滑替换为 Hilt。
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val repository = LogisticsRepository.get(applicationContext)

        setContent {
            val vm: LogisticsViewModel = viewModel(
                factory = object : ViewModelProvider.Factory {
                    @Suppress("UNCHECKED_CAST")
                    override fun <T : ViewModel> create(modelClass: Class<T>): T =
                        LogisticsViewModel(repository) as T
                }
            )
            LogisticsApp(vm)
        }
    }
}
