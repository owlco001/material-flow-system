# 工单齐套情况 + 缺料清单

> 需求来源：`tasks/需求2-齐套与缺料清单.md`（老王 2026-10-27 第 2 条）
> 实现版本：0.24.0；不改表结构，仅新增只读查询接口、xlsx 导出与管理台页面。
> 物料状态（在库可满足 / 已到货待入库 / 在途采购 / 无采购计划）见
> `docs/shortage-status.md`（0.25.0，需求 3）：它叠加在本文的缺口口径之上，不改这里的数字。

## 数据口径

| 概念 | 定义 |
| --- | --- |
| 项目 | `production_orders.order_no`（U9 项目号 = 订单号） |
| 机台 | `order_devices.device_no` |
| 需求行 | `order_material_requirements`，按 `order_id + device_id + material_id` 关联 `materials` |
| 采购缺口 `procureGapQuantity` | `max(required_quantity − arrived_quantity, 0)`：还要催采购多少 |
| 备料缺口 `prepareGapQuantity` | `max(required_quantity − in_stock_quantity, 0)`：库里还差多少才能上线 |
| 齐套行 | 采购缺口为 0（即 `arrived >= required`）；状态仍是 ARRIVED/IN_STOCK 之外时另计 `statusAnomalyLineCount` |
| 缺料行 | 采购缺口 > 0 |
| 齐套率 | 齐套行 / 需求行，保留 1 位小数；没有需求行时返回 `null`（不冒充 0%） |

### 三处口径取舍（与需求文档字面表述的差异，均为避免虚高）

1. **「到货」但只到一部分的行按缺料算。** 需求文档写「齐套行 = ARRIVED + IN_STOCK」，
   但现场 `ARRIVED` 常见「部分到货」（`required=10, arrived=4`）。若只看状态，催料中的
   项目会显示 100% 齐套。因此齐套行要求采购缺口为 0（即 `arrived >= required`），
   这类行进入缺料清单，最不利状态显示「到货」。
2. **状态异常行单列但不重复计数。** 状态仍是 `OUT_OF_STOCK`、但 `arrived` 已等于
   `required`（状态未回写）时，该行按到货口径**算齐套**，同时计入
   `statusAnomalyLineCount`（齐套行的子集）。因此恒等式是
   `需求行数 = 齐套行 + 缺料行`，`statusAnomalyLineCount` 只用来看数据质量，
   不参与加法；原 `zeroGapLineCount` 字段随双口径改造移除（它只是恒等式的兜底，
   现在不需要了）。
3. **需求行必须挂在机台上。** `order_material_requirements.device_id` 为空或指向别的项目
   机台的行，无法归属机台，不计入齐套口径；概览用 `unmappedRequirementCount` 提示
   （仅非 ASSEMBLER 返回，避免向装配工泄露项目级统计）。该计数按 `material_code`
   过滤（与概览其它数同作用域）；按 `device_no` 过滤时这些行本就不属于该视图，返回
   `null` 而不是 `0`。

> **0.24.0 口径变更（2026-11-25，审查 P1-2）**：单一口径 `required − in_stock` 会把
> 「已全量到货、尚未入库」（`arrived == required`、`in_stock == 0`）的行算成缺料——
> 26B-013 因此虚增 1005 件缺口、齐套率被压到 10.0%（按到货口径为 67.8%）。现在改为
> **双口径并列**（见下节）：催采购看采购缺口、备料看备料缺口，齐套率按到货口径。
> 含义含糊的 `totalShortageQuantity` 已从本模块移除（需求 1 的
> `/api/v1/orders/material-status` 仍保留该字段，未改动）。

缺料清单只列采购缺口 > 0 的料号：物料主档存在但本项目没有需求记录的料号不会出现（不造假）。

## 双口径说明

为什么不再用单一口径：一张需求行同时被两类人看——采购关心「还差多少没到货」（催料），
仓库关心「库里还差多少才能上线」（备料）。用 `in_stock` 单口径会让「到货在途、未入库」
的行既被算成缺料（采购重复催）、又不算齐套（车间以为缺料）；用 `arrived` 单口径则看不
出「货到了但没入库」的备料压力。两者数据源不同、结论天然不同，强行合并成一个数只会让
一方误判，所以并列给出。

