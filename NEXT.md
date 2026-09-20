# NEXT.md — 交接看板

> 给下一个对话用。**不要复述上下文，读这个文件即可。**
> 最后更新：2026-09-20 21:20

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
| 当前 HEAD | `637e435` |
| 工作区 | 干净，无未提交改动 |
| 远端 Gitee | 已同步至 `637e435` ✅ |
| 远端 GitHub | 停在更早的提交，**与 Gitee 分叉** |

**构建方式**：

```bash
# Android（Gradle wrapper 已缓存在沙箱，可直接用）
cd logistics-android && ./gradlew :app:compileDebugKotlin

# 后端
cd material-flow-backend && python3 -m pytest tests/<单个文件>.py -q
pytest 全量跑会因脚手架污染出现 23 个假失败，必须逐文件跑
```

---

## 2. 最近一轮已完成（提交 637e435）

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

### P1 — 测试基础设施（重要，当前测试不可信）

- [ ] **Android：消除跨测试类顺序依赖**
  `LogisticsViewModel*` 系列混跑时约 50% 概率间歇失败。
  根因：测试用 `Dispatchers.Unconfined` 且未等待 `init` 里的会话恢复协程，恢复协程后落地会覆盖 `login()` 写入的 ADMIN 登录态。
  正确修法：改用 `StandardTestDispatcher` + `runTest`，或在同一调度器上显式等待恢复流程结束。
  **已确认是基线既有问题，非 637e435 引入**（stash 全部改动后仍复现）。

- [ ] **后端：消除全量混跑的状态污染**
  逐文件隔离运行 28/28 通过；全量跑 23 failed / 52 passed。
  同样是基线既有的脚手架问题。

### P2 — 收尾

- [ ] `delivery/boyang-report/index.html` 仍含旧产品名
- [ ] 包名 `logistics` 与产品名「智慧工厂」语义不一致（技术债）
- [ ] Gitee 与 GitHub 两远端 `main` 已分叉，若需合并先比对差异

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
