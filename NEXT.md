# NEXT.md — 交接看板

> 给下一个对话用。**不要复述上下文，读这个文件即可。**
> 最后更新：2026-10-21（下方更早的 0–6 节为历史记录，版本/测试数已过时，以本节为准）

---

## ★ 最新状态（2026-10-21）

| 项 | 值 |
|----|-----|
| 主仓库 | GitHub `owlco001/material-flow-system`（镜像 Gitee `owlco001/material-flow-system`），主干 `main` |
| 基线 | 后端 **0.22.0**（feature/design-sync；main 为 0.21.16）、App **0.5.50**（versionCode 57），本地工作目录 `/root/mf-main`（GitHub 干净 clone） |
| 生产 | `jsby@221.231.106.194:5556` 的 `/srv/material-flow`（systemd `material-flow.service`，cloudflared 221）；更新用 `sudo -n /bin/bash /srv/material-flow/deploy/update.sh`（含 DB 热备 + 失败自动回滚） |
| 后端测试 | `cd material-flow-backend && env -u PYTHONPATH ./.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` → **540 passed**（含设计同步 28 例），约 4 分钟 |
| App 测试 | `cd logistics-android && ANDROID_HOME=/opt/android-sdk ./gradlew :app:testDebugUnitTest` → **206 例 0 失败**；发版 `:app:assembleRelease` |
| 门禁 | `python3 scripts/check_emoji_icons.py` 必须 PASS（75 个 .kt） |

**2026-10-23 设计同步 API（后端 0.22.0，分支 `feature/design-sync`）**
- 新增 `app/design_sync.py`：`/api/v1/design-sync/*`（上传 / 列表 / 详情 / 比对 / 绑定 / 下载），契约见 `docs/设计同步API契约.md`
- 机器身份 `X-Design-Key`（只存前缀 + Argon2id 哈希），每个密钥有来源网段 `ip_scope`（默认全部内网段，不要求 HTTPS）和可选机台范围；
  来源地址沿用 `request.client.host`（uvicorn 只信任 127.0.0.1 的 XFF），另对 `Cf-Connecting-Ip` 做只收紧的校验
- 设计包 BOM 只归档 + 比对，不写 bom_versions / materials；绑定时 PDF/DXF 走现有图纸入库，GLB 注册为机型码 `DS-<机台编号>` 并设 `devices.model_3d_code`
- 管理台「主数据 → 设计包」（仅 ADMIN）：列表、比对、下载、绑定、密钥生成 / 编辑 / 吊销；新表在 `init_db` 里建
- **注意**：若某个密钥要给经 cloudflared 进来的公网客户端用，需在管理台显式放开对应网段；默认网段含 127.0.0.0/8，
  反代若不转发 `Cf-Connecting-Ip` 会把隧道流量看成本机——上线前在 221 上确认 cloudflared → 反代链路保留该头

**2026-10-20～21 这一批已完成**（都已在 221 上线 / 双端 Release）
- 批次一 P2：离线解包误判、损坏静默成功、跨订单测试、SQL 别名改写
- 装配工物料可见性：关联键改机台号 `device_no` 并纳入协作成员（U9/报价导入订单原来物料恒空）
- 主管统计按车间收敛（`user_workshops`，未配置=全厂）+ 订单维度进度（`/workshop/order-progress`、订单详情 `progress`）
- App：离线队列退避重试上限（30s~4min、5 次转终态）、图片凭证拍照/相册（含离线入队）、盘点页（含离线）
- 盘点确认真正调整库存 + 创建校验库位并落真实 id；异常审批通过按差异校正库存（可指定库位，未指定按单库位/最大库位）
- 审批页详情改为在当张卡片下展开；U9 库存同步把已清零/不再返回的库位归零
- 新增只读巡检 `GET /api/v1/inventory/consistency`（ADMIN/仓管）对账 `materials.total_quantity` 与 `sum(inventory.quantity)`
- 盘点 / 异常列表分页（`page`/`pageSize`/`total`），App 盘点页每页 50 条 + 「加载更多」
- App 0.5.50 订单页去掉「物料汇总」表，只保留按机台分组的物料需求明细（汇总数字仍在顶部指标里）
- App 0.5.49 物料分页改触底自动加载（不再需要滚到底点按钮），按钮保留为兜底
- App 0.5.48 订单页物料清单开分页（每页 200 + 「加载更多物料」），齐套率/缺料数全走服务端
  （`shortageLineCount`）；`detail.materialSummary` 口径不变
