# Android 真机验收清单

本清单只适用于 `logistics-android`。它是可执行的验收操作规程，不是真机结果报告；仓库当前不包含“真机已通过”的结论。只有实际执行命令并保留脱敏证据后，才能在项目外的验收记录中填写通过或失败。

所有命令均在仓库根目录执行，除非命令块先执行了 `cd logistics-android`。命令输出、截图和验收记录不得包含真实后端地址、账号、密码、Authorization、access token、refresh token 或完整原始日志。

## 1. 代码基线

提交文档前先以以下值对照源码；它们是检查目标，不代表已经在设备上验证通过。

| 项目 | 当前代码值 | 对照位置 |
| --- | --- | --- |
| `applicationId` / `namespace` | `com.company.logistics` | `app/build.gradle.kts` |
| `versionCode` / `versionName` | `6` / `0.3.5` | `app/build.gradle.kts` |
| 启动组件 | `com.company.logistics/.MainActivity` | `app/src/main/AndroidManifest.xml` |
| Debug 包 | `app/build/outputs/apk/debug/app-debug.apk` | Android Gradle Plugin 默认输出 |
| Release 包 | `app/build/outputs/apk/release/app-release-unsigned.apk` | 未配置 release 签名，不能当作可发布包 |

网络配置的代码基线如下：

| 变体 | 资源文件 | 代码行为 |
| --- | --- | --- |
| Release | `app/src/main/res/xml/network_security_config.xml` | `base-config cleartextTrafficPermitted="false"`，只信任系统证书链；Manifest 引用同名资源 |
| Debug | `app/src/debug/res/xml/network_security_config.xml` | `base-config cleartextTrafficPermitted="true"`，并列出本机/模拟器专用域名例外；由于 base-config 为 true，受控验收时其他 HTTP 主机也会被允许 |

`app/build.gradle.kts` 的构建期地址优先级为：`-PapiBaseUrl` / `gradle.properties` 中的 `apiBaseUrl`，然后是环境变量 `API_BASE_URL`，最后回退到占位值 `https://api.example.invalid`。构建期字段不会由 Gradle 校验 URL，也不会自动把 HTTP 改成 HTTPS；不要向这些入口传入带凭据、查询串或片段的值。运行时“服务端配置”页面才会执行地址格式校验，Release 运行时还会拒绝保存 `http://`。

`local.properties` 仅用于本机 Android SDK 定位（例如 `sdk.dir`），不是本项目的 API 地址注入入口；它和本地环境变量都不得提交。

## 2. 安全边界与前置检查

不要在仓库、命令历史、截图或验收记录中写入真实值。需要临时指定地址时，在当前 shell 中使用不含凭据的本机变量；下面的地址是无效占位地址，不可直接用于联网验收：

```bash
cd logistics-android
export LOGISTICS_QA_BASE_URL='https://qa-host.example.invalid'
test -n "$LOGISTICS_QA_BASE_URL"
```

构建前确认 Gradle、JDK、Android SDK 和 ADB 可用。若 SDK 通过环境变量提供：

```bash
test -x ./gradlew
java -version
test -n "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
command -v adb
./gradlew --version
```

若使用 `local.properties` 提供 SDK，则改为确认文件存在且包含本机有效的 `sdk.dir`；不要把该文件内容复制到验收记录。SDK 不可用时应停止并记录“环境未就绪”，不能把静态检查或 Gradle 配置成功当成真机通过。

先核验 Manifest、包名和 Debug/Release 网络资源没有漂移：

```bash
cd logistics-android

rg -n 'applicationId = "com\.company\.logistics"|namespace = "com\.company\.logistics"|versionCode = 6|versionName = "0\.3\.5"' app/build.gradle.kts
rg -n 'android:name="\.MainActivity"|android:networkSecurityConfig="@xml/network_security_config"' app/src/main/AndroidManifest.xml
rg -n '<base-config cleartextTrafficPermitted="false">' app/src/main/res/xml/network_security_config.xml
rg -n '<base-config cleartextTrafficPermitted="true">' app/src/debug/res/xml/network_security_config.xml
rg -n '<domain ' app/src/debug/res/xml/network_security_config.xml
! rg -q '<base-config cleartextTrafficPermitted="true">' app/src/main/res/xml/network_security_config.xml
```

