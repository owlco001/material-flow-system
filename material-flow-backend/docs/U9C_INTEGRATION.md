# U9 Cloud（U9C）REST 对接说明（0.8.0 / 0.8.1）

智慧工厂后端通过 U9C 开放的 `/webapi` REST 接口**只读**同步四类数据（U9 → 本地）：
料品档案、BOM、库存现存量/可用量、生产订单。回写（完工汇报、领料）属于 Phase 2，未实现。

代码位置：`app/u9/`（`config.py` 配置、`client.py` HTTP 客户端、`mappers.py` 字段映射、
`sync.py` 同步编排、`routes.py` API），管理页 `/admin/u9`（模板 `templates/u9.html`）。

## 1. 环境变量

写在服务的 EnvironmentFile（生产环境为 `/etc/material-flow/material-flow.env`，root 所有、
建议 `chmod 600`），改完 `sudo systemctl restart material-flow`。

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `U9_ENABLED` | 是 | `0` | 总开关；未开启时所有 U9 接口返回 503，不访问 U9、不写数据 |
| `U9_MODE` | | `u9c` | `u9c`=U9 Cloud REST（`cloud` 视同 u9c）；`classic`=经典 U9 SOAP（仍为占位） |
| `U9_BASE_URL` | 是 | | 如 `http://<U9C 服务器>/u9c`，**不带** `/webapi`，注意路径大小写与现场一致 |
| `U9_CLIENT_ID` | 是 | | 第三方应用 clientid |
| `U9_CLIENT_SECRET` | 是 | | 第三方应用 clientsecret（只放环境文件，不进代码/日志/页面） |
| `U9_ENT_CODE` | 是 | | 企业编码（entcode） |
| `U9_ORG_CODE` | 是 | | 组织编码（orgcode），BOM/库存查询也用它 |
| `U9_USER_CODE` | 是 | | U9 用户编码（userCode），接口以该用户身份执行 |
| `U9_DEFAULT_WH` | | 空 | 库存同步默认存储地点编码；空=组织下全部存储地点 |
| `U9_TIMEOUT_SECONDS` | | `30` | 单次请求超时 |
| `U9_VERIFY_SSL` | | `1` | https 时是否校验证书 |
| `U9_CONTENT_TYPE` | | `application/json` | swagger 写的是 x-www-form-urlencoded，但请求体实际是 JSON；如现场网关只认前者可改 |
| `U9_AUTH_PATH` | | `/webapi/OAuth2/AuthLogin` | 授权接口路径 |
| `U9_TOKEN_TTL_SECONDS` | | `240` | token 进程内缓存时长（4 分钟；现场实测 AuthLogin 返回「token有效时间为5分钟」）；业务接口遇到 401/405 还会自动重登一次 |
| `U9_CONCURRENCY` | | `4` | 库存按料号逐个查询时的并发数 |
| `U9_BATCH_SIZE` | | `50` | 按料号/单号批量查询时每批条数 |
| `U9_BOM_RECURSIVE` | | `1` | BOM 是否逐层展开子件的下层 BOM |
| `U9_BOM_MAX_DEPTH` | | `8` | 最大展开层数（母件下第几层），超出标记「已截断」 |
| `U9_BOM_MAX_LINES` | | `20000` | 单个母件展开行数上限 |
| `U9_BOM_ENRICH` | | `1` | 是否批量查料品补齐子件规格/单位/形态（并据此跳过采购件的下层查询） |
| `U9_INVENTORY_QTY_FIELD` | | `store` | 本地库存数量取 `store`=现存量 `m_storeQty`，或 `available`=可用量 `m_canUseQty` |

`/api/v1/u9/status` 与管理页只显示脱敏摘要（clientid 只露前 4 位，密钥只显示「已设置」）。

## 2. 启用步骤

1. **U9C 侧登记第三方应用**：系统管理 → 第三方应用授权（各版本菜单名略有差异，也可能在
   「开放平台 / 应用授权」下），新增应用得到 `clientid`、`clientsecret`，指定企业、组织与
   执行用户；**把智慧工厂服务器访问 U9C 时的出口 IP 加入该应用的 IP 白名单**，否则授权返回
   `601 客户端IP未授权`。
