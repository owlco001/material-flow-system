# 设计同步 API 契约（后端 0.22.0）

桌面设计端把「设计包」（zip + manifest）推送到智慧工厂，做版本归档、版本比对，并按需绑定到机台 / 物料，
让包内图纸出现在现有 App 图纸接口、`model.glb` 成为机台 3D 模型。

- 路由前缀：`/api/v1/design-sync`，实现 `material-flow-backend/app/design_sync.py`，测试 `tests/test_design_sync.py`
- 与既有接口互不影响：不使用用户 Bearer / 管理台会话，既有 `/api/v1/*` 行为不变
- 回滚：下线 `design_sync` 路由即可；新表独立（`design_keys`、`design_packages`、`design_package_files`、
  `design_package_drawings`、`design_package_bindings`），由 `init_db` 以 `CREATE TABLE IF NOT EXISTS` 建立

## 1. 鉴权：`X-Design-Key`

| 项 | 规则 |
|----|------|
| 生成 | 管理台 `/admin/design-packages`（仅 ADMIN）→「生成密钥」，明文**只显示一次** |
| 格式 | `mfds_<8 位十六进制前缀>_<随机串>`，如 `mfds_1a2b3c4d_xxxxxxxx…` |
| 存储 | 只存前缀（查找）+ Argon2id 哈希（与用户密码同一哈希器），不存明文 |
| 吊销 | 管理台「吊销」，立即生效，不可恢复 |
| 机台范围 | 可选，管理台按机台编号填写，服务端存 `devices.id`；为空 = 全部机台 |
| 审计 | 写入现有 `audit_logs` / `audit_events`，`actor_user_id` / `operator_id` = `design-key:<keyId>`，角色 `DESIGN_KEY` |

缺失、格式不对、不存在、哈希不匹配、已吊销 → **401** `DESIGN_KEY_INVALID`（不区分原因）。

## 2. 来源 IP 网段（取代 HTTPS 要求）

内网明文 HTTP 部署，不强制 HTTPS。每个密钥带一个来源网段列表 `ip_scope`（CIDR），不在其中 → **403** `DESIGN_KEY_IP_FORBIDDEN`。

- 默认：`10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, ::1/128, fc00::/7`
- 管理台可按密钥收紧（每行一个 CIDR，至少一个，最多 64 个）；需要放开时显式填写网段
- 来源地址取 `request.client.host`（与现有审计 `source_ip` 同口径）。生产 uvicorn 只监听 `127.0.0.1`，
  前置 nginx/Caddy；uvicorn 0.34 默认只信任来自 `127.0.0.1` 的 `X-Forwarded-For` 并据此还原客户端地址。
  本接口**不自行解析** `X-Forwarded-For`
- IPv4-mapped IPv6（`::ffff:10.1.2.3`）按 IPv4 判断；无法解析的地址一律拒绝
- 附加规则（只收紧）：请求带 `Cf-Connecting-Ip`（经 Cloudflare 隧道）时，该地址也必须在网段内。
  这样公网流量经 cloudflared → 本机反代进来、对端显示为 `127.0.0.1` 时仍会被拒

## 3. 端点

所有响应带 `traceId`；错误统一为 `{"error": {code, message, retryable, traceId, details?}}`。

### 3.1 上传设计包 `POST /packages`

```
Content-Type: multipart/form-data
X-Design-Key: mfds_…
Idempotency-Key: <zip 文件的 SHA-256，64 位十六进制>

deviceId   机台编号 devices.device_no（也接受 devices.id），服务端解析
manifest   JSON 字符串（见 §4），≤ 1 MB
file       zip，≤ 200 MB
```

| 结果 | 状态码 | 响应体 |
|------|--------|--------|
| 新包 | **201** | `{packageId, version, uploadedAt, duplicated:false, deviceId, deviceNo, projectId}` |
| 相同 zip 已在同一机台 | **200** | 同上，`duplicated:true`，返回已有包，库中仍只一条 |

- `version` 按 (机台, projectId) 自增，从 1 开始
- 同一份 zip（SHA-256 相同）已上传到**其他机台** → 409 `DESIGN_PACKAGE_DEVICE_CONFLICT`（`details.packageId` 给出已有包，请用 bind 绑定）
- 存储：`$MATERIAL_FLOW_DATA/design_packages/<packageId>/package.zip`（原件）+ `files/<manifest 路径>`
- 所有校验在写盘前完成，任何 4xx 都不在存储目录留下文件
- 表单里的 `manifest` 是权威来源；zip 里的 `manifest.json` 及 manifest 未声明的条目只随原件归档、不解压

