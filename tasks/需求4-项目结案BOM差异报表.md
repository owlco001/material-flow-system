# 需求 4：项目物料实际需求 vs 工程 BOM 差异报表

## 用户原话

> 4、项目结束后能根据机台分类汇总，整个物料实际需求量和工程 BOM 的量差异（因为变更的物料会在备料表里添加）

## 数据事实（2026-11-25 在 221 生产库实测，勿凭印象改）

### 可用的两侧数据

| 侧 | 表 | 口径 |
|---|---|---|
| 实际需求 | `order_material_requirements` | `required_quantity`，按 `order_id` + `device_id` + `material_id` |
| 工程 BOM | `bom_versions` + `bom_items` | `bom_versions.model_code` **就是机台号**（如 `26B-017-P1-01`），取 `status='PUBLISHED'` 的最新 `version_no`；`bom_items.quantity` 为该机台单件用量 |

- `bom_items` 是**单层平铺**：一个 `bom_version_id` 下同一料号只出现一次（实测 113 行 / 113 个唯一料号），不需要递归展开。
- `agg_bom_items`（备料表）行数远大于需求行（21,854 vs 24,441），且批次多为 `U9:组件名` 的 BOM 片段，**不适合当工程 BOM 基线**，本需求不要用它做对比侧。

### 覆盖率（关键约束，决定报表必须能表达"缺基线"）

```
JSBY-26B-001:  机台 7   → 有工程BOM 0    需求行 220
JSBY-26B-013:  机台 115 → 有工程BOM 0    需求行 11,775
JSBY-26B-017:  机台 115 → 有工程BOM 115  需求行 11,637
JSBY-26B-024:  机台 1   → 有工程BOM 1    需求行 70
```

**只有 26B-017 和 26B-024 本地有工程 BOM。** 26B-013 的 115 台一条都没有 —— 报表必须把这类机台单列为「无工程 BOM，无法比对」，**绝不允许把缺失当成"差异为 0"或"完全一致"**。

### 26B-017 实测对照（验收基准）

```
26B-017-P1-01: BOM 113 行 / 556.85 件 | 需求 113 行 / 560 件 | 料号交集 113
26B-017-P1-33: BOM 164 行 / 4221.1064 件 | 需求 164 行 / 4224 件 | 料号交集 164
```

需求是整数、BOM 有小数（备料取整），所以正常机台的差异应表现为「同料号、差几件」，而不是大量新增料号。若报表把整数量当差异主因，说明取整口径写错了。

## 功能定义

### 差异分类（每个 机台×料号 一格）

| kind | 判定 | 含义 |
|---|---|---|
| `ADDED` | BOM 无、需求有 | 变更追加 / 现场补料 |
| `INCREASED` | 两侧都有，`required > bom` | 实际用量大于 BOM |
| `DECREASED` | 两侧都有，`required < bom` | 实际用量小于 BOM（多领或 BOM 多写）|
| `SAME` | 两侧都有，相等 | 一致 |
| `MISSING_IN_REQUIREMENT` | BOM 有、需求无 | BOM 有但没建需求（漏备料，需标红）|

- 数量一律保留 **2 位小数**，不要 `int()` 截断（BOM 侧本来就有小数）。
- 差异量 `delta = required - bom`，带符号。

### 汇总口径

- **按机台**分组（需求说"根据机台分类汇总"）：每台给出 BOM 件数、需求件数、差异件数、各类 kind 的料号数。
- **项目合计**：对所有「有工程 BOM」的机台求和；`devicesWithoutBom` 单列，不进合计，页面顶部显式提示「N 台无工程 BOM，未纳入比对」。
- `coverage = devicesWithBom / devices` 必须出现在响应和页面。

## 接口

### `GET /api/v1/projects/{project_no}/bom-variance`

| 参数 | 说明 |
|---|---|
| `device_no` | 可选，只看某台 |
| `kind` | 可选，`ADDED/INCREASED/DECREASED/SAME/MISSING_IN_REQUIREMENT` |
| `only_diff` | 默认 `1`，等价于排除 `SAME` |
| `page` / `page_size` | 默认 1 / 100，最大 500 |

响应：

