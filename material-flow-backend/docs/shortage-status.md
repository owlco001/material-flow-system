# 缺料物料状态（四档）

> 需求来源：`tasks/需求3-缺料物料状态.md`（老王 2026-10-27 第 3 条）
> 实现版本：0.25.0。只新增 `u9_po_headers` / `u9_po_lines` 两张表，不改既有表结构，
> 不改需求 1 `/api/v1/orders/*` 与需求 2 缺料清单的任何缺口数字。

## 解决什么问题

需求 2 的缺料清单只说「还差多少」（采购缺口 / 备料缺口），但同样是「差 50 件」，
可能完全不用管（货已到厂，等入库）、要催货（在途）、或者真该下单（无计划）。
本功能在缺料行上叠加一层**物料状态**，把「差多少」变成「下一步做什么」。

## 四档定义（判定顺序严格 1→2→3→4）

| 档 | code | 中文 | 判据 | 现场动作 |
| --- | --- | --- | --- | --- |
| 1 | `IN_STOCK_READY` | 在库可满足 | 库存可用量 ≥ 采购缺口 | 直接领料（到货/入库记录未回写） |
| 2 | `ARRIVED_NOT_STOCKED` | 已到货待入库 | 未结案 PO 已到货量 ≥ 采购缺口 | 催仓库入库 |
| 3 | `IN_TRANSIT` | 在途采购 | 未结案 PO 未到货量 > 0（可部分覆盖） | 催供应商，看「可覆盖量」 |
| 4 | `NO_PLAN` | 无采购计划 | 以上都不满足 | 真缺，需要下采购 |

另有第五种标记（不是第四档的分类，而是数据质量提示）：

| 标记 | code | 含义 |
| --- | --- | --- |
| 料号未识别 | `UNKNOWN_MATERIAL` | 采购数据已同步，但该料号在 U9 料品档案里查不到（料号体系不一致），**不塞进 NO_PLAN**，避免重复下单 |

判定顺序的理由：先看库里能不能直接满足（最容易、不用等任何人），再看已到货（只差入库），
再看在途（要催货），最后才认定无计划。前档满足时不会被后档抢走（有单测固定）。

### 各档的数量口径

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `procureGapQuantity` | 需求 2 原口径 `max(required − arrived, 0)` | **未改动**，四档的「缺口」就是它 |
| `prepareGapQuantity` | 需求 2 原口径 `max(required − in_stock, 0)` | **未改动**，只并列展示 |
| `availableStockQty` | `materials.available_quantity` | 库存可用量（U9 库存同步回写），即「还能自由领用的量」 |
| `arrivedNotStockedQty` | 未结案 PO 的 `Σ TotalRecievedQtyTU` | 已到货未入库量；**不计入在途** |
| `inTransitQty` | 未结案 PO 的 `Σ max(PurQtyTU − TotalRecievedQtyTU, 0)` | 未到货量；已到货的部分不重复计入 |
| `coverableQuantity` | `min(在库 + 已到货 + 在途, 缺口)` | 可覆盖量，第 3 档部分覆盖时用来判断还差多少 |
| `openPoNos` / `suppliers` | 未结案 PO 单号 / 供应商简称 | 催货用；最多各 20 个 |

两个取舍（写进文档避免被当成 bug）：

1. **在途不看「是否够覆盖」**：只要存在未结案 PO 的未到货量（> 0），就是「在途采购」，
   `partialCoverage=true` + `coverableQuantity < 缺口` 表示只覆盖一部分。若把它判成
   「无采购计划」，采购会重复下单。
2. **已到货量按 PO 累计**：`TotalRecievedQtyTU` 是 U9 采购行上的累计到货量，不扣本单已入库
   数量，因此 `arrivedNotStockedQty` 是「货已到厂」的上界；它只用于判断第 2 档覆盖关系，
   不参与缺口计算，不影响需求 2 的数字。
3. **已结案订单不算在途**：按 `Status.Name` 判定（`Closed / Cancel / Void / 关闭 / 作废 /
   取消 / 结案…`）。本版 U9 的 `Status.Value` 数字语义未全部实测，因此不硬编码数字，
   只按名称判定，避免把「已审核」（如 Value=2, Name=Approved）误判成已关闭。

