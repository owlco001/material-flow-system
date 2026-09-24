"""S22 订单状态流转契约测试（docs/order-creation-contract.md §4-5、
admin-web-contract.md §6.17/§8.16）。"""
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
        ("u_wh", "wh-admin", "WAREHOUSE_ADMIN", "Wh@2026x"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    for oid, ono, status in (
        ("ord_rel", "PO-REL", "RELEASED"),
        ("ord_prog", "PO-PROG", "IN_PROGRESS"),
        ("ord_done", "PO-DONE", "COMPLETED"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO production_orders(id,order_no,product_name,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?)",
            (oid, ono, "测试产品", status, backend.now(), backend.now()),
        )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed()
    yield


def auth_login(client, username, password):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": "s22-device"},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def form_csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def status_of(order_no: str) -> str:
    c = backend.db()
    row = c.execute("SELECT status FROM production_orders WHERE order_no=?", (order_no,)).fetchone()
    c.close()
    return row["status"] if row else ""


def test_status_advance_via_api_and_side_effects():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        r = client.post(
            "/api/v1/orders/PO-REL/status",
            json={"clientOperationId": str(uuid.uuid4()), "status": "IN_PROGRESS"},
            headers=headers,
        )
        assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS"
        assert status_of("PO-REL") == "IN_PROGRESS"
        c = backend.db()
        audit = c.execute(
            "SELECT 1 FROM audit_events WHERE event_type='ORDER_STATUS_CHANGED' AND entity_id=?",
            (r.json()["orderId"],),
        ).fetchone()
        ops = c.execute(
            "SELECT action FROM order_operations WHERE order_id=? AND action='SET_ORDER_STATUS'",
            (r.json()["orderId"],),
        ).fetchone()
        c.close()
        assert audit is not None and ops is not None


def test_status_state_machine_guards():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        skip = client.post(
            "/api/v1/orders/PO-REL/status",
            json={"clientOperationId": str(uuid.uuid4()), "status": "COMPLETED"},
            headers=headers,
        )
        assert skip.status_code == 409 and skip.json()["error"]["message"] == "订单状态不允许变更"
        same = client.post(
            "/api/v1/orders/PO-DONE/status",
            json={"clientOperationId": str(uuid.uuid4()), "status": "COMPLETED"},
            headers=headers,
        )
        assert same.status_code == 409
        missing = client.post(
            "/api/v1/orders/NOPE/status",
            json={"clientOperationId": str(uuid.uuid4()), "status": "IN_PROGRESS"},
            headers=headers,
        )
        assert missing.status_code == 404
        assert status_of("PO-REL") == "RELEASED"


def test_status_roles_and_idempotency():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "wh-admin", "Wh@2026x")
        forbidden = client.post(
            "/api/v1/orders/PO-PROG/status",
            json={"clientOperationId": str(uuid.uuid4()), "status": "COMPLETED"},
            headers=headers,
        )
        assert forbidden.status_code == 403
    with TestClient(backend.app) as client2:
        headers = auth_login(client2, "planner1", "Plan@2026")
        body = {"clientOperationId": str(uuid.uuid4()), "status": "COMPLETED"}
        first = client2.post("/api/v1/orders/PO-PROG/status", json=body, headers=headers)
        assert first.status_code == 200 and first.json()["idempotent"] is False
        replay = client2.post("/api/v1/orders/PO-PROG/status", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json()["idempotent"] is True
        assert status_of("PO-PROG") == "COMPLETED"


def test_status_change_via_web_form():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        page = client.get("/admin/orders")
        assert "开始生产" in page.text
        r = client.post(
            "/admin/orders/PO-REL/status",
            data={"csrf_token": form_csrf(page.text), "status": "IN_PROGRESS"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/orders?notice=order_status_changed"
        assert status_of("PO-REL") == "IN_PROGRESS"


def test_status_web_invalid_transition_inline_error():
    with TestClient(backend.app) as client:
        assert web_login(client, "planner1", "Plan@2026").status_code == 303
        page = client.get("/admin/orders")
        r = client.post(
            "/admin/orders/PO-DONE/status",
            data={"csrf_token": form_csrf(page.text), "status": "COMPLETED"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "订单状态不允许变更" in r.text
