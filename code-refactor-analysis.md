# 代码质量深度分析报告（重构准备）

> 项目：`~/workspace/projects/material-flow-system`
> 范围：`material-flow-backend` 深度分析；`logistics-android` 高层概述
> 方法：tech-code-refactoring 工作流（code-analyzer → agent-git-oracle → uncle-bob → code-refactoring）
> 日期：2026-10-01
> 约束：只分析，不改代码

---

## 执行摘要

后端是单体 FastAPI 应用，**13,587 行 Python** 中有 **9,709 行挤在两个文件里**（`main.py` 6,720 行 + `admin_web.py` 2,989 行）。70 个 API 路由和 71 个管理后台路由的处理器函数里，**业务规则、SQL、事务管理、权限校验四层混写**，没有 service/repository 分层。

最危险的组合是**高变更频率 × 高复杂度**：`admin_web.py`（36 次变更）、`main.py`（26 次变更）是 git 历史上改动最频繁的两个文件，而圈复杂度最高的函数（`_decide_handover` cx=67、`_validate_handover_relation` cx=46）恰好也在其中——这是典型的技术债务累积区。

好消息：测试有 51 个文件、10,154 行，核心链路（交接、幂等、工作台、BOM）有契约测试覆盖，重构有安全网。错误码已收敛为 `CODE_*` 常量（259 处引用），说明团队有过一次成功的收敛重构，同样的手法可以复制到角色和状态字符串上。

---

## 一、结构分析（code-analyzer）

### 1.1 规模与分层

| 文件 | 行数 | 函数数 | 职责 |
|---|---|---|---|
| `app/main.py` | 6,720 | 158 | 70 个 API 路由 + 建表 + 迁移 + 全部业务 SQL |
| `app/admin_web.py` | 2,989 | 103 | 71 个管理后台页面路由 + 模板渲染 |
| `app/agent/importer.py` | 866 | — | 智能导入（列识别/校验/提交） |
| `app/agent/tools.py` | 421 | — | Agent 只读工具（10 个） |
| `app/assembly_routes.py` | 354 | 1（闭包内 30+） | 装配任务路由（闭包注册模式） |
| `app/u9/` | ~600 | — | U9 集成骨架（新，结构干净） |
| `app/barcodes.py` | 241 | — | 条码生成（有设计文档注释，干净） |
| `app/xlsx_parser.py` | 208 | — | xlsx 解析（dataclass，不可变，干净） |

架构风格：**事务脚本（Transaction Script）** —— 路由函数即业务单元，SQL 直接写在函数里。没有分层：表现层→数据层零距离。

### 1.2 DDD 边界识别

从表结构和外键可识别出 6 个聚合根，但**代码里没有任何聚合概念**，一致性边界靠人工维护：

| 聚合根 | 包含实体 | 一致性规则（散落在代码各处） |
|---|---|---|
| ProductionOrder（生产订单） | order_devices, order_material_requirements | 订单机型数量校验（main.py:2540） |
| MaterialHandover（物料交接） | handover_operations, 审计事件 | 状态机 PENDING→CONFIRMED/REJECTED/CANCELLED（_decide_handover） |
| TransferRequest（流转单） | material_handovers | 审批后才能交接（_decide_handover L5906-5922） |
| AssemblyTask（装配任务） | stages, members, labor_records | 阶段流转、成员归属校验（assembly_routes.py） |
| Inventory（库存） | locations, snapshots, import_batches | 导入幂等、版本冲突（importer.py） |
| BomVersion（BOM 版本） | bom_items | DRAFT→PUBLISHED 发布流 |

**核心复杂度来源**：`material_work_items`（责任人投影）与 `order_material_requirements`（正式需求）的双轨关系。`_decide_handover`、`_validate_handover_relation`、`_workspace_requirements` 三个最复杂的函数，全部在处理这两张表的对齐逻辑——这是领域建模不清的直接后果。

### 1.3 复杂度热力（圈复杂度 Top）