| 口径 | 公式 | 用途 | 谁看 |
| --- | --- | --- | --- |
| 采购缺口 `procureGapQuantity` | `max(required − arrived, 0)` | 还要催采购多少；**决定缺料清单与齐套率** | 采购 / 计划员 / 车间主管 |
| 备料缺口 `prepareGapQuantity` | `max(required − in_stock, 0)` | 库里还差多少才能上线（含已到货未入库） | 仓库 / 备料员 |

- **齐套率按到货口径**：齐套行 = `arrived >= required`（采购缺口为 0），与缺料行
  （采购缺口 > 0）互补，`需求行 = 齐套行 + 缺料行` 恒成立。
- **缺料清单按采购缺口筛行**：`arrived == required && in_stock == 0` 的行采购上不缺，
  不出现在清单里；但它的备料缺口仍计入概览的 `totalPrepareGapQuantity`。
- **两个合计的差异是正常的**：`totalProcureGapQuantity` 只由缺料行累加（齐套行必为 0），
  所以等于清单列求和；`totalPrepareGapQuantity` 按**当前视图的全部需求行**累加，清单每行
  的备料缺口也按该料号**全部需求行**累加（含同料号里「已到货未入库」的行），因此清单列
  求和与合计相等。唯一的例外是**整个料号一行缺料都没有**（全部行都已到货、只是没入库）：
  它不进缺料清单，它的备料缺口只计入 `totalPrepareGapQuantity`；接口另给
  `listPrepareGapQuantity` 表示清单内的备料缺口合计，两者之差就是这部分。
- **排序**：`sort=procure`（默认）按采购缺口降序，`sort=prepare` 按备料缺口降序；
  第二键都是影响机台数降序、第三键料号升序，保证翻页不重不漏。

## 权限（与需求 1 完全同口径，复用同一批函数）

- `ADMIN` / `WORKSHOP_SUPERVISOR` / `PLANNER`：全项目。
- `WAREHOUSE_ADMIN`：可查（仓库要催料清单）；注意需求 1 的
  `/api/v1/projects/{no}/material-requirements` 对仓库仍是 403，本次没有放宽。
- `ASSEMBLER`：只看本人参与机台（`assembly_tasks.assigned_assembler_id` 或
  `assembly_task_members.assembler_id` 且 `removed_at IS NULL`）。项目有设备但本人一台
  都没参与 → `403 PROJECT_FORBIDDEN`；指定他人机台 → `403 FORBIDDEN`（不是空数据）。
- 其余角色 → 403；未登录 → 401。

实现上直接调用 `_pmr_authorize` / `_pmr_resolve_project` / `_pmr_project_devices` /
`_pmr_device_names`，没有第二套权限判断。项目号解析同样支持精确、唯一模糊（项目号或
项目名）；模糊命中多个返回 409 `PROJECT_AMBIGUOUS` 并给出候选。

## 接口

### `GET /api/v1/projects/{project_no}/kitting-summary`

可选 `device_no`（只看单台机台）与 `material_code`（料号包含匹配，与缺料清单同作用域）。

