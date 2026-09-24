# Web 管理界面 —— 可执行契约（admin-web-contract.md）

状态：S1（骨架/登录/守卫）实现中；S2–S5 待做。任何切片的文件边界与验收门禁以本契约为准。
本文件为唯一 wire-format 来源：禁止实现者自行发明表单字段名、Cookie 名、状态码或重定向目标。

## 1. 目标与范围

为厂内物料流转系统提供浏览器端管理界面（服务端渲染），面向 ADMIN（管理员）与
WAREHOUSE_ADMIN（仓库管理员）。业务主线：生产订单—细分机型—物料—厂内库位流转；
所有管理操作沿用现有留痕/审计语义，不新建业务事实表。

| 切片 | 内容 | 准入角色 | 状态 |
|------|------|----------|------|
| S1 | 契约文档、登录/登出、Cookie 会话、CSRF、鉴权守卫、仪表盘骨架 | ADMIN | 进行中 |
| S2 | 用户管理（列表/新建/编辑/重置密码/删除），复用 /admin/users 服务语义 | ADMIN | 待做 |
| S3 | 流转申请/交接留痕查询与审批（transfer_requests、handovers timeline） | ADMIN + WAREHOUSE_ADMIN | 待做 |
| S4 | 工时汇总（人员/任务/机台/订单）与车间概览 | ADMIN + WAREHOUSE_ADMIN | 待做 |
| S5 | 装配模型管理（版本/发布查询） | ADMIN | 待做 |