| 函数 | 位置 | 行数 | 圈复杂度 | 等级 |
|---|---|---|---|---|
| `_decide_handover` | main.py:5841 | 241 | **67** | 🔴 灾难 |
| `_validate_handover_relation` | main.py:5616 | 131 | 46 | 🔴 |
| `_workspace_item` | main.py:4611 | 187 | 41 | 🔴 |
| `_execute_transfer_items` | main.py:5218 | 144 | 30 | 🟠 |
| `_migrate_schema` | main.py:761 | 75 | 29 | 🟠 |
| `edit_employee` | main.py:1351 | 60 | 29 | 🟠 |
| `upload_assembly_model` | main.py:1691 | 94 | 28 | 🟠 |
| `create_transfer_handover` | main.py:6157 | 121 | 27 | 🟠 |
| `admin_models_list` | admin_web.py:1023 | 149 | 25 | 🟠 |
| `assembly_routes.register` | assembly_routes.py:4 | 350 | 119（含嵌套闭包） | 🔴 结构 |

---

## 二、Git 热点分析（agent-git-oracle）

183 个提交中改动最频繁的文件：

| 文件 | 变更次数 | 复杂度 | 债务评级 |
|---|---|---|---|
| `material-flow-backend/app/admin_web.py` | 36 | 高（103 函数，cx 最高 25） | 🔴 最高 |
| `logistics-android/app/build.gradle.kts` | 34 | 低（版本 bump） | 🟢 无害 |
| `material-flow-backend/app/main.py` | 26 | 极高（cx 67 函数在此） | 🔴 最高 |
| `material-flow-backend/docs/admin-web-contract.md` | 23 | 文档 | 🟢 |
| `app/templates/base.html` | 20 | 模板 | 🟡 |

**债务公式验证**：`admin_web.py` 和 `main.py` 同时满足"改得最多"和"最复杂"——每次需求变更都要在这两个上帝文件里改，回归风险随提交线性增长。Android 侧 `LogisticsViewModel.kt`（13 次变更，2,926 行）是镜像问题。

---

## 三、Top 15 代码坏味道（按严重程度排序）

### 🔴 1. 上帝函数 `_decide_handover` —— 长方法 + 深嵌套 + Switch on Type

**位置**：`material-flow-backend/app/main.py:5841–6081`（241 行，圈复杂度 67）

```python
def _decide_handover(hid: str, body: HandoverDecision, request: Request, user: sqlite3.Row,
                     x_request_id: str | None, idempotency_key: str | None, action: str) -> dict[str, Any]:
    trace_id = _handover_headers(x_request_id, idempotency_key, body.clientOperationId)
    if action == "CONFIRMED" and user["role"] not in {"OPERATOR", "WAREHOUSE_ADMIN", "ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无确认权限", trace_id=trace_id)
    if action == "REJECTED" and user["role"] not in {"WAREHOUSE_ADMIN", "ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "仅仓库管理员或管理员可驳回交接", trace_id=trace_id)
    if action == "CANCELLED" and user["role"] not in {"WAREHOUSE_ADMIN", "ADMIN", "MATERIAL"}:
        raise ApiError(403, CODE_FORBIDDEN, "无取消权限", trace_id=trace_id)
    ...（权限 6 段 → 幂等闸门 → 关系校验 8 段 → 状态转移 → 事件派发）
```

一个函数干了 5 种活：权限校验、幂等、关系一致性校验、状态机转移、审计事件派发。`action` 的三分支（CONFIRMED/REJECTED/CANCELLED）在函数内多处 `if/else` 重复出现。

**重构手法**：Extract Method 拆成 `_check_handover_permission()` / `_replay_handover_operation()` / `_validate_transfer_link()` / `_transition_handover()` / `_emit_handover_events()`；三动作分支用**策略表**（dict 映射 action → 权限集合 + 事件类型）替代散落的条件。

---

### 🔴 2. 事务样板代码 69+71 处重复 —— 缺少统一会话管理

**位置**：`material-flow-backend/app/main.py` 全文件（`c = db()` 69 处，`c.rollback()` 71 处，`except ApiError` 约 25 处）

```python
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        ...
    except ApiError:
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise ApiError(500, ...)
    c.close()
```

每个写接口手写一遍"开连接 → BEGIN IMMEDIATE → commit/rollback → close"。漏 `close()` 就是连接泄漏；`db()` 本身每次 `sqlite3.connect` + 设 PRAGMA，没有池化概念。