```json
{
  "projectNo": "JSBY-26B-200",
  "projectName": "26B-200 顶升移栽机项目",
  "projectStatus": "IN_PROGRESS",
  "resolvedFrom": "order_no",
  "deviceFilter": null,
  "materialFilter": null,
  "summary": {
    "deviceCount": 7,
    "projectDeviceCount": 7,
    "requirementLineCount": 14,
    "kittedLineCount": 1,
    "shortageLineCount": 13,
    "statusAnomalyLineCount": 0,
    "shortageMaterialCount": 5,
    "totalProcureGapQuantity": 96,
    "totalPrepareGapQuantity": 101,
    "kittingRate": 7.1,
    "statusCounts": {"outOfStock": 12, "arrived": 1, "inStock": 1, "other": 0},
    "unmappedRequirementCount": 0
  },
  "devices": [
    {
      "deviceNo": "26B-200-P5-01",
      "deviceName": "顶升移栽机",
      "deviceType": "机台",
      "sequenceNo": 1,
      "requirementLineCount": 3,
      "kittedLineCount": 0,
      "shortageLineCount": 3,
      "statusAnomalyLineCount": 0,
      "shortageMaterialCount": 3,
      "procureGapQuantity": 17,
      "prepareGapQuantity": 17,
      "kittingRate": 0.0,
      "statusCounts": {"outOfStock": 3, "arrived": 0, "inStock": 0, "other": 0},
      "topShortages": [
        {"materialCode": "MAT-1001", "materialName": "液压站总成",
         "procureGapQuantity": 10, "prepareGapQuantity": 10},
        {"materialCode": "MAT-1002", "materialName": "输送链条",
         "procureGapQuantity": 5, "prepareGapQuantity": 5},
        {"materialCode": "MAT-1004", "materialName": "张紧座",
         "procureGapQuantity": 2, "prepareGapQuantity": 2}
      ]
    }
  ],
  "serverTime": "2026-11-25T03:16:47+00:00"
}
```

`topShortages` 是每台机台的卡点料号 Top3（按 `sort` 指定的缺口降序，默认采购缺口）。
`kittingRate` 为 `null` 表示该项目/机台没有需求行。`kitting-summary` 不返回
`items`，也不返回到清单级合计。

### `GET /api/v1/projects/{project_no}/shortage-list`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `device_no` | 空 | 只看某台机台（不可见 → 403/404，与需求 1 一致） |
| `material_code` | 空 | 料号包含匹配（不区分大小写），**同时作用于概览统计与清单**（见下） |
| `page` | 1 | 页码，< 1 → 422 |
| `pageSize` | 50 | 最大 200，越界 → 422 |
| `sort` | `procure` | `procure` 按采购缺口降序 / `prepare` 按备料缺口降序；其它值 → 422 |

排序：所选缺口降序 → 影响机台数降序 → 料号升序（第三关键字保证翻页不重不漏）。

```json
{
  "projectNo": "JSBY-26B-200",
  "projectName": "26B-200 顶升移栽机项目",
  "projectStatus": "IN_PROGRESS",
  "resolvedFrom": "order_no",
  "deviceFilter": null,
  "materialFilter": null,
  "sort": "procure",
  "items": [
    {
      "materialCode": "MAT-1001",
      "materialName": "液压站总成",
      "specification": "YB-63",
      "unit": "件",
      "requiredQuantity": 60,
      "arrivedQuantity": 0,
      "inStockQuantity": 0,
      "procureGapQuantity": 60,
      "prepareGapQuantity": 60,
      "lineCount": 6,
      "outOfStockLineCount": 6,
      "arrivedLineCount": 0,
      "affectedDeviceCount": 6,
      "affectedDeviceNos": ["26B-200-P5-01", "26B-200-P5-02", "26B-200-P5-03",
                           "26B-200-P5-04", "26B-200-P5-05", "26B-200-P5-06"],
      "deviceNoText": "26B-200-P5-01,26B-200-P5-02,26B-200-P5-03,26B-200-P5-04,26B-200-P5-05 等 6 台",
      "statusCode": "OUT_OF_STOCK",
      "statusLabel": "缺货",
      "orderNo": "JSBY-26B-200"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 50,
  "hasMore": false,
  "summary": {
    "mergedMaterialCount": 5,
    "totalProcureGapQuantity": 96,
    "totalPrepareGapQuantity": 101,
    "listPrepareGapQuantity": 96,
    "deviceCount": 7,
    "shortageLineCount": 13
  },
  "serverTime": "2026-11-25T03:16:47+00:00"
}
```