明确排除：外部物流、线边库、配送工位；APP 端 /api/v1/* JSON 契约一律不动。

## 2. 技术选型与理由

- **Jinja2 服务端渲染（SSR）+ 原生 HTML 表单**：管理界面为内部表单/表格密集型工具，
  SSR 免去前端构建链，模板渲染与表单提交可直接用现有 pytest + TestClient 全覆盖。
- **Cookie 会话（独立 web_sessions 表）**：APP 的 sessions 表承载 Bearer token +
  refresh rotation（consumed_refresh_tokens）语义，浏览器会话不与其混用生命周期，
  避免污染 APP 会话契约；web_sessions 仅服务本界面。
- **CSRF：每会话随机 csrf_token，所有 POST 表单携带并用 hmac.compare_digest 比对**。
- **密码处理**：登录仅校验；重置密码走既有 Argon2id 语义（S2 起），任何响应/模板
  均不回显密码明文、不读取旧密码。POST 体内的密码字段一律不进入日志/渲染上下文。
- 依赖新增：`jinja2`（仅此一项；版本随 requirements.txt 锁定）。

## 3. 模块图（文字版）

```
浏览器(桌面)
   │  GET/POST /admin/*（HTML + Cookie）
   ▼
app/main.py ──挂载──▶ app/admin_web.py（本切片新增，全部页面路由）
                        │  Depends: web_current_admin(request)
                        │  ├─ 校验 Cookie mf_session → web_sessions.expires_at
                        │  ├─ join users：active=1 且 role='ADMIN'（S3 起放宽 WAREHOUSE_ADMIN）
                        │  └─ 校验 POST 的 csrf_token（hmac.compare_digest）
                        ▼
                     app.main 服务层/SQL（复用现有查询与写语义，不复制业务事实）
                        ▼
                     SQLite（DATA_DIR/material_flow.db；web_sessions 为新表）
```

依赖方向：admin_web.py → main.py（单向；main.py 仅 import 并 include_router，不反向依赖模板细节）。

## 4. 数据结构（DDL；进 init_db()，幂等建表）

```sql
CREATE TABLE IF NOT EXISTS web_sessions (
  id           TEXT PRIMARY KEY,            -- secrets.token_urlsafe(32)
  user_id      TEXT NOT NULL REFERENCES users(id),
  csrf_token   TEXT NOT NULL,               -- secrets.token_urlsafe(32)
  created_at   TEXT NOT NULL,               -- ISO8601 UTC，沿用 now()
  expires_at   INTEGER NOT NULL,            -- unix 秒；固定 8 小时，不滑动续期
  last_seen_at INTEGER NOT NULL             -- unix 秒；仅观测用，不延长有效期
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id);
```

## 5. 会话/安全规则

1. Cookie 名固定 `mf_session`，属性：`HttpOnly; SameSite=Lax; Path=/`（Secure 由 HTTPS
   部署层保证，开发明文仅限本机测试）。
2. 会话有效期固定 8 小时（`expires_at = now_epoch + 8*3600`），到期即失效，不滑动。
3. 登录失败一律返回 `error=1`，不区分「用户不存在/密码错误/账号停用」。
4. 登出删除 web_sessions 行并使 Cookie 过期（Max-Age=0）。
5. 过期/不存在的会话一律 303 → `/admin/login`。
6. 非准入角色（已登录但 role 不符）返回 403 页面，不重定向登录。
7. POST 未携带或不匹配 csrf_token → 403，不产生任何写入。
8. 密码字段不回显、不入日志；错误文案统一「用户名或密码错误」。

## 6. 路由签名与精确响应（S1）

统一约定：303 See Other 用于 POST 后跳转；Location 头精确如列。

- `GET /admin/login`
  - 无会话：200 HTML（base 继承，含 username/password/csrf 表单）。
  - 有有效会话：303 → `/admin/`
- `POST /admin/login`（application/x-www-form-urlencoded）
  - 字段：`username`、`password`、`csrf_token`（匿名 CSRF：= 登录页渲染时种入的
    `anon_csrf` Cookie 值；见 §6.1）
  - 成功：303 → `/admin/`；Set-Cookie `mf_session=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800`
  - 失败：303 → `/admin/login?error=1`
- `POST /admin/logout`（表单；带 csrf_token）
  - 成功：303 → `/admin/login`；Set-Cookie `mf_session=; ...; Max-Age=0`
- `GET /admin/`
  - 需 ADMIN 会话；否则 §5.5/§5.6
  - 200 HTML 仪表盘：当前用户显示名、APP_VERSION、三张计数卡片
    （users 总数/active 数、transfer_requests 中 PENDING_APPROVAL 数、
    handovers(material_handovers) 中 PENDING 数）——SQL COUNT，无业务写入。
- `POST /admin/unauthorized`（不提供）；403 即内联 HTML 页面。

### 6.1 匿名 CSRF

登录表单自身需要防跨站伪造：`GET /admin/login` 同时种 `anon_csrf` Cookie
（`HttpOnly; SameSite=Lax; Path=/admin`；未过期会话访问会 303，故匿名态才下发），
表单 `csrf_token` 字段= 该 Cookie 值；POST 用 hmac.compare_digest 校验后即轮换。

## 7. 幂等与事务规则（S1）

- S1 无业务写入（仅会话行的插入/删除，天然幂等：INSERT 一次、DELETE 可重放）。
- S2 起沿用既有 `clientOperationId` + `Idempotency-Key` 语义，与 /api/v1/admin/users
  契约一致（重放返回 idempotent=true，冲突 409）。

## 8. 测试断言清单（tests/test_admin_web.py）

1. `GET /admin/login`（匿名）→ 200，含 username/password 字段与 csrf_token hidden。
2. 有效会话访问 `GET /admin/login` → 303，Location=/admin/。
3. `POST /admin/login` 正确凭据 → 303 Location=/admin/；Set-Cookie 含 `mf_session=`
   与 `HttpOnly`；web_sessions 新增 1 行且 user_id 正确。
4. `POST /admin/login` 错误凭据 → 303 Location=/admin/login?error=1；web_sessions 无新增；
   响应体不含提交的密码串。
5. 无 Cookie 访问 `GET /admin/` → 303 Location=/admin/login。
6. OPERATOR 会话（登录后手工种 web_sessions 或经 UI 路径）访问 `GET /admin/` → 403。
7. `POST /admin/logout` 缺 csrf_token → 403 且 web_sessions 行仍存在。
8. `POST /admin/logout` 合法 → 303 Location=/admin/login；web_sessions 行删除；
   Set-Cookie 含 Max-Age=0。
9. 过期会话（expires_at < now）访问 `GET /admin/` → 303 Location=/admin/login。
10. 仪表盘渲染：200 含当前用户名与三个计数数字（用已知种子数据断言）。
11. 全部 GET/POST 响应体不包含任何 `password` 明文回显（提交值不出现）。

## 9. 验收门禁（每个切片完成时全绿）

1. `python -m pytest tests/test_admin_web.py -q` 全绿（S2 起追加对应测试文件）。
2. `python -m pytest tests/ -q` 全量不回归（既有失败单独记录，不并入本切片）。
3. `git diff --check` 干净；`git diff --name-only` 不超出切片文件边界。
4. 脱敏扫描：仓库内无 VPS 地址/主机名/凭据/数据库绝对路径（DATA_DIR 默认值除外）。
5. 契约文档与实现的字段名/状态码/重定向逐条一致。

## 10. 切片文件边界

- S1：`requirements.txt`、`app/main.py`（仅 init_db DDL + 挂载行）、`app/admin_web.py`、
  `app/templates/base.html|login.html|home.html`、`tests/test_admin_web.py`、本文档。
- 越界文件（API 路由、Android、无关测试）一律不进本切片提交。
