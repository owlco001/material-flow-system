# 物料主档导入（U9 ItemMaster.xlsx）

## 源文件结构（2026-10-10 导出）
- 单个工作表「物料主档」；第 1 行为标题，第 2 行为表头，共 70 列，约 3.7 万行。
- `料号` 全表唯一，作为主键；`品名`、`库存单位.名称` 必填（缺后者时回退 `库存主单位`）。
- 特殊格式：
  - `有效性.失效日期`：Excel 日期序列值；≥ 2958000（9999 年）视为永久 → `9999-12-31`。
  - `创建时间` / `修改时间`：`YYYY.MM.DD HH:MM:SS` 文本 → `YYYY-MM-DD HH:MM:SS`。
  - `可销售/可委外/可生产/可库存交易/可采购/专用料/是否进行工程变更版本控制`：`√` 或空 → 1/NULL。
  - `T6料号`、`设备编号` 等带前导零的编码一律按文本保存。
- 当前导出为空或与其他列重复的 11 列（库存主单位、财务分类、采购分类、料品形态、控制组织、
  是否版本数量控制、是否成分控制、是否等级控制、MRP分类、可用量检查、可用量规则）原样存入 `extra_json`。

## 数据模型
- `materials`：仍只放流转核心字段。导入时同步 `name/specification/unit`，`category` 仅在为空时用「主分类」回填；
  **不改** `total_quantity / available_quantity / batch_no / expiry_date`。
- `material_master`：按 `code` 1:1，59 个类型化字段 + `extra_json` + `row_hash`（增量比对）。
  索引：U9图号、T6料号、设备编号、项目号、主分类、形态属性、存储地点。
- `material_master_import_batches` / `material_master_import_operations`：预览批次与幂等提交记录。

## 流程
1. 预览：上传（≤ 30 MB）→ 流式解析 → 校验 → 与库内 `row_hash` 比对，得到新增 / 变更 / 未变化 / 错误 /
   「库内有、文件无」计数，变更字段统计与前 200 条差异样本。上传文件暂存于 `DATA_DIR/imports/material_master/`，有效期 2 小时。
2. 提交：校验文件 sha256 未变后重新解析并写库（同一事务），只写新增和变更行；有错误行时需显式 `skipInvalid`。
3. 文件里缺失的本地料号只统计、不删除。

实测（3.7 万行、13.8 MB）：预览约 11 s，首次全量写入约 12 s，重复导入同一文件 0 写入；峰值内存约 120 MB。

## 接口
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/material-master/import/preview` | multipart `file`；ADMIN / WAREHOUSE_ADMIN / PLANNER |
| POST | `/api/v1/material-master/import/commit` | `{previewId, clientOperationId, skipInvalid}` + `Idempotency-Key` |
| GET | `/api/v1/material-master/import/batches[/{id}]` | 批次列表 / 详情 |
| GET | `/api/v1/material-master` | 搜索：`q`、`itemForm`、`storageLocation`、`mainCategoryCode`、`deviceNo`、`projectNo` |
| GET | `/api/v1/material-master/facets` | 形态 / 存储地点 / 财务分类计数 |
| GET | `/api/v1/materials/{code}/master` | 单料号主档；`refCost/latestCost` 仅 ADMIN 可见 |

管理台：侧边栏「仓库物流 → 物料主档」（`/admin/material-master`）。