- APP 更新通道加固：服务重启后自动把“下载中”收尾为失败并清半截 .part（现场“卡 45%”的根因），新增取消同步（接口 + 管理台按钮）、下载停滞 90 秒放弃、运维 CLI `python -m app.app_sync`
- 审计日志保留策略：`GET /api/v1/admin/audit/retention` 预览 + `POST /api/v1/admin/audit/purge`
  （仅 ADMIN，days 30..3650，dryRun 默认 true，清理动作自身留 PURGE 审计）；管理台审计页加"保留策略"卡片
- 订单物料状态（App 订单页用的 `POST /orders/material-status`）也支持分页：`page`/`pageSize`
  + `total`/`hasMore`，齐套率改由服务端全量汇总（本机 2 万行 493ms/9.0MB → 87ms/0.78MB）；
  但 App 仍按全量请求——批量出库、缺料数依赖整份清单，翻页要连 UI 一起改
- 订单详情物料清单分页（`materialsPage`/`materialsPageSize`，0=全量兼容旧客户端）+
  汇总改由 SQL 聚合；本机 2 万行订单实测 610ms/9.0MB → 161ms/0.74MB（每页 100）
- `/workspace/summary` 轻量化：复用同一份状态派生逻辑只累加计数，不再构造全部工作项（本机 2 万需求行 504ms → 243ms）

**已知未决**
- U9 库存同步长期"全部不变"（行数恒 9643）：已记录核查 SQL，疑 U9 侧 `InvTrans_WhQoh` 未刷新，待现场结论
- 库存数量仍只存整数（78.65→79；变化 <0.5 判不变）。**2026-10-21 老王决定搁置**：要动表结构 + API 契约 + App 输入/显示 + 历史值补偿，收益不抵风险。除非现场明确要求按小数收发料，否则不再提议；若重启，按「后端先加兼容层、App 跟随一版」的顺序做
- `docs/物流流转系统-当前实现架构复核.md` 里"execute_transfer 未更新汇总/未用乐观锁"等条目已过时

---

**本轮功能：订单页 / 机台页物料勾选批量操作**（出库申请 / 退料 / 厂内调拨 / 异常提报）
- 后端：流转类型新增 `RETURN`（退料，按入库审批规则）；`TRANSFER`（厂内调拨）创建时强制来源+目标库位、限定角色，
  执行改为只在库位间移动、不再扣减物料汇总（原实现的 bug）；创建角色集中为 `TRANSFER_CREATE_ROLES` / `EXCEPTION_CREATE_ROLES`。
  机台任务详情 `materials[]` 增加 `requirementId/materialId/deviceId`。契约见 `docs/物料流转系统-V1-API契约冻结补遗.md` §1.2 增补。
- 管理台：`/admin/orders?order_no=` 物料需求与 `/admin/orders/{单号}/tasks/{任务}` 机台物料加勾选 + 全选本页（跨页保留）+ 四个批量按钮
  （按角色裁剪），提交 `POST /admin/orders/{单号}/materials/batch`（会话 + CSRF），服务端逐条调用既有 API，逐条回显结果，
  另记一条 `BATCH_<动作>` 审计（SUCCESS / PARTIAL / FAILURE）。脚本 `app/static/admin/material-batch.js`，模板宏 `_material_batch.html`。
- App：共享组件 `ui/components/MaterialBatchComponents.kt`（勾选框 / 批量操作栏 / 批量对话框，可逐条改数量）；
  订单页新增「物料需求明细」可勾选列表（机台下钻页同样可勾选），机台页批量栏从单一「提交流转申请」扩展为四个动作；
  `LogisticsViewModel.submitMaterialBatch` 统一处理（预览只读拦截、角色裁剪、逐条部分失败、离线入队）；离线队列新增 `RETURN`，
  `RELOCATION` 首次接入重放（documentNo / 来源库位打包在 remark JSON）。