**重构手法**：引入 contextmanager（**Extract Method + Replace with Decorator**）：

```python
@contextmanager
def tx():
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE"); yield c; c.commit()
    except ApiError: c.rollback(); raise
    except Exception: c.rollback(); raise ApiError(500, ...)
    finally: c.close()
```

调用点从 12 行样板变成 `with tx() as c:`。这是**投入产出比最高的重构**。

---

### 🔴 3. 幂等闸门双轨制 —— 已有 helper 但只被 3 个端点采用

**位置**：
- Helper：`main.py:2522` `def _operation_replay(c, table, operation_id, payload, trace_id)`
- 采用者：`main.py:2545`、`2572`、`2661`（3 个新建端点）
- 内联变体（未迁移）：`main.py:1371`（edit_employee）、`1466`（delete_employee）、`5890`、`6178`（handover 两处）

```python
# 内联变体（delete_employee, L1465-1485）：15 行手写
prior = c.execute(
    "SELECT * FROM admin_user_delete_operations WHERE client_operation_id=?",
    (operation_id,)).fetchone()
if prior:
    if (prior["user_id"] != user_id
        or _payload_digest(prior["payload_json"]) != _payload_digest(payload)):
        raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
    result = json.loads(prior["result_json"])
    result.update(idempotent=True, traceId=trace_id, serverTime=now())
    c.rollback()
    return result
```

同一逻辑两种写法并存，新人必然抄错（比如 `handover` 变体多做了 `handover_id` 一致性校验，语义已分叉）。

**重构手法**：**统一收敛**到 `_operation_replay`（行为无变化，纯搬运）；长期可再抽成 `@idempotent(table)` 装饰器。

---

### 🔴 4. 权限角色魔法字符串 110+ 处 —— 基本类型偏执

**位置**：`main.py` 全文件 —— `"ADMIN"` 53 次、`"WAREHOUSE_ADMIN"` 22 次、`"ASSEMBLER"` 12 次、`"MATERIAL"` 12 次、`"OPERATOR"` 11 次

```python
# main.py:1451
if user["role"] != "ADMIN":
    raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可删除用户", trace_id=trace_id)
# 同样的守卫在 966 / 1310 / 1356 / 1419 / 1451 / 1618 / 3635 / 6400 ... 重复 10+ 次
```

`ROLES` 元组（L91）只做了"角色是否合法"校验，真正的权限判断全是裸字符串比较。新增一个角色要改几十处，极易遗漏。

**重构手法**：**引入值对象** `class Role(str, Enum)` + **守卫函数**：

```python
def require_roles(user, *roles: Role, trace_id: str) -> None: ...
# 调用点：
require_roles(user, Role.ADMIN, trace_id=trace_id)
```

---

### 🟠 5. 状态机用 if/elif 链 + 内联字典分发 —— Switch on Type

**位置**：`main.py:6013–6043`（`_decide_handover` 事件派发段）

```python
        else:
            event_types.append({
                "REJECTED": "HANDOVER_REJECTED",
                "CANCELLED": "HANDOVER_CANCELLED",
            }[action])                       # L6014-6017：内联字典分发
        ...
        for event_type in event_types:
            event_status = {
                "HANDOVER_CONFIRMED": "CONFIRMED",
                "MATERIAL_PICKED_UP": "PICKED_UP",
                ...
            }[event_type]                    # L6022-6028：第二个内联字典
            if event_type == "HANDOVER_CONFIRMED":
                event_before = {"handoverStatus": "PENDING", "workspaceStatus": "PENDING"}
            elif event_type == "MATERIAL_PICKED_UP":
                event_before = {"handoverStatus": "CONFIRMED", "workspaceStatus": "PENDING"}
            elif event_type == "MATERIAL_AT_STATION":
                ...
            else: ...                        # L6034-6043：if/elif 链
```

状态转移规则（event → before/after 状态）是**领域核心知识**，却以匿名 `if/elif` 藏在 6000 行深处，改状态机必须读完全函数。

