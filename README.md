# 厂内物料流转系统

厂内物料流转系统由 FastAPI 后端和 Android 客户端组成，面向生产现场的物料状态、库存和流转协同。系统支持导入三类基础业务表：BOM、库存和生产订单；在此基础上提供 BOM 需求、库存分布、生产订单与细分机型管理，以及入库、出库、调拨、盘点和交接记录。

核心业务包括：

- BOM、库存、生产订单三表导入与校验。
- 生产订单、细分机型、物料需求和库存的关联查询。
- 按订单和机型计算物料齐套情况，展示缺料、到料、在库和领料状态。
- 厂内物料入库、出库、调拨、盘点、审批、交接和审计留痕。
- Android 端扫码查询、现场操作和离线操作同步。

系统边界是厂内物料流转：覆盖生产订单到厂内库位、仓储及生产现场交接的状态和记录；不包含外部运输、承运商、物流轨迹，也不把线边库或配送工位作为本 V1 的业务对象。

## 功能总览（全图版）

> 以下截图均为系统实拍。三端协同：**Android 移动端**（车间现场作业）+ **Web 管理后台**（管理侧治理）+ **FastAPI 后端**（单服务双出口：REST API `/api/v1` + 管理台 `/admin`），同一数据库、同一套 RBAC 角色、同一条审计流水。

### 移动端：车间现场作业

#### 角色化工作台 + 统一扫码

<table>
<tr>
<td width="50%"><img src="docs/screenshots/app-workspace.png" alt="工作台"></td>
<td width="50%"><img src="docs/screenshots/app-scan.png" alt="扫码"></td>
</tr>
</table>

- 五类角色（管理员 / 物料员 / 操作工 / 库管 / 装配工）分栏呈现，角色可切换试览；服务端地址在 App 内可视化配置。
- 一码到底：物料码、订单码、机台码、库位码、流转码统一由服务端判别并直达对应页面；扫码结果先确认再进入，防误扫。

#### 生产订单与齐套分析

<table>
<tr>
<td width="50%"><img src="docs/screenshots/app-order-detail.png" alt="订单详情"></td>
<td width="50%"><img src="docs/screenshots/app-order-materials.png" alt="订单物料"></td>
</tr>
</table>

- 订单头卡：中文状态徽章（已下达 / 待领料 / 生产中 / 已完成…）、齐套率、缺料项数、服务端时间。
- 单订单 118 台机台的装配任务分页浏览：阶段进度、任务版本、责任人一目了然。
- 物料需求清单使用 U9 真实物料编码，支持扫码定位。
- 流转时间线：每次下达、领料、审批的操作人与时间全量留痕展示。

#### 机台详情：装配作业全息视图

<table>
<tr>
<td width="50%"><img src="docs/screenshots/app-device-detail.png" alt="机台详情"></td>
<td width="50%"><img src="docs/screenshots/app-device-personnel.png" alt="装配进度与人员"></td>
</tr>
</table>

- 一页聚合：机台状态、物料情况、流转申请、装配进度、工时、人员、关联订单、3D 模型入口。
- 物料按电气 / 机械 / 其他自动归类，支持搜索、分类筛选、分页与横向表格。
- 勾选多条物料直接发起批量流转申请；装配人员按账号去重展示（服务端按机台精确过滤）。

#### Filament 3D 机台查看器

<table>
<tr>
<td width="33%"><img src="docs/screenshots/app-3d.png" alt="3D 模型"></td>
<td width="33%"><img src="docs/screenshots/app-3d-parts.png" alt="零件导航"></td>
<td width="33%"><img src="docs/screenshots/app-3d-part.png" alt="零件定位"></td>
</tr>
</table>

- 基于 Filament（Google 渲染引擎）加载 glTF/GLB 机台模型，网格地面 + 轨道环绕相机。
- 零件导航清单可搜索，点选即 3D 定位：目标零件高亮、其余半透明（ghost 材质）。
- 爆炸图滑杆 0-100% 无级拆解；模型由 Web 后台上传并与机台型号绑定。

#### 库存查询 + 审批中心

<table>
<tr>
<td width="50%"><img src="docs/screenshots/app-inventory.png" alt="库存"></td>
<td width="50%"><img src="docs/screenshots/app-approval.png" alt="审批"></td>
</tr>
</table>