- 同料号跨机台合并成一行：`procureGapQuantity` 是各机台缺料行的采购缺口之和；
  `prepareGapQuantity` 是该料号**全部需求行**的备料缺口之和（同料号里
  「已到货未入库」的行也要备料，不能漏）；`requiredQuantity` / `arrivedQuantity` /
  `inStockQuantity` / `lineCount` 只统计**缺料行**，`affectedDeviceCount` 是这些行的
  去重机台数，`affectedDeviceNos` 是完整机台列表。
- **过滤作用域统一**：`device_no` 与 `material_code` 同时作用于 `summary`
  （含 `requirementLineCount` / `kittingRate` / 两个缺口合计 / `unmappedRequirementCount`）
  与 `items`。因此 `summary.totalProcureGapQuantity` 与清单列求和恒等，页面不会出现
  「概览 96 / 清单 18」这类同屏两套数。
- `summary.totalPrepareGapQuantity` 是**当前视图全部需求行**的备料缺口
  （与 `kitting-summary` 同定义），清单列求和与它相等；`summary.listPrepareGapQuantity`
  是清单内料号的备料缺口合计，只有「整个料号都没有缺料行」的备料缺口不在其中。
- `projectDeviceCount` 对 `ASSEMBLER` 返回 `null`（只给 `deviceCount` = 本人可见机台数），
  与 `unmappedRequirementCount` 一致，避免泄露项目规模。
- `deviceNoText` 供页面/导出直接展示：≤ 5 台用逗号连接，> 5 台折叠为
  `前5台 等 N 台`。
- `statusCode` 是该料号的**最不利状态**：任一行 `OUT_OF_STOCK` → 无在库；否则任一行
  `ARRIVED` → 部分到货；否则为 `IN_STOCK`（状态未回写但仍有缺口）。

### `GET /api/v1/projects/{project_no}/shortage-list/export`

`device_no` / `material_code` / `sort` 与列表一致，但**不受分页限制**。返回 `.xlsx`
（两 sheet，文件名同时带 ASCII 与 UTF-8 名）：

| Sheet | 列 |
| --- | --- |
| 齐套概览 | 项目号、项目名称、范围（项目合计/机台）、机台号、机台名称、需求行数、齐套行数、缺料行数、状态异常行数、齐套率(%)、缺料料号数、采购缺口、备料缺口 |
| 缺料清单 | 料号、品名、规格、单位、采购缺口、备料缺口、在库量、影响机台数、影响机台、最不利状态、关联订单号 |

概览里 `需求行数 = 齐套行数 + 缺料行数` 可直接对账（`状态异常行数` 是齐套行的子集，
不加进等式）；无需求行的项目齐套率写 `""` 而不是 0。缺料清单两列缺口并列，
`sort=prepare` 时按备料缺口排序，导出与页面顺序一致。

导出的缺料行与接口同源（`_kitting_dataset`），不会出现「页面 5 个料号、Excel 4 个」。
控制字符过滤沿用 0.23.0 的 `xlsx_writer._clean`。

## 管理台

- 页面：`/admin/projects/kitting`（侧栏「生产 → 齐套与缺料」）
- 导出：`/admin/projects/kitting/export?project_no=...`
- 顶部项目搜索（项目号 / 项目名），齐套概览卡片（采购缺口 / 备料缺口两张卡），
  按机台表（含两个缺口列与卡点 Top3），缺料清单表（可按机台、料号过滤，可切换
  `sort=procure|prepare`，分页 50），一键导出；页面上有一行图例说明两个缺口的用途。
- 网页端可访问角色：ADMIN / WORKSHOP_SUPERVISOR / PLANNER / WAREHOUSE_ADMIN；
  装配工仍走 App 接口，不上管理台。

## 需求 3（已实现，0.25.0）

缺料的请购/采购/在途状态分档已完成，见 `docs/shortage-status.md`：在本文的缺料行上叠加
「在库可满足 / 已到货待入库 / 在途采购 / 无采购计划 / 料号未识别」，**不改这里的缺口数字与导出前 11 列**。

同步口径提醒：采购订单同步默认 `only_open=false`（含已结案订单），关闭单在读取侧过滤；
这样 U9 上关单后本地能学到关闭、不再把已关闭单算在途。展示数量只截断不进位。