**重构手法**：**状态转移表常量化**（模块级 `HANDOVER_EVENT_TRANSITIONS: dict[str, Transition]`），`if/elif` 链 → 查表。符合 OCP：加新事件只加表行，不改逻辑。

---

### 🟠 6. `assembly_routes.register()` 350 行闭包 —— 路由无法独立导入/测试

**位置**：`material-flow-backend/app/assembly_routes.py:4–354`

```python
def register(app, db, now, current_user, audit_event, api_error=None):
    def forbidden(message='无装配操作权限'): ...
    def actor(u): ...
    def task(c, tid, u): ...
    ...（30+ 个嵌套 def，路由处理器全是闭包）
    def stage_start(tid: str, ...): ...
```

依赖靠参数注入（`db, now, current_user`）——方向是对的（DIP 雏形），但实现成闭包导致：① 无法 `from assembly_routes import stage_start` 单独测试；② 逼出了下游的 hack——

**`admin_web.py:930` 的 `_api_endpoint()`** 靠**路由表路径字符串反查** endpoint 并直接调用：

```python
def _api_endpoint(path: str, method: str = "GET"):
    """从 FastAPI 路由表解析既有 API 处理函数并直接调用（统计口径零漂移）。
    workshop 统计端点注册在 assembly_routes.register() 闭包内，无法按模块名导入；
    路由表解析保持「同一函数、同一 SQL」语义。"""
    for route in backend_app.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"API endpoint not found: {method} {path}")
```

路径字符串一改名，管理后台直接 500，且是运行时才炸。这是**脆弱的字符串耦合**，注释里自己都承认了是 workaround。

**重构手法**：闭包提升为模块级函数 + `APIRouter`；admin 改为直接 import。注意这是 P2（见优先级），先不动 `_api_endpoint`。

---

### 🟠 7. 内联 `__import__('datetime')` —— 300 字符单行多语句

**位置**：`material-flow-backend/app/assembly_routes.py:36`

```python
    def finish(c, rid):
        t=now(); r=c.execute('SELECT started_at FROM labor_records WHERE id=?',(rid,)).fetchone(); secs=max(0, int(__import__('datetime').datetime.fromisoformat(t).timestamp()-__import__('datetime').datetime.fromisoformat(r['started_at']).timestamp()))
        c.execute("UPDATE labor_records SET status='COMPLETED',ended_at=?,duration_minutes=? WHERE id=?",(t,secs//60,rid))
```

一行塞了 4 个语句、用 `__import__` 动态导入（两次！）、无空格、无类型。`r` 可能为 None 也没处理（`r['started_at']` 会 TypeError）。

**重构手法**：顶层 `import datetime` + Extract Method `_finish_labor_record(c, rid)` + 守卫 `if not r: return`。纯机械化，零风险。

---

### 🟠 8. 幂等 UUID 三元组校验 4 个变体 —— 重复逻辑且错误码不一致

**位置**：`assembly_routes.py:18`（`op`）、`:43`（`stage_operation`）、`:59`（`transfer_operation`）、`:140`（`assignment_operation`）

```python
# stage_operation (L43)：严格版，校验 clientOperationId + Idempotency-Key + X-Request-Id
# transfer_operation (L59)：注释写着 "Validate the same UUID trio used by the other assembly writes."
#   —— 注释承认是重复，但还是复制了一份，细节还不一样（大小写处理、异常类型）
# assignment_operation (L140)：clientOperationId 非法时报 422，其他两个报 400
```

"same trio" 复制了 4 份，错误码 400/422 不统一，客户端看到的行为取决于走了哪个端点。

**重构手法**：**Extract Method** 合并为 `validate_operation_trio(body, key, request)`，错误码统一为 400（以多数为准）。

---

### 🟠 9. `_workspace_requirements` SQL 字符串拼接 203 行 —— 按角色分支拼 WHERE

**位置**：`main.py:4175–4377`（203 行）