### 3.2 列表 `GET /packages?deviceId=&projectId=&limit=20`

`limit` 1..100。受机台范围限制的密钥只看到范围内机台的包。

```json
{"items": [{"packageId","deviceId","deviceNo","projectId","projectName","version","bomSha256","zipSha256",
            "zipSize","fileCount","createdAt","uploadedAt","uploadedBy"}]}
```

`deviceId` = `devices.id`（内部 ID），`deviceNo` = 机台编号；`createdAt` 为 manifest 原值，`uploadedAt` 为服务器 UTC 时间。

### 3.3 详情 `GET /packages/{packageId}`

列表字段 + `appVersion`、`files[] {path, kind, sha256, size}`、`bom[]`（manifest 原样）、`bindings[]`。

### 3.4 比对 `GET /packages/{packageId}/diff?against=latest|previous|<packageId>`

| against | 基线 |
|---------|------|
| `latest`（默认） | 同机台同项目中**除本包外**版本号最大的包 |
| `previous` | 同机台同项目中版本号小于本包的最大版本 |
| `<packageId>` | 指定包（须在密钥机台范围内） |

方向为「基线 → 本包」。无基线时 `base:null`，本包全部视为新增。

```json
{"packageId","version","base":{"packageId","version"},
 "bomAdded":[<行>], "bomRemoved":[<行>], "bomChanged":[{"no","field","from","to"}],
 "filesAdded":[{path,kind,sha256,size}], "filesRemoved":[…],
 "filesChanged":[{"path","kind","fromSha256","toSha256","fromSize","toSize"}]}
```

BOM 以 `no` 为键（`1` 与 `"1"` 视为同一行），`bomChanged` 对每个变化字段各一条；文件以 `path` 为键、按 `sha256` 判断变更。

### 3.5 绑定 `POST /bind`

```json
{"packageId": "dsp_…", "targetType": "DEVICE" | "MATERIAL", "targetKey": "机台编号/ID 或 物料编码"}
→ 200 {"bound":true, "alreadyBound":false, "packageId", "targetType", "targetKey",
       "drawings":[{"drawingId","path","status"}], "model":{"modelCode","version"}|null, "warnings":[]}
```

- 包内 `PDF` / `DXF`：用现有图纸入库流程注册为图纸（拆页、缩略图），写 `drawing_bindings`；
  标题 `<项目名> · <文件名>`，图号 = projectId，版本 = `V<version>`。同一包的同一文件只入库一次，绑定多个目标时复用
  → 现有 `GET /api/v1/devices/{id}/drawings`、`GET /api/v1/materials/{code}/drawings` 直接可见
- DXF 需服务器配置 `DRAWING_CAD_TO_PDF`，否则状态 `UNSUPPORTED`，终端不显示（`warnings` 会说明）
- `GLTF`（仅 `.glb`，≤ 15 MiB，校验 `glTF` 魔数），**仅 DEVICE 绑定**：注册为 `assembly_model_versions` 新的已发布版本，
  机型码 `DS-<机台编号>`（非法字符替换为 `-`），并把 `devices.model_3d_code` 设为该机型码 → App 现有 3D 模型接口可见。
  同一 GLB（sha256 相同）不重复建版本；超限/非 GLB 只归档并在 `warnings` 说明
- `STEP`、`BOM`：只归档
- 幂等：同一 (包, 类型, 目标) 再次绑定返回 `alreadyBound:true` 与首次结果，不重复入库
- 目标机台须在密钥机台范围内（否则 403）；机台 / 物料不存在 → 404

### 3.6 下载原件 `GET /packages/{packageId}/download`

`application/zip`，文件名 `<projectId>-v<version>.zip`，`ETag` = zip SHA-256。

## 4. manifest（schemaVersion 1）

```json
{
  "schemaVersion": 1,
  "projectId": "asm-slide-cyl",
  "projectName": "滑台气缸装配体",
  "appVersion": "0.2.0",
  "createdAt": "2026-10-22T14:00:00Z",
  "bomSha256": "…64 位十六进制…",
  "files": [{"path": "bom.csv", "sha256": "…", "size": 1234, "kind": "BOM"}],
  "bom": [{"no": 1, "name": "底板", "spec": "BASE-01", "qty": 1, "mat": "AL6061"}]
}
```