2. **网络**：智慧工厂服务器必须能直连 `U9_BASE_URL`（通常是工厂内网地址）。先在服务器上
   `curl -m 5 -o /dev/null -w '%{http_code}' http://<U9C>/u9c/` 确认可达。
3. 把第 1 节的变量写入环境文件（`chmod 600`），`sudo systemctl restart material-flow`。
4. 管理员打开 `/admin/u9` → 「测试连接」（只做一次授权登录并显示耗时）。
5. 每类数据先点「预览同步」看样例行与新增/更新/跳过统计，确认无误再「执行同步」。

## 3. 接口与同步口径

授权：`GET {base}/webapi/OAuth2/AuthLogin?clientid=&clientsecret=&userCode=&entcode=&orgcode=`
→ `{"ResCode":0,"Data":"<token>"}`。业务接口请求头 `token: <token>`，统一返回
`{ResCode(0=成功), Success, ResMsg, Data}`；`ResCode≠0` 转成中文错误（含 ResMsg）。

授权错误码：401 未授权、405 授权码已过期、406 用户名不存在、408 组织编码不存在、601 客户端 IP 未授权。

| 实体 | U9C 接口 | 请求体 | 查询范围（默认） |
|---|---|---|---|
| 料品 items | `POST /webapi/ItemMaster/Query` | 数组 `[{ItemMaster:{Code}, OtherID}]`，每批 50 | 本地 `materials` 全部编码 + 页面/参数 `codes` |
| BOM boms | `POST /webapi/BOM/Query` | 数组 `[{Org:{Code}, ItemMaster:{Code}, OtherID}]` | 参数 `product_codes`（必填），逐层批量展开 |
| 库存 inventory | `POST /webapi/Invtrans/QueryQohAndAvailable` | **对象** `{Org, Wh?, ItemCode}` | 本地 `materials` 全部料号逐个查（并发 `U9_CONCURRENCY`）；给 `codes` 只查这些；`scope=all` 才按组织/存储地点整体查 |
| 生产订单 orders | `POST /webapi/MODoc/Query` | 数组 `[{DocNo}]`，每批 50 | 本地 `production_orders` 全部单号 + 参数 `doc_nos` |

批量查询整批返回业务错误时自动逐条重试，单条失败记入结果的 `errors`，不影响其它条。

API（全部 ADMIN，`dry_run` 默认 `true`）：

- `GET  /api/v1/u9/status`、`POST /api/v1/u9/health`
- `POST /api/v1/u9/sync/items?codes=&include_local=true`
- `POST /api/v1/u9/sync/boms?product_codes=A,B`
- `POST /api/v1/u9/sync/inventory?wh=&codes=&update_totals=true&scope=local|all`
- `POST /api/v1/u9/sync/orders?doc_nos=&include_local=true`
- `GET  /api/v1/u9/logs`、`GET /api/v1/u9/logs/{log_id}`（含预览样例）

## 4. 字段映射

**料品 ItemMasterDTOData → materials**

| 本地 | U9C 字段 |
|---|---|
| code | `m_code` |
| name | `m_name`（空则用料号） |
| specification | `m_sPECS` |
| unit | `m_inventoryUOM.m_name`（退回 `m_inventoryUOM.m_code` / 生产、采购单位；都没有为「个」） |
| （预览）形态 | `m_itemFormAttribute`：9 采购件、10 制造件、4 委外加工件 … 全表见 `mappers.ITEM_FORM_LABELS` |
| （预览）主分类 | `m_mainItemCategory.m_name` |

料品同步只改 name/specification/unit，**不改**数量、批次、效期；本地没有的料号（仅来自 `codes`）新增，数量为 0。

**BOM BOMMasterDTO4CreateSvData → agg_bom_batches / agg_bom_items**

