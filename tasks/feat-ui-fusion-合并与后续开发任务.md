# feat/ui-fusion 合并与后续开发任务

## 状态

- [x] 已拉取 `origin/feat/ui-fusion`。
- [x] 已完成变更审查。
- [x] 规格补遗 §8/§9（技术栈取舍 + 接口一致性约束）已提交 `e5dabb6` 并推送。
- [x] **P0-1 API 地址去敏感化**（commit `f2056ed`，已合入并推送）：`MaterialFlowApi.kt` 删除真实 IP，`baseUrl` 改为 BuildConfig 注入 + 无效占位回退；`AndroidManifest.xml` 移除 `usesCleartextTraffic`；接收端重建 `:app:assembleDebug` BUILD SUCCESSFUL；全树 IP grep 0 命中。
- [x] P0-4 请求类型统一：已核实通过——代码中 `PRODUCTION_ORDER`/`MATERIAL_CODE`/`LOCATION_CODE`/`FLOW_RECORD` 已对齐契约，`LOGISTICS_NO` 仅存在于模型文档注释（说明已删除该语义），非运行时分支，无需改动。
- [x] **P0-2 文案与品牌清理**：已派发 Hermes 子 Agent（独立 worktree `/tmp/mf-p0-copy`，基线 `b3b514b`）——Android 源码运行时 UI 文案中"物流/线边库/配送工位/承运商/运输中"等禁用术语清洗，保持包名与 API 契约不变。
- [ ] 暂不合并到 `main`：P0 修复未全部完成。
- [ ] 业务源码修改由 Codex 在隔离 worktree 执行，架构 Agent 仅验收 docs/tasks 与门禁结果。

## P0 合并前修复

1. 在 `logistics-android/app/src/main/java/com/company/logistics/data/remote/MaterialFlowApi.kt` 删除真实 IP 和 HTTP 测试地址。
2. 将 `baseUrl` 改为构建配置注入，默认值只能是无效占位地址；本地测试地址通过未提交的 `local.properties` 或 CI Secret 注入。
3. 确认 `AndroidManifest.xml` 未开启生产环境明文 HTTP。
4. 修正 APP 请求类型为 `PRODUCTION_ORDER`、`MATERIAL_CODE`、`LOCATION_CODE`、`FLOW_RECORD`。
5. 清理运行时页面中的“物流号”“物流轨迹”“线边库”“配送工位”等业务文案。
6. 执行 Git 历史和当前树敏感信息扫描；发现真实地址、密码、Token、私钥时不得合并。

## P1 后续开发

1. 生产订单列表、订单详情和细分机型详情使用正式 API。
2. 机型详情展示物料需求、库存分布和厂内流转时间线。
3. 库位扫码展示库位物料清单和最近流转记录。
4. 厂内流转单扫码展示流转明细、来源库位、目标库位、数量、操作人和服务端时间。
5. 完成入库、出库审批列表、审批详情、批准/拒绝和执行状态刷新。
6. 完成 Room 编译器配置、数据库迁移、离线队列持久化和 WorkManager 重试。
7. 增加 API 错误码映射、鉴权过期处理、幂等冲突提示和乐观锁冲突提示。
8. 图片凭证完成拍摄、压缩、上传、断点重试和业务绑定。

## 验证门禁

- Android `./gradlew --no-daemon :app:assembleDebug` 成功。
- 后端 `python3 -m py_compile material-flow-backend/app/main.py` 成功。
- 扫码服务端返回类型符合当前契约。
- 公开仓库扫描不包含真实 API 地址和凭据。
- 入库/出库审批未通过时库存不变。
- 调拨库存不足或版本冲突时事务回滚。
- Room 离线队列重启 APP 后数据仍存在。
- 合并前创建 PR；PR 验证通过后 squash merge 到 `main`。

## 合并命令流程（由有源码修改权限的开发 Agent 执行）

```bash
git switch feat/ui-fusion
# 完成 P0 修复并验证
git push origin feat/ui-fusion
gh pr create --base main --head feat/ui-fusion
gh pr merge --squash --delete-branch

git switch main
git pull --ff-only origin main
git status --short
```

## 架构约束

- 产品定位：公司内部厂内物料流转。
- 订单：公司生产订单，可包含多个细分机型。
- 不建立外部物流、物流轨迹、承运商、线边库或配送工位模块。
- 所有库存数量为整数，禁止负库存。
- 入库和出库必须审批；调拨和库位绑定简化但必须审计。