| 字段 | 规则 |
|------|------|
| `schemaVersion` | 必须为整数 `1` |
| `projectId` | 必填，1–128 位字母 / 数字 / 汉字 / `._-` |
| `projectName` | 可选，≤ 200 |
| `appVersion` | 可选，≤ 64 |
| `createdAt` | 必填，**ISO 8601 且必须带时区**（`Z` 或 `+08:00`）；缺时区、空格分隔、时间戳数字均 400 |
| `bomSha256` | 可选，64 位十六进制，仅存档不校验 |
| `files[]` | ≤ 500 项；`path` 相对路径、唯一（不区分大小写）；`sha256` 64 位十六进制；`size` 非负整数；`kind` ∈ `BOM/PDF/STEP/GLTF/DXF` |
| `bom[]` | ≤ 10000 行；`no` 整数或非空字符串且唯一；**`qty` 必须是 JSON 正整数**（`1.5`、`1.0`、`"2"`、`true`、`0` 均 400）；`name/spec/mat` 若给出须为字符串；其他字段原样保留并参与比对 |

设计包 BOM **只做归档与比对**，不写入 `bom_versions` / `bom_items` / `materials`。

## 5. 限制与安全校验

| 项 | 上限 / 规则 | 超出 |
|----|-------------|------|
| zip 大小 | 200 MB（流式落临时文件并计数，不读入内存；Content-Length 超限直接拒） | **413** `DESIGN_PACKAGE_TOO_LARGE` |
| manifest | 1 MB | **413** `DESIGN_PACKAGE_TOO_LARGE` |
| zip 条目数 | 500 | 400 `DESIGN_PACKAGE_INVALID` |
| 解压总量 | 1 GB（按条目声明大小累计 + 实际读取计数，防 zip bomb） | 400 `DESIGN_PACKAGE_INVALID` |
| 路径 | 拒绝绝对路径、盘符、反斜杠、空段、`.`、`..`、控制字符 | 400 `DESIGN_PACKAGE_UNSAFE_PATH` |
| 符号链接 / 加密条目 / 重复条目 | 拒绝 | 400 |
| 文件校验 | manifest 每个文件必须在 zip 中，大小与 SHA-256 一致 | 400 `DESIGN_PACKAGE_FILE_MISMATCH` |

## 6. 错误码汇总

| 状态 | code | 场景 |
|------|------|------|
| 400 | `VALIDATION_ERROR` | `Idempotency-Key` 缺失或不是 64 位十六进制 |
| 400 | `IDEMPOTENCY_KEY_MISMATCH` | `Idempotency-Key` ≠ zip 实际 SHA-256 |
| 400 | `DESIGN_MANIFEST_INVALID` | manifest JSON / 字段不合规（含非整数 qty、无时区 createdAt） |
| 400 | `DESIGN_PACKAGE_INVALID` | 非 multipart、缺字段、不是 zip、条目数 / 解压总量超限、加密条目 |
| 400 | `DESIGN_PACKAGE_UNSAFE_PATH` | 路径穿越、绝对路径、符号链接 |
| 400 | `DESIGN_PACKAGE_FILE_MISMATCH` | 文件缺失、大小或 SHA-256 不一致 |
| 401 | `DESIGN_KEY_INVALID` | 密钥无效 / 已吊销 |
| 403 | `DESIGN_KEY_IP_FORBIDDEN` | 来源地址不在网段内 |
| 403 | `DESIGN_KEY_DEVICE_FORBIDDEN` | 机台不在密钥机台范围内 |
| 404 | `DEVICE_NOT_FOUND` / `MATERIAL_NOT_FOUND` / `DESIGN_PACKAGE_NOT_FOUND` | 对象不存在（范围外的包也返回 404） |
| 409 | `DESIGN_PACKAGE_DEVICE_CONFLICT` | 同一 zip 已上传到其他机台 |
| 413 | `DESIGN_PACKAGE_TOO_LARGE` | zip > 200 MB 或 manifest > 1 MB |

## 7. 管理台「设计包」（`/admin/design-packages`，仅 ADMIN）

- 列表（按机台编号 / 项目筛选）、详情（文件、BOM、与上一版比对、绑定记录）、下载原件、绑定到机台 / 物料
- 密钥管理：生成（明文只显示一次）、修改名称 / 机台范围 / 来源网段、吊销
- 复用现有布局、侧边栏（主数据 → 设计包）、会话、CSRF；管理台绑定的操作人记为当前管理员