```python
    query = """SELECT r.id AS requirement_id, ...（60 行 SQL，含 3 个重复子查询）... WHERE 1=1 """
    args: list[Any] = []
    if order_no is not None:
        query += " AND o.order_no = ?"; args.append(order_no)
    if effective_role == "OPERATOR":
        operator_predicate = "w.assigned_user_id = ?" if operator_id else (...)
        handover_predicate = (...)
        query += """ AND ( EXISTS (...) OR EXISTS (...) ) """.format(...)
        if operator_id: args.extend([operator_id] * 4)
    elif effective_role == "MATERIAL":
        ...（另一套 40 行拼接）
```

SQL 里用 Python `if/elif` 按角色拼接谓词，还有 `.format()` 插值（此处插的是代码生成的片段，非用户输入，无注入风险，但可读性差）。同一个"找 work_item"的子查询在 SELECT 里重复了 3 次。

**重构手法**：**按角色拆查询函数**（Replace Conditional with Polymorphism / Strategy）：`_requirements_for_operator()` / `_requirements_for_material()` / `_requirements_for_admin()`，公共 SELECT 列抽成常量。或者引入轻量 Query Builder 收敛 `WHERE 1=1` + `+=` 模式（该模式在 admin_web 出现 10+ 次）。

---

### 🟠 10. `_migrate_schema` 顺序迁移脚本 —— 圈复杂度 29

**位置**：`main.py:761–835`

```python
def _migrate_schema(c: sqlite3.Connection) -> None:
    """幂等的增量迁移。SQLite 不支持 ADD COLUMN IF NOT EXISTS，故先查 PRAGMA 再补。"""
    c.execute("UPDATE users SET role='MATERIAL' WHERE role='MATERIAL_CLERK'")
    temporary_transfer_columns = {r["name"] for r in c.execute("PRAGMA table_info(temporary_transfers)").fetchall()}
    if "device_id" not in temporary_transfer_columns:
        c.execute("ALTER TABLE temporary_transfers ADD COLUMN device_id TEXT")
    ...（十几个 if not in → ALTER TABLE 块，外加整表重建逻辑）
```

所有历史迁移挤在一个函数里顺序执行，越往后越长。新加迁移只能往尾部追加，无法单独测试某次迁移。

**重构手法**：**迁移注册表**：`MIGRATIONS: list[tuple[str, Callable]] = [("add device_id to temporary_transfers", _migrate_xxx), ...]`，runner 循环执行。每个迁移独立、可单独测试。

---

### 🟡 11. `_validate_handover_relation` 死分支 —— 守卫后重复判断

**位置**：`main.py:5627–5630`

```python
    if work:
        if not work["requirement_id"]:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "工作项未关联正式订单需求", trace_id=trace_id)
        if work["requirement_id"]:   # ← 恒为真：上一行已把 falsy 情况 raise 掉了
            req = c.execute(
                "SELECT * FROM order_material_requirements WHERE id=?",
                (work["requirement_id"],),
            ).fetchone()
```

**重构手法**：**Simplify Conditional**——删掉 `if work["requirement_id"]:` 这一层，直接取 `req`。顺带把该函数（131 行，cx=46）按"查 work → 查 req → 交叉校验"拆成 3 个小函数。

---

### 🟡 12. 长参数列表 —— `_audit_event` 11 参、`audit_logs` 12 参

**位置**：
- `main.py:5551` `def _audit_event(c, event_type, entity_id, actor, request_id, operation_id, before, after, ...)` —— 11 个参数
- `main.py:6385` `def audit_logs(page, pageSize, from_, to_, eventType, entityType, operatorId, action, ...)` —— 12 个参数
- `app/u9/sync.py:52` `def _log(conn, run_id, entity, dry_run, ...)` —— 12 个参数

```python
_audit_event(
    c, event_type, hid, user, trace_id, operation_id,
    event_before, after, request,
)   # 调用点靠位置传参，9 个参数排错一个就静默错位
```

**重构手法**：**Introduce Parameter Object**——`@dataclass class AuditEvent: event_type, entity_id, actor, ...`；查询类用 `AuditLogQuery`。调用点变成关键字传参，错位即报错。

---

### 🟡 13. `admin_models_list` 149 行三职责 + 魔法数字 `page_size = 20`

**位置**：`admin_web.py:1023–1171`

一个页面处理器同时做三件事：查模型版本列表、查机台列表（3D 绑定用）、查机台关联的装配任务。`page_size = 20` 在 `admin_web.py` 里硬编码出现 10+ 处（L852、1030、1065、2003…），改默认分页要全文替换。

