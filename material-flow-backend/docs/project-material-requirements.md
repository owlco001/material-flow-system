# 项目物料需求（按设备展开到底）

> 需求来源：`tasks/需求1-项目物料需求展开.md`（老王 2026-10-27 第 1 条）
> 实现版本：0.23.0；不改表结构，仅新增只读查询接口与管理台页面。

## 数据口径

| 概念 | 数据源 | 说明 |
| --- | --- | --- |
| 项目 | `production_orders.order_no` | U9 项目号 = 订单号，例如 `JSBY-26B-012` |
| 机台 | `order_devices.device_no` | U9 口径为「项目短号-机台编号」，例如 `26B-012-P5-12` |
| 备料表 | `agg_bom_items`（按 `batch_id` 分组） | `agg_bom_batches` 只有 `project_name`，没有项目号 |
| 物料需求状态 | `order_material_requirements` | 按 `order_id + device_id + material_id` 关联，通过 `materials.code = agg_bom_items.material_code` 对齐 |
| 层级 | `level_no` + `parent_code` | `parent_code` 为空即顶层；同料号多处出现时取最近的浅层父节点 |

层级还原：先按 `parent_code` 找**上一级**同料号节点（Excel 导入格式，最精确）；找不到时按
`line_no` 顺序用层级栈取最近的上一级节点。现场 U9 聚合 BOM 的「母件料号」整段都填成设备根
（例如 26B-012 的 25,955 行里 3/4 层行的 parent_code 仍是设备根），只按 `parent_code` 会把
所有深层件平铺到根上，因此必须有第二级回退。

### 项目 → 批次匹配（两级 + 兜底）

`agg_bom_batches` 没有项目号，因此按以下顺序匹配，命中即停，同级取 `created_at` 最新的批次：

1. `device_no`：批次内存在 `agg_bom_items.device_code` 与本项目 `order_devices.device_no` 完全相同；
2. `device_prefix`：`device_code` 以「项目短号-」开头（项目短号由项目号去掉公司前缀得到，口径与 `app/u9/sync.py:project_short` 一致）；
3. `project_name`：`agg_bom_batches.project_name` 模糊命中项目号 / 项目短号 / 项目名称。

响应里的 `matchedBy` 就是上面的取值（`device_no` / `device_prefix` / `project_name` / `none`），管理台页面直接展示，让用户知道这份备料表是靠什么匹配上的。

按项目名称兜底匹配时，若批次里的 `device_code` 认不到订单机台，仍按设备编码成组返回，但 `linked=false`（管理台标「未匹配机台档案」），且不挂需求状态——不假装已经对上机台。

## 接口

### `GET /api/v1/projects`

项目搜索（项目号 / 项目名模糊），供管理台选项目。

- 权限：ADMIN / WORKSHOP_SUPERVISOR / PLANNER 全量；ASSEMBLER 仅本人参与的项目；其他角色 403。
- 参数：`q`（关键字，可空）、`limit`（1-100，默认 20）。
- 响应：`{"projects":[{"projectNo","projectName","status","deviceCount","createdAt"}],"count","serverTime"}`。

### `GET /api/v1/projects/{project_no}/material-requirements`

- 参数：`expand=all|top`（默认 `top`，只返回第一层，子节点带 `childCount`）、`device_no`（只看一台）。
- 权限：ADMIN / WORKSHOP_SUPERVISOR / PLANNER 全量；ASSEMBLER 仅本人主责或协作的机台（按 `assembly_tasks.device_no` 关联）；其他角色 403。
- 项目号大小写不敏感；精确未命中时按项目号 / 项目名唯一模糊匹配（`resolvedFrom=fuzzy`），多个命中返回 `409 PROJECT_AMBIGUOUS` 并附候选；不存在返回 `404 PROJECT_NOT_FOUND`。
- 空态：`reason` 取值 `NO_DEVICES` / `NO_BATCH` / `NO_ITEMS`，`message` 为中文提示；`NO_BATCH` 时仍列出机台（空树）。

响应结构：

