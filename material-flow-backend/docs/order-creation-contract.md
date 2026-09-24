# 生产订单创建 API 契约（order-creation-contract.md）

> 内核域契约。Web 管理台按 admin-web-contract.md §内核边界 复用本 API 处理函数，
> 不得绕过直写表。APP 端现有 /api/v1/* 语义不受影响（纯新增端点）。

## 1. 端点

`POST /api/v1/orders`（201 Created）

创建生产订单及其细分机型（业务主线：生产订单—细分机型—物料的源头）。

**准入**：`ADMIN`、`PLANNER`。其他角色 403 `ROLE_FORBIDDEN`。

**请求头**
- `X-Request-Id`：可选，非 UUID 时服务端生成（同全站 `_safe_trace_id` 语义）。
- `Idempotency-Key`：可选；缺省时以 body.clientOperationId 为准（本端点幂等键
  取 body 内 `clientOperationId`，与 BOM 导入 commit 同模式）。

**请求体**（`application/json`）

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| clientOperationId | UUID 字符串 | 必填 | 幂等键（同键同体重放返回原结果；同键异体 409） |
| orderNo | string | 1..64，`[A-Za-z0-9_\-]+` | 订单号，全局唯一 |
| productName | string | 1..128（trim 后） | 产品名 |
| status | string | 可选，默认 `RELEASED` | 取值 `RELEASED`/`IN_PROGRESS`/`COMPLETED` |
| models | array | 1..20 项 | 细分机型列表 |
| models[].modelCode | string | `MODEL_CODE_RE` | 机型码，全局唯一（含跨订单） |
| models[].modelName | string | 1..128 | 机型名 |
| models[].plannedQuantity | integer | ≥ 1 | 计划数量 |

**成功响应 201**
```json
{
  "orderId": "...", "orderNo": "...", "status": "RELEASED",
  "models": [{"id": "...", "modelCode": "...", "modelName": "...", "plannedQuantity": 1}],
  "serverTime": "...", "traceId": "...", "idempotent": false
}
```

**错误语义**（与全站 ApiError 形态一致：
`{"error":{"code","message","retryable","traceId"}}`）
- 422 `VALIDATION_ERROR`：字段约束不满足；同一请求内 modelCode 重复 →
  「modelCode 重复」。
- 409 `ORDER_NO_TAKEN`：订单号已存在 → 「生产订单号已存在」。
- 409 `MODEL_CODE_TAKEN`：modelCode 已被占用（含跨订单）→ 「机型码已被占用」。
- 409 `IDEMPOTENCY_PAYLOAD_MISMATCH`：同幂等键请求体不一致 → 「相同幂等键的请求体不一致」。
- 403 `ROLE_FORBIDDEN`。

**幂等**：表 `order_operations`（统一 operations 形态：client_operation_id PK /
order_id / action='CREATE_ORDER' / payload_json / result_json / created_at）。
同键重放：`idempotent: true` + 原结果，无新落库。

**审计**：`audit_events` 记 `ORDER_CREATED`（entity_type='PRODUCTION_ORDER'，
before_json=null 快照 `{}`，after_json=成功响应快照）。

**事务**：`BEGIN IMMEDIATE` 包裹幂等查询/校验/双表插入/审计/幂等记录；任一冲突
整体回滚（无部分落库）。

## 2. DDL 新增（init_db 追加）

```sql
CREATE TABLE IF NOT EXISTS order_operations(
  client_operation_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL);
```

既有 `production_orders` / `production_order_models` 表结构不动（model_code 全局
UNIQUE 已由既有 DDL 保证；冲突由显式预检转译为上述 409 文案）。

## 3. 测试断言（tests/test_order_creation_api.py）

1. ADMIN/PLANNER 创建 → 201 + 落库（orders/models 行 + ORDER_CREATED 审计 +
   order_operations 行）；WAREHOUSE_ADMIN → 403。
2. 字段约束：空 orderNo/models 空/plannedQuantity<1/同请求重复 modelCode → 422 且无落库。
3. orderNo 冲突 → 409「生产订单号已存在」；modelCode 冲突（跨订单）→ 409「机型码已被占用」。
4. 幂等：同键同体重放 → `idempotent: true` 同 orderId 且不新增行；同键异体 → 409 payload mismatch。

## 4. 订单状态推进

`POST /api/v1/orders/{order_no}/status`（200）——**准入**：ADMIN、PLANNER。

**请求体**：`{"clientOperationId": UUID, "status": "IN_PROGRESS" | "COMPLETED"}`

**状态机**（单向，无跳级/回退/重复）：
`RELEASED → IN_PROGRESS → COMPLETED`。当前状态无对应合法迁移 → 409
`ORDER_STATUS_INVALID`「订单状态不允许变更」。未知单号 → 404「生产订单不存在」。

**成功响应**：`{orderId, orderNo, previousStatus, status, serverTime, traceId,
idempotent}`。

**幂等**：`order_operations(action='SET_ORDER_STATUS')`（同键同体 200
`idempotent: true`；同键异体 409 payload mismatch）。**审计**：`ORDER_STATUS_CHANGED`
（before_json=原状态快照）。

## 5. 测试断言（tests/test_order_status.py）

见 admin-web-contract.md §8.16（API 与 web 同契约测试文件覆盖）。
