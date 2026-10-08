"""异常处理页（管理台首页）顶部待办卡片。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as backend
from tests.test_admin_web_nav import seed, web_login


def test_exceptions_page_shows_todo_cards():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/exceptions")
        assert r.status_code == 200
        for label, href in (
            ("待审批流转", "/admin/flows?status=PENDING_APPROVAL"),
            ("待确认交接", "/admin/handovers"),
            ("待料任务", "/admin/tasks?status=WAITING_MATERIAL"),
            ("装配进行中", "/admin/tasks?status=IN_PROGRESS"),
        ):
            assert label in r.text
            assert f'href="{href}"' in r.text


def _seed_expiry():
    from datetime import date, timedelta
    today = date.today()
    c = backend.db()
    c.execute("DELETE FROM materials WHERE code LIKE 'EXP-%'")
    for code, d, qty in (
        ("EXP-OLD", (today - timedelta(days=3)).isoformat(), 5),
        ("EXP-SOON", (today + timedelta(days=10)).strftime("%Y/%m/%d"), 2),
        ("EXP-FAR", (today + timedelta(days=200)).isoformat(), 9),
        ("EXP-EMPTY", (today - timedelta(days=1)).isoformat(), 0),
        ("EXP-BAD", "不详", 4),
    ):
        c.execute(
            "INSERT INTO materials(id, code, name, unit, expiry_date, total_quantity, available_quantity)"
            " VALUES(?,?,?,?,?,?,?)",
            ("m_" + code, code, code, "件", d, qty, qty),
        )
    c.commit()
    c.close()


def test_expiring_materials_filter():
    from app.admin_web import _expiring_materials
    _seed_expiry()
    c = backend.db()
    try:
        codes = [m["code"] for m in _expiring_materials(c) if m["code"].startswith("EXP-")]
    finally:
        c.close()
    assert codes == ["EXP-OLD", "EXP-SOON"]


def test_warehouse_shows_expiry_section():
    seed()
    _seed_expiry()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/warehouse")
        assert r.status_code == 200
        assert 'id="expiry"' in r.text
        assert "EXP-OLD" in r.text and "已过期" in r.text
        assert "EXP-SOON" in r.text and "天后到期" in r.text
        assert "EXP-FAR" not in r.text
        home = client.get("/admin/exceptions")
        assert "临期/过期物料" in home.text and 'href="/admin/warehouse#expiry"' in home.text
