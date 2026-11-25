# 工单齐套情况 + 缺料清单

> 需求来源：`tasks/需求2-齐套与缺料清单.md`（老王 2026-10-27 第 2 条）
> 实现版本：0.24.0；不改表结构，仅新增只读查询接口、xlsx 导出与管理台页面。

## 数据口径

| 概念 | 定义 |
| --- | --- |
| 项目 | `production_orders.order_no`（U9 项目号 = 订单号） |
| 机台 | `order_devices.device_no` |
| 需求行 | `order_material_requirements`，按 `order_id + device_id + material_id` 关联 `materials` |
| 缺口 | `max(required_quantity - in_stock_quantity, 0)`，负数按 0（多备不算负缺口） |
| 齐套行 | 缺口为 0 **且** `status_code ∈ (ARRIVED, IN_STOCK)` |
| 缺料行 | 缺口 > 0 |
| 齐套率 | 齐套行 / 需求行，保留 1 位小数；没有需求行时返回 `null`（不冒充 0%） |

### 三处口径取舍（与需求文档字面表述的差异，均为避免虚高）

1. **「到货」但缺口 > 0 的行按缺料算。** 需求文档写「齐套行 = ARRIVED + IN_STOCK」，
   但现场 `ARRIVED` 常见「部分到货」（`required=10, arrived=4`）。若只看状态，催料中的
   项目会显示 100% 齐套。因此齐套行额外要求缺口为 0，这类行进入缺料清单，最不利状态
   显示「到货」。
2. **状态非齐套但缺口为 0 的行单列。** 例如状态仍是 `OUT_OF_STOCK`、但 `in_stock`
   已等于 `required`（状态未回写）。这类行不计齐套、也不进缺料清单，单独计入
   `zeroGapLineCount`，保证
   `需求行数 = 齐套行 + 缺料行 + zeroGapLineCount` 恒等，账能对上。
3. **需求行必须挂在机台上。** `order_material_requirements.device_id` 为空或指向别的项目
   机台的行，无法归属机台，不计入齐套口径；概览用 `unmappedRequirementCount` 提示
   （仅非 ASSEMBLER 返回，避免向装配工泄露项目级统计）。

缺料清单只列缺口 > 0 的料号：物料主档存在但本项目没有需求记录的料号不会出现（不造假）。

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

可选 `device_no`（只看单台机台）。

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
    "zeroGapLineCount": 0,
    "shortageMaterialCount": 5,
    "totalShortageQuantity": 96,
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
      "zeroGapLineCount": 0,
      "shortageMaterialCount": 3,
      "shortageQuantity": 17,
      "kittingRate": 0.0,
      "statusCounts": {"outOfStock": 3, "arrived": 0, "inStock": 0, "other": 0},
      "topShortages": [
        {"materialCode": "MAT-1001", "materialName": "液压站总成", "shortageQuantity": 10},
        {"materialCode": "MAT-1002", "materialName": "输送链条", "shortageQuantity": 5},
        {"materialCode": "MAT-1004", "materialName": "张紧座", "shortageQuantity": 2}
      ]
    }
  ],
  "serverTime": "2026-11-25T03:16:47+00:00"
}
```

`topShortages` 是每台机台的卡点料号 Top3（缺口降序）。`kittingRate` 为 `null` 表示
该项目/机台没有需求行。

### `GET /api/v1/projects/{project_no}/shortage-list`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `device_no` | 空 | 只看某台机台（不可见 → 403/404，与需求 1 一致） |
| `material_code` | 空 | 料号包含匹配（不区分大小写） |
| `page` | 1 | 页码，< 1 → 422 |
| `pageSize` | 50 | 最大 200，越界 → 422 |

排序：缺口量降序 → 影响机台数降序 → 料号升序（第三关键字保证翻页不重不漏）。

```json
{
  "projectNo": "JSBY-26B-200",
  "projectName": "26B-200 顶升移栽机项目",
  "projectStatus": "IN_PROGRESS",
  "resolvedFrom": "order_no",
  "deviceFilter": null,
  "materialFilter": null,
  "items": [
    {
      "materialCode": "MAT-1001",
      "materialName": "液压站总成",
      "specification": "YB-63",
      "unit": "件",
      "requiredQuantity": 60,
      "arrivedQuantity": 0,
      "inStockQuantity": 0,
      "shortageQuantity": 60,
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
    "totalShortageQuantity": 96,
    "deviceCount": 7,
    "shortageLineCount": 13
  },
  "serverTime": "2026-11-25T03:16:47+00:00"
}
```

- 同料号跨机台合并成一行：`shortageQuantity` 是各机台缺口之和，
  `affectedDeviceCount` 是去重机台数，`affectedDeviceNos` 是完整机台列表。
- `deviceNoText` 供页面/导出直接展示：≤ 5 台用逗号连接，> 5 台折叠为
  `前5台 等 N 台`。
- `statusCode` 是该料号的**最不利状态**：任一行 `OUT_OF_STOCK` → 无在库；否则任一行
  `ARRIVED` → 部分到货；否则为 `IN_STOCK`（状态未回写但仍有缺口）。

### `GET /api/v1/projects/{project_no}/shortage-list/export`

`device_no` / `material_code` 与列表一致，但**不受分页限制**。返回 `.xlsx`
（两 sheet，文件名同时带 ASCII 与 UTF-8 名）：

| Sheet | 列 |
| --- | --- |
| 齐套概览 | 项目号、项目名称、范围（项目合计/机台）、机台号、机台名称、需求行数、齐套行数、缺料行数、齐套率(%)、缺料料号数、缺口总量 |
| 缺料清单 | 料号、品名、规格、单位、总缺口量、影响机台数、影响机台、最不利状态、关联订单号 |

导出的缺料行与接口同源（`_kitting_dataset`），不会出现「页面 5 个料号、Excel 4 个」。
控制字符过滤沿用 0.23.0 的 `xlsx_writer._clean`。

## 管理台

- 页面：`/admin/projects/kitting`（侧栏「生产 → 齐套与缺料」）
- 导出：`/admin/projects/kitting/export?project_no=...`
- 顶部项目搜索（项目号 / 项目名），齐套概览卡片，按机台表（含卡点 Top3），缺料清单表
  （可按机台、料号过滤，分页 50），一键导出。
- 网页端可访问角色：ADMIN / WORKSHOP_SUPERVISOR / PLANNER / WAREHOUSE_ADMIN；
  装配工仍走 App 接口，不上管理台。

## 未做（留给需求 3）

缺料的请购/采购/在途状态分档，等 U9 请购单实体与采购到货字段确认后再做。当前只回答
「缺什么、缺多少、影响谁」。