通过条件：上述每个正向 `rg` 至少命中一次，最后一个命令返回成功；Release 主资源不得出现 `cleartextTrafficPermitted="true"`。这里检查的是源码基线，不代表 APK 已构建或设备已安装。

## 3. 构建 Debug 包

先执行单测和 Debug 打包。使用完整任务路径，避免在多模块工程中误调用同名任务：

```bash
cd logistics-android
./gradlew :app:clean :app:testDebugUnitTest :app:assembleDebug

DEBUG_APK='app/build/outputs/apk/debug/app-debug.apk'
test -s "$DEBUG_APK"
sha256sum "$DEBUG_APK"
```

通过条件：

- `:app:testDebugUnitTest` 成功完成；
- `DEBUG_APK` 是非空文件，路径为 `app/build/outputs/apk/debug/app-debug.apk`；
- 输出没有真实地址、账号、密码或令牌；
- Debug 包只用于受控验收，不得作为生产包发布。

如需验证构建期地址注入，优先使用环境变量，避免把内部地址放进 Gradle 命令参数：

```bash
cd logistics-android
test -n "${LOGISTICS_QA_BASE_URL:-}"
API_BASE_URL="$LOGISTICS_QA_BASE_URL" ./gradlew :app:assembleDebug
unset API_BASE_URL
```

如需专门验证命令行属性优先级，只能使用不含凭据的临时地址，并在命令结束后清除变量：

```bash
cd logistics-android
test -n "${LOGISTICS_QA_BASE_URL:-}"
./gradlew :app:assembleDebug -PapiBaseUrl="$LOGISTICS_QA_BASE_URL"
unset LOGISTICS_QA_BASE_URL
```

上述地址会被编译进 `BuildConfig.API_BASE_URL`。构建完成后不要把包含该值的 APK、Gradle 输出或命令历史上传到仓库。

## 4. 构建 Release 检查包

Release 当前明确为不可调试、未启用压缩、未配置签名的检查包。它可以用于验证变体和网络策略，不代表签名、发布或生产验收：

```bash
cd logistics-android
./gradlew :app:assembleRelease

RELEASE_APK='app/build/outputs/apk/release/app-release-unsigned.apk'
test -s "$RELEASE_APK"
sha256sum "$RELEASE_APK"
```

未签名 Release 包不能直接安装到设备；需要做 Release 真机验收时，必须使用正式流程生成的、签名配置相同的 Release 包，并重新核对包名、版本和网络策略。

如需使用 QA 地址重新生成 Release 检查包，应使用 HTTPS 且证书链必须被设备系统信任：

```bash
cd logistics-android
test -n "${LOGISTICS_QA_BASE_URL:-}"
case "$LOGISTICS_QA_BASE_URL" in
  https://*) ;;
  *) echo 'Release 检查必须使用 HTTPS 地址' >&2; exit 1 ;;
esac
./gradlew :app:assembleRelease -PapiBaseUrl="$LOGISTICS_QA_BASE_URL"
```

Release 的通过条件：

- `RELEASE_APK` 存在且为非空文件；
- APK 仍是不可调试变体；
- Release 没有合并 Debug 的明文网络资源；
- 运行时输入 `http://` 被应用拒绝，若构建期误注入 HTTP，则请求因 Release 网络安全策略失败；
- 没有把未签名检查包当作发布包。正式签名、收缩和发布由 CI/发布流程负责。

## 5. 安装前核验包身份

安装前先核对 APK 元数据。优先使用 Android SDK 的 `apkanalyzer` 或 `aapt2`；如果本机没有这些工具，可在安装后使用下一段的 `adb` 命令核验包信息。

使用 `apkanalyzer` 时：

```bash
cd logistics-android
apkanalyzer manifest application-id app/build/outputs/apk/debug/app-debug.apk
apkanalyzer manifest version-code app/build/outputs/apk/debug/app-debug.apk
apkanalyzer manifest version-name app/build/outputs/apk/debug/app-debug.apk
```

预期分别为 `com.company.logistics`、`6`、`0.3.5`。使用 `aapt2` 时：

```bash
aapt2 dump badging app/build/outputs/apk/debug/app-debug.apk \
  | grep -E "package: name=|versionCode=|versionName="
```

输出必须包含包名 `com.company.logistics`、`versionCode='6'` 和 `versionName='0.3.5'`。不要用 `aapt2` 输出替代真机测试结果。

## 6. 连接、安装和基础信息