**待办 / 注意**
- 本沙箱无 JDK / Android SDK，App 本轮**未编译、未跑单测**（新增 `LogisticsViewModelMaterialBatchTest`、改了 `VersionConfigTest`），合并前务必
  `cd logistics-android && ./gradlew :app:compileDebugKotlin :app:testDebugUnitTest`。
- `scripts/check_emoji_icons.py` 在 main 上即有 3 处历史 FAIL（MaterialDetailScreen / Model3dViewerScreen / OrderDetailScreen「✓ 物料齐套」），本轮未新增。
- 已知遗留：OUTBOUND 离线重放仍不带 documentNo（历史行为，未改）。

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
| 当前 HEAD | `ed99846`（本地领先 Gitee 2 个提交，待推） |
| 工作区 | 干净，无未提交改动 |
| 远端 Gitee | 已同步至 `650b10b`；`ef85bfa` `8d0f876` `ed99846` **待推** |
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

**最新提交 `ed99846`**：emoji 功能图标全量收敛为矢量图标族（P0-1）。
新增 `LogisticsIcons` 图标族 + `fromSymbol` 翻译表；`StatusTag` 内部收口
（签名不变，调用点零改动）；6 个屏幕直接渲染点改造；数据层契约符号刻意不动。
新增门禁脚本 `scripts/check_emoji_icons.py`。121 测试全绿。

**上一提交 `8d0f876`**：登记 emoji 欠账 + 校正 NEXT.md 过时状态。

**`ef85bfa`**：测试基础设施修复（Android 跨测试类顺序依赖 + 后端全量混跑污染），
生产代码零改动，Android 121 测试 5 轮全绿 / 后端 75 passed 10 轮稳定，3 处变异全部被捕获。

**`637e435`**：产品更名。

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
- [ ] **服务器公网地址（[REDACTED]）为公网明文 HTTP**，建议上 TLS。

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

### P1 — P0-1 emoji 图标欠账（**已完成**，见下）

> 扫描范围：`logistics-android/app/src/main`（`*.kt`），正则见团队 P0-1。
> 2026-09-13 完成渲染层收口；数据层契约符号按设计保留。

### P1 — emoji 图标欠账（**已完成**）

> 本段保留原始清点表，作为工作量与决策依据；实际落地已收敛为下述"实现结果"。

**实现结果**：

| 项 | 内容 |
|----|------|
| 新增图标族 | `LogisticsIcons`（`ui/components/ScannerComponents.kt`）—— `Check` / `Alert` / `CircleOutline` / `Play` / `List` / `Exclamation`，24dp 视口 + `SolidColor`，与 `FlashOn/FlashOff` 同构，零外部依赖 |
| 翻译层 | `LogisticsIcons.fromSymbol(String?): ImageVector?` —— 命中则渲染矢量图标，未命中回退文本 |
| 渲染接缝 | `StatusTag` 内部改造（**签名 `symbol: String?` 不变**），所有经它的状态标签自动升级为图标 |
| 直接渲染点 | 6 个屏幕文件改为 `Image + ColorFilter.tint` |
| 导航栏 | `LogisticsApp` 底部导航走 `fromSymbol` 翻译 |
| 数据层 | **刻意不动** —— `Models.kt` 等返回的符号字符串是服务端数据契约（`WorkspaceApiParserTest` 直接断言），改类型会破坏契约 |
| 门禁脚本 | `scripts/check_emoji_icons.py` —— 分类判定，区分"UI 图标违规"与"合法数据契约符号"，可机械执行 |
| 验收 | 编译 `BUILD SUCCESSFUL`；121 测试全绿；门禁 PASS；反向注入验证可抓到违规 |
| 生产行为变更 | 无 —— 所有 `Text(symbol)` 的合法字符（`●` `!` `↓` `→` `?` 等）仍走文本渲染，仅 `✓ ⚠ ☰ ○ ▶` 改为矢量图标 |

