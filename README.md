# 厂内物料流转系统

厂内物料流转系统由 FastAPI 后端和 Android 客户端组成，面向生产现场的物料状态、库存和流转协同。系统支持导入三类基础业务表：BOM、库存和生产订单；在此基础上提供 BOM 需求、库存分布、生产订单与细分机型管理，以及入库、出库、调拨、盘点和交接记录。

核心业务包括：

- BOM、库存、生产订单三表导入与校验。
- 生产订单、细分机型、物料需求和库存的关联查询。
- 按订单和机型计算物料齐套情况，展示缺料、到料、在库和领料状态。
- 厂内物料入库、出库、调拨、盘点、审批、交接和审计留痕。
- Android 端扫码查询、现场操作和离线操作同步。

系统边界是厂内物料流转：覆盖生产订单到厂内库位、仓储及生产现场交接的状态和记录；不包含外部运输、承运商、物流轨迹，也不把线边库或配送工位作为本 V1 的业务对象。

## 目录

- `material-flow-backend/`：FastAPI 服务、SQLite 数据库初始化、测试和 systemd 单元。
- `logistics-android/`：Android 客户端。
- `material-flow-backend/docs/DEPLOYMENT.md`：后端部署包契约和发布检查清单。
- `logistics-android/docs/device-acceptance.md`：Android 构建与设备验收说明。

## 部署后端

以下步骤使用仓库现有文件和命令。示例服务地址只绑定本机回环地址；对外访问时应由现有反向代理和网络策略提供访问控制与 TLS。

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