## 数据来源与同步

### 新表（`CREATE TABLE IF NOT EXISTS`）

```sql
u9_po_headers(id, doc_no UNIQUE, status_value, status_name, supplier_code,
              supplier_name, business_date, org_code, synced_at)
u9_po_lines(id, doc_no, line_no, item_code, item_name, pur_qty_tu,
            recieved_qty_tu, delivery_date, synced_at, UNIQUE(doc_no, line_no))
```

### 同步入口

- `POST /api/v1/u9/sync/po?dry_run=false&since_days=180&only_open=true&check_items=true`
- 管理台 `/admin/u9` → 「采购订单（在途/到货）」，与其它实体同样的「预览同步 / 执行同步」。
- 幂等：头按 `doc_no`、行按 `(doc_no, line_no)` upsert。重复同步不产生重复行；数量/状态
  变化只更新；本次同步到的单据里 U9 已删掉的行会删除；窗口外的历史单据保留。

### U9 取数字段（已实测）

`POST /webapi/CommonEntity/Query`，`EntityFullName=UFIDA.U9.PM.PO.PurchaseOrder`，
`ReturnFields` 必含：

```
DocNo, BusinessDate, Status.Value, Status.Name, Supplier_Code, Supplier_ShortName,
DeliveryDate, POLines.DocLineNo, POLines.ItemInfo.ItemCode, POLines.ItemInfo.ItemName,
POLines.PurQtyTU, POLines.TotalRecievedQtyTU
```

- 到货量字段是 **`TotalRecievedQtyTU`**（U9 自己把 Received 拼成 Recieved），照抄，不要「修正」。
- 状态取 `Status.Value` / `Status.Name`；直接取 `Status` 只得到枚举类型名。
- **U9 对不存在的字段静默忽略**：取回后逐项校验（`mappers.validate_po_rows`），
  关键字段在所有记录里都不存在时抛错并中止同步，绝不把缺失的到货量当 0——
  否则「已到货待入库」会被误判成「无采购计划」。校验同时覆盖客户端与管理台两条入口，
  也覆盖 sync 层（防止自定义 client 绕过校验）。
- 分页：请求带 `PageSize` / `PageIndex`；某页不满或没有新单号就停止（兼容不支持分页的网关）。

### 料号体系核对（UNKNOWN_MATERIAL）

请求路径不直连 U9。采购订单同步时顺带用 U9 料品档案（`fetch_items`）核对**本地全部需求料号**，
查不到的记入本次同步日志 `u9_sync_logs.detail_json.unknownMaterials`；缺料清单一侧读最近一次
成功执行的 `po` 同步日志得到这组料号，据此标 `UNKNOWN_MATERIAL`。

- 核对失败（U9 报错/料号超过 2000 个）只记错误，不影响采购订单入库；此时不做该判定。
- 没有历史 `po` 同步日志 → 不判定，全部按四档走（通常是降级状态，见下）。

## 数据新鲜度与降级

| 情况 | 响应 | 页面 |
| --- | --- | --- |
| `u9_po_*` 为空（从未同步） | `poDataFresh=false`，`poData.fresh=false`，所有缺料行状态一律 `NO_PLAN` | 顶部红框「采购数据未同步，状态判断不可信」，并指向 `/admin/u9` |
| 有数据、最近同步 ≤ 24h | `poDataFresh=true`，`poData.syncedAt` / `openOrderCount` | 「采购数据同步于 YYYY-MM-DD HH:mm，共 N 张未结案订单」 |
| 有数据、最近同步 > 24h | `poDataFresh=true`，`poData.stale=true` | 同一行提示**标黄**「超过 24 小时未同步，建议重新同步」 |

降级时**不假装有数据**：状态是 `NO_PLAN` 但同时给出 `poDataFresh=false`，让调用方/页面能区分
「真的没计划」和「没同步过所以不知道」。

## 接口

### `GET /api/v1/projects/{project_no}/shortage-list`

每行新增：