| 本地 | U9C 字段 |
|---|---|
| 母件行 level_no=1, device_code=material_code=母件编码 | `m_itemMaster.m_code/m_name` |
| 子件 level_no | 直接子件 2，子件的子件 3 …（与人工导出的聚合 BOM 口径一致：需求生成只取 level>1） |
| parent_code | 上一层母件料号 |
| material_code / material_name | `m_bOMComponents[].m_itemMaster.m_code / m_name` |
| quantity | `m_usageQty / m_parentQty`（母件底数为 0 时取 `m_usageQty`），未计损耗 `m_scrap` |
| unit | `m_issueUOM.m_name`（退回料品库存单位） |
| specification / material_form | 料品查询补齐：`m_sPECS`、`m_itemFormAttribute` 中文名 |
| line_no | 深度优先顺序（同层按 `m_sequence`） |
| batch.project_name / file_name | `U9:<母件名>` / `U9C BOM <母件> v<m_bOMVersionCode>` |

跳过 `m_isDelete=true`、`m_isEffective=false`、`m_disableDate` 早于今天的子件；替代料
（`m_bOMCompSubstituteDTO4CreateSv`）不展开。BOM 内容（层级/料号/用量/单位/形态/规格）未变时
不重复建批次。环路（子件又包含祖先）只记一行并在 errors 里提示，不再往下展开。

**库存 INVBinLotShipmentDistributeDTOData → locations / inventory / materials**

| 本地 | U9C 字段 |
|---|---|
| 物料 | `m_itemCode` → 本地 `materials.code`（本地没有的料号跳过并列出） |
| locations.code | `m_whCode-m_binCode`（无库位时只用 `m_whCode`） |
| locations.name | `m_whName / m_binName` |
| inventory.quantity | 同料号+库位各批次（`m_lotCode`）汇总后的 `m_storeQty`（或 `m_canUseQty`），四舍五入为整数、负数按 0 |
| materials.total_quantity / available_quantity | 该料号全部库位 `m_storeQty` 合计 / `m_canUseQty` 合计（`update_totals=true` 时） |

**生产订单 MODTOData → production_orders**

| 本地 | U9C 字段 |
|---|---|
| order_no | `m_docNo` |
| product_name | `m_itemMaster.m_name`（空则 `m_itemMaster.m_code`） |
| （预览）数量/单位/开工/完工 | `m_productQty`、`m_productUOM.m_name`、`m_startDate`、`m_completeDate` |
| status | 见下方假设 |

**生产订单备料表 m_mOPickListDTOs → order_material_requirements（0.9.0 起，真实同步才写）**

| 本地 | U9C 字段 |
|---|---|
| 物料（按料号汇总，订单级，device_id 为空） | `m_itemMaster.m_code` / `m_itemMaster.m_name`；本地无此料号时自动建档，单位取 `m_issueUOM.m_name` |
| required_quantity | `m_actualReqQty`（缺省 `m_bOMReqQty`），同料号相加，小数向上取整 |
| 跳过 | `m_isDisable=True` 或需求量 ≤ 0 的行 |

再次同步按 U9 更新需求量（不低于已到货量），不删除本地多出的需求。订单已有按机台拆分的需求（人工导入 BOM 生成）时不写入，避免重复。U9 已发料量 `m_issuedQty` 不写入本地到货/在库。

## 5. 假设与待现场确认

