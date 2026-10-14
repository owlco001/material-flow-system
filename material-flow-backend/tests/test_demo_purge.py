"""清除演示 / 测试数据（系统页）。"""
import os

from fastapi.testclient import TestClient

import app.main as backend
from test_admin_exceptions_todo import seed, web_login


def _csrf(text):
    import re
    return re.search(r'name="csrf_token" value="([^"]+)"', text).group(1)


def _seed_fixture(monkeypatch):
    from app import seed as seed_mod
    monkeypatch.setenv("ENABLE_TEST_DATA_SEED", "true")
    monkeypatch.setenv("TEST_DATA_SEED_PASSWORD", "Seed@2026x")
    seed_mod.seed_test_fixtures()


def test_plan_and_purge_keeps_real_data(monkeypatch):
    seed()
    _seed_fixture(monkeypatch)
    from app import demo_purge
    c = backend.db()
    c.execute("INSERT INTO materials(id, code, name, unit, total_quantity, available_quantity) VALUES('u9-REAL','REAL-1','真料','个',5,5)")
    c.execute("INSERT INTO locations(id, code, name) VALUES('loc-real','14-D3','大宗 D3')")
    c.execute("INSERT INTO inventory(id, material_id, location_id, quantity) VALUES('inv-real','u9-REAL','loc-real',5)")
    c.commit()
    p = demo_purge.plan(c)
    assert p["total"] > 0
    assert any("AL-型材-002" in m for m in p["material_sample"])
    assert "26B-013" in p["order_nos"] and "MF_TEST_ORDER_01" in p["order_nos"]
    assert c.execute("SELECT COUNT(*) FROM materials WHERE code='AL-型材-002'").fetchone()[0] == 1  # plan 不写库
    demo_purge.purge(c, backend.now())
    left = c.execute("SELECT code FROM materials ORDER BY code").fetchall()
    assert [r[0] for r in left] == ["REAL-1"]
    assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no IN ('26B-013','MF_TEST_ORDER_01')").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM users WHERE id LIKE 'MF_TEST_SEED_V1%'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM users WHERE username='owlco'").fetchone()[0] == 1
    assert c.execute("SELECT quantity FROM inventory WHERE id='inv-real'").fetchone()[0] == 5
    assert not c.execute("PRAGMA foreign_key_check").fetchall()
    assert demo_purge.plan(c)["total"] == 0
    c.close()
    # 重启（init_db）不再补种演示订单
    backend.init_db()
    c = backend.db()
    assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='26B-013'").fetchone()[0] == 0
    c.close()


def test_system_page_purge_requires_confirm():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/system")
        assert page.status_code == 200 and "清除演示数据" in page.text and "26B-013" in page.text
        tok = _csrf(page.text)
        r = client.post("/admin/system/purge-demo", data={"csrf_token": tok, "confirm": "x"}, follow_redirects=False)
        assert r.status_code == 303 and "DEMO_CONFIRM" in r.headers["location"]
        r = client.post("/admin/system/purge-demo", data={"csrf_token": "bad", "confirm": "清除"}, follow_redirects=False)
        assert r.status_code == 403
        r = client.post("/admin/system/purge-demo", data={"csrf_token": tok, "confirm": "清除"}, follow_redirects=False)
        assert r.status_code == 303 and "DEMO_PURGED" in r.headers["location"]
        page = client.get("/admin/system")
        assert "没有演示 / 测试数据" in page.text and "已清除过" in page.text
