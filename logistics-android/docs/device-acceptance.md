# Android 真机验收清单

本清单只适用于 `logistics-android`。它提供可复制的构建、安装、`adb` 检查命令和人工检查点；没有在文档中声称已经完成真机验收。每次实际执行后，请在项目外的验收记录中填写通过/失败，不要把账号、密码、令牌、真实后端地址或原始日志提交到仓库。

## 1. 安全边界

- 源码和仓库中的后端地址只能是占位地址；不得提交真实域名、IP、端口、账号、密码、令牌或带凭据的 URL。
- `API_BASE_URL`/`apiBaseUrl` 只接受不含凭据、查询串和片段的 `http(s)` 基础地址。不要写成 `user:password@host`。
- `local.properties`、本地环境变量和运行时「服务端配置」都属于本机配置，不要纳入 commit。
- Debug 变体仅用于受控内网验收，允许明文 HTTP；Release 变体默认拒绝明文 HTTP，只接受系统信任链上的 HTTPS。
- 会话令牌只允许写入 `EncryptedSharedPreferences`。如果 Android Keystore 不可用，应用可以继续本次内存登录，但不得恢复或新写明文会话。

本地构建时使用任务专用变量，不要把真实值写进命令历史、截图或验收记录：

```bash
cd logistics-android
export LOGISTICS_QA_BASE_URL='https://qa-host.example.invalid'
```

上面的地址只是占位示例。实际执行时仅在本机替换变量值；不要把替换后的值写回文档或源码。

## 2. 构建与变体

在 `logistics-android/` 目录执行：

```bash
export ANDROID_HOME=/opt/android-sdk
export ANDROID_SDK_ROOT="$ANDROID_HOME"
./gradlew clean testDebugUnitTest assembleDebug
```

通过条件：

- `testDebugUnitTest` 成功完成；
- `app/build/outputs/apk/debug/app-debug.apk` 存在；
- 构建输出没有把真实地址、令牌或凭据打印出来；
- Debug APK 仅安装到验收设备，不作为生产包发布。

如需验证地址注入链路，只在本机临时传入 QA 地址：

```bash
./gradlew clean testDebugUnitTest assembleDebug -PapiBaseUrl="$LOGISTICS_QA_BASE_URL"
```

Release 配置检查（不代表已签名或已完成发布验收）：

```bash
./gradlew assembleRelease -PapiBaseUrl="$LOGISTICS_QA_BASE_URL"
```

通过条件：Release 仍为不可调试变体；生产地址使用 HTTPS；没有把 Debug 明文网络策略合并进 Release。签名、收缩和发布仍由正式 CI/发布流程负责。

## 3. 安装与基础信息

先连接一台已授权的 Android 真机，并确认设备序列号不属于其他人的设备：

```bash
adb devices -l
adb install -r -d app/build/outputs/apk/debug/app-debug.apk
adb shell dumpsys package com.company.logistics | grep -E 'versionCode|versionName|android.permission.CAMERA'
adb shell appops get com.company.logistics CAMERA
adb shell am force-stop com.company.logistics
adb shell monkey -p com.company.logistics 1
```

多台设备同时连接时，为每条 `adb` 命令追加 `-s <设备序列号>`。不要在验收记录中粘贴包含其他应用信息的完整 `dumpsys` 输出。

## 4. 启动页、会话恢复与退出

### 启动页

1. 执行 `adb shell am force-stop com.company.logistics` 后重新启动应用。
2. 检查冷启动首帧为品牌深蓝背景，没有明显白屏闪烁。
3. 检查启动动画结束后只出现一次后续页面；旋转屏幕或 Activity 重建不应重复播放启动动画。
4. 将系统动画缩放设为 0 后重新启动，检查启动页直接呈现终态，不长时间停留在动画中：

```bash
adb shell settings put global animator_duration_scale 0
adb shell am force-stop com.company.logistics
adb shell monkey -p com.company.logistics 1
adb shell settings put global animator_duration_scale 1
```

### 会话恢复

1. 在应用内配置本次验收使用的后端地址，保存后再登录；确认保存后无需重启即可由后续请求使用新地址。
2. 登录页勾选「保持登录」，登录成功并进入工作台。
3. 执行以下命令模拟进程级退出，再启动应用：

```bash
adb shell am force-stop com.company.logistics
adb shell monkey -p com.company.logistics 1
```

