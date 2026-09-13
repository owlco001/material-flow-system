package com.company.logistics.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.BuildConfig
import com.company.logistics.data.EndpointStore
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.PrimaryButton
import com.company.logistics.ui.components.SecondaryButton
import com.company.logistics.ui.components.SectionTitle
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.Dimens
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.LogisticsType
import com.company.logistics.ui.theme.Spacing
import kotlinx.coroutines.delay

/**
 * 后端地址配置页。
 *
 * 定位：现场部署与调试入口，不面向日常作业。
 * 设计取舍：
 *  - 放在「我的 → 服务端配置」，不做角色限制 ——
 *    配错地址本身无提权风险，且现场常有运维代配的需求；
 *  - 保存后提供「测试连通性」，但不强制通过才允许保存 ——
 *    后端可能暂时没起来，不应因此卡住配置流程；
 *  - 明文 http 给出显式警示，但不阻止（内网部署的常态）。
 */
@Composable
fun EndpointConfigScreen(
    store: EndpointStore,
    onEndpointChanged: (String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = LogisticsTheme.colors

    var input by remember { mutableStateOf(store.effectiveUrl) }
    var errorText by remember { mutableStateOf<String?>(null) }
    var savedHint by remember { mutableStateOf<String?>(null) }
    var testing by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<TestResult?>(null) }

    // 保存成功提示 2 秒后自动消失，避免常驻干扰
    LaunchedEffect(savedHint) {
        if (savedHint != null) {
            delay(2000)
            savedHint = null
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Dimens.PagePadding),
    ) {
        VSpace(Spacing.sm)

        // ---------- 当前生效地址 ----------
        AppCard(
            accentColor = when {
                store.isPlaceholder -> colors.danger
                store.savedUrl != null -> colors.success
                else -> colors.warning
            },
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(8.dp)
                        .background(
                            when {
                                store.isPlaceholder -> colors.danger
                                store.savedUrl != null -> colors.success
                                else -> colors.warning
                            },
                            CircleShape,
                        )
                )
                Spacer(Modifier.width(Spacing.sm))
                Text(
                    when {
                        store.isPlaceholder -> "未配置 · 无法连接"
                        store.savedUrl != null -> "已配置（人工）"
                        else -> "使用构建默认值"
                    },
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = colors.textPrimary,
                )
            }
            VSpace(6.dp)
            Text(
                store.effectiveUrl,
                fontSize = 13.sp,
                fontFamily = LogisticsType.MonoFamily,
                color = colors.textSecondary,
            )
        }

        if (store.isPlaceholder) {
            VSpace(Spacing.md)
            AppCard(accentColor = colors.danger) {
                Text(
                    "当前是占位地址，登录与扫码查询都会失败",
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = colors.danger,
                )
                VSpace(4.dp)
                Text(
                    "请在下方填入实际后端地址后保存。",
                    fontSize = 12.sp,
                    color = colors.textSecondary,
                    lineHeight = 18.sp,
                )
            }
        }

        VSpace(Spacing.lg)
        SectionTitle("后端地址")

        VSpace(Spacing.sm)
        OutlinedTextField(
            value = input,
            onValueChange = {
                input = it
                errorText = null
                testResult = null
            },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            isError = errorText != null,
            shape = MaterialTheme.shapes.small,
            placeholder = {
                Text(
                    "http://192.168.1.10:8000",
                    fontSize = 14.sp,
                    color = colors.textTertiary,
                    fontFamily = LogisticsType.MonoFamily,
                )
            },
            supportingText = errorText?.let {
                { Text(it, fontSize = 12.sp) }
            },
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Uri,
                imeAction = ImeAction.Done,
            ),
            textStyle = androidx.compose.ui.text.TextStyle(
                fontFamily = LogisticsType.MonoFamily,
                fontSize = 14.sp,
            ),
        )

        VSpace(Spacing.xs)
        Text(
            "可不写协议，默认按 http:// 处理；末尾斜杠会被忽略",
            fontSize = 11.sp,
            color = colors.textTertiary,
        )

        VSpace(Spacing.md)
        PrimaryButton(
            text = "保存",
            onClick = {
                store.save(input)
                    .onSuccess {
                        input = it
                        onEndpointChanged(it)
                        errorText = null
                        savedHint = "已保存"
                    }
                    .onFailure {
                        errorText = it.message ?: EndpointStore.ERR_INVALID
                    }
            },
            enabled = input.isNotBlank(),
        )

        // ---------- 连通性测试 ----------
        VSpace(Spacing.md)
        SecondaryButton(
            text = if (testing) "测试中…" else "测试连通性",
            onClick = {
                testing = true
                testResult = null
            },
            enabled = !testing,
        )

        if (testing) {
            LaunchedEffect(input, testing) {
                testResult = EndpointTester.test(store.effectiveUrl)
                testing = false
            }
        }

        testResult?.let { result ->
            VSpace(Spacing.sm)
            AppCard(
                accentColor = if (result.ok) colors.success else colors.danger,
            ) {
                Text(
                    if (result.ok) "连接正常" else "连接失败",
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = if (result.ok) colors.success else colors.danger,
                )
                VSpace(4.dp)
                Text(
                    result.detail,
                    fontSize = 12.sp,
                    color = colors.textSecondary,
                    lineHeight = 18.sp,
                )
            }
        }

        savedHint?.let {
            VSpace(Spacing.sm)
            Text(it, fontSize = 12.sp, fontWeight = FontWeight.Bold, color = colors.success)
        }

        // ---------- 明文流量提示 ----------
        if (store.effectiveUrl.startsWith("http://", ignoreCase = true)) {
            VSpace(Spacing.lg)
            AppCard(accentColor = colors.warning) {
                Text(
                    "使用的是明文 HTTP",
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = colors.warning,
                )
                VSpace(4.dp)
                Text(
                    if (BuildConfig.DEBUG) {
                        "Debug 验收版本允许受控内网使用明文 HTTP，但流量可被同网段嗅探或篡改；" +
                            "仅限受控内网使用，生产环境请改用 HTTPS。"
                    } else {
                        "正式版不允许明文 HTTP，请改用 HTTPS 证书地址。"
                    },
                    fontSize = 12.sp,
                    color = colors.textSecondary,
                    lineHeight = 18.sp,
                )
            }
        }

        VSpace(Spacing.lg)
        SectionTitle("恢复默认")
        VSpace(Spacing.sm)
        SecondaryButton(
            text = "清除自定义地址",
            onClick = {
                store.clear()
                input = store.effectiveUrl
                onEndpointChanged(store.effectiveUrl)
                errorText = null
                savedHint = "已恢复默认值"
                testResult = null
            },
            enabled = store.savedUrl != null,
        )

        VSpace(Spacing.xl)
        TextButton(onClick = onBack) {
            Text("返回", fontSize = 14.sp, color = colors.primary)
        }

        VSpace(Spacing.xxl)
    }
}

