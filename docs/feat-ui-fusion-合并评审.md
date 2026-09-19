# feat/ui-fusion 合并评审

## 结论

当前不建议直接合并 `origin/feat/ui-fusion` 到 `main`。该分支包含较大范围 Android UI、离线数据层和后端扫码解析变更，且仍存在公开仓库脱敏问题：`MaterialFlowApi.kt` 保留真实测试 API 地址。必须先修复并重新扫描，再进入合并。

本文件只做架构评审，不修改业务源码。业务源码合并和修复应由开发 Agent 执行。

## 分支信息

- 分支：`feat/ui-fusion`
- 标签：`v0.3.0-ui-fusion`
- 最新提交：`841150d`
- 功能提交：`cbbe1c2`
- 相对 `main`：27 个文件变更，约 5646 行新增、79 行删除

## 已发现的更新

### Android

- Compose 主题、颜色、形状和字体体系。
- 登录、扫码、生产订单、物料详情、审批、个人资料、离线队列页面。
- `LogisticsViewModel`、`LogisticsRepository`。
- API 封装和 JSON 解析层。
- Room 数据模型和离线队列结构。
- 幂等键、乐观锁和队列状态模型。

### 后端

- `resolve_scan` 增加正则识别。
- 识别生产订单、料号、库位码和厂内流转单号。
- 不再实际识别 `LOGISTICS_NO` 前缀。

### 设计资料

- 新增 `docs/design/design-system.svg`。
- 新增 `docs/design/screens-overview.svg`。
- 新增 UI 融合说明文档。

## 阻塞项

1. `logistics-android/app/src/main/java/com/company/logistics/data/remote/MaterialFlowApi.kt` 仍包含公网测试 API 地址，不能进入公开仓库主分支。
2. 远端分支仍有旧的“物流流转系统”标题和历史术语，需要区分“历史设计资料”与当前厂内物料流转规范；运行时 UI 文案必须完成统一。
3. 远端 Android 代码规模增加较大，必须执行编译验证；本架构 Agent 不构建或修改业务源码。
4. Room 数据层需要确认是否配置了完整 compiler/processor；仅有实体和 DAO 文件不足以证明可运行。
5. 后端扫码返回类型应与当前契约一致：`PRODUCTION_ORDER`、`MATERIAL_CODE`、`LOCATION_CODE`、`FLOW_RECORD`。
6. APP 仍需确认默认 API 地址是否从构建配置注入，禁止硬编码生产地址或测试地址。

## 合并门禁

- [ ] 开发 Agent 删除真实测试 API 地址并改为安全构建配置注入。
- [ ] 开发 Agent 执行公开仓库敏感信息扫描，结果为空。
- [ ] Android `assembleDebug` 通过。
- [ ] 后端 `py_compile` 和 API 冒烟测试通过。
- [ ] 生产订单扫码类型为 `PRODUCTION_ORDER`，不再发送 `ORDER_NO`。
- [ ] 库位和流转单扫码类型与契约一致。
- [ ] 入库、出库、审批、执行、离线队列行为未回归。
- [ ] 通过后再创建 PR 或合并到 `main`，并删除已合并功能分支。

## 合并策略

采用 PR 合并，不直接强推 `main`：

1. 在 `feat/ui-fusion` 修复脱敏和契约问题。
2. 运行 Android、后端和敏感信息检查。
3. 创建 PR 到 `main`。
4. 检查通过后采用 squash merge。
5. 拉取 `main` 并核对远端提交和工作区状态。