只连接一台已授权、属于本次验收的 Android 真机；多台设备时必须使用明确序列号。下面的 `read` 不会把序列号写进命令历史：

```bash
cd logistics-android
adb devices -l
read -r -p '输入本次验收设备序列号: ' DEVICE_SERIAL
test -n "$DEVICE_SERIAL"
ADB=(adb -s "$DEVICE_SERIAL")
"${ADB[@]}" get-state
```

安装 Debug APK。`pm clear` 会删除该应用在验收设备上的本地数据，只能在确认设备和包名无误后执行：

```bash
DEBUG_APK='app/build/outputs/apk/debug/app-debug.apk'
test -s "$DEBUG_APK"
"${ADB[@]}" install -r -d "$DEBUG_APK"
"${ADB[@]}" shell pm clear com.company.logistics
"${ADB[@]}" shell dumpsys package com.company.logistics \
  | grep -E 'versionCode|versionName|android.permission.CAMERA'
"${ADB[@]}" shell pm path com.company.logistics
"${ADB[@]}" shell appops get com.company.logistics CAMERA
"${ADB[@]}" shell am force-stop com.company.logistics
"${ADB[@]}" shell am start -n com.company.logistics/.MainActivity
```

通过条件：`pm path` 返回已安装 APK，包名为 `com.company.logistics`，版本为 `6` / `0.3.5`；Manifest 权限包含 `android.permission.CAMERA`；应用能够启动。不要粘贴包含其他应用信息的完整 `dumpsys` 输出。

## 7. 启动页、会话恢复与退出

### 启动页

1. 执行 `"${ADB[@]}" shell am force-stop com.company.logistics`，再执行 `"${ADB[@]}" shell am start -n com.company.logistics/.MainActivity`。
2. 检查冷启动首帧为品牌深蓝背景，没有明显白屏闪烁。
3. 检查启动动画结束后只出现一次后续页面；旋转屏幕或 Activity 重建不应重复播放启动动画。
4. 验证系统动画缩放为 0 时直接呈现终态，完成后必须恢复设备设置：

```bash
"${ADB[@]}" shell settings put global animator_duration_scale 0
"${ADB[@]}" shell am force-stop com.company.logistics
"${ADB[@]}" shell am start -n com.company.logistics/.MainActivity
"${ADB[@]}" shell settings put global animator_duration_scale 1
```

### 会话恢复

1. 在应用的“我的 → 服务端配置”输入本次验收地址并保存；确认保存后无需重启，后续请求已使用新地址。
2. 在登录页勾选“保持登录”，使用验收环境账号登录并进入工作台。账号只在现场输入，不写入仓库或记录。
3. 模拟进程级退出并启动：

```bash
"${ADB[@]}" shell am force-stop com.company.logistics
"${ADB[@]}" shell am start -n com.company.logistics/.MainActivity
```

4. 检查顺序为启动页/“正在恢复会话…”后直接进入已登录工作台，不应短暂闪出登录页。
5. 只检查偏好文件名，不读取文件内容：

```bash
"${ADB[@]}" shell run-as com.company.logistics ls -la shared_prefs
```

6. 点击退出登录，再强制停止并启动；必须回到登录页，不能恢复上一账号。
7. 不要使用 `cat`、`strings`、截图或日志导出偏好文件；不要在验收记录中写入任何 token。

代码约束是：会话令牌只能写入 `EncryptedSharedPreferences`。Android Keystore 不可用时，应用不得把令牌写入明文 fallback 偏好，只能保留本次进程内登录；偏好文件名检查不能替代代码审查，也不能证明设备已经通过会话验收。

## 8. 相机权限与扫码页

先清掉相机权限，验证首次请求和拒绝降级：

```bash
"${ADB[@]}" shell am force-stop com.company.logistics
"${ADB[@]}" shell pm revoke com.company.logistics android.permission.CAMERA || true
"${ADB[@]}" shell am start -n com.company.logistics/.MainActivity
```

检查以下结果并在外部记录中逐项填写：

- Manifest 和系统权限均包含 `android.permission.CAMERA`；摄像头硬件是可选能力，没有摄像头的设备仍应能安装并使用手动输入。
- 首次进入扫码页出现系统相机授权请求；拒绝后显示原因、手动输入入口和再次授权/系统设置入口。
- 授权后相机预览出现，扫码页显示固定取景框；相机启动超时、被占用或设备无摄像头时，页面显示可读失败原因、重试和手动输入，而不是永久转圈。
- 离开扫码页或应用进入后台后，相机停止占用；回到扫码页能够重新绑定预览。
- 相同条码快速连续出现时不会重复提交；网络失败文案不显示令牌、请求头或服务端堆栈。