**关键约束（改之前必读）**：`Models.kt` / `WorkspaceScreen.kt` / `LogisticsViewModel.kt` /
`ApprovalAndProfileScreens.kt` 里的符号字符串**不是 UI 图标**，是数据契约值。
它们一律经渲染层翻译，**不要**去改这些字段的类型或字面量——
`Models.kt` 顶部注释已写明这一点。

**门禁接入**（每次涉及 UI 的提交前跑）：

```bash
python3 scripts/check_emoji_icons.py --verbose
```

<details>
<summary>原始清点表（历史存档）</summary>

**选定方案**：在 `ScannerIcons` 旁扩展 `LogisticsIcons` 图标族，与 `FlashOn/FlashOff`
同构，零外部依赖。

| 文件 | 行 | 符号 | 语义 | 承载方式 |
|------|----|------|------|----------|
| `ui/screens/LoginScreen.kt` | 153 | ⚠ | 登录错误提示前缀 | 行内 16px 图标 + `Text` |
| `ui/screens/ScannerScreen.kt` | 586 | ⚠ | 摄像头启动失败 | 32px 图标 |
| `ui/screens/AdminActivationScreen.kt` | 111 | ⚠ | 激活失败提示 | 行内 13px 图标 + `Text` |
| `ui/screens/QueueScreen.kt` | 232 | ⚠ | 队列项异常状态 | 12px 图标 + tint |
| `ui/screens/MaterialDetailScreen.kt` | 78 | ✓ | 物料已完成 | 13px 白色图标 |
| `ui/screens/OrderDetailScreen.kt` | 184 | ✓ | 工序已完成 | 交由 `StatusTag` |
| `ui/screens/ApprovalAndProfileScreens.kt` | 615 | ✓ / — | 权限允许 / 不允许 | 图标 / 文本 |
| `ui/screens/ApprovalAndProfileScreens.kt` | 448 | ✓ | `EXECUTED` 状态标签 | `StatusTag` 映射 |
| `ui/screens/AuditScreen.kt` | 192 | ✓ / ! | 审计成功 / 失败 | `StatusTag` 映射 |
| `ui/screens/WorkshopAssemblyScreens.kt` | 742 | ✓ / ○ | 阶段完成 / 未完成 | `StatusTag` 映射 |
| `ui/screens/WorkshopAssemblyScreens.kt` | 771 | ▶ / ✓ | 进行中 / 已完成 | `StatusTag` 映射 |
| `ui/screens/WorkspaceScreen.kt` | 70, 73 | ✓ | 任务状态映射 | 数据层契约（不改） |
| `model/Models.kt` | 79, 134, 656 | ✓ / ☰ | 状态与类型映射 | 数据层契约（不改） |
| `ui/LogisticsViewModel.kt` | 90, 93 | ☰ / ✓ | 底部导航图标 | 导航图标 |
| `app/src/test/.../WorkspaceApiParserTest.kt` | 154 | ✓ | 数据契约断言 | 保留 |

</details>

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

1. **跑门禁**（涉及 UI 改动时）：
   `python3 scripts/check_emoji_icons.py --verbose`
2. `git add -A && git commit`
3. `git push gitee feature/ui-polish-camera`
4. **更新本文件**（NEXT.md）的「待办」与「当前 HEAD」
5. 一句「已完成 X，下一步 Y」交代给用户

> **教训（务必内化）**：提交前把 emoji 扫描扫**全仓**，不要只扫 `git diff` 里的文件。
> 2026-09-13 那次就是只扫了改动文件，差点漏掉 11 个文件的历史欠账。
> 现已固化为 `scripts/check_emoji_icons.py`，按纪律跑即可，不要再手工 grep。

## 版本号规则（2026-10-11 起）
- 后端每个改动运行代码的 PR 必须递增 `material-flow-backend/VERSION`，CI「后端版本号必须递增」强制检查。
- 修复/小调整：patch +1（0.6.0 → 0.6.1）；新功能：minor +1（0.6.x → 0.7.0）。
- 0.6.0 = PR #20–#26（Gitee 回退、trend_compare、Agent 输出、悬浮球流式、BOM 聚合、物料主档整改）。