1. **《接口授权》**：按 U9C 标准 `OAuth2/AuthLogin`（GET，查询串传参）实现，用户重发的授权文档已确认该格式；路径可用 `U9_AUTH_PATH` 调整。
2. **请求体格式**：swagger 标注 `application/x-www-form-urlencoded`，但示例是 JSON，默认按 `application/json` 发送，可用 `U9_CONTENT_TYPE` 改。
3. **生产订单状态**：提供的 MODTOData 字段表**没有单据状态字段**。若现场版本返回 `m_docState` / `m_status` / `DocState`，按 U9 MODocState 常见取值映射：0 开立→`DRAFT`、1 已核准→`RELEASED`、2 开工→`IN_PROGRESS`、3 完工→`COMPLETED`、4 关闭→`COMPLETED`。2026-10-14 用真实完工单实测：`MODoc/Query` 返回的 51 个顶层字段里**确实没有状态/完工数量**。没有状态字段时：新订单记为 `RELEASED`（已下达，0.9.0 前为 `IN_PROGRESS`），已有订单不改状态，需在管理台手动推进；**0.10.0 起**：同步时另调 U9 自带 `POST /webapi/QueryCommon/QueryInfoBySql` 读 `MO_MO.DocState / TotalCompleteQty`（按 `Base_Organization.Code=U9_ORG_CODE` 过滤；单号与组织编码先过白名单 `[A-Za-z0-9_.-]`，只发固定 SELECT），实库核对 2=开工、3=完工。查询失败只记错误、不影响订单头与备料同步；`U9_MO_STATE_SQL=0` 可关闭。注意：该接口允许本接口账号执行任意 SQL，建议 U9 实施方限制其权限。状态**只前进不后退**（本地已完成/已取消的不会被拉回）。
4. **BOM 版本选择**：同一母件返回多个 BOM 时，优先 `m_alternateType=0`（主制造）、`m_bOMType=0`，再取版本号最大、生效日期最新者。
5. **料品形态枚举**：`m_itemFormAttribute` 数值→中文按 U9 `ItemFormAttribute` 公开资料（9 采购件、10 制造件、4 委外加工件 …）。只有「采购件 / 委外加工件 / 制造件」会参与本地 BOM 需求生成。
6. **库存数量**：本地 `inventory.quantity` 为整数，U9 小数数量四舍五入（预览统计 `fractional_rounded` 条数）。
7. 网络层只用 Python 标准库（`urllib`），不新增依赖。
8. **现场实测（2026-10-13，0.8.0 部署后从生产服务器只读探测）**：授权登录成功（ResCode 0，约 55–75 ms，token 有效期 5 分钟）。
   库存接口「按组织整体查询」（只给 Org 或 Org+Wh）在 U9 服务器端报错：
   `未能加载文件或程序集 ufida.u9.sql.clrlib`（SQL Server CLR 程序集信任问题，需 U9 实施/运维处理）；
   给 ItemCode 的按料号查询正常。因此 0.8.1 起库存同步默认按本地料号逐个查询。
   各查询接口均为**精确匹配**（料号 `%`、空编码不返回数据；BOM 必须给母件料号）。

## 6. 排错

| 现象 | 处理 |
|---|---|
| 503 U9 集成未启用 / 连接信息不完整 | 检查环境文件变量与 `U9_ENABLED=1`，重启服务 |
| 无法连接 U9 服务器 / 超时 | 服务器到 U9C 网络不通（内网地址、防火墙、VPN），或 `U9_BASE_URL` 路径大小写不对 |
| 授权失败 401 | clientid/secret 错误或应用未启用 |
| 授权失败 601 | 把服务器出口 IP 加入 U9C 第三方应用 IP 白名单 |
| 406 / 408 | `U9_USER_CODE` / `U9_ORG_CODE` 不存在 |
| ResCode≠0 + ResMsg | U9 业务校验错误，原文显示在提示与同步记录里 |
| 库存报「ufida.u9.sql.clrlib 程序集加载失败」 | U9 数据库服务器的 SQL CLR 配置问题，联系 U9 实施/运维；期间使用默认的按料号查询 |

## 同步执行保护（0.8.2）

- 管理台 `/admin/u9` 的测试连接与同步在线程池中执行，不再阻塞 Web 事件循环；同步期间 App 接口与管理台照常响应。
- 同一时间只允许一个 U9 同步运行；重复点击会提示“已有 U9 同步在运行”，API 返回 409 `U9_SYNC_BUSY`。
- 单次同步总时限 `U9_SYNC_MAX_SECONDS`（默认 300 秒），超时主动中止并记 FAILED 日志。
- 单次编码上限 `U9_SYNC_MAX_KEYS`（默认 5000，料品/生产订单）；BOM 一次最多 `U9_BOM_MAX_PRODUCTS`（默认 20）个设备。
- 管理台请求经 nginx 转发，超过 nginx 读超时会显示 504，但同步仍在后台完成，结果可在同步日志中查看。

## 时间存储