- 扫料号即查实时库存与库位分布，可就地绑定库位、发起流转申请。
- 流转申请、盘点确认、异常审核、交接确认四类待办集中审批；按钮由服务端状态机驱动，驳回必填原因，批准 / 执行二次确认。

#### 我的：权限、离线与可追溯

<p><img src="docs/screenshots/app-profile.png" alt="我的" width="320"></p>

- 权限范围逐条可视化；离线队列断网续传（幂等键保证不重不漏）；我的异常与我的流转随时回查。

### Web 管理后台

#### 订单管理 + 条码工场 + 装配派工

<p><img src="docs/screenshots/web-orders.png" alt="订单管理"></p>

- 新建生产订单（多机型多数量）、状态变更；订单 / 机台二维码与 Code128 条码一键生成并批量打印。
- 订单下机台卡片直接展示进度与装配工分配状态；机台任务详情页可分配 / 移除装配工（主装配工 + 协同成员），与 App 实时同步。

#### 装配工作台与工时报表

<table>
<tr>
<td width="50%"><img src="docs/screenshots/web-workspace.png" alt="装配工作台"></td>
<td width="50%"><img src="docs/screenshots/web-labor.png" alt="工时报表"></td>
</tr>
</table>

- 全厂装配任务状态总览，按状态 / 订单过滤，一键导出 Excel。
- 按机台 / 订单 / 装配工多维查询装配工时，支持导出；配机台作业报表。

#### 仓库治理 / 流转与交接

<table>
<tr>
<td width="50%"><img src="docs/screenshots/web-warehouse.png" alt="仓库"></td>
<td width="50%"><img src="docs/screenshots/web-flows.png" alt="流转交接"></td>
</tr>
</table>

- 库存 / 库位全景查询，盘点确认、异常审核页面闭环。
- 流转申请审批 / 驳回 / 执行全流程页面化；交接单状态跟踪。

#### BOM / 3D 模型 / 审计 / 用户

<table>
<tr>
<td width="50%"><img src="docs/screenshots/web-boms.png" alt="BOM"></td>
<td width="50%"><img src="docs/screenshots/web-models.png" alt="模型"></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/web-audit.png" alt="审计"></td>
<td width="50%"><img src="docs/screenshots/web-users.png" alt="用户"></td>
</tr>
</table>

- BOM：Excel 上传 → 校验预览 → 确认发布，版本化存档（草稿 / 已发布 / 归档），支持聚合 BOM。
- 3D 模型：GLB 上传（SHA-256 校验）、与机台型号绑定、浏览器内直接预览。
- 审计：全量操作留痕（谁、何时、对什么数据、做了什么），多条件检索 + Excel 导出。
- 用户：账号增删改、角色分配、密码重置、会话吊销（强制下线）。

#### U9 ERP 集成与 AI 智能助手

<table>
<tr>
<td width="50%"><img src="docs/screenshots/web-u9.png" alt="U9"></td>
<td width="50%"><img src="docs/screenshots/web-agent.png" alt="AI 助手"></td>
</tr>
</table>

- U9 同步：物料 / 生产订单 / 库存两段式同步（先试跑 dry-run 再提交），主数据与车间系统一致。
- AI 助手：LLM 数据问答（如"哪些物料库存不足"）；CSV / Excel 智能导入——自动解析字段、生成预览，人工确认后入库。

### 典型业务闭环

| 步骤 | 角色 / 端 | 动作 |
|------|-----------|------|
| 1 | 装配工 · App | 机台详情勾选缺料物料 → 批量提交流转申请 |
| 2 | 管理员 · App / Web | 审批中心收到待办 → 批准（或驳回并填写原因） |
| 3 | 物料员 · App | 按申请出库拣料 → 发起交接，生成流转码 |
| 4 | 接收方 · App | 扫码确认交接 → 库存扣减（乐观锁校验）→ 状态闭环 |
| 5 | 系统 · 后端 | 全程审计事件入库，时间线可回放 |

## 目录

- `material-flow-backend/`：FastAPI 服务、SQLite 数据库初始化、测试和 systemd 单元。
- `logistics-android/`：Android 客户端。
- `material-flow-backend/docs/DEPLOYMENT.md`：后端部署包契约和发布检查清单。
- `material-flow-backend/docs/admin-web-contract.md`：Web 管理台路由与 wire-format 契约（管理面唯一事实源）。
- `material-flow-backend/docs/order-creation-contract.md`：生产订单创建/状态推进内核契约。
- `material-flow-backend/deploy/bootstrap.sh`：后端一键部署脚本（含管理员设定）。
- `logistics-android/docs/device-acceptance.md`：Android 构建与设备验收说明。

