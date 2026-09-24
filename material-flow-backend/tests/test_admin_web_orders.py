"""S7 生产订单查询契约测试（docs/admin-web-contract.md §8.6）。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "管理员", "ADMIN", backend.hash_password("Admin@2026"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_sup", "supervisor1", "车间主管", "WORKSHOP_SUPERVISOR", backend.hash_password("Sup@2026x"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_wh", "wh-admin", "仓库管理员", "WAREHOUSE_ADMIN", backend.hash_password("Wh@2026x"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_asm", "fitter1", "装配工甲", "ASSEMBLER", backend.hash_password("User@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_order():
    c = backend.db()
    c.execute(
        "INSERT INTO production_orders(id,order_no,product_name,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        ("po_1", "SO-TEST", "齿轮箱总成", "IN_PROGRESS", backend.now(), backend.now()),
    )
    c.execute(
        "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,"
        "task_version,assigned_assembler_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("task_1", "SO-TEST", "dev_1", "MC-001", "IN_PROGRESS", 1, 1, "u_asm",
         backend.now(), backend.now()),
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


def test_orders_supervisor_ok_warehouse_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/orders").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "wh-admin", "Wh@2026x").status_code == 303
        assert client2.get("/admin/orders").status_code == 403


def test_orders_list_shows_seeded_row():
    seed_order()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/orders")
        assert r.status_code == 200
        assert "SO-TEST" in r.text and "齿轮箱总成" in r.text and "IN_PROGRESS" in r.text


def test_orders_detail_hit_and_missing():
    seed_order()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        hit = client.get("/admin/orders?order_no=SO-TEST")
        assert hit.status_code == 200
        assert "订单详情" in hit.text and "MC-001" in hit.text
        miss = client.get("/admin/orders?order_no=NOPE")
        assert miss.status_code == 200
        assert "订单不存在" in miss.text


def test_orders_empty_db():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/orders")
        # init_db 自带演示订单种子（DEMO 单据），列表恒非空——锚定该事实。
        assert r.status_code == 200 and "订单列表" in r.text and "26B-013" in r.text