```json
{
  "projectNo": "JSBY-26B-012",
  "projectName": "26B-012 顶升移栽机项目",
  "projectStatus": "IN_PROGRESS",
  "resolvedFrom": "order_no",
  "expand": "top",
  "deviceFilter": null,
  "matchedBy": "device_no",
  "matchedByLabel": "机台号精确匹配",
  "matchedValue": "26B-012-P5-12",
  "batch": {"batchId": "…", "createdAt": "2026-10-20T02:11:03+00:00", "fileName": "26B-012.xlsx",
            "projectName": "26B-012 顶升移栽机项目", "rowCount": 128, "deviceCount": 4, "materialCount": 57},
  "summary": {"deviceCount": 4, "projectDeviceCount": 4, "itemCount": 128, "materialCount": 57,
              "levelCount": 4, "totalQuantity": 312.0, "trackedItemCount": 40},
  "devices": [{
    "deviceNo": "26B-012-P5-12", "deviceName": "顶升移栽机", "deviceType": "顶升旋转设备",
    "sequenceNo": 1, "linked": true, "matchedBy": "device_no",
    "itemCount": 32, "materialCount": 20, "levelCount": 4, "totalQuantity": 78.0,
    "requirementSummary": {"tracked": 12, "outOfStock": 3, "arrived": 5, "inStock": 4},
    "tree": [{
      "materialCode": "C1", "materialName": "链轮", "specification": "φ80", "unit": "件",
      "quantity": 2, "levelNo": 2, "parentCode": "26B-012-P5-12", "materialForm": "采购件",
      "lineNo": 2, "deviceTotalQuantity": 2, "childCount": 1,
      "requirement": {"requiredQuantity": 10, "arrivedQuantity": 4, "inStockQuantity": 2,
                       "statusCode": "ARRIVED", "statusLabel": "到货"},
      "children": []
    }]
  }],
  "reason": null, "message": null, "serverTime": "…"
}
```

`requirement` 为 `null` 表示该机台 × 料号在 `order_material_requirements` 中没有记录；此时前端留空，不得显示 0。

### `GET /api/v1/projects/{project_no}/material-requirements/export`

导出 xlsx（单 sheet「项目物料需求」）：保留层级（料号按深度全角缩进 + 层级列）与每台机台小计行、项目合计行；需求状态列在无记录时留空。无设备 / 无备料批次返回 `409`。

## 管理台

- 页面：`/admin/projects/material-requirements?project_no=…&expand=all|top&device_no=…`
- 导出：`/admin/projects/material-requirements/export?project_no=…[&device_no=…]`
- 准入：ADMIN / WORKSHOP_SUPERVISOR / PLANNER（与 `/admin/orders` 一致）。
- 交互：默认只加载第一层（26B-012 实测 0.23 MB / 0.4 s），行首 `+` 按机台展开到叶子
  （0.26 MB / 0.16 s）；「全部展开」是整项目一次性展开（同项目 14.95 MB / 1.6 s，页面已标注
  数据量大）；「全部收起」链接服务端渲染（无 JS 也可用）；顶部标注匹配方式、批次文件与批次时间。

## 测试

`tests/test_project_material_requirements.py`：层级还原（含 U9「母件料号=设备根」的层级栈回退）、批次选择（最新）、多机台分组、状态联动、`expand` 两种取值、机台过滤、项目搜索与模糊歧义、无备料批次 / 无设备 / 项目不存在、`matchedBy` 三种来源、导出列与合计行、装配工只见本人机台、管理台渲染与导出、既有订单详情契约不变。

## 真实数据核对（2026-11-25）

用部署库 `/opt/smart-factory/data/material_flow.db` 的只读副本跑过（未写入生产库）：

- 项目 `26B-012`（118 台机台）→ `matchedBy=device_no`，命中最新批次
  `09c05196…`（25,955 行 / 3,001 个料号）；批次内 119 个设备编码取值 = 118 个非空编码
  （全部与 `order_devices.device_no` 精确一致）+ 1 个空值（1,556 行，项目公共层级）。
- `expand=top` 全项目 25,955 项、6 层、24,231 行已建需求，响应 73 KB / 0.38 s；
  单台 `P5-13` 展开到叶子 419 项，根节点 208 个直接子件（与 `level_no` 分布一致）。

未覆盖：项目短号前缀与项目名称两条兜底路径目前只有夹具测试，现场库里没有对应的真实批次
（该库两个批次都走机台号精确匹配）。