## Web 管理台

服务自带 Web 管理台（`/admin/login`），登录后进入异常处理页，按登录角色展示：管理员全量管理面（工作台/异常处理/概览/工时/任务/流转/留痕/仓库/模型/BOM/订单/用户/审计/条码）、物料员出库视图、操作员领取/到工位视图、仓库管理员审批/交接视图。覆盖用户管理（含密码重置与强制下线）、流转审批与留痕（默认显示最近交接列表）、出库执行、工时与车间概览、BOM 两段式导入与聚合 BOM（智能导入）展示、装配模型上传与机台 3D 绑定、生产订单创建与状态推进（含 118 台机台列表搜索/分页/标色）、异常处理（异常单+导入错误聚合）、CSV 导出和移动端适配。各列表页统一支持搜索、状态筛选、分页，状态中文显示+标色。所有写操作与 APP 共用同一套 API 处理函数与审计/幂等语义，wire-format 见 `docs/admin-web-contract.md`。

## 智能助手（Agent）

管理后台集成智能助手（`/admin/agent`），提供数据分析对话与智能导入。Web 端每个页面右下角有助手悬浮球（App 端不加）。

### 智能导入

支持物料、库存、订单三类表格导入，流程为"上传→预览→确认提交"，幂等写入：

- **智能表头检测**：自动跳过标题行，定位真正的表头行。
- **智能列识别**：关键词打分制自动映射列（如"料号"→物料编码、"现存量"→数量、"库位编码"→库位），无需手动补映射；有防抢列逻辑（如 `T6料号` 不会抢 `料号`）。
- **空行自动跳过**：不计为错误。
- **错误行自动记录**：校验失败的行自动写入 `agent_import_error_logs` 表（状态 PENDING 待人工处理），不阻塞有效行的提交。
- **自动建档**：物料不存在时自动创建物料档案，库位不存在时自动创建库位。
- **聚合 BOM 导入**：支持 ERP 导出的多层级、多设备聚合 BOM（如 26B-012 项目文件），自动生成生产订单、机台档案、装配任务、物料需求。

### 数据分析对话

基于 LLM（默认 DeepSeek 兼容接口，可配 MiMo 等）的 ReAct 对话，10 个只读数据分析工具，配置见环境变量 `AGENT_LLM_API_KEY` 等。

## 用友 U9 ERP 集成

`material-flow-backend/app/u9/` 提供与用友 U9 的数据同步骨架，目标是替代"U9 导出 Excel → 人工导入"的手工链路。

- **分期**：Phase 1 只读同步（U9 → 本地：料品档案、BOM、库存现存量、生产订单）；Phase 2 回写（完工汇报、领料出库，待实施商确认业务校验规则）。
- **默认关闭**：`U9_ENABLED=0` 时所有同步接口返回 503，不写任何数据。
- **安全**：连接信息与凭证只从环境变量读取（`U9_WSDL_URL`/`U9_USERNAME`/`U9_PASSWORD` 等），不落盘、不进日志；管理接口返回脱敏配置。
- **同步语义**：接口默认 `dry_run=true`（只预览不写库）；真实写入为幂等 upsert（按 `materials.code`、`production_orders.order_no` 等自然键）；每次运行写入 `u9_sync_logs`。
- **管理接口**（需 ADMIN）：`GET /api/v1/u9/status`（配置状态+最近同步）、`POST /api/v1/u9/sync/{items|boms|inventory|orders}`、`GET /api/v1/u9/logs`。

当前状态：模块骨架已就绪，SOAP/REST 客户端与 U9 字段映射待实施商提供《接口开发文档》、WSDL 地址与接口凭证后填充。过渡期 Excel 导入保留作为兜底。

## 部署后端

推荐使用一键部署脚本（在仓库内以 root 执行）：

```bash
cd material-flow-backend
bash deploy/bootstrap.sh
```

脚本完成：运行目录与虚拟环境、依赖安装、**管理员设定**（交互式输入初始密码，不回显，写入 0600 环境文件；用户名 `owlco`，首次登录强制改密）、数据库迁移、systemd 服务、nginx 反代（含模型上传所需 `client_max_body_size 32m` 与根路径 302）和冒烟检查。试装可用 `--no-service --no-nginx`（数据目录自动锚定到本实例，互不干扰）；自动化场景用 `--admin-password-stdin` 从 stdin 喂一次性密码。