授权路径可辅助确认权限状态：

```bash
"${ADB[@]}" shell pm grant com.company.logistics android.permission.CAMERA
"${ADB[@]}" shell appops get com.company.logistics CAMERA
```

## 9. 后端地址和 Debug/Release 网络策略

### 地址输入

在“我的 → 服务端配置”执行：

1. 保存本次验收地址，检查末尾斜杠被规范化。
2. 输入非法协议、空 host、凭据、查询串或片段，确认被拒绝。
3. 不重启应用，返回登录页执行一次网络请求，确认请求使用刚保存的地址。
4. 切换地址后先退出并重新登录，避免把旧环境会话带到新环境。
5. 使用“测试连通性”，确认它只请求 `/healthz`；失败时应显示可行动的 DNS、超时、TLS 或 HTTP 状态提示，不显示原始响应中的敏感内容。

### Debug 网络

Debug 包的 `app/src/debug/res/xml/network_security_config.xml` 允许明文 HTTP。只可在受控内网验收，不能把 Debug 包作为生产包。用 HTTP 测试时记录“Debug 临时明文、受控设备”，但不要记录实际地址。

### Release 网络

Release 包使用 `app/src/main/res/xml/network_security_config.xml`，默认拒绝明文 HTTP，只接受系统信任链上的 HTTPS。分别验证：

- 在 Release 运行时保存 `http://...`：应用应拒绝保存；
- 若 Release 构建期错误注入 HTTP：应用可以完成打包，但 HTTP 请求应被网络安全策略阻断；这不算配置正确；
- 使用 HTTPS 且设备信任证书链：只有在实际服务和账号可用时，才继续做登录与业务验收。

## 10. 工作台错误状态

使用可控的测试环境故障注入，不要在生产环境执行。每一项都必须有实际 UI 证据才可标记通过：

| 场景 | 操作 | 通过条件 |
| --- | --- | --- |
| 摘要接口 404/405 | 让 `/api/v1/workspace/summary` 返回接口不存在 | 工作台显示“工作台接口不可用”，指标用 `—` 表示不可用，不把缺失当作 0；页面可继续操作或返回 |
| 工作项接口 404/405 | 让 `/api/v1/workspace/material-items` 返回接口不存在 | 工作项区域显示“工作项接口不可用”和重试入口，不崩溃、不显示空白假成功 |
| 任一接口 5xx/超时 | 暂时阻断接口或返回服务错误 | 显示“读取失败/暂时不可用”和“重试”；恢复后点击重试能回到内容或明确空态 |
| 正常空页 | 服务端返回合法空列表和总数 0 | 显示“当前没有工作项”，说明这是空数据而不是接口不可用 |
| 会话失效 | 让业务请求返回 401 且刷新失败 | 清理会话并回到登录页，不持续重试，不暴露响应详情 |

执行 UI 故障检查时可以采集崩溃摘要；不要上传未经脱敏的完整日志：

```bash
"${ADB[@]}" logcat -c
# 在应用内执行对应场景后：
"${ADB[@]}" logcat -d -b crash -v brief
"${ADB[@]}" shell dumpsys activity activities \
  | grep -E 'mResumedActivity|com.company.logistics'
```

## 11. 收尾与证据

验收结束后恢复测试设备状态，并确认动画设置即使中途失败也已恢复：

```bash
"${ADB[@]}" shell settings put global animator_duration_scale 1
"${ADB[@]}" shell svc wifi enable
"${ADB[@]}" shell svc data enable
"${ADB[@]}" shell am force-stop com.company.logistics
```

外部验收记录只保留以下脱敏信息：日期、设备型号和 Android 大版本、测试包 SHA-256、包名/版本、测试项逐项结果、阻塞问题编号和必要的脱敏截图。不要记录序列号、真实地址、账号、密码或令牌。

提交文档前只检查本次允许的目录：

```bash
git status --short -- logistics-android/docs tasks
git diff --check -- logistics-android/docs tasks
git diff --stat -- logistics-android/docs tasks
```

本清单不包含真实后端地址、测试账号、密码、令牌或真机通过结果。
