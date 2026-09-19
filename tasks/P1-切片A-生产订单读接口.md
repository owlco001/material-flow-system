# P1 切片 A：生产订单读接口（后端）

> 基线 `f89e92a`（feat/ui-fusion）。本切片为后端独立可验收单元，Android 客户端接入在下一切片（切片 B）。
> 架构约束：只处理公司生产订单与厂内物料流转；数量整数、禁止负库存；不新增任何外部物流字段/文案。

## 1. 模块边界（文字版）

```text
material-flow-backend/app/main.py
├── init_db()           # +3 张新表（幂等 CREATE TABLE IF NOT EXISTS）
├── production_order_seed()  # 新增：orders 表为空时注入确定性演示订单（固定 id，幂等）
├── GET /api/v1/production-orders
├── GET /api/v1/production-orders/{orderNo}
├── GET /api/v1/production-orders/{orderNo}/models/{modelCode}
└── GET /api/v1/production-orders/{orderNo}/models/{modelCode}/flow-records
material-flow-backend/tests/test_production_orders.py   # 新增
（本切片不动 Android、不动 docs 契约正文、不动其他路由）
```

## 2. 数据结构定义

### 2.1 新表（SQLite，init_db 内幂等建表）

```sql
CREATE TABLE IF NOT EXISTS production_orders(
  id TEXT PRIMARY KEY,               -- 'ord_001' 类确定性前缀 id
  order_no TEXT UNIQUE NOT NULL,     -- 'SO20260919'，匹配扫码正则 ^(SO|PO)\d+$
  product_name TEXT NOT NULL,
  planned_quantity INTEGER NOT NULL CHECK(planned_quantity >= 0),
  planned_delivery_date TEXT,        -- 'YYYY-MM-DD' 或 NULL
  status TEXT NOT NULL DEFAULT 'RELEASED',  -- PLANNED/RELEASED/IN_PRODUCTION/COMPLETED/CLOSED
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS production_order_models(
  id TEXT PRIMARY KEY,               -- 'pom_001'
  order_id TEXT NOT NULL REFERENCES production_orders(id),
  model_code TEXT NOT NULL,
  model_name TEXT NOT NULL,
  planned_quantity INTEGER NOT NULL CHECK(planned_quantity >= 0),
  status TEXT NOT NULL DEFAULT 'INTERNAL_PROCESSING', -- SHORTAGE/INTERNAL_PROCESSING/READY/IN_PRODUCTION/COMPLETED
  UNIQUE(order_id, model_code)
);
CREATE TABLE IF NOT EXISTS material_requirements(
  id TEXT PRIMARY KEY,               -- 'mr_001'
  order_model_id TEXT NOT NULL REFERENCES production_order_models(id),
  material_id TEXT NOT NULL,         -- 关联 materials.id（真实物料主数据外键，无物料不造行）
  required_quantity INTEGER NOT NULL CHECK(required_quantity > 0),
  arrived_quantity INTEGER NOT NULL DEFAULT 0,
  in_stock_quantity INTEGER NOT NULL DEFAULT 0,
  issued_quantity INTEGER NOT NULL DEFAULT 0
);
```

派生量（不入库）：`availableQuantity = max(0, inStockQuantity - issuedQuantity)`；
`shortageQuantity = max(0, requiredQuantity - availableQuantity)`；
`statusCode = SHORTAGE`（shortage>0）/ `IN_PROCESS`（已部分可用）/ `AVAILABLE`（齐套）；
`label/colorToken` 由服务端按上表映射（红/黄/绿三档，与既有 material-status 契约一致）。

### 2.2 演示种子（幂等：仅当 production_orders 为空时注入）

- 订单：`SO20260919` / ord_001 / "工业轴承总成" / 计划 200 / RELEASED / 交期 2026-10-05
- 机型：`BDX-6205` / pom_001 / "6205-2RS 轴承" / 计划 200
- 需求：mr_001 → materials.mat_001（MTR-001，现有物料行）required=200, arrived=986, in_stock=986, issued=0
  → available=986, shortage=0, AVAILABLE / status-green