**重构手法**：Extract Method 拆成 `_query_model_versions()` / `_query_devices_for_binding()` / `_query_device_tasks()`；`DEFAULT_PAGE_SIZE = 20` 模块常量。

---

### 🟡 14. `edit_employee` 角色白名单与 `ROLES` 不一致 —— 硬编码子集

**位置**：`main.py:1362`

```python
ROLES = ("OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR", "ASSEMBLER")  # L91

# edit_employee L1362：
if "role" in changes and changes["role"] not in ("OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "WORKSHOP_SUPERVISOR", "ASSEMBLER"):
    raise ApiError(400, CODE_VALIDATION_ERROR, "角色无效", trace_id=trace_id)
```

白名单是手写的 5 元组，`ROLES` 改了这里不会同步（比如 PLANNER/ADMIN 能否被编辑，全靠这个字面量决定，意图不透明）。

**重构手法**：`ASSIGNABLE_ROLES = set(ROLES) - {"ADMIN"}`（或显式常量并注释原因），配合 #4 的 Role 枚举。

---

### 🟡 15. `raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, …)` 23 处 —— 重复的 raise 模式

**位置**：`main.py` 全文件（`_decide_handover` 内占 10+ 处，L5906–5980）

```python
        if not transfer or transfer["type"] != "OUTBOUND":
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接未关联有效出库单", trace_id=trace_id)
        if transfer["status"] not in {"APPROVED", "EXECUTED"}:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "关联流转单尚未审批通过", trace_id=trace_id)
        ...（同样的三行结构重复 10 次，只是消息不同）
```

错误码常量化已经做得很好（`CODE_*` 共 259 处引用），但调用点仍是三行样板。

**重构手法**：**Extract Method** 小助手 `def _conflict(msg, trace_id): raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, msg, trace_id=trace_id)`，调用点变一行。机械化替换，零风险。

---

## 四、SOLID / Clean Code 评估（uncle-bob）

| 原则 | 评级 | 说明 |
|---|---|---|
| **SRP** | 🔴 严重违反 | `main.py` 一个文件承担路由+SQL+业务+迁移；`_decide_handover` 一个函数 5 种职责；`admin_models_list` 一个页面 3 种查询 |
| **OCP** | 🔴 违反 | 加新动作类型 / 新角色 / 新事件类型，都要改 `if/elif` 链（#1、#5、#9）。扩展靠修改，不靠新增 |
| **LSP** | 🟢 n/a | 基本无继承体系，无违反 |
| **ISP** | 🟢 n/a | 无臃肿接口 |
| **DIP** | 🟡 部分违反 | 路由直接依赖 `sqlite3` 和裸 SQL，无仓储抽象；但 `assembly_routes.register(app, db, now, ...)` 的参数注入是好的 DIP 雏形，只是形式错了（闭包） |
| **命名** | 🟢 好 | 函数名基本表意（`_decide_handover`、`_validate_handover_relation`）；中文错误信息对管理后台友好 |
| **函数长度** | 🔴 | 241/203/187/149/142 行的函数有 8+ 个，远超"一屏"标准 |
| **注释** | 🟡 | 关键并发语义有注释（如 BEGIN IMMEDIATE 的说明），但也有注释代替重构的情况（`_api_endpoint` 的 docstring 是在解释 workaround） |
| **错误处理** | 🟡 | 错误码常量化做得好；但 `except ApiError: rollback; raise` 样板 25 处重复，且 `assembly_routes.py` 里 `api_error`/`HTTPException` 双协议并存 |
| **DRY** | 🔴 | 事务样板、幂等闸门、UUID 校验、角色守卫四处大规模重复 |

**整洁架构分层评估**：当前是 2 层（路由函数 → sqlite3），Entities/Use Cases/Adapters 完全未分离。短期内不需要推倒做 Clean Architecture——事务脚本风格对这个规模的 CRUD 系统是够用的，**优先做"函数级"重构而非"架构级"重构**。

---

## 五、Android 高层概述（logistics-android）

