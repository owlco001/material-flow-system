"""v0.6.2 管理台性能 / 安全修复回归（P0 + P1）。

- P0-1 工作台：SQL 分页与全量切片一致；页面只计算一次。
- P0-2 导出：单次查询 + 流式 CSV，输出与旧的逐页拼接逐字节一致。
- P1-3 智能助手接口：X-CSRF-Token / JSON Content-Type / 角色。
- P1-4 agent.html：插值全部转义，动态按钮不拼 onclick。
- P1-5 列表分页：流转 / 订单 / 仓库 / 异常 / 用户。
- P1-6 N+1：流转列表、任务列表 SQL 条数不随行数增长。
- P1-7 索引迁移。
- P1-8 任务页装配工候选只渲染一次。
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as backend

TEMPLATES = Path(backend.__file__).resolve().parent / "templates"
STATIC = Path(backend.__file__).resolve().parent / "static" / "admin"


def template_with_assets(name: str) -> str:
    """模板 + 它引用的静态脚本/样式（v0.6.3 起 base.html / agent.html 的 JS/CSS 抽到 /static）。"""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for rel in re.findall(r"static_url\('admin/([^']+)'\)", text):
        text += "\n" + (STATIC / rel).read_text(encoding="utf-8")
    return text

USERS = (
    ("u_admin", "owlco", "ADMIN", "Admin@2026"),
    ("u_wh", "wh-admin", "WAREHOUSE_ADMIN", "Wh@2026x"),
    ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
    ("u_plan", "planner1", "PLANNER", "Plan@2026x"),
    ("u_op", "operator1", "OPERATOR", "Op@2026xx"),
    ("u_a1", "asm1", "ASSEMBLER", "Asm@2026x"),
    ("u_a2", "asm2", "ASSEMBLER", "Asm@2026x"),
)


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, role, pwd in USERS:
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_users()
    yield


def form_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "表单必须携带 csrf_token"
    return match.group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def login(client, username):
    pwd = {u[1]: u[3] for u in USERS}[username]
    assert web_login(client, username, pwd).status_code == 303
    landing = {"owlco": "/admin/agent", "wh-admin": "/admin/flows", "supervisor1": "/admin/tasks",
               "planner1": "/admin/orders"}.get(username)
    if landing is None:  # 无管理台页面的角色：角色校验先于 CSRF，令牌值无关
        return "no-page-token"
    page = client.get(landing)
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
    assert match, "已登录页面必须输出 csrf-token meta"
    return match.group(1)


def user_row(username="owlco"):
    c = backend.db()
    try:
        return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    finally:
        c.close()


def ts(i: int) -> str:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return (base + timedelta(minutes=i)).isoformat()


def legacy_csv(rows: list[dict]) -> bytes:
    """旧 _csv_response 的精确实现（参考输出）。"""
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def legacy_collect(fn, page_size, **kwargs) -> list[dict]:
    """旧 _collect_all：按 API 单页逐页拉取。"""
    rows, page = [], 1
    while True:
        batch = fn(page=page, pageSize=page_size, **kwargs).get("items") or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        page += 1


def seed_fixture():
    from app.seed import _insert_fixture
    c = backend.db()
    try:
        _insert_fixture(c)
        c.commit()
    finally:
        c.close()


def seed_tasks_and_labor(n: int = 25):
    c = backend.db()
    for i in range(n):
        tid = f"task_{i:03d}"
        c.execute(
            "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,assigned_assembler_id,status,"
            "progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, f"ORD-{i % 4}", f"dev_{i % 7}", f"DEV-{i % 7:02d}", "u_a1" if i % 2 else "u_a2",
             "COMPLETED" if i % 3 == 0 else "IN_PROGRESS", i % 4, 1, ts(i), ts(i)),
        )
        c.execute(
            "INSERT INTO labor_records(id,task_id,worker_user_id,type,status,started_at,ended_at,"
            "duration_minutes,remark,client_operation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"lr_{i:03d}", tid, "u_a1" if i % 2 else "u_a2", "ASSEMBLY", "COMPLETED",
             ts(i), ts(i + 30), 30 + i, None, str(uuid.uuid4()), ts(i)),
        )
        c.execute(
            "INSERT INTO assembly_task_stages(task_id,stage_no,status,version,updated_at) VALUES(?,?,?,?,?)",
            (tid, 1, "COMPLETED", 1, ts(i)),
        )
    c.commit()
    c.close()


def seed_audit(n: int = 260):
    c = backend.db()
    for i in range(n):
        c.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
            "request_id,client_operation_id,before_json,after_json,server_time,result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("FLOW_TRANSFER_EXECUTED" if i % 2 else "OUTBOUND_APPROVED", "MATERIAL_FLOW", f"mf_{i}",
             "u_admin", "ADMIN", str(uuid.uuid4()), str(uuid.uuid4()), "{}", '{"a": "中文,逗号"}',
             ts(i // 3), "SUCCESS"),
        )
        c.execute(
            "INSERT INTO audit_logs(operator_id, role, action, resource_type, resource_id, request_id,"
            " occurred_at, result) VALUES(?,?,?,?,?,?,?,?)",
            ("u_admin", "ADMIN", "UPDATE", "USER", f"u_{i}", "r", ts(i // 3), "SUCCESS"),
        )
    c.commit()
    c.close()


# ---------------------------------------------------------------- P0-2 导出


def test_audit_export_matches_legacy_paged_output():
    seed_audit()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        r = client.get("/admin/audit/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/csv; charset=utf-8"
        assert r.headers["content-disposition"] == 'attachment; filename="audit-logs.csv"'
        expected = legacy_csv(legacy_collect(
            backend.audit_logs, 100, from_=None, to_=None, eventType=None, entityType=None,
            operatorId=None, action=None, resourceType=None, resourceId=None, entityId=None,
            user=user_row(),
        ))
        assert r.content == expected
        assert r.content.count(b"\n") > 500
        filtered = client.get("/admin/audit/export?eventType=OUTBOUND_APPROVED")
        expected_filtered = legacy_csv(legacy_collect(
            backend.audit_logs, 100, from_=None, to_=None, eventType="OUTBOUND_APPROVED",
            entityType=None, operatorId=None, action=None, resourceType=None, resourceId=None,
            entityId=None, user=user_row(),
        ))
        assert filtered.content == expected_filtered


def test_workspace_export_matches_full_computation():
    seed_fixture()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        user = user_row()
        c = backend.db()
        try:
            all_items = backend._workspace_items(c, user)
        finally:
            c.close()
        assert len(all_items) > 20
        r = client.get("/admin/workspace/export")
        assert r.status_code == 200
        assert r.headers["content-disposition"] == 'attachment; filename="material-workspace.csv"'
        assert r.content == legacy_csv(all_items)
        r2 = client.get("/admin/workspace/export?status=OUT_OF_STOCK")
        assert r2.content == legacy_csv(backend._workspace_filter_status(all_items, "OUT_OF_STOCK"))
        order_no = all_items[0]["orderNo"]
        r3 = client.get(f"/admin/workspace/export?orderNo={order_no}")
        assert r3.content == legacy_csv([i for i in all_items if i["orderNo"] == order_no])


def test_reports_exports_match_legacy_paged_output():
    from app.admin_web import _api_endpoint

    seed_tasks_and_labor(45)
    with TestClient(backend.app) as client:
        login(client, "supervisor1")
        sup = user_row("supervisor1")
        machines = client.get("/admin/reports/machines/export")
        assert machines.status_code == 200
        expected = legacy_csv(legacy_collect(
            _api_endpoint("/api/v1/workshop/machine-progress"), 20, user=sup, deviceId=None,
        ))
        assert machines.content == expected
        labor = client.get("/admin/reports/labor/export")
        assert labor.status_code == 200
        expected = legacy_csv(legacy_collect(
            _api_endpoint("/api/v1/workshop/labor-summary"), 20,
            deviceId=None, orderNo=None, assemblerId=None, user=sup,
        ))
        assert labor.content == expected
        assert labor.content.count(b"\n") == 46  # 表头 + 45 行


def test_empty_export_is_bom_only():
    with TestClient(backend.app) as client:
        login(client, "supervisor1")
        r = client.get("/admin/reports/labor/export")
        assert r.content == "\ufeff".encode("utf-8")


# ---------------------------------------------------------------- P0-1 工作台


def test_workspace_api_sql_pagination_matches_full_slice():
    seed_fixture()
    user = user_row()
    c = backend.db()
    try:
        full = backend._workspace_items(c, user)
    finally:
        c.close()
    page_size = 7
    pages = (len(full) + page_size - 1) // page_size
    for page in range(1, pages + 2):
        data = backend.workspace_material_items(
            viewRole=None, status=None, orderNo=None, page=page, pageSize=page_size,
            user=user, x_request_id=None, x_client_operation_id=None,
        )
        assert data["total"] == len(full)
        assert data["totalPages"] == pages
        assert data["items"] == full[(page - 1) * page_size: page * page_size]
    order_no = full[-1]["orderNo"]
    data = backend.workspace_material_items(
        viewRole=None, status=None, orderNo=f"  {order_no} ", page=1, pageSize=50,
        user=user, x_request_id=None, x_client_operation_id=None,
    )
    assert data["items"] == [i for i in full if i["orderNo"] == order_no][:50]


def test_workspace_page_computes_items_once(monkeypatch):
    seed_fixture()
    calls = []
    original = backend._workspace_requirements

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(backend, "_workspace_requirements", counting)
    with TestClient(backend.app) as client:
        login(client, "owlco")
        r = client.get("/admin/workspace?page=2")
        assert r.status_code == 200
    assert len(calls) == 1


def test_workspace_requirements_single_work_item_subquery():
    query, _ = backend._workspace_requirements_sql(user_row(), None, None)
    assert query.count("FROM material_work_items") == 1


# ---------------------------------------------------------------- P1-3 CSRF


def test_agent_json_posts_require_csrf_and_json_content_type():
    with TestClient(backend.app) as client:
        token = login(client, "owlco")
        endpoints = [
            ("/admin/agent/chat", {"message": "hi"}),
            ("/admin/agent/chat/stream", {"message": "hi"}),
            ("/admin/agent/sessions/s1", {"title": "x"}),
            ("/admin/agent/feedback", {"message_id": "m", "rating": 1}),
            ("/admin/agent/import/commit", {"job_id": "j"}),
            ("/admin/agent/import/bom/commit", {"preview_id": "p"}),
            ("/admin/agent/import/aggbom/commit", {"preview_id": "p"}),
            ("/admin/agent/settings", {"daily_token_limit": "1"}),
        ]
        for path, body in endpoints:
            r = client.post(path, json=body)
            assert r.status_code == 403, path
            assert r.json()["error"]["code"] == "CSRF_FAILED", path
            bad = client.post(path, json=body, headers={"X-CSRF-Token": "wrong"})
            assert bad.status_code == 403, path
            plain = client.post(path, content=b'{"message":"hi"}',
                                headers={"X-CSRF-Token": token, "Content-Type": "text/plain"})
            assert plain.status_code == 415, path
            assert plain.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
        assert client.delete("/admin/agent/sessions/s1").status_code == 403
        # 带头即可通过守卫（后续业务结果与旧版一致）
        ok = client.post("/admin/agent/settings", json={"daily_token_limit": "0"},
                         headers={"X-CSRF-Token": token})
        assert ok.status_code == 200 and ok.json() == {"ok": True}
        missing = client.post("/admin/agent/import/commit", json={},
                              headers={"X-CSRF-Token": token})
        assert missing.status_code == 400


def test_agent_multipart_uploads_require_csrf():
    # raise_server_exceptions=False：bom/preview 对非法文件的旧错误分支本身会 500（与本修复无关）
    with TestClient(backend.app, raise_server_exceptions=False) as client:
        token = login(client, "owlco")
        uploads = [
            ("/admin/agent/import/preview", {"target": "inventory"}),
            ("/admin/agent/import/bom/preview", {"model_code": "M-1"}),
            ("/admin/agent/import/aggbom/preview", {}),
        ]
        for path, data in uploads:
            files = {"file": ("a.csv", b"x,y\n1,2\n", "text/csv")}
            r = client.post(path, data=data, files=files)
            assert r.status_code == 403, path
            assert r.json()["error"]["code"] == "CSRF_FAILED"
            files = {"file": ("a.csv", b"x,y\n1,2\n", "text/csv")}
            passed = client.post(path, data=data, files=files, headers={"X-CSRF-Token": token})
            assert passed.status_code not in (401, 403, 415), (path, passed.text)


def test_agent_role_checks():
    with TestClient(backend.app) as client:
        token = login(client, "operator1")
        r = client.post("/admin/agent/chat/stream", json={"message": "hi"}, headers={"X-CSRF-Token": token})
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
        assert client.get("/admin/agent/sessions").status_code == 403
        assert client.get("/admin/agent/bom/models").status_code == 403
    with TestClient(backend.app) as client:
        token = login(client, "wh-admin")
        # 仓库管理员可用 BOM / 聚合 BOM 接口（含 GET）
        # 单机型 BOM 导入仍受 main._bom_authorized（BOM_IMPORT_ROLES）约束：守卫放行后由业务层干净地 403
        r = client.post("/admin/agent/import/bom/commit", json={"preview_id": "p"},
                        headers={"X-CSRF-Token": token})
        assert r.status_code in (400, 403, 409, 422), r.text
        r = client.post("/admin/agent/import/aggbom/commit", json={"preview_id": "p"},
                        headers={"X-CSRF-Token": token})
        assert r.status_code not in (401, 403), r.text
        assert client.get("/admin/agent/aggbom/batches").status_code == 200
        assert client.get("/admin/agent/bom/models").status_code == 200
        r = client.get("/admin/agent/bom/aggregate?items=")
        assert r.status_code not in (401, 403), r.text
        r = client.get("/admin/agent/aggbom/aggregate?batch_id=nope")
        assert r.status_code not in (401, 403), r.text
        # CSRF 仍然生效
        r = client.post("/admin/agent/import/aggbom/commit", json={"preview_id": "p"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
        # 仓库管理员仍可用智能导入
        r = client.post("/admin/agent/import/commit", json={}, headers={"X-CSRF-Token": token})
        assert r.status_code == 400
    with TestClient(backend.app) as client:
        token = login(client, "planner1")
        r = client.post("/admin/agent/settings", json={"model": "x"}, headers={"X-CSRF-Token": token})
        assert r.status_code == 403
        assert client.get("/admin/agent/settings").status_code == 403
        assert client.get("/admin/agent/bom/models").status_code == 200
    with TestClient(backend.app) as client:
        r = client.post("/admin/agent/chat", json={"message": "hi"})
        assert r.status_code == 401


def test_agent_and_boms_pages_send_csrf_header():
    agent = template_with_assets("agent.html")
    base = template_with_assets("base.html")
    assert 'name="csrf-token"' in base and "function csrfHeaders" in base
    # 所有 POST / DELETE fetch 都带 csrfHeaders
    for match in re.finditer(r'fetch\(([^;]*?)method: "(POST|DELETE)"([^;]*?)\)', agent, re.S):
        assert "csrfHeaders(" in match.group(0), match.group(0)[:120]
    assert 'headers: csrfHeaders({ "Content-Type": "application/json" })' in agent  # apiCall
    assert "csrfHeaders(" in (TEMPLATES / "boms.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- P1-4 XSS


def test_agent_template_escapes_interpolations_and_has_no_inline_onclick_ids():
    agent = template_with_assets("agent.html")
    assert "onclick=\"showAggBomDevices" not in agent
    assert "onclick=\\\"showAggBomDevices" not in agent
    assert "showAggBomDevices(\\'" not in agent
    assert 'data-action="aggbom-devices" data-batch-id="\' + agEsc(b.id)' in agent
    safe_markers = ("agEsc(", "escHtml(", "textContent", "alert(", "throw new Error")
    for expr in ("b.project_name || b.file_name", "d.device_code", "j.projectName", "b.id",
                 "e.message", "m.material_code", "m.material_name", "row[c]", "res.modelCode",
                 "j.orderNo", "res.versionNo"):
        for m in re.finditer(re.escape(expr), agent):
            line_start = agent.rfind("\n", 0, m.start()) + 1
            line = agent[line_start:agent.find("\n", m.end())]
            assert any(marker in line for marker in safe_markers), (expr, line.strip()[:160])
    # escHtml 必须容忍 null / 数字
    base = template_with_assets("base.html")
    assert 'String(t == null ? "" : t)' in base


def test_boms_template_uses_shared_esc():
    boms = (TEMPLATES / "boms.html").read_text(encoding="utf-8")
    assert "const aggEsc = escHtml;" in boms


# ---------------------------------------------------------------- P1-5 分页


def test_users_list_paginates():
    c = backend.db()
    for i in range(60):
        c.execute(
            "INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,"
            "active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (f"u_bulk_{i:02d}", f"bulk{i:02d}", f"批量{i:02d}", "OPERATOR", "x", 0, 1, ts(1000 + i)),
        )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        p1 = client.get("/admin/users")
        assert p1.status_code == 200
        assert "bulk59" not in p1.text
        assert "下一页" in p1.text and "共 2 页 / 67 条" in p1.text
        p2 = client.get("/admin/users?page=2")
        assert "bulk59" in p2.text and "上一页" in p2.text
        q = client.get("/admin/users?q=bulk5")
        assert "bulk55" in q.text and "下一页" not in q.text


def test_flows_list_paginates_beyond_100():
    c = backend.db()
    for i in range(105):
        c.execute(
            "INSERT INTO transfer_requests(id,client_operation_id,type,status,payload_json,"
            "created_by,created_at) VALUES(?,?,?,?,?,?,?)",
            (f"tr_{i:03d}", f"op-{i}", "OUTBOUND", "PENDING_APPROVAL", '{"items": []}', "u_op", ts(i)),
        )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        login(client, "wh-admin")
        p1 = client.get("/admin/flows")
        assert "tr_104" in p1.text and "tr_000" not in p1.text
        assert "共 2 页 / 105 条" in p1.text
        p2 = client.get("/admin/flows?page=2&status=PENDING_APPROVAL")
        assert "tr_000" in p2.text and "tr_104" not in p2.text
        assert "status=PENDING_APPROVAL&amp;page=1" in p2.text or "status=PENDING_APPROVAL&page=1" in p2.text


def test_orders_list_paginates_beyond_100():
    c = backend.db()
    c.execute("DELETE FROM production_orders WHERE order_no LIKE 'PAGE-%'")
    for i in range(105):
        c.execute(
            "INSERT INTO production_orders(id, order_no, product_name, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (f"po_{i:03d}", f"PAGE-{i:03d}", "产品", "RELEASED", ts(i), ts(i)),
        )
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM production_orders").fetchone()[0]
    c.close()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        p1 = client.get("/admin/orders")
        assert "PAGE-104" in p1.text and "PAGE-000" not in p1.text
        assert f"/ {total} 条" in p1.text
        last = client.get(f"/admin/orders?page={(total + 99) // 100}")
        assert "PAGE-000" in last.text


def test_exceptions_and_warehouse_paginate_beyond_100():
    c = backend.db()
    for i in range(105):
        c.execute(
            "INSERT INTO exceptions(id,type,status,description,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (f"ex_{i:03d}", "SHORTAGE", "PENDING" if i < 104 else "RESOLVED", "d", "u_op", ts(i)),
        )
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM exceptions WHERE status IN ('OPEN','PENDING')").fetchone()[0]
    c.close()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        p1 = client.get("/admin/exceptions")
        assert p1.status_code == 200
        assert "ex_000" not in p1.text
        # 统计卡片按全部异常计数
        assert re.search(r'待处理</p><p class="stat-num"[^>]*>%d<' % pending, p1.text)
        p2 = client.get("/admin/exceptions?page=2")
        assert "ex_000" in p2.text
        w1 = client.get("/admin/warehouse")
        assert "ex_000" not in w1.text and f"/ {total} 条" in w1.text
        w2 = client.get("/admin/warehouse?ex_page=2")
        assert "ex_000" in w2.text


# ---------------------------------------------------------------- P1-6 N+1


class _SqlCounter:
    def __init__(self, monkeypatch):
        self.n = 0
        real_connect = sqlite3.connect

        def connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(self._cb)
            return conn

        monkeypatch.setattr(backend.sqlite3, "connect", connect)

    def _cb(self, _sql):
        self.n += 1


def _seed_transfers_with_handovers(start: int, n: int):
    c = backend.db()
    for i in range(start, start + n):
        c.execute(
            "INSERT INTO transfer_requests(id,client_operation_id,type,status,payload_json,"
            "created_by,created_at,approved_by) VALUES(?,?,?,?,?,?,?,?)",
            (f"trn_{i:03d}", f"opn-{i}", "OUTBOUND", "APPROVED", '{"items": []}', "u_op", ts(i), "u_wh"),
        )
        c.execute(
            "INSERT INTO material_handovers(id,work_item_id,transfer_request_id,quantity,"
            "from_location,client_operation_id,status,created_by,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (f"ho_{i:03d}", f"wi_{i}", f"trn_{i:03d}", 1, "A-01", f"op-ho-{i}",
             "CONFIRMED" if i % 2 else "PENDING", "u_op", ts(i)),
        )
    c.commit()
    c.close()


def test_flows_list_query_count_independent_of_rows(monkeypatch):
    _seed_transfers_with_handovers(0, 5)
    with TestClient(backend.app) as client:
        login(client, "wh-admin")
        counter = _SqlCounter(monkeypatch)
        client.get("/admin/flows")
        small = counter.n
        _seed_transfers_with_handovers(5, 40)
        counter.n = 0
        page = client.get("/admin/flows")
        assert counter.n == small, (small, counter.n)
        assert "trn_044" in page.text


def test_transfer_list_items_match_single_row_projection():
    _seed_transfers_with_handovers(0, 12)
    user = user_row("wh-admin")
    listed = backend._list_transfers(status=None, page=1, page_size=100, user=user)["items"]
    c = backend.db()
    try:
        for item in listed:
            row = c.execute("SELECT * FROM transfer_requests WHERE id=?", (item["id"],)).fetchone()
            single = backend._public_transfer(c, row)
            assert {k: v for k, v in single.items() if k != "rejection_reason"} == \
                {k: v for k, v in item.items() if k != "rejection_reason"}
    finally:
        c.close()


def test_tasks_list_query_count_independent_of_rows(monkeypatch):
    seed_tasks_and_labor(3)
    with TestClient(backend.app) as client:
        login(client, "supervisor1")
        counter = _SqlCounter(monkeypatch)
        client.get("/admin/tasks")
        small = counter.n
        c = backend.db()
        for i in range(3, 20):
            c.execute(
                "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,assigned_assembler_id,status,"
                "progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"task_{i:03d}", "ORD-X", f"dev_{i}", f"DEV-{i}", "u_a1", "IN_PROGRESS", 0, 1, ts(i), ts(i)),
            )
        c.commit()
        c.close()
        counter.n = 0
        client.get("/admin/tasks")
        assert counter.n == small, (small, counter.n)


def test_batched_task_dicts_match_single():
    seed_tasks_and_labor(6)
    c = backend.db()
    try:
        c.execute(
            "INSERT INTO assembly_task_members(task_id,assembler_id,assignment_role,assigned_by,assigned_at)"
            " VALUES('task_001','u_a2','MEMBER','u_sup',?)", (ts(1),))
        c.execute(
            "INSERT INTO assembly_task_members(task_id,assembler_id,assignment_role,assigned_by,assigned_at)"
            " VALUES('task_001','u_a1','LEAD','u_sup',?)", (ts(1),))
        rows = c.execute("SELECT * FROM assembly_tasks ORDER BY created_at DESC").fetchall()
        batched = backend._assembly_tasks_to_dicts(c, rows)
        for row, item in zip(rows, batched):
            assert backend._assembly_tasks_to_dicts(c, [row])[0] == item
        t1 = next(i for i in batched if i["id"] == "task_001")
        assert [m["assemblerId"] for m in t1["members"]] == ["u_a1", "u_a2"]
        assert t1["accumulatedLaborMinutes"] == 31
        assert [s["stageNo"] for s in t1["stages"]] == [1, 2, 3]
    finally:
        c.close()


# ---------------------------------------------------------------- P1-7 索引


def test_performance_indexes_created_idempotently():
    backend.init_db()
    backend.init_db()
    c = backend.db()
    try:
        names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for name, table, columns in backend.PERFORMANCE_INDEXES:
            assert name in names, name
        plan = " ".join(str(tuple(r)) for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM material_work_items WHERE requirement_id=?", ("x",)))
        assert "idx_material_work_items_requirement" in plan
    finally:
        c.close()


def test_performance_indexes_skip_missing_columns():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE exceptions(id TEXT PRIMARY KEY)")  # 缺 created_at
    backend._ensure_performance_indexes(conn)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'").fetchall()


# ---------------------------------------------------------------- P1-8 任务页


def test_tasks_page_renders_assembler_options_once():
    seed_tasks_and_labor(5)
    with TestClient(backend.app) as client:
        login(client, "supervisor1")
        r = client.get("/admin/tasks")
        assert r.status_code == 200
        assert r.text.count('name="assemblerIds"') == 2  # 2 名装配工，只渲染一次
        assert r.text.count("data-assembler-picker") >= 5
        assert '<template id="assembler-options">' in r.text
