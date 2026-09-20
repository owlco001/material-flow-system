# NEXT.md — 交接看板

> 给下一个对话用。**不要复述上下文，读这个文件即可。**
> 最后更新：2026-09-13 13:40

---

## 0. 一句话唤醒

新对话开场白（直接复制）：

```
克隆 https://gitee.com/owlco001/material-flow-system.git
切到 feature/ui-polish-camera 分支，读 NEXT.md，按上面的待办继续
```

---

## 1. 项目基本盘

| 项 | 值 |
|----|-----|
| 产品名 | 博阳智造「智慧工厂」 |
| 技术栈 | Android Jetpack Compose + FastAPI + SQLite |
| 仓库 | `https://gitee.com/owlco001/material-flow-system.git` |
| 当前分支 | `feature/ui-polish-camera` |
| 当前 HEAD | `ef85bfa`（本地领先 Gitee 1 个提交，待推） |
| 工作区 | 干净，无未提交改动 |
| 远端 Gitee | 已同步至 `650b10b`；`ef85bfa` **待推** |
| 远端 GitHub | 停在更早的提交，**与 Gitee 分叉** |

**构建方式**：

```bash
# Android（Gradle wrapper 已缓存在沙箱，可直接用）
cd logistics-android && ./gradlew :app:compileDebugKotlin

# 后端
cd material-flow-backend && python3 -m pytest tests/ -q
全量已可跑通（75 passed），见 ef85bfa 的修复
```

---

## 2. 最近一轮已完成

**最新提交 `ef85bfa`**：测试基础设施修复（Android 跨测试类顺序依赖 + 后端全量混跑污染），
生产代码零改动，Android 121 测试 5 轮全绿 / 后端 75 passed 10 轮稳定，3 处变异全部被捕获。

**上一提交 `637e435`**：产品更名。

**产品更名**：对外名称统一为「智慧工厂」，App 名「博阳智造」不变。

| 分类 | 改动 |
|------|------|
| 品牌文案 | 收敛到 `strings.xml` 单一来源（`app_name`/`brand_subtitle`/`brand_tagline`），开机动画与登录页改用 `stringResource`，消除硬编码 |
| 图标合规 | `ScannerScreen` 手电筒的 emoji 图标 → `ScannerIcons.FlashOn/FlashOff` 矢量图标 |
| 后端 | `FastAPI(title="智慧工厂 API")`、`VERSION 0.3.7→0.4.0` |
| Android | `versionCode 13→14`、`versionName 0.4.2→0.5.0` |
| 文档 | `docs/` 8 篇 + `tasks/` 2 篇正文更名；新增 `docs/智慧工厂-更名说明.md`、`docs/智慧工厂-更名交付摘要.md` |

**刻意保留**（有充分理由，别乱改）：
- 包名 `com.company.logistics` —— 改包名 = 发布新 App，已装设备无法覆盖升级
- 数据路径 `MATERIAL_FLOW_DATA` / `/srv/material-flow/data` / `material_flow.db` / `SERVICE_NAME=material-flow` —— 改名而无迁移脚本会导致服务重启新建空库
- 文档文件名及代码中对它们的引用

**详细依据**：`docs/智慧工厂-更名说明.md`

---

## 3. 待办

### P0 — 安全

- [ ] **轮换 Gitee 访问令牌**
  该令牌已出现在对话与仓库历史中，属泄露状态。
  重建时务必在「授权仓库」里勾选 `material-flow-system`（上次 403 就是因为没勾）。
- [ ] **`107.173.70.115` 为公网明文 HTTP**，建议上 TLS。

### P1 — 测试基础设施（已修复并提交 ef85bfa，待推送）

- [已完成] **Android：消除跨测试类顺序依赖**
  根因（经实测修正）：测试 fake 未覆写 `workspaceMaterialItems()`，`login()` 后的工作台加载回落到真实网络调用，失败触发 `expireSession()` 异步重置 `authState`。
  **注意**：早前「恢复协程覆盖登录态」的说法**已证伪**，勿再沿用。
  修法：给缺失的 fake 补 `workspaceMaterialItems` / `workspaceSummary` 覆写（2 个测试文件，零新依赖，不碰生产代码）。
  验收：混跑 20 轮全绿 + 变异测试捕获。

- [已完成] **后端：消除全量混跑的状态污染**
  根因：`INITIAL_ADMIN_PASSWORD` 导入期快照竞态（首个导入文件的密码胜出）。
  修法：`conftest.py` 内按各测试文件声明的密码对齐模块常量。
  验收：全量 75 passed，连续 10 轮稳定。

- 详见 `docs/decisions/ADR-001-测试基础设施改造.md` 与 `tasks/测试修复-验收标准.md`

### P1 — ⛔ P0-1 emoji 图标欠账（本次扫描新发现，**用户决定：只记录不动**）

> 扫描范围：`logistics-android/app/src/main`（`*.kt`），正则见团队 P0-1。
> **本次不修**，仅登记。修的时候一次性收敛，不要零散改。

**选定方案（已拍板）**：在 `ui/components/ScannerComponents.kt` 的 `ScannerIcons` 旁
**扩展一个完整 ImageVector 图标族**（如 `LogisticsIcons`），把 `Check / Circle / Play / List / Alert`
全部矢量化为 `ImageVector`，沿用 24dp 视口 + `SolidColor` 填充 + 16/20/24px 三档取用，
与现有 `FlashOn/FlashOff` 完全同构，**零外部依赖**。

