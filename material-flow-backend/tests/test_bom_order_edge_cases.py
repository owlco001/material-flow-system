import csv
import io
import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402


PASSWORD = "TestUser@2026"


def rid():
    return str(uuid.uuid4())


def request_headers(operation_id):
    return {"X-Request-Id": rid(), "Idempotency-Key": operation_id}


def csv_bytes(model_code="M-001"):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "modelCode", "materialCode", "materialName", "specification",
            "unit", "quantity", "scrapRate", "substituteMaterialCodes",
        ],
    )
    writer.writeheader()
    writer.writerow({
        "modelCode": model_code, "materialCode": "MTR-001", "materialName": "轴承",
        "specification": "", "unit": "件", "quantity": "2", "scrapRate": "0",
        "substituteMaterialCodes": "",
    })
    return output.getvalue().encode()


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


def add_user(user_id, username, role):
    c = backend.db()
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (user_id, username, username, role, backend.hash_password(PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def login(client, username="owlco", password="Admin@2026"):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": rid()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def preview(client, auth):
    response = client.post(
        "/api/v1/boms/import/preview",
        files={"file": ("bom.csv", csv_bytes(), "text/csv")},
        data={"modelCode": "M-001"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()["previewId"]


def commit(client, auth, preview_id, operation_id=None, publish=False):
    operation_id = operation_id or rid()
    payload = {"previewId": preview_id, "clientOperationId": operation_id, "publish": publish}
    return client.post(
        "/api/v1/boms/import/commit",
        json=payload,
        headers={**auth, **request_headers(operation_id)},
    )


def test_bom_preview_is_bound_to_user_and_rejects_invalid_or_expired_commit(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch, with_model=True)
    with TestClient(backend.app) as client:
        add_user("planner-1", "planner1", "PLANNER")
        admin = login(client)
        planner = login(client, "planner1", PASSWORD)
        preview_id = preview(client, admin)

        cross_user = commit(client, planner, preview_id)
        assert cross_user.status_code == 409
        assert cross_user.json()["error"]["code"] == "BOM_PREVIEW_EXPIRED"

        missing = commit(client, admin, rid())
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "BOM_PREVIEW_EXPIRED"

        expired_id = preview(client, admin)
        backend.BOM_PREVIEWS[expired_id]["created"] -= 1801
        expired = commit(client, admin, expired_id)
        assert expired.status_code == 409
        assert expired.json()["error"]["code"] == "BOM_PREVIEW_EXPIRED"

        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM bom_versions").fetchone()[0] == 0
        c.close()


def test_bom_commit_rejects_idempotency_payload_conflict_without_partial_rows(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch, with_model=True)
    with TestClient(backend.app) as client:
        auth = login(client)
        preview_id = preview(client, auth)
        operation_id = rid()
        first = commit(client, auth, preview_id, operation_id, publish=True)
        assert first.status_code == 200
        conflict = commit(client, auth, preview_id, operation_id, publish=False)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM bom_versions").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM bom_items").fetchone()[0] == 1
        c.close()


def order_payload(operation_id, bom_version_id="bom-pub", order_no="SO-EDGE"):
    return {
        "clientOperationId": operation_id, "orderNo": order_no, "productName": "产品",
        "plannedQuantity": 1,
        "models": [{"modelCode": "M-001", "modelName": "机型", "plannedQuantity": 1,
                    "bomVersionId": bom_version_id}],
    }


def seed_published_bom():
    c = backend.db()
    c.execute(
        "INSERT INTO bom_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("bom-pub", "M-001", 1, "PUBLISHED", "sha", 1, "u_admin", backend.now(), "u_admin", backend.now()),
    )
    c.commit()
    c.close()


def test_order_requires_published_bom_and_conflicting_replay_is_atomic(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client)
        operation_id = rid()
        unpublished = client.post(
            "/api/v1/production-orders",
            json=order_payload(operation_id, "missing"),
            headers={**auth, **request_headers(operation_id)},
        )
        assert unpublished.status_code == 422
        assert unpublished.json()["error"]["code"] == "BOM_NOT_PUBLISHED"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders").fetchone()[0] == 1
        c.close()

        seed_published_bom()
        first = client.post(
            "/api/v1/production-orders",
            json=order_payload(operation_id),
            headers={**auth, **request_headers(operation_id)},
        )
        assert first.status_code == 200
        conflict = client.post(
            "/api/v1/production-orders",
            json=order_payload(operation_id, order_no="SO-OTHER"),
            headers={**auth, **request_headers(operation_id)},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_orders").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM production_order_models").fetchone()[0] == 1
        c.close()


def test_order_device_writes_enforce_role_capability_version_and_transactionality(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        add_user("operator-1", "operator1", "OPERATOR")
        operator = login(client, "operator1", PASSWORD)
        denied_op = rid()
        denied = client.post(
            "/api/v1/devices",
            json={"clientOperationId": denied_op, "deviceNo": "D-DENY", "deviceName": "机台", "workshop": "A", "modelCapability": "M-001"},
            headers={**operator, **request_headers(denied_op)},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "FORBIDDEN"

        admin = login(client)
        seed_published_bom()
        order_op = rid()
        order = client.post(
            "/api/v1/production-orders",
            json=order_payload(order_op),
            headers={**admin, **request_headers(order_op)},
        )
        assert order.status_code == 200
        device_op = rid()
        device = client.post(
            "/api/v1/devices",
            json={"clientOperationId": device_op, "deviceNo": "D-EDGE", "deviceName": "机台", "workshop": "A", "modelCapability": "OTHER"},
            headers={**admin, **request_headers(device_op)},
        )
        assert device.status_code == 200
        assignment_op = rid()
        payload = {"clientOperationId": assignment_op, "deviceId": device.json()["deviceId"], "expectedVersion": 1}
        mismatch = client.post(
            "/api/v1/production-orders/SO-EDGE/models/M-001/assign-device",
            json=payload,
            headers={**admin, **request_headers(assignment_op)},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "DEVICE_CAPABILITY_MISMATCH"

        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_model_devices").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM assembly_tasks").fetchone()[0] == 0
        c.execute("UPDATE devices SET model_capability='M-001' WHERE device_no='D-EDGE'")
        c.commit()
        c.close()

        ok = client.post(
            "/api/v1/production-orders/SO-EDGE/models/M-001/assign-device",
            json=payload,
            headers={**admin, **request_headers(assignment_op)},
        )
        assert ok.status_code == 200
        stale_op = rid()
        stale = client.post(
            "/api/v1/production-orders/SO-EDGE/models/M-001/assign-device",
            json={**payload, "clientOperationId": stale_op, "expectedVersion": 1},
            headers={**admin, **request_headers(stale_op)},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "ORDER_MODEL_VERSION_CONFLICT"
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_model_devices").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM assembly_tasks").fetchone()[0] == 1
        c.close()