/** 连通性测试结果 */
data class TestResult(val ok: Boolean, val detail: String)

/**
 * 连通性测试器。
 *
 * 只打 /healthz —— 该端点无需鉴权，能最快区分
 * 「地址/网络不通」与「服务在但账号有问题」。
 */
private object EndpointTester {
    suspend fun test(baseUrl: String): TestResult =
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            try {
                val url = java.net.URL(baseUrl.trimEnd('/') + "/healthz")
                val conn = (url.openConnection() as java.net.HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 8000
                    readTimeout = 8000
                }
                val code = conn.responseCode
                val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                    ?.bufferedReader()?.use { it.readText() }
                conn.disconnect()
                when {
                    code in 200..299 -> TestResult(true, "HTTP $code · ${body?.take(120) ?: ""}")
                    code == 404 -> TestResult(false, "HTTP 404 · 地址可达，但该路径不存在，请检查是否多/少了路径前缀")
                    else -> TestResult(false, "HTTP $code · ${body?.take(120) ?: "无响应体"}")
                }
            } catch (e: java.net.UnknownHostException) {
                TestResult(false, "域名无法解析：${e.message ?: baseUrl}")
            } catch (e: java.net.SocketTimeoutException) {
                TestResult(false, "连接超时 · 地址可能不可达，或端口未开放")
            } catch (e: javax.net.ssl.SSLException) {
                TestResult(false, "TLS 握手失败 · 若为自签证书需改用 http 或配置信任链")
            } catch (e: java.io.IOException) {
                // 明文策略已放开，理论上不会被系统拦截；
                // 保留该分支以防后续收窄为白名单时静默失败。
                val msg = e.message.orEmpty()
                if (msg.contains("Cleartext", ignoreCase = true)) {
                    TestResult(false, "明文流量被系统拦截 · 当前构建的明文策略为关闭状态")
                } else {
                    TestResult(false, "网络异常：$msg")
                }
            } catch (e: Exception) {
                TestResult(false, "未知错误：${e.javaClass.simpleName} ${e.message.orEmpty()}")
            }
        }
}
