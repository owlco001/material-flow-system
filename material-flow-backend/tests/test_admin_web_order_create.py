"""S21 web 生产订单创建契约测试（docs/admin-web-contract.md §6.16/§8.15）。"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, role, pwd in (
        ("u_admin", "owlco", "ADMIN", "Admin@2026"),
        ("u_planner", "planner1", "PLANNER", "Plan@2026"),
        ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed()
    yield


def form_csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def create_payload(**overrides):
    data = {
        "csrf_token": "",
        "orderNo": "PO-WEB1",
        "productName": "齿轮箱总成",
        "modelCode1": "WebBoxA",
        "modelName1": "A型",
        "modelQty1": "4",
        "modelCode2": "WebBoxB",
        "modelName2": "B型",
        "modelQty2": "2",
        "modelCode3": "",
    }
    data.update(overrides)
    return data


def test_order_new_page_roles():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        assert client.get("/admin/orders/new").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "supervisor1", "Sup@2026x").status_code == 303
        assert client2.get("/admin/orders/new").status_code == 403


def test_planner_sees_orders_list():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        assert client.get("/admin/orders").status_code == 200


def test_create_order_from_web_persists():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        page = client.get("/admin/orders/new")
        data = create_payload(csrf_token=form_csrf(page.text))
        r = client.post("/admin/orders/create", data=data, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/orders?notice=order_created"
        c = backend.db()
        order = c.execute("SELECT * FROM production_orders WHERE order_no='PO-WEB1'").fetchone()
        models = c.execute(
            "SELECT model_code, planned_quantity FROM production_order_models"
            " WHERE model_code LIKE 'WebBox%' ORDER BY model_code"
        ).fetchall()
        c.close()
        assert order is not None
        assert [(m["model_code"], m["planned_quantity"]) for m in models] == [("WebBoxA", 4), ("WebBoxB", 2)]


def test_create_order_conflict_inline_error():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        page = client.get("/admin/orders/new")
        ok = client.post("/admin/orders/create", data=create_payload(csrf_token=form_csrf(page.text)),
                         follow_redirects=False)
        assert ok.status_code == 303
        again = client.post("/admin/orders/create", data=create_payload(csrf_token=form_csrf(page.text)),
                            follow_redirects=False)
        assert again.status_code == 200
        assert "生产订单号已存在" in again.text


def test_create_order_validation_and_csrf():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        page = client.get("/admin/orders/new")
        no_models = create_payload(csrf_token=form_csrf(page.text), modelCode1="", modelCode2="")
        r = client.post("/admin/orders/create", data=no_models, follow_redirects=False)
        assert r.status_code == 200 and "1..20 项" in r.text
        bad_csrf = create_payload(csrf_token="")
        r2 = client.post("/admin/orders/create", data=bad_csrf, follow_redirects=False)
        assert r2.status_code == 403
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no LIKE 'PO-%'").fetchone()[0] == 0
        c.close()