96 个 Kotlin 文件，26,319 行。包划分合理（`ui/` `data/` `domain/` `model/` `rendering/`），但出现与后端镜像的问题：

| 文件 | 行数 | 问题 |
|---|---|---|
| `ui/LogisticsViewModel.kt` | 2,926 | 🔴 上帝 ViewModel（git 13 次变更，热点） |
| `ui/screens/WorkspaceScreen.kt` | 1,196 | 🟡 大屏 |
| `data/remote/MaterialFlowApi.kt` | 1,073 | 🟡 API 接口集中（可接受） |
| `ui/screens/ScannerScreen.kt` | 1,028 | 🟡 |
| `ui/screens/WorkshopAssemblyScreens.kt` | 1,012 | 🟡 |
| `data/remote/ApiParser.kt` | 984 | 🟡 解析逻辑集中（git 12 次变更，热点） |
| `model/Models.kt` | 977 | 🟡 数据类集中（可接受） |

结论：Android 的主要债务是 `LogisticsViewModel.kt` 过大，建议按屏幕拆分成多个 ViewModel。`ApiParser.kt` 是第二热点，解析逻辑可按领域拆分。本次不深入，后续需要可单独做一次。

---

## 六、重构优先级建议

### P0 —— 先做（机械化、低风险、测试覆盖好）

| # | 项 | 手法 | 预期收益 |
|---|---|---|---|
| #2 | 事务 contextmanager | Replace try/except with decorator | 消灭 69 处样板，杜绝连接泄漏 |
| #7 | 内联 `__import__` | 顶层 import + Extract Method | 可读性，顺手补 None 守卫 |
| #11 | 死分支 | Simplify Conditional | 去 1 层嵌套 |
| #13 | 分页常量 | Magic number → 常量 | 改分页只改一处 |
| #15 | `_conflict()` 助手 | Extract Method | 23 处 raise 变 1 行 |

### P1 —— 收敛重复（行为不变，需跑全量测试）

| # | 项 | 手法 |
|---|---|---|
| #3 | 幂等闸门统一到 `_operation_replay` | 统一收敛 |
| #4 | Role 枚举 + `require_roles()` 守卫 | 值对象 + 守卫函数 |
| #5 | 状态转移表常量化 | 查表替代 if/elif |
| #8 | UUID 三元组校验统一 | Extract Method（4→1） |
| #14 | 可分配角色常量 | 与 ROLES 联动 |

### P2 —— 结构性（先补测试再动，拆小步）

| # | 项 | 手法 | 风险提示 |
|---|---|---|---|
| #1 | 拆 `_decide_handover` | Extract Method + 策略表 | 核心链路，必须先有契约测试（已有 test_handover_contract.py，可先跑通） |
| #9 | 拆 `_workspace_requirements` | 按角色拆查询函数 | SQL 等价性需逐角色验证 |
| #10 | 迁移注册表 | 注册表模式 | 一次性改动，启动时验证 |
| #6 | assembly_routes 模块化 | 闭包→模块函数+APIRouter | 改完才能删 `_api_endpoint` hack |

### ⛔ 不动

- **`_api_endpoint()` hack**：在 #6 完成前别动，动了管理后台统计口径会漂移（注释里写明的"统计口径零漂移"是刻意设计）。
- **`app/u9/`**：新骨架，结构干净，保持现状。
- **`app/agent/`**：导入器逻辑复杂但测试覆盖全（test_agent.py 等），只在修 bug 时顺手重构。
- **整库分层重构（Clean Architecture）**：当前规模下事务脚本够用，ROI 为负。等聚合根超过 10 个或团队超过 3 人再考虑。

---

## 附：量化指标一览

- 后端 Python：13,587 行 / 22 文件；`main.py` 占 49%
- 测试：10,154 行 / 51 文件（安全网良好）
- `c = db()` 69 处 / `c.rollback()` 71 处
- 角色字面量 110+ 处 / 状态字面量 40+ 处
- 圈复杂度 >25 的函数：9 个
- Git 热点：admin_web.py（36）> main.py（26）
- Android：26,319 行 / 96 文件；最大 ViewModel 2,926 行

*本报告只做分析，未修改任何代码。*