管理员账号后续维护使用管理 CLI（密码只经 getpass/stdin，不进命令行参数）：

```bash
.venv/bin/python -m app.manage_admin create --employee-no <工号> --name <姓名>
.venv/bin/python -m app.manage_admin reset-password --employee-no <工号>
.venv/bin/python -m app.manage_admin list
```

以下为等价的手动部署步骤。示例服务地址只绑定本机回环地址；对外访问时应由现有反向代理和网络策略提供访问控制与 TLS（当前部署形态为内网 HTTP）。

### 1. 准备运行目录和虚拟环境

将 `material-flow-backend/` 作为部署目录，然后在该目录执行：

```bash
cd material-flow-backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` 固定 FastAPI、`uvicorn[standard]`、`python-multipart` 和 `argon2-cffi` 运行依赖。不要把环境文件、密码、数据库文件、上传文件或日志提交到 Git。

### 2. 配置服务环境

systemd 单元从外部环境文件读取配置。创建 `/etc/material-flow/material-flow.env`，至少准备首次初始化管理员所需的 `INITIAL_ADMIN_PASSWORD`；密码只写入该受保护文件，不要写入命令行、仓库或日志。需要调整运行数据目录时，可设置 `MATERIAL_FLOW_DATA` 和 `MATERIAL_FLOW_UPLOADS`。

例如，环境文件可以只包含变量名和由部署者安全填入的值：

```dotenv
INITIAL_ADMIN_PASSWORD=<set-outside-git>
# MATERIAL_FLOW_DATA=/srv/material-flow/data
# MATERIAL_FLOW_UPLOADS=/srv/material-flow/uploads
```

创建服务用户可写的数据和上传目录，并确保部署目录与环境文件的权限符合本机安全策略。仓库中的 systemd 单元默认工作目录为 `/srv/material-flow`，部署到其他目录时需同步调整该单元中的 `WorkingDirectory`、`.venv` 和 `ReadWritePaths`。

### 3. 初始化数据库并启动 API

数据库由服务启动流程初始化，不需要手工创建 SQLite 文件。首次启动时，`ExecStartPre` 会运行迁移；应用启动时仍保留初始化安全网。直接从后端目录手工验证时可执行：

```bash
cd material-flow-backend
.venv/bin/python -m app.migrate
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

安装仓库已有的 service 文件并启动：

```bash
sudo install -m 0644 material-flow.service /etc/systemd/system/material-flow.service
sudo systemctl daemon-reload
sudo systemctl enable --now material-flow
sudo systemctl status material-flow
```

该单元实际执行：

```text
ExecStartPre=.venv/bin/python -m app.migrate
ExecStart=.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl --fail --silent http://127.0.0.1:8000/healthz
```

预期响应包含 `status: "ok"`、`service: "material-flow"` 和 `database: "ok"`。完整的发布检查、迁移和测试数据说明见 [`material-flow-backend/docs/DEPLOYMENT.md`](material-flow-backend/docs/DEPLOYMENT.md)。

## 构建 Android 客户端

客户端构建使用仓库自带 Gradle Wrapper。API 地址通过构建期属性 `apiBaseUrl` 或环境变量 `API_BASE_URL` 注入，源码不会落真实服务端点。使用本地后端时：

```bash
cd logistics-android
API_BASE_URL=http://127.0.0.1:8000 ./gradlew --no-daemon :app:assembleDebug
```

也可以显式传入 Gradle 属性：

```bash
./gradlew --no-daemon :app:assembleDebug -PapiBaseUrl=<API_BASE_URL>
```

构建产物位于 `app/build/outputs/apk/debug/app-debug.apk`。在 Android 真机上访问开发机时，`127.0.0.1` 指向手机本身；应改为测试环境可达的 `<API_BASE_URL>`，且不要把真实地址、凭据或 token 写进仓库。

## 测试与验证

后端测试从 `material-flow-backend/` 执行：

```bash
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m pytest -q
.venv/bin/python tests/test_auth_security.py
.venv/bin/python tests/test_contract_acceptance.py
.venv/bin/python tests/test_order_material_status.py
.venv/bin/python tests/test_token_lifecycle.py
```

Android 单元测试和 Debug 构建从 `logistics-android/` 执行：

```bash
./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

文档或发布前检查：

```bash
git diff --check
```
