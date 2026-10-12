"""v0.7.0 管理台 UI 优化 Top5：表格容器、本地时间与中文标签、移动端修正、导航分组与面包屑、仓库默认库存视图。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as backend
from app import ui_labels

APP_DIR = Path(__file__).resolve().parents[1] / "app"
TEMPLATES = APP_DIR / "templates"
CSS = (APP_DIR / "static" / "admin" / "base.css").read_text(encoding="utf-8")

USERS = (
    ("u_admin", "owlco", "系统管理员", "ADMIN", "Admin@2026"),
    ("u_sup", "supervisor1", "车间主管甲", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
    ("u_plan", "planner1", "计划员甲", "PLANNER", "Plan@2026x"),
    ("u_wh", "wh-admin", "仓管员甲", "WAREHOUSE_ADMIN", "Wh@2026x"),
    ("u_asm", "fitter1", "装配工甲", "ASSEMBLER", "User@2026"),
)


def seed():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, name, role, pwd in USERS:
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, name, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed()
    yield


def form_csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, username, password):
    page = client.get("/admin/login")
    r = client.post("/admin/login", data={"username": username, "password": password,
                                          "csrf_token": form_csrf(page.text)}, follow_redirects=False)
    assert r.status_code == 303
    return client


RAW_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


# ---------------- 2. 时间与中文标签（纯函数） ----------------

def test_local_dt_converts_utc_to_beijing():
    assert ui_labels.local_dt("2026-10-12T11:34:30.713795+00:00") == "2026-10-12 19:34"
    assert ui_labels.local_dt("2026-10-12T20:00:00Z") == "2026-10-13 04:00"
    assert ui_labels.local_dt("2026-10-12T11:34:30") == "2026-10-12 19:34"  # 无时区按 UTC
    assert ui_labels.local_dt("2027-01-01") == "2027-01-01"  # 纯日期（效期）不换算
    assert ui_labels.local_dt(None) == "—" and ui_labels.local_dt("") == "—"
    assert ui_labels.local_dt("not-a-time") == "not-a-time"


def test_label_map_is_central_and_contextual():
    assert ui_labels.label("SHORTAGE", "exception_type") == "缺料"
    assert ui_labels.label("FLOW_TRANSFER_EXECUTED", "event") == "流转执行"
    assert ui_labels.label("DRAFT", "bom_status") == "草稿"
    assert ui_labels.label("ARRIVED", "workspace_status") == "到货"
    # 同一个码按语境取不同中文
    assert ui_labels.label("PENDING", "exception_status") == "待处理"
    assert ui_labels.label("PENDING", "handover_status") == "待确认"
    # 无 kind 也能兜底；未知码原样返回
    assert ui_labels.label("SHORTAGE") == "缺料"
    assert ui_labels.label("NO_SUCH_CODE") == "NO_SUCH_CODE"


def test_beijing_day_bounds():
    start = ui_labels.beijing_day_bounds("2026-10-12")
    end = ui_labels.beijing_day_bounds("2026-10-12", end=True)
    assert datetime.fromisoformat(start) == datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
    assert datetime.fromisoformat(end) == datetime(2026, 10, 12, 15, 59, 59, 999999, tzinfo=timezone.utc)
    assert ui_labels.beijing_day_bounds("") is None
    assert ui_labels.beijing_day_bounds("2026-10-12T00:00:00+08:00") == "2026-10-12T00:00:00+08:00"


# ---------------- 1. 表格容器 ----------------

def test_list_tables_are_wrapped_and_fill_width():
    assert re.search(r"\.table-wrap\s*\{[^}]*overflow-x:\s*auto", CSS)
    assert re.search(r"table\s*\{\s*display:\s*table;", CSS)
    assert "display: block; overflow-x: auto" not in CSS
    for path in TEMPLATES.glob("*.html"):
        if path.name == "base.html" or "print" in path.name:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith("<table"):
                prev = lines[i - 1].strip() if i else ""
                assert prev.startswith('<div class="table-wrap"'), f"{path.name}:{i + 1} 表格未包 .table-wrap"


def test_orders_list_local_time_badges_and_links():
    c = backend.db()
    c.execute(
        "INSERT INTO production_orders(id,order_no,product_name,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        ("po_1", "SO-TEST", "齿轮箱总成", "IN_PROGRESS", "2026-10-12T11:34:30.713795+00:00",
         "2026-10-12T11:40:00+00:00"),
    )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        r = client.get("/admin/orders")
        assert r.status_code == 200
        assert "2026-10-12 19:34" in r.text and "2026-10-12 19:40" in r.text
        assert not RAW_ISO.search(r.text)
        assert 'class="table-wrap"' in r.text
        assert '<a href="/admin/orders?order_no=SO-TEST"><b>SO-TEST</b></a>' in r.text
        assert '<span class="badge b-warn">生产中</span>' in r.text
        assert 'data-confirm="确认完成订单 SO-TEST？完成后不可撤回。"' in r.text


# ---------------- 4. 导航与面包屑 ----------------

def test_nav_groups_icons_and_renames():
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        html = client.get("/admin/users").text
        for group in ("工作区", "生产", "仓储", "主数据", "系统"):
            assert f"<span>{group}</span>" in html
        assert "<span>审计日志</span>" in html and "<span>交接记录</span>" in html
        assert "留痕</span>" not in html
        # 新建订单不在导航里
        nav = html[html.index('id="sideNav"'):html.index("</nav>")]
        assert "/admin/orders/new" not in nav
        # BOM / 物料主档、U9 / 系统更新 图标互不相同
        icon = lambda href: re.search(r'href="%s" title="[^"]*"><i><svg class="ico" aria-hidden="true"><use href="#([\w-]+)"' % re.escape(href), nav).group(1)
        assert icon("/admin/boms") != icon("/admin/material-master")
        assert icon("/admin/u9") != icon("/admin/system")
        # 条码归入仓储，模型/物料主档/图纸/BOM 归入主数据
        sec = lambda key: nav[nav.index(f'data-sec="{key}"'):].split('class="side-sec', 1)[0]
        assert "/admin/barcodes" in sec("wh")
        for href in ("/admin/models", "/admin/material-master", "/admin/drawings", "/admin/boms"):
            assert href in sec("master")
        # 当前页所在分组默认展开
        assert re.search(r'class="side-sec open active-sec" data-sec="sys"', nav)


def test_new_order_button_role_scoped():
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        assert 'href="/admin/orders/new"' in client.get("/admin/orders").text
    with TestClient(backend.app) as client:
        login(client, "planner1", "Plan@2026x")
        assert 'href="/admin/orders/new"' in client.get("/admin/orders").text
    with TestClient(backend.app) as client:
        login(client, "supervisor1", "Sup@2026x")
        html = client.get("/admin/orders").text
        assert 'href="/admin/orders/new"' not in html
        # 角色可见性不变：主管仍看不到用户/审计/流转
        assert 'href="/admin/users"' not in html and 'href="/admin/flows"' not in html
        assert 'href="/admin/boms"' in html and 'href="/admin/tasks"' in html


def test_title_and_breadcrumb_not_duplicated():
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        html = client.get("/admin/orders").text
        assert "<title>生产订单 · 智慧工厂</title>" in html
        assert '<div class="crumb"><b>生产订单</b></div>' in html
        assert "生产订单 · 智慧工厂</b>" not in html
        login_html = client.get("/admin/login").text
    with TestClient(backend.app) as client:
        r = client.get("/admin/login")
        assert "<title>登录 · 智慧工厂</title>" in r.text
        assert 'id="agent-fab"' not in r.text  # 未登录不显示助手悬浮球
    assert login_html


# ---------------- 3. 移动端 ----------------

def test_mobile_css_rules():
    assert re.search(r"@media \(max-width: 640px\)\s*\{\s*\.mat-list[^}]*\}\s*\.mat-row\s*\{[^}]*grid-template-areas", CSS)
    assert ".mat-row > * { min-width: 0; }" in CSS
    assert re.search(r"@media \(max-width: 900px\)\s*\{\s*\.ag-main\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\)", CSS)
    agent = (TEMPLATES / "agent.html").read_text(encoding="utf-8")
    assert '<div class="ag-main">' in agent and "grid-template-columns:1fr 1fr;gap:16px" not in agent
    head_js = (APP_DIR / "static" / "admin" / "base-head.js").read_text(encoding="utf-8")
    assert "function friendlyAgentError" in head_js and "未配置 API Key" in head_js
    agent_js = (APP_DIR / "static" / "admin" / "agent.js").read_text(encoding="utf-8")
    assert "friendlyAgentError(" in agent_js


# ---------------- 2. 页面级中文化 ----------------

def _seed_warehouse():
    c = backend.db()
    c.execute("INSERT INTO locations(id,code,name) VALUES(?,?,?)", ("loc_1", "A-01", "一号库位"))
    for mid, code, name, avail, cat in (
        ("mat_ok", "M-OK", "轴承", 80, "机械"),
        ("mat_low", "M-LOW", "螺栓", 3, "机械"),
        ("mat_out", "M-OUT", "控制器", 0, "电气"),
    ):
        c.execute(
            "INSERT INTO materials(id,code,name,specification,unit,batch_no,expiry_date,"
            "total_quantity,available_quantity,version,category) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (mid, code, name, "S", "个", "B1", None, 100, avail, 1, cat),
        )
    c.execute(
        "INSERT INTO exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ex_1", "mat_low", "SHORTAGE", 10, 8, -2, "PENDING", "缺件两只", "[]", "u_wh",
         "2026-10-12T02:00:00+00:00", None, None, "SO-TEST", "dev_1"),
    )
    c.commit()
    c.close()


def test_warehouse_default_inventory_list_and_exception_summary():
    _seed_warehouse()
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        r = client.get("/admin/warehouse")
        assert r.status_code == 200
        html = r.text
        # 默认就有库存列表，缺料/低库存置顶
        assert html.index("M-OUT") < html.index("M-LOW") < html.index("M-OK")
        assert "缺料</span>" in html and "低库存</span>" in html
        # 异常区是摘要：中文类型、本地时间、链接到异常处理页
        assert "缺件两只" in html and ">缺料</span>" in html and "SHORTAGE</td>" not in html
        assert "2026-10-12 10:00" in html
        assert 'href="/admin/exceptions"' in html
        # 搜索过滤
        q = client.get("/admin/warehouse?q=螺栓").text
        assert "M-LOW" in q and "M-OK</b>" not in q
        cat = client.get("/admin/warehouse?cat=电气").text
        assert "M-OUT" in cat and "M-OK</b>" not in cat
    # 仓库管理员看不到 /admin/exceptions（无权限），但仍可在本页审核
    with TestClient(backend.app) as client:
        login(client, "wh-admin", "Wh@2026x")
        html = client.get("/admin/warehouse").text
        assert "查看全部异常" not in html
        assert 'action="/admin/warehouse/exceptions/ex_1/review"' in html


def test_audit_date_filter_chinese_event_and_operator_name():
    c = backend.db()
    rows = (
        ("FLOW_TRANSFER_EXECUTED", "MATERIAL_FLOW", "gt_1", "2026-10-11T17:00:00+00:00"),  # 北京 10-12 01:00
        ("ORDER_CREATED", "PRODUCTION_ORDER", "SO-1", "2026-10-10T03:00:00+00:00"),       # 北京 10-10 11:00
    )
    for i, (ev, et, eid, ts) in enumerate(rows):
        c.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
            "request_id,client_operation_id,before_json,after_json,server_time,result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ev, et, eid, "u_wh", "WAREHOUSE_ADMIN", f"req-{i}", f"op-{i}", "{}", "{}", ts, "SUCCESS"),
        )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        html = client.get("/admin/audit?from=2026-10-12&to=2026-10-12").text
        assert 'type="date"' in html
        assert "流转执行" in html and "订单创建</td>" not in html
        assert "2026-10-12 01:00" in html and "仓管员甲" in html
        assert ">u_wh<" not in html
        by_name = client.get("/admin/audit?operatorId=仓管员甲").text
        assert "流转执行" in by_name and "订单创建" in by_name


def test_workspace_status_filter_is_chinese_select():
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        html = client.get("/admin/workspace").text
        assert '<select name="status" id="ws-status">' in html
        assert '<option value="ARRIVED" >到货</option>' in html or 'value="ARRIVED"' in html and ">到货</option>" in html
        assert "状态码（如 ARRIVED" not in html


def test_handovers_searchable_by_document_number():
    c = backend.db()
    c.execute(
        "INSERT INTO transfer_requests(id,client_operation_id,type,document_no,status,payload_json,"
        "created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("tr_1", "op-tr-1", "OUTBOUND", "DOC-2026-001", "APPROVED", '{"items": []}', "u_wh", backend.now()),
    )
    c.execute(
        "INSERT INTO material_handovers(id,work_item_id,transfer_request_id,quantity,"
        "from_location,client_operation_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("ho_1", "wi_1", "tr_1", 3, "A-01", "op-ho-1", "PENDING", "u_wh", backend.now()),
    )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        login(client, "wh-admin", "Wh@2026x")
        lst = client.get("/admin/handovers").text
        assert "<b>DOC-2026-001</b>" in lst and "仓管员甲" in lst
        hit = client.get("/admin/handovers?query=DOC-2026-001").text
        assert "交接时间线" in hit and "ho_1" in hit
        miss = client.get("/admin/handovers?query=NOPE-1").text
        assert "未找到匹配的交接记录" in miss


def test_labor_filter_by_names():
    with TestClient(backend.app) as client:
        login(client, "supervisor1", "Sup@2026x")
        html = client.get("/admin/reports/labor").text
        assert 'name="deviceNo"' in html and 'name="assembler"' in html
        assert "人员 ID" not in html and "机台 ID" not in html
        miss = client.get("/admin/reports/labor?assembler=不存在的人").text
        assert "暂无数据" in miss


def test_release_page_chinese_field_labels():
    with TestClient(backend.app) as client:
        login(client, "owlco", "Admin@2026")
        html = client.get("/admin/releases").text
        assert "版本号" in html and "版本名称" in html
        assert "<label>versionCode" not in html and "<label>versionName" not in html