4. 检查顺序为启动页/「正在恢复会话…」后直接进入已登录工作台，不应短暂闪出登录页。
5. 只检查会话文件是否存在，不读取文件内容：

```bash
adb shell run-as com.company.logistics find shared_prefs -maxdepth 1 -type f -print
```

6. 点击退出登录，再强制停止并重启；检查必须回到登录页，不能恢复上一账号。
7. 不要用 `cat`、`strings`、截图或日志导出会话文件；不要在验收记录中写入任何 token。

## 5. 相机权限与扫码页

先清掉权限，验证首次请求和拒绝降级：

```bash
adb shell am force-stop com.company.logistics
adb shell pm revoke com.company.logistics android.permission.CAMERA
adb shell monkey -p com.company.logistics 1
```

检查点：

- Manifest 和系统权限均包含 `android.permission.CAMERA`；相机硬件是可选能力，缺少摄像头不应阻止应用安装。
- 首次进入扫码页出现系统相机授权请求；拒绝后显示原因、手动输入入口和再次授权/系统设置入口。
- 授权后相机预览出现，扫码页显示固定取景框；若相机启动超时、被占用或设备无摄像头，页面显示可读失败原因、重试和手动输入，而不是永久转圈。
- 离开扫码页或应用进入后台后，相机停止占用；回到扫码页能够重新绑定预览。
- 相同条码快速连续出现时不会重复提交；网络失败时错误文案不显示令牌、请求头或服务端堆栈。

授权路径可用下面命令辅助确认：

```bash
adb shell pm grant com.company.logistics android.permission.CAMERA
adb shell appops get com.company.logistics CAMERA
```

## 6. 后端地址与脱敏检查

在「我的 → 服务端配置」执行：

1. 输入本次验收地址并保存；检查末尾斜杠被规范化，非法协议、空 host、凭据、查询串和片段被拒绝。
2. 不重启应用，返回登录页执行一次网络请求；检查请求已使用刚保存的地址。若切换地址，建议先退出并重新登录，避免把旧地址的会话带到新环境。
3. 使用「测试连通性」只验证 `/healthz`；失败时应显示可行动的 DNS、超时、TLS 或 HTTP 状态提示，不显示原始响应中的敏感内容。
4. Debug 使用 HTTP 时确认设备位于受控内网，并记录这是临时验收条件；Release 使用 HTTP 应被拒绝或因网络安全策略失败。
5. 验收记录、截图和 `adb logcat` 中不得出现完整真实地址、账号、密码、Authorization、refresh token 或后端堆栈。分享日志前先人工脱敏。

## 7. 工作台错误状态

使用可控的测试环境故障注入，不要在生产环境执行。至少覆盖：

| 场景 | 操作 | 通过条件 |
| --- | --- | --- |
| 摘要接口 404/405 | 让 `/api/v1/workspace/summary` 返回接口不存在 | 工作台显示「工作台接口不可用」，指标用 `—` 表示不可用，不把缺失当作 0；页面可继续操作或返回 |
| 工作项接口 404/405 | 让 `/api/v1/workspace/material-items` 返回接口不存在 | 工作项区域显示「工作项接口不可用」和重试入口，不崩溃、不显示空白假成功 |
| 任一接口 5xx/超时 | 暂时阻断接口或返回服务错误 | 显示「读取失败/暂时不可用」和「重试」；恢复后点击重试能回到内容或明确空态 |
| 正常空页 | 服务端返回合法空列表和总数 0 | 显示「当前没有工作项」，说明这是空数据而不是接口不可用 |
| 会话失效 | 让业务请求返回 401 且刷新失败 | 清理会话并回到登录页，不持续重试，不暴露响应详情 |

执行 UI 故障检查时可用以下命令辅助采集崩溃摘要；不要上传未经脱敏的完整日志：

```bash
adb logcat -c
# 在应用内执行对应场景后：
adb logcat -d -b crash -v brief
adb shell dumpsys activity activities | grep -E 'mResumedActivity|com.company.logistics'
```

## 8. 收尾

验收结束后恢复测试设备状态：

```bash
adb shell settings put global animator_duration_scale 1
adb shell svc wifi enable
adb shell svc data enable
adb shell am force-stop com.company.logistics
```

提交前仅检查项目内变更，不要打印敏感配置：

```bash
git status --short -- logistics-android
git diff --check -- logistics-android
git diff --stat -- logistics-android
```

本清单本身不包含真实后端地址、测试账号、密码、令牌或真机测试结果。
