# 项目结案 BOM 差异报表（实际需求 vs 工程 BOM）

> 需求来源：`tasks/需求4-项目结案BOM差异报表.md`（老王第 4 条：机台分类汇总实际需求量与工程 BOM 量的差异）
> 实现版本：0.26.0；不改表结构，仅新增只读接口、xlsx 导出与管理台页面。

## 数据口径

| 概念 | 定义 |
| --- | --- |
| 实际需求侧 | `order_material_requirements.required_quantity`，按 `order_id + device_id + material_id` |
| 工程 BOM 侧 | `bom_versions`（`model_code` **就是机台号**，取 `status='PUBLISHED'` 的最新 `version_no`）+ `bom_items.quantity`（该机台单件用量） |
| 比对格 | 一个 `机台 × 料号`。`bom_items` 是单层平铺、同版本内料号唯一，不递归展开 |
| 差异量 `deltaQty` | `required - bom`，带符号，保留 2 位小数 |
| 覆盖率 `coverage` | `devicesWithBom / devices`（0~1 小数）；页面显示百分比 |

不用 `agg_bom_items`（备料表）当对比基线：它的批次多是 `U9:组件名` 的 BOM 片段，行数比需求行还多。

## 差异分类

| kind | 判定 | 含义 |
| --- | --- | --- |
| `ADDED` | BOM 无、需求有 | 变更追加 / 现场补料 |
| `INCREASED` | 两侧都有，`required > bom` | 实际用量大于 BOM |
| `DECREASED` | 两侧都有，`required < bom` | 实际用量小于 BOM（多领或 BOM 多写） |
| `SAME` | 两侧都有，相等 | 一致 |
| `MISSING_IN_REQUIREMENT` | BOM 有、需求无 | BOM 有但没建需求（漏备料） |

分类与差异量都按 **2 位小数** 比较：`bom=8.01 / required=8` 是 `DECREASED -0.01`，
`bom=4.004 / required=4` 是 `SAME`（2 位小数后相等）。绝不 `int()` 截断 BOM 量。

## 三处口径取舍

1. **无工程 BOM 的机台不是「差异为 0」。** 机台的 `hasBom=false`、`bomQty`/`deltaQty`
   与五类计数全部返回 `null`，只有 `requiredQty`（需求事实）照给；这类机台不进项目合计，
   只在 `devices` 里单列并把 `devicesWithoutBom` 报出来（页面顶部黄色提示条）。
   一台可比机台都没有时（如 26B-013）`summary.bomQty/requiredQty/deltaQty` 也是 `null`，
   明细为空 —— 不能拿 0 冒充「比对过、没差异」。
2. **有 PUBLISHED 版本但一条 BOM 明细都没有，等同没有基线。** 否则需求会被整片判成
   `ADDED`，看起来像「全是变更追加」，比报「无法比对」更误导。
3. **`summary` 是当前机台范围的全量口径，不受 `kind` / `only_diff` 影响。** 明细可以只看
   差异，但汇总始终描述「这些机台比出了什么」；`only_diff=0` 时逐列求和可复核汇总。
   按 `device_no` 过滤时汇总一起收窄（不是只过滤明细）。

需求侧挂在 `device_id` 为空的行无法归属机台，不进比对；接口不返回该计数（装配工可见范围内
也不应看到项目级统计）。

## 接口

`GET /api/v1/projects/{project_no}/bom-variance`

| 参数 | 说明 |
| --- | --- |
| `device_no` | 可选，只看某台 |
| `kind` | 可选，五类之一；给了 `kind` 就以它为准（`kind=SAME` 不受 `only_diff` 影响） |
| `only_diff` | 默认 `1`，等价于排除 `SAME` |
| `page` / `page_size` | 默认 1 / 100，`page_size` 最大 500 |

响应 `summary` 含 `devices / devicesWithBom / devicesWithoutBom / materials / bomQty /
requiredQty / deltaQty / added / increased / decreased / same / missingInRequirement /
diffMaterials / coverage`，`materials` 是参与比对的「机台×料号」格数，与 `total`
（不加过滤时）相等。`devices` 每台一行，`items` 为分页后的明细。

`GET .../bom-variance/export`：xlsx 两个 sheet。

| sheet | 列 |
| --- | --- |
| 机台汇总 | 机台号、是否有工程BOM、BOM版本、BOM件数、需求件数、差异件数、新增料号数、增加数、减少数、一致数、漏备料数 |
| 料号差异 | 机台号、料号、品名、规格、单位、BOM量、需求量、差异量、类型 |

第 1 行是加粗的项目合计（只有有 BOM 的机台，标签写明「N 台无工程BOM未纳入」），
数量列固定为 D~K，无 BOM 机台的数量列留空。导出与接口同源（`_bom_variance_dataset`），
过滤条件与列表一致；文本控制字符由 `xlsx_writer` 过滤。

## 权限

| 角色 | 行为 |
| --- | --- |
| ADMIN / PLANNER / WORKSHOP_SUPERVISOR | 全部项目（本系统没有 PROJECT_MANAGER，项目级口径由这三个角色承担） |
| WAREHOUSE_ADMIN | 只读全部（与齐套口径一致） |
| ASSEMBLER | 仅本人参与装配的机台（`assembly_task_members.removed_at IS NULL`）；查非参与项目返回 403 `PROJECT_FORBIDDEN`，不返回空列表 |
| 其它角色 | 403 `FORBIDDEN` |

## 管理台

- 页面：`/admin/projects/bom-variance`（侧栏「生产 → BOM 差异」，与「项目物料需求」「齐套与缺料」并列）
- 导出：`/admin/projects/bom-variance/export?project_no=...`
- 摘要卡：机台数 / 有工程 BOM 机台 / 覆盖率 / 差异料号数 / BOM 与需求件数 / 差异件数；
  覆盖率 < 100% 时黄色提示条「N 台机台无工程 BOM，未纳入比对（先在 U9 同步 BOM）」。
- 明细默认 `only_diff=1`，可按机台、类型过滤，分页 100；数量统一 2 位小数。
- 网页端可访问角色：ADMIN / WORKSHOP_SUPERVISOR / PLANNER / WAREHOUSE_ADMIN；
  项目搜索直接查 `production_orders`（`_pmr_search_projects`），不走带 `_pmr_authorize`
  的 API 函数，否则仓库管理员搜索会吃 403 JSON。

## 明确不做

- 不做 BOM 版本历史对比（只比当前 PUBLISHED 版本）。
- 不自动补建缺失的工程 BOM（26B-013 的 BOM 另走 U9 同步）。
- 不改 `agg_bom_items` 结构、不加 `source` 列。
