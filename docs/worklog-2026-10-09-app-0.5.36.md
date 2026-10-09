# 2026-10-09 工作记录（APP 0.5.36 发布）

## 概述
- Android 版本：0.5.35 → 0.5.36（versionCode 42 → 43）
- 构建环境：PVE（内网构建，符合要求）
- 发布渠道：GitHub Release + 后端 APP 更新通道

## 改动内容

### 1. 内网 HTTP 明文允许
- 文件：`logistics-android/app/src/main/res/xml/network_security_config.xml`
- 改动：`base-config` 的 `cleartextTrafficPermitted` 从 `false` 改为 `true`
- 原因：工厂内网无 HTTPS 需求，更新通道下载 APK 需要 HTTP

### 2. 恢复 filamat 依赖与 arm64 ABI 过滤（0.5.36 graft 事故）
- 问题：0.5.36 版本号提交的 graft 过程中，`build.gradle.kts` 的以下两项被意外删除：
  - `implementation("com.google.android.filament:filamat-android:1.75.1")`
  - `ndk { abiFilters += listOf("arm64-v8a") }`
- 影响：APK 从 24MB 膨胀到 80MB（四种 ABI 的 so 全被打入）
- 修复：已恢复，0.5.36 重新打包为 24MB

### 3. 后端更新通道接线（PVE）
- `app/updates.py` 的 router 已存在但未在 `main.py` 注册，已手动添加
- `app/admin_web.py` 添加 `/admin/releases` 路由（模板 `releases.html` 已存在但无路由）
- APK 存放目录：`/srv/material-flow/app-releases/`（命名：`app-{versionCode}-{versionName}.apk`）
- 公开接口：
  - `GET /api/v1/app/updates/latest`（最新版本信息）
  - `GET /api/v1/app/updates/download`（下载 APK）

## 已知问题
- APP 内"检查更新"卡片不显示：`ProfileScreen` 的 `appUpdateChecker` 参数默认 null，
  `LogisticsApp.kt` 调用时未传入，导致 `if (appUpdateChecker != null)` 永远 false。
  需修复依赖注入。
- 0.5.34 旧版 APP 的更新检查逻辑有 bug，无法通过通道升级，需手动安装新版。

## GitHub Release
- v0.5.35: `https://github.com/owlco001/material-flow-system/releases/download/v0.5.35/app-42-0.5.35.apk`
- v0.5.36: `https://github.com/owlco001/material-flow-system/releases/download/v0.5.36/app-43-0.5.36.apk`

## 其他
- LibreDWG 已在 PVE 编译（`/tmp/libredwg`），`dwgread` 可用；用户上传的两个 DWG 文件经检测非有效 DWG 格式，待重传。
- PVE 后端实际数据库：`/srv/material-flow/data/material_flow.db`（非 `/opt/smart-factory/material_flow.db`）。