## 3. 接口定义（函数签名级）

```python
def list_production_orders(
    page: int, page_size: int, keyword: str | None, status: str | None,
    user: UserContext,
) -> dict: ...
def get_production_order_detail(order_no: str, user: UserContext) -> dict: ...
def get_model_detail(order_no: str, model_code: str, user: UserContext) -> dict: ...
def list_model_flow_records(order_no: str, model_code: str,
                            page: int, page_size: int, flow_type: str | None,
                            user: UserContext) -> dict: ...
```

### 3.1 路由与响应结构

```text
GET /api/v1/production-orders
  query: page:int=1, pageSize:int=20, keyword:string|null, status:string|null
  分页结构: { items:[ProductionOrderSummary], page, pageSize, total, serverTime }
  ProductionOrderSummary: { orderNo, productName, plannedQuantity, plannedDeliveryDate,
    status, modelCount, materialCompletionRate(int 0-100), shortageCount,
    lastFlowAt:datetime|null, createdAt, updatedAt }
  404: 无；空列表 total=0

GET /api/v1/production-orders/{orderNo}
  200: { order:ProductionOrderSummary+detail, models:[ModelSummary], serverTime }
  ModelSummary: { modelCode, modelName, plannedQuantity, status,
    requiredMaterialCount, shortageMaterialCount, completionRate }
  404: {"detail":"生产订单不存在"}

GET /api/v1/production-orders/{orderNo}/models/{modelCode}
  200: { model:ModelSummary, requirements:[RequirementView], serverTime }
  RequirementView: { materialId, materialCode, materialName, specification, unit, batchNo,
    requiredQuantity, arrivedQuantity, inStockQuantity, issuedQuantity, availableQuantity,
    shortageQuantity, statusCode, label, colorToken }
  404: 订单或机型不存在

GET /api/v1/production-orders/{orderNo}/models/{modelCode}/flow-records
  query: page:int=1, pageSize:int=50, flowType:string|null
  200: { items:[FlowRecordView], page, pageSize, total, serverTime }
  数据来源：transfer_requests WHERE document_no=orderNo（JOIN materials 补 materialCode），
    按 created_at DESC 分页；FlowRecordView:
    { flowNo, documentNo, type, quantityTotal(int), status, createdBy, createdAt,
      approvedBy, approvedAt, executedAt, materialCodes:[...] }
  空流转记录返回 total=0 的合法空页（不允许 5xx）
  404: 订单或机型不存在
```

权限：四个读接口仅需登录态（`current_user` 依赖），与既有读接口一致；不新增角色分支。
keyword 匹配 order_no / product_name LIKE；status 精确匹配。

## 4. 验收标准（切片 A 门禁）

1. `python3 -m pytest material-flow-backend/tests/ -q` 全绿（既有 test_setup + 新 test_production_orders）。
2. 新测试必须覆盖：
   - 分页（page/pageSize 越界与 total 正确性）
   - keyword / status 过滤
   - 订单不存在 404；机型不存在 404
   - 机型需求派生量：构造 shortage>0 的断言路径（测试内直接写需求行）
   - flow-records 空页合法（total=0）与有记录（经 transfer-requests 创建 INBOUND 申请后查询）
   - 401 未登录
3. `py_compile` 通过；git 提交仅含 `main.py` + `tests/test_production_orders.py`。
4. 不提交数据库文件、VPS 地址、凭据；不修改 Android 与 docs/tasks（tasks 由架构 Agent 单独提交）。

## 5. 已知取舍与理由

- 读接口先于写接口（规格 §7 顺序 2→3）：订单/机型是 P1 页面数据源，先打通契约。
- 流转记录复用 transfer_requests（不新建 flow_records 表）：审批门禁与审计已在此表闭环，避免双事实源；flowType 查询参数对 transfer type 过滤。
- 演示种子用固定 id + 条件注入：确定性、幂等、外键真实（mat_001 已存在），重启不重复。
