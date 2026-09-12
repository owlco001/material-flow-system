# UI 融合说明（v0.3.0）

本次将设计体系的 UI 层完整融合进 `material-flow-system` 工程，并按《V1 需求冻结与接口契约》《厂内流转业务模型更正》对齐业务语义。

---

## 一、融合原则：视觉归设计，语义归契约

原工程 Android 端为单文件技术验证原型（`MainActivity.kt` 内联全部 UI 与网络逻辑）。
本次重构为「主题 → 模型 → 网络 → 组件 → 页面 → 导航」分层架构。

关键取舍：

| 层面 | 依据 | 说明 |
|---|---|---|
| 色值 / 字号 / 间距 / 圆角 / 组件样式 | **设计体系** | 现场作业可读性优化，可按需调优 |
| 状态枚举 / 角色名 / 字段结构 / 业务层级 | **接口契约** | 属数据契约，改动将导致接口不通 |

> 结论：颜色是表现层可自由调整；**状态码与角色名是数据层，必须与后端一致**。

---

## 二、与契约的对齐清单

### 2.1 状态色（契约 2.1 强制）

| 状态枚举 | 契约色 | 实现位置 |
|---|---|---|
| `OUT_OF_STOCK` 缺货 | 红 `#D92D20` | `MaterialStatusColors.Shortage` |
| `ARRIVED` 到货 | 黄 `#F2A900` | `MaterialStatusColors.Arrived` |
| `IN_STOCK` 在库 | 绿 `#1F9D55` | `MaterialStatusColors.InStock` |

契约要求「客户端不得只依赖颜色」——`StatusTag` 组件强制同时渲染 **颜色 + 符号 + 文字** 三通道。

### 2.2 角色（契约 1 节）

契约角色为 **管理员 / 仓库管理员 / 物料员 / 操作员**，V1 **无「主管」角色**。
审批能力集中在 `WAREHOUSE_ADMIN` 与 `ADMIN`（`UserRole.canApprove`）。

### 2.3 审批状态机（契约 2.2）

`DRAFT → SUBMITTED → PENDING_APPROVAL → APPROVED → EXECUTED`，含 `REJECTED` 分支。
已建模为 `ApprovalStatus`，并提供 `isApprovable` / `isExecutable` 供 UI 控制操作可用性。

### 2.4 扫码类型（业务模型更正）

已**移除 `LOGISTICS_NO`**（物流号概念作废），识别类型为：
`PRODUCTION_ORDER` / `MATERIAL_CODE` / `LOCATION_CODE` / `FLOW_RECORD` / `UNKNOWN`。

同步修正后端 `POST /api/v1/scan/resolve`：原先存在的 `WL` / `LOG` 前缀分支已删除，改为正则匹配。

---

## 三、新增代码结构

```
logistics-android/app/src/main/java/com/company/logistics/
├── MainActivity.kt                    应用入口（轻量依赖装配）
├── model/
│   └── Models.kt                      领域模型：角色/状态/流转类型/审批状态/扫码类型
├── data/
│   ├── LogisticsDatabase.kt           Room 离线队列（v2，含幂等键与重放 payload）
│   ├── DatabaseProvider.kt            Room 单例
│   ├── LogisticsRepository.kt         仓库层：网络 + 离线优先策略
│   └── remote/
│       ├── MaterialFlowApi.kt         契约 API 客户端
│       └── ApiParser.kt               响应解析 + 统一错误结构
└── ui/
    ├── theme/
    │   ├── Color.kt                   色彩系统（含契约状态色）
    │   ├── Type.kt                    字号阶梯 + 等宽料号样式
    │   ├── Shape.kt                   圆角 / 间距 / 尺寸常量
    │   └── Theme.kt                   Material3 主题装配 + 扩展语义色
    ├── components/
    │   ├── LogisticsComponents.kt     按钮/标签/卡片/空状态（四态齐全）
    │   └── ScannerComponents.kt       取景框/工具条/离线横幅
    ├── screens/
    │   ├── LoginScreen.kt             登录（角色由服务端返回）
    │   ├── ScannerScreen.kt           扫码作业（核心）
    │   ├── MaterialDetailScreen.kt    料号库存详情
    │   ├── OrderDetailScreen.kt       生产订单物料状态
    │   ├── QueueScreen.kt             离线队列与冲突处理
    │   └── ApprovalAndProfileScreens.kt  审批/库存/我的
    ├── LogisticsViewModel.kt          状态与路由（按角色生成导航）
    └── LogisticsApp.kt                导航骨架 + 底部导航栏
```

