from __future__ import annotations

import csv
import io
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402


def rid() -> str:
    return str(uuid.uuid4())


def headers(operation_id: str) -> dict[str, str]:
    return {"X-Request-Id": rid(), "Idempotency-Key": operation_id}


def setup_backend(tmp_path, monkeypatch, *, with_model=False):
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend, "DB_PATH", tmp_path / "data" / "material_flow.db")
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
    if with_model:
        c.execute(
            "INSERT INTO production_order_models(id,model_code,model_name,created_at) VALUES(?,?,?,?)",
            ("model-1", "M-001", "测试机型", backend.now()),
        )
    c.commit()
    c.close()


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owlco", "password": "Admin@2026", "deviceId": rid()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def csv_bytes() -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "modelCode", "materialCode", "materialName", "specification", "unit",
            "quantity", "scrapRate", "substituteMaterialCodes",
        ],
    )
    writer.writeheader()
    writer.writerow({
        "modelCode": "M-001", "materialCode": "MTR-001", "materialName": "轴承",
        "specification": "", "unit": "件", "quantity": "2", "scrapRate": "0",
        "substituteMaterialCodes": "",
    })
    return out.getvalue().encode()


def seed_published_bom():
    c = backend.db()
    c.execute(
        "INSERT INTO bom_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("bom-pub", "M-001", 1, "PUBLISHED", "sha", 1, "u_admin", backend.now(), "u_admin", backend.now()),
    )
    c.commit()
    c.close()


def order_payload(operation_id: str, *, order_no="SO-CONCURRENT"):
    return {
        "clientOperationId": operation_id,
        "orderNo": order_no,
        "productName": "产品",
        "plannedQuantity": 1,
        "models": [{"modelCode": "M-001", "modelName": "机型", "plannedQuantity": 1, "bomVersionId": "bom-pub"}],
    }


def concurrent_posts(client, path, payload, auth, operation_id, count=8):
    barrier = threading.Barrier(count)

    def send(_):
        barrier.wait()
        with TestClient(backend.app) as thread_client:
            return thread_client.post(path, json=payload, headers={**auth, **headers(operation_id)})

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(send, range(count)))


def test_concurrent_order_idempotency_creates_one_order_and_model(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    seed_published_bom()
    with TestClient(backend.app) as client:
        auth = login(client)
        op = rid()
        responses = concurrent_posts(client, "/api/v1/production-orders", order_payload(op), auth, op)
        assert sum(r.status_code == 200 for r in responses) == len(responses)
        assert sum(r.json().get("idempotent") is False for r in responses) == 1
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='SO-CONCURRENT'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM production_order_models WHERE order_id=(SELECT id FROM production_orders WHERE order_no='SO-CONCURRENT')").fetchone()[0] == 1
        c.close()


def test_concurrent_bom_commit_creates_one_version_and_items(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch, with_model=True)
    with TestClient(backend.app) as client:
        auth = login(client)
        preview = client.post(
            "/api/v1/boms/import/preview",
            files={"file": ("bom.csv", csv_bytes(), "text/csv")},
            data={"modelCode": "M-001"}, headers=auth,
        ).json()["previewId"]
        op = rid()
        payload = {"previewId": preview, "clientOperationId": op, "publish": True}
        responses = concurrent_posts(client, "/api/v1/boms/import/commit", payload, auth, op)
        assert sum(r.status_code == 200 for r in responses) == len(responses)
        assert sum(r.json().get("idempotent") is False for r in responses) == 1
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM bom_versions WHERE model_code='M-001'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM bom_items").fetchone()[0] == 1
        c.close()


def test_concurrent_assignment_creates_one_task_and_disabled_maintenance_are_rejected(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    seed_published_bom()
    with TestClient(backend.app) as client:
        auth = login(client)
        order_op = rid()
        assert client.post("/api/v1/production-orders", json=order_payload(order_op), headers={**auth, **headers(order_op)}).status_code == 200
        device_op = rid()
        device = client.post(
            "/api/v1/devices",
            json={"clientOperationId": device_op, "deviceNo": "D-CONCURRENT", "deviceName": "机台", "workshop": "A", "modelCapability": "M-001"},
            headers={**auth, **headers(device_op)},
        ).json()
        op = rid()
        payload = {"clientOperationId": op, "deviceId": device["deviceId"], "expectedVersion": 1}
        responses = concurrent_posts(client, "/api/v1/production-orders/SO-CONCURRENT/models/M-001/assign-device", payload, auth, op)
        assert sum(r.status_code == 200 for r in responses) == len(responses)
        assert sum(r.json().get("idempotent") is False for r in responses) == 1
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_model_devices").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM assembly_tasks WHERE order_no='SO-CONCURRENT'").fetchone()[0] == 1
        c.execute("UPDATE devices SET status='DISABLED' WHERE id=?", (device["deviceId"],))
        c.commit()
        c.close()
        rejected_op = rid()
        rejected = client.post(
            "/api/v1/production-orders/SO-CONCURRENT/models/M-001/assign-device",
            json={**payload, "clientOperationId": rejected_op, "expectedVersion": 2},
            headers={**auth, **headers(rejected_op)},
        )
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "DEVICE_NOT_ACTIVE"


def test_maintenance_and_non_executable_order_cannot_bind(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    seed_published_bom()
    with TestClient(backend.app) as client:
        auth = login(client)
        order_op = rid()
        assert client.post("/api/v1/production-orders", json=order_payload(order_op), headers={**auth, **headers(order_op)}).status_code == 200
        device_op = rid()
        device = client.post(
            "/api/v1/devices",
            json={"clientOperationId": device_op, "deviceNo": "D-MAINT", "deviceName": "机台", "workshop": "A", "modelCapability": "M-001"},
            headers={**auth, **headers(device_op)},
        ).json()
        c = backend.db()
        c.execute("UPDATE devices SET status='MAINTENANCE' WHERE id=?", (device["deviceId"],))
        c.execute("UPDATE production_orders SET status='COMPLETED' WHERE order_no='SO-CONCURRENT'")
        c.commit()
        c.close()
        op = rid()
        response = client.post(
            "/api/v1/production-orders/SO-CONCURRENT/models/M-001/assign-device",
            json={"clientOperationId": op, "deviceId": device["deviceId"], "expectedVersion": 1},
            headers={**auth, **headers(op)},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ORDER_STATE_CONFLICT"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_model_devices").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM assembly_tasks").fetchone()[0] == 0
        c.close()


def test_database_failure_rolls_back_order_and_bom_half_products(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    seed_published_bom()
    with TestClient(backend.app, raise_server_exceptions=False) as client:
        auth = login(client)
        original_audit = backend.audit

        def fail_audit(*args, **kwargs):
            raise sqlite3.OperationalError("injected failure")

        import sqlite3
        monkeypatch.setattr(backend, "audit", fail_audit)
        op = rid()
        response = client.post("/api/v1/production-orders", json=order_payload(op, order_no="SO-ROLLBACK"), headers={**auth, **headers(op)})
        assert response.status_code == 500
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='SO-ROLLBACK'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM production_order_operations WHERE client_operation_id=?", (op,)).fetchone()[0] == 0
        c.close()
    monkeypatch.setattr(backend, "audit", original_audit)
