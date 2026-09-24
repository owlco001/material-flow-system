"""生产订单创建 API 契约测试（docs/order-creation-contract.md §3）。"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
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
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_users()
    yield


def auth_login(client, username, password):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": "contract-test-device"},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def order_body(**overrides):
    body = {
        "clientOperationId": str(uuid.uuid4()),
        "orderNo": "PO-2601",
        "productName": "齿轮箱总成",
        "models": [
            {"modelCode": "GearboxA", "modelName": "齿轮箱A型", "plannedQuantity": 5},
            {"modelCode": "GearboxB", "modelName": "齿轮箱B型", "plannedQuantity": 3},
        ],
    }
    body.update(overrides)
    return body


def test_create_order_success_and_side_effects():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        r = client.post("/api/v1/orders", json=order_body(), headers=headers)
        assert r.status_code == 201
        data = r.json()
        assert data["orderNo"] == "PO-2601" and data["status"] == "RELEASED"
        assert len(data["models"]) == 2 and data["idempotent"] is False
        c = backend.db()
        order = c.execute("SELECT * FROM production_orders WHERE order_no='PO-2601'").fetchone()
        models = c.execute(
            "SELECT model_code FROM production_order_models WHERE order_id=? ORDER BY model_code",
            (data["orderId"],),
        ).fetchall()
        audit = c.execute(
            "SELECT 1 FROM audit_events WHERE event_type='ORDER_CREATED' AND entity_id=?",
            (data["orderId"],),
        ).fetchone()
        ops = c.execute("SELECT action FROM order_operations WHERE order_id=?", (data["orderId"],)).fetchone()
        c.close()
        assert order["status"] == "RELEASED" and order["product_name"] == "齿轮箱总成"
        assert [m["model_code"] for m in models] == ["GearboxA", "GearboxB"]
        assert audit is not None and ops["action"] == "CREATE_ORDER"


def test_create_order_planner_ok_warehouse_forbidden():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "planner1", "Plan@2026")
        r = client.post("/api/v1/orders", json=order_body(orderNo="PO-P1"), headers=headers)
        assert r.status_code == 201
    with TestClient(backend.app) as client2:
        headers = auth_login(client2, "wh-admin", "Wh@2026x")
        r = client2.post("/api/v1/orders", json=order_body(orderNo="PO-W1"), headers=headers)
        assert r.status_code == 403
        c = backend.db()
        assert c.execute("SELECT 1 FROM production_orders WHERE order_no='PO-P1'").fetchone()
        count = c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='PO-W1'").fetchone()[0]
        c.close()
        assert count == 0


def test_create_order_validation_errors_are_atomic():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        for bad in (
            order_body(orderNo=""),
            order_body(models=[]),
            order_body(models=[{"modelCode": "GearboxA", "modelName": "x", "plannedQuantity": 0}]),
            order_body(models=[
                {"modelCode": "Dup1", "modelName": "x", "plannedQuantity": 1},
                {"modelCode": "Dup1", "modelName": "y", "plannedQuantity": 1},
            ]),
        ):
            r = client.post("/api/v1/orders", json=bad, headers=headers)
            assert r.status_code == 422
        c = backend.db()
        count = c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no LIKE 'PO-%'").fetchone()[0]
        c.close()
        assert count == 0


def test_create_order_conflicts():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        assert client.post("/api/v1/orders", json=order_body(), headers=headers).status_code == 201
        same_no = client.post("/api/v1/orders", json=order_body(orderNo="PO-2601",
                               clientOperationId=str(uuid.uuid4())), headers=headers)
        assert same_no.status_code == 409 and same_no.json()["error"]["message"] == "生产订单号已存在"
        same_model = client.post(
            "/api/v1/orders",
            json=order_body(orderNo="PO-2602", clientOperationId=str(uuid.uuid4()),
                            models=[{"modelCode": "GearboxA", "modelName": "x", "plannedQuantity": 1}]),
            headers=headers,
        )
        assert same_model.status_code == 409 and same_model.json()["error"]["message"] == "机型码已被占用"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no LIKE 'PO-%'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM production_order_models WHERE model_code LIKE 'Gearbox%'").fetchone()[0] == 2
        c.close()


def test_create_order_idempotent_replay_and_mismatch():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        body = order_body(orderNo="PO-IDEM")
        first = client.post("/api/v1/orders", json=body, headers=headers)
        assert first.status_code == 201
        replay = client.post("/api/v1/orders", json=body, headers=headers)
        assert replay.status_code == 201
        assert replay.json()["idempotent"] is True
        assert replay.json()["orderId"] == first.json()["orderId"]
        mismatch = client.post(
            "/api/v1/orders",
            json=order_body(orderNo="PO-OTHER", clientOperationId=body["clientOperationId"]),
            headers=headers,
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["message"] == "相同幂等键的请求体不一致"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='PO-IDEM'").fetchone()[0] == 1
        c.close()
