package com.company.logistics.data

import android.content.Context
import android.content.SharedPreferences
import com.company.logistics.BuildConfig

/**
 * 后端地址配置（可运行时修改）。
 *
 * 为什么需要它：构建期注入（`-PapiBaseUrl`）要求每次换环境都重新打包，
 * 现场调试 / 多环境切换时不可行。改成「构建期给默认值 + 运行时可覆盖」：
 *
 *   优先级：用户保存的地址 > 构建期注入值 > fail-closed 占位符
 *
 * 安全约束：
 *  - 仅接受 http/https，且必须能解析出 host，防止误填 `abc` 之类导致崩溃；
 *  - 生产环境应使用 HTTPS；当前构建为内网调试放开了明文策略，
 *    上线前应收紧 network_security_config.xml 并改用 https 地址；
 *  - 地址本身不含凭据，无需加密存储。
 */
class EndpointStore private constructor(
    private val prefs: SharedPreferences
) {

    /** 用户显式配置的地址；null 表示未配置过，走构建期默认值 */
    val savedUrl: String?
        get() = prefs.getString(KEY_URL, null)

    /** 当前生效的地址 */
    val effectiveUrl: String get() = savedUrl?.takeIf { it.isNotBlank() } ?: BuildConfig.API_BASE_URL

    /** 是否正在使用构建期默认值（未人工配置） */
    val usingBuildDefault: Boolean get() = savedUrl.isNullOrBlank()

    /** 是否命中占位符 —— 说明既没配也没注入，必然连不上 */
    val isPlaceholder: Boolean
        get() = effectiveUrl.contains("example.invalid")

    fun save(rawUrl: String): Result<String> {
        val normalized = normalize(rawUrl)
            ?: return Result.failure(IllegalArgumentException(ERR_INVALID))
        prefs.edit().putString(KEY_URL, normalized).apply()
        return Result.success(normalized)
    }

    fun clear() {
        prefs.edit().remove(KEY_URL).apply()
    }

    companion object {
        private const val FILE_NAME = "logistics_endpoint"
        private const val KEY_URL = "api_base_url"

        const val ERR_INVALID = "地址格式不正确，请填写形如 http://192.168.1.10:8000 的完整地址"
        const val ERR_SCHEME = "只支持 http:// 或 https:// 开头的地址"

        @Volatile
        private var instance: EndpointStore? = null

        fun get(context: Context): EndpointStore = instance ?: synchronized(this) {
            instance ?: EndpointStore(
                context.applicationContext.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)
            ).also { instance = it }
        }

        /**
         * 规范化用户输入。
         *
         * 容错处理（现场手工输入很容易漏字符）：
         *  - 去除首尾空白与末尾斜杠
         *  - 未写协议时补 `http://`（内网 IP 场景最常见）
         *  - 校验 host 非空
         *
         * 返回 null 表示无法解析为合法地址。
         */
        fun normalize(raw: String): String? {
            var s = raw.trim()
            if (s.isEmpty()) return null

            // 未带协议：按内网调试场景补 http://
            if (!s.startsWith("http://", ignoreCase = true) &&
                !s.startsWith("https://", ignoreCase = true)
            ) {
                // 若用户写了别的协议（如 ftp://），拒绝而不是硬加前缀
                if (s.contains("://")) return null
                s = "http://$s"
            }

            s = s.trimEnd('/')

            // 校验 host 段非空且不含空格
            val authority = s.substringAfter("://")
            val host = authority.substringBefore('/').substringBefore('?')
            val hostOnly = host.substringBeforeLast(':', host)
            if (hostOnly.isBlank() || hostOnly.contains(' ')) return null
            if (s.endsWith("://")) return null

            return s
        }
    }
}