| 文件 | 行 | 符号 | 语义 | 承载方式 |
|------|----|------|------|----------|
| `ui/screens/LoginScreen.kt` | 153 | ⚠ | 登录错误提示前缀 | 行内 16px 图标 + `Text` |
| `ui/screens/ScannerScreen.kt` | 586 | ⚠ | 摄像头启动失败 | 32px 图标（替换独占一行的 `Text`） |
| `ui/screens/AdminActivationScreen.kt` | 111 | ⚠ | 激活失败提示 | 行内 16px 图标 + `Text` |
| `ui/screens/QueueScreen.kt` | 232 | ⚠ | 队列项异常状态 | 状态图标（需带 `statusColor` tint） |
| `ui/screens/MaterialDetailScreen.kt` | 78 | ✓ | 物料已完成 | 20px 白色图标 |
| `ui/screens/OrderDetailScreen.kt` | 184 | ✓ | 工序已完成 | 交由 `StatusTag` 的 `symbol` 参数 |
| `ui/screens/ApprovalAndProfileScreens.kt` | 615 | ✓ / — | 权限允许 / 不允许 | 权限矩阵单元格 |
| `ui/screens/ApprovalAndProfileScreens.kt` | 448 | ✓ | `EXECUTED` 状态标签 | `StatusTag` 映射 |
| `ui/screens/AuditScreen.kt` | 192 | ✓ / ! | 审计成功 / 失败 | 图标 + tint |
| `ui/screens/WorkshopAssemblyScreens.kt` | 742 | ✓ / ○ | 阶段完成 / 未完成 | `StatusTag` 的 `symbol` |
| `ui/screens/WorkshopAssemblyScreens.kt` | 771 | ▶ / ✓ | 进行中 / 已完成 | 同上 |
| `ui/screens/WorkspaceScreen.kt` | 70, 73 | ✓ | 任务状态映射 | **数据层映射，见下注** |
| `model/Models.kt` | 79, 656 | ✓ | 物料/流转状态映射 | **数据层映射，见下注** |
| `model/Models.kt` | 134 | ☰ | 盘点类型图标 | **数据层映射，见下注** |
| `ui/LogisticsViewModel.kt` | 90, 93 | ☰ / ✓ | 底部导航「订单」「审批」 | 导航图标，改 `ImageVector` |
| `app/src/test/.../WorkspaceApiParserTest.kt` | — | ✓ | 测试夹具字符串 | 若为纯数据断言可保留，需确认 |

**架构注记（动手前必读）**：
`Models.kt` / `WorkspaceScreen.kt` 里的 `symbol` 是**数据层的字符串字段**，被 UI 直接当图标渲染。
清理时有两个选项，需架构师定夺：
1. 把字段类型从 `String` 改为 `ImageVector`（或在 UI 层做 `String → ImageVector` 映射表）——
   代价是动 `model` 层，测试夹具要跟着改；
2. 保留字符串字段但在 UI 渲染处统一走映射表翻译成 `ImageVector` —— 改动面小，但数据层仍存 emoji 语义。
**建议 2**（数据契约零破坏，且 `WorkspaceApiParserTest` 的断言可原样保留）。

**回归要求**：改完必须重跑 `./gradlew :app:testDebugUnitTest`（当前 121 测试全绿为基线），
并重跑 P0-1 emoji 正则扫描确认归零。

### P2 — 收尾

- [ ] `delivery/boyang-report/index.html` 仍含旧产品名
- [ ] 包名 `logistics` 与产品名「智慧工厂」语义不一致（技术债）
- [ ] Gitee 与 GitHub 两远端 `main` 已分叉，若需合并先比对差异
- [ ] 可选加固：30 处 `Dispatchers.Unconfined` 迁移到 `StandardTestDispatcher` + `runTest`（需引入 `kotlinx-coroutines-test`，当前未引入）

---

## 4. 已知问题（更名前即存在，未修）

1. `docs/V1角色与枚举契约冻结决议.md:51` 引用的 `docs/物料流转系统-V1-需求冻结与接口契约.md` 不存在（实际文件名少一个连字符）
2. `docs/物流流转系统-APP开发进度.md:19-23` 引用 `/root/project_workspace/docs/...` 绝对路径，目录不存在

---

## 5. 环境须知（沙箱隔离相关）

**每个对话的沙箱独立**，文件与已装包不跨对话共享。

- **状态外置**：改动务必 `git commit` + `git push`，否则沙箱销毁即丢失
- **Gradle 缓存**：`/root/.gradle`（wrapper + 依赖）已缓存，**不要删**，重下曾失败
- **清理安全区**：`/root/.cache/pip/http-v2`（约 5G）可清；`/tmp` 下日志可清
- **不要清**：`/root/.gradle/caches`、`.gradle/wrapper/dists`、`.cache/ms-playwright`

---

## 6. 收尾纪律

每轮任务结束前：

1. `git add -A && git commit`
2. `git push gitee feature/ui-polish-camera`
3. **更新本文件**（NEXT.md）的「待办」与「当前 HEAD」
4. 一句「已完成 X，下一步 Y」交代给用户

> **教训（务必内化）**：提交前把 emoji 扫描扫**全仓**，不要只扫 `git diff` 里的文件。
> 本次就是只扫了改动文件，差点漏掉 11 个文件的历史欠账。