```json
{
  "stockStatus": "IN_TRANSIT",
  "stockStatusLabel": "在途采购",
  "availableStockQty": 0,
  "arrivedNotStockedQty": 0,
  "inTransitQty": 50,
  "coverableQuantity": 50,
  "partialCoverage": false,
  "openPoNos": ["PO-2602"],
  "suppliers": ["上海在途供应商"]
}
```

- 既有字段（含 `statusCode` / `statusLabel`，那是需求 2 的「最不利状态」）全部保留。
- 新增查询参数 `status=`（`IN_STOCK_READY|ARRIVED_NOT_STOCKED|IN_TRANSIT|NO_PLAN|
  UNKNOWN_MATERIAL`），非法值 422。
- 响应新增 `statusBreakdown`（五键计数）、`statusFilter`、`poDataFresh`、`poData{...}`。
- `statusBreakdown` 统计**当前范围**（机台/料号过滤后）的全部缺料料号，**不受 `status` 过滤影响**，
  这样页面上的状态计数才能当作过滤入口；`summary.totalProcureGapQuantity` 等仍是清单行求和
  （受 `status` 过滤影响，与列表一致）。

### `GET /api/v1/projects/{project_no}/kitting-summary`

`summary.statusBreakdown` + 顶层 `statusBreakdown` / `poDataFresh` / `poData`；支持 `status=`。
需求 2 的既有字段与数字不变。

### 导出 `GET /api/v1/projects/{project_no}/shortage-list/export`

`缺料清单` sheet 在**末尾追加** 7 列（历史列位置不变，老报表对账不受影响）：
`物料状态、在库可用量、已到货未入库、在途量、可覆盖量、未结案PO、供应商`。支持 `status=`。
`齐套概览` sheet 不变。

### 管理台 `/admin/projects/kitting`

- 缺料清单新增「物料状态」（四色标签）、「在库可用量」「已到货未入库」「在途量」
  「未结案采购单（含供应商）」列。
- 过滤下拉「全部状态 / 五档」；概览下方的状态分布标签也可点击过滤（`?status=`）。
- 页头采购数据新鲜度提示（见上表），导出链接带上当前 `status` 过滤。

## 测试覆盖（`tests/test_shortage_status.py`）

- 四档边界：每档「正好覆盖」→ 本档；「差 1 件」→ 落到下一档。
- 判定顺序：三档都够时判第 1 档；在库不够、到货与在途都够时判第 2 档。
- 已关闭 PO 不计在途（同步存了关闭单，读取侧也过滤）。
- 已到货未入库不计入在途（`pur=100, recv=100` → 在途 0）。
- PO 表为空：`poDataFresh=false`、全部 `NO_PLAN`、页面出现「采购数据未同步，状态判断不可信」。
- 料号体系不一致 → `UNKNOWN_MATERIAL`，与 `NO_PLAN` 分开计数。
- 同步幂等：连续同步两次行数不变、第二次 `inserted=0`；改到货量只更新不新增。
- U9 缺 `TotalRecievedQtyTU` 时，客户端与 sync 两层都报错且不写库（不是当 0）。
- 导出列、状态过滤、管理台列/下拉/新鲜度提示、`/admin/u9` 出现 po 实体。
- 需求 2 的 `procureGapQuantity` / `prepareGapQuantity` 在状态层加入后逐料号不变。

## 已知限制

- 真实 U9（221 环境）的采购订单接口只做了字段口径与请求体的代码实现，本地用假 transport
  验证了「请求字段齐全 / 缺字段报错 / 关闭单与超窗单过滤」；**尚未在真实 U9 上跑通**，
  首次上线请先点「预览同步」看样例。
- `Status.Value` 各数字的中文语义未全部实测；关闭判定只认名称。若现场状态名既不是英文
  （Closed/Cancel）也不是中文（关闭/作废），需要把新词加进
  `mappers.PO_CLOSED_NAME_MARKERS`（测试会覆盖该函数）。
- POs 只同步最近 `since_days`（默认 180 天）的订单，更早的未结案订单不会出现在状态判断里；
  可在同步参数里调大。
- `availableStockQty` 依赖库存同步回写 `materials.available_quantity`；长期不同步库存时它是旧值，
  会让第 1 档判断偏乐观。