```json
{
  "projectNo": "JSBY-26B-017",
  "summary": {
    "devices": 115, "devicesWithBom": 115, "devicesWithoutBom": 0,
    "materials": 1130, "bomQty": 64037.75, "requiredQty": 64400,
    "deltaQty": 362.25, "added": 0, "increased": 42, "decreased": 8,
    "same": 1080, "missingInRequirement": 0
  },
  "devices": [
    {"deviceNo": "26B-017-P1-01", "hasBom": true, "bomVersion": 1,
     "bomQty": 556.85, "requiredQty": 560, "deltaQty": 3.15,
     "added": 0, "increased": 2, "decreased": 0, "same": 111}
  ],
  "items": [
    {"deviceNo": "26B-017-P1-01", "materialCode": "30208002-00238",
     "materialName": "…", "specification": "…", "unit": "件",
     "bomQty": 2.0, "requiredQty": 2, "deltaQty": 0, "kind": "SAME"}
  ],
  "page": 1, "pageSize": 100, "total": 113
}
```

- 汇总里的 `materials/bomQty/requiredQty/...` 必须与 `items` 明细**同源同口径**：`summary.requiredQty` 要等于「所有参与比对机台的需求明细求和」，不能是另一条 SQL 顺手算的。
- 无工程 BOM 的机台：`hasBom=false`，`bomQty=null`、`deltaQty=null`，**不要填 0**。

### `GET /api/v1/projects/{project_no}/bom-variance/export`

xlsx 两个 sheet：
1. `机台汇总`：机台号、是否有工程BOM、BOM版本、BOM件数、需求件数、差异件数、新增料号数、增加数、减少数、一致数、漏备料数
2. `料号差异`：机台号、料号、品名、规格、单位、BOM量、需求量、差异量、类型

- 导出遵循 `app/xlsx_writer.py` 的 `build_xlsx_workbook`（多 sheet）。
- **列位必须显式断言**：小计/合计行的数字要落在对应数量列，不允许左移一列（需求 1 踩过这个坑）。
- 文本要过 XML 非法字符过滤（品名里可能有 `\x0b`）。

### 管理台页面 `/admin/projects/bom-variance`

- 侧栏「生产 → BOM 差异」（与「项目物料需求」「齐套与缺料」并列）。
- 项目号搜索（复用 `/api/v1/projects` 的搜索辅助函数，或直接查 `production_orders`，**页面不能调用带 `_pmr_authorize` 的 API 函数**，否则 WAREHOUSE_ADMIN 会吃 403 JSON）。
- 顶部摘要卡：机台数 / 有BOM机台数 / 覆盖率 / 差异料号数。
- 覆盖率 < 100% 时显示黄色提示条：「N 台机台无工程 BOM，未纳入比对（先在 U9 同步 BOM）」。
- 表格默认 `only_diff=1`，可按机台筛选、可按 kind 筛选。
- 页面展示数量保留 2 位小数，与接口一致。

## 权限

| 角色 | 行为 |
|---|---|
| ADMIN / PROJECT_MANAGER | 全部项目 |
| ASSEMBLER | 仅本人参与装配的机台（`assembly_task_members.removed_at IS NULL`）；查非参与项目返回 **403 `PROJECT_FORBIDDEN`**，不要返回空列表冒充"无差异" |
| WAREHOUSE_ADMIN | 只读全部（与齐套口径一致）|

## 明确不做

- 不做「BOM 版本历史对比」（只比当前 PUBLISHED 版本）。
- 不自动补建缺失的工程 BOM（26B-013 的 BOM 要另走 U9 同步，单独任务）。
- 不修改 `agg_bom_items` 结构、不加 `source` 列（原方案作废：用 `bom_versions/bom_items` 做基线即可，不需要迁移历史数据）。

## 验收

1. `GET /api/v1/projects/JSBY-26B-017/bom-variance?only_diff=0` → `devicesWithBom=115`、`devicesWithoutBom=0`、每台 `bomQty≈requiredQty`（差值来自取整，单台 |delta| < 10 件）。
2. `GET .../JSBY-26B-013/bom-variance` → `devicesWithBom=0`、`devicesWithoutBom=115`，`items` 为空，**不得**返回 `deltaQty=0` 冒充一致。
3. `summary` 与明细求和一致（`requiredQty`、`bomQty`、各 kind 计数）。
4. 导出的 xlsx：两个 sheet、行数正确、数量列在正确列位（用 `ElementTree` 解析校验，环境无 openpyxl）。
5. ASSEMBLER 查非参与项目 → 403 `PROJECT_FORBIDDEN`。
6. 容器内全量 pytest 不低于基线（611 passed），新增用例 ≥ 8。
7. 版本号 bump 到 0.26.0（`material-flow-backend/VERSION`）。