0.9.0 起 U9 同步写入的时间（订单创建/更新、同步日志、BOM 批次）一律存 UTC ISO（带 `+00:00`），与主程序一致。此前存的是无时区的北京时间，显示时被再加 8 小时；启动迁移 `_migrate_u9_naive_times` 会把历史 U9 行改正（可重复执行）。

## 库存走 SQL 直读（0.11.0）

现场 `Invtrans/QueryQohAndAvailable` 因 U9 数据库 SQL CLR 信任问题（HRESULT 0x80FC80F1）报错。默认 `U9_INVENTORY_VIA_SQL=1`：改用 `QueryInfoBySql` 直读 `InvTrans_WhQoh`（关联 `CBO_Wh`/`CBO_Wh_Trl`/`Base_Organization`，按组织编码过滤、`StoreQty<>0`），按 料号+存储地点+库位+批号 汇总：
- 现存量 = 各存储类型 `StoreQty` 之和；
- 可用量 ≈ 存储类型 4（可用）的 `StoreQty − ResvOccupyStQty`（近似，未扣待发等）。

料号/存储地点/组织编码先过白名单再拼入固定 SELECT。实测全组织约 9600 行 0.3 秒。U9 方修好 CLR 后可设 `U9_INVENTORY_VIA_SQL=0` 回到标准接口。

## 物料分类树（0.12.0）

新增同步实体 `categories`（管理台 U9 页「物料分类」）：经 `QueryInfoBySql` 读本组织 `CBO_Category`（中文名取 `CBO_Category_Trl` zh-CN）写入 `material_categories`，父节点按“最长真前缀编码”推导（3 → 304 → 30404 → 30404001）；同时读 `CBO_ItemMaster.MainItemCategory`，按料号回填本地物料的 `category`（分类名）与 `category_code`。只改分类字段，不新增物料、不动数量。料品同步（items）也会写入分类名与编码。

仓库页分类筛选：下拉显示前两级（带物料数，含全部下级），选中后显示面包屑与下级分类统计，按编码前缀筛选。实测 1715 个分类、37427 个料号映射 0.36 秒。


## 订单 / 机台（按项目）同步

业务口径：U9 项目号（`CBO_Project.Code`，如 `JSBY-26B-001`）对应本地生产订单；项目下每张生产订单（`MO-` 开头）对应一台机台；机台物料需求取该 MO 的备料表（U9 已按 BOM×数量展开，含替代料）。没挂项目号的 MO 不导入。

- 接口：`POST /api/v1/u9/sync/projects?projects=JSBY-26B-001,JSBY-26B-002&dry_run=true`
- 写入：`production_orders`（id `u9prj-<项目号>`，状态按项目下 MO 汇总：全部完工→已完成，有开工→生产中，否则已下达，只往前走）、`devices` / `order_devices` / `assembly_tasks`（机台号 = 项目短号-机台编号，如 `26B-012-P5-12`；机台编号取 MO 产品料品私有段 2，跨项目会重复故带项目前缀；未填时退回 MO 单号；同项目重复时附 `(MO 单号)`；旧的按 MO 单号建的机台再同步时自动改号）、`order_material_requirements`（device_id = 该机台），以及机台 BOM 清单：每张 MO 的备料表写成 `bom_versions`（机型编码 = 机台号，id `u9bomv-<MO>-<版本>`，已发布；每台用量 = 备料需求 ÷ MO 生产数量）并经 `production_order_models`（id `u9pom-<MO>`，计划台数 = MO 生产数量）挂到项目订单，BOM 管理页可直接看版本、按订单聚合算缺料。备料表没变不出新版本，变了出新发布版本、旧版归档；机台改号时 BOM 机型编码跟着改。
- 本地没有的料号自动建物料档案；已有需求只更新需求量（不低于已到货量），不删除本地多出的行。
- 管理台：U9 集成页「订单 / 机台（按项目）」卡片，或查找页按年份/项目号查出后一键同步。
- 测试环境清理：查找页按前缀清理，同时清 `u9mo-`（旧口径按 MO 导入）与 `u9prj-` 订单及其机台、装配任务、U9 建的机台档案。