---

## 四、关键设计决策

| 决策 | 数值 | 理由 |
|---|---|---|
| 主操作按钮高度 | 56dp（扫码键 72dp） | 现场戴手套，48dp 底线不足 |
| 正文字号 | ≥16sp | 强光下小字不可读；12sp 为下限 |
| 料号字体 | 等宽 Mono + 字距 0.5sp | 防止数字认读错误 |
| 页面主色 | `#0E5FD8` 工业蓝 | 白底对比度 5.9:1（AA） |
| 底部导航 | 图标 + 文字双标注 | 纯图标在强光/手套场景不可识别 |
| 主操作位置 | 屏幕下 1/3 | 单手拇指可达域 |
| 权限落地 | 无权限页面不渲染 | 避免现场误触后产生疑问 |

---

## 五、离线优先策略

```
提交操作
  ├─ 成功           → 直接完成
  ├─ 业务失败(4xx)  → 不入队，直接提示（重放也不会成功）
  └─ 网络异常/5xx   → 写入本地队列（PENDING）
                        ↓ 联网后重放
                     服务端按幂等键去重（契约第 5 节）
                        ↓
                     成功 → SYNCED ／ 冲突 → CONFLICT（交用户处理）
```

幂等键 `clientOperationId` 随记录持久化，重放时原样回传，保证不产生重复单据。

---

## 六、构建与验证

```bash
cd logistics-android
./gradlew :app:assembleDebug
```

**本次已验证**：`BUILD SUCCESSFUL`，产出 `app-debug.apk`（v0.3.0，9.4MB），
经 `aapt2 dump badging` 校验包名/版本/权限正确。

> 构建环境说明：沙箱内使用 Android SDK 34 + Build-Tools 34.0.0 + Gradle 8.7 + JDK 20 完成编译验证。

---

## 七、已知限制与后续待办

### 尚未接入
1. **相机扫码**：V1 取景框为可点击模拟入口（点击触发示例条码），需接入 CameraX + 运行时权限。
2. **库位绑定页 / 提交流转页**：路由已保留，当前通过详情页直接提交。
3. **机型维度**：业务层级为「订单 → 机型 → 物料需求」，当前接口仅返回订单级物料清单。
4. **调拨 / 盘点 / 异常**：后端 API 已就绪，客户端 UI 待接入（仓库层已预留类型）。
5. **图片凭证上传**：`uploadFile()` 已实现，UI 未接入（契约要求单单据最多 9 张）。

### 需要后端配合
6. **生产订单接口缺失**：契约 4.3 为 `/api/v1/orders/material-status`，
   业务更正文档建议 `/api/v1/production-orders/{orderNo}`，两者路径不一致，需确认统一。
   当前后端 `material_status` 实现为返回该批次**全部物料**并要求 `ORDER_NO` 前缀，
   与契约定义（按订单号返回订单物料）语义有偏差，建议后端补齐订单数据模型。
7. **明文流量**：`AndroidManifest.xml` 仍开启 `usesCleartextTraffic="true"`，
   正式环境应切换 HTTPS 并移除（契约第 5 节要求）。
8. **初始密码**：`INITIAL_ADMIN_PASSWORD` 由环境变量注入，未硬编码，符合契约要求。

---

## 八、设计资料

`docs/design/` 下保留矢量设计稿源文件：
- `design-system.svg` —— 设计系统总览（色板/字阶/间距/圆角/组件状态）
- `screens-overview.svg` —— 关键页面高保真稿
