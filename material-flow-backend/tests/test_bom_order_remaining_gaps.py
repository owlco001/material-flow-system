import csv
import io
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402

PASSWORD = "TestUser@2026"


def rid():
    return str(uuid.uuid4())


def headers(operation_id):
    return {"X-Request-Id": rid(), "Idempotency-Key": operation_id}


def setup_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend, "DB_PATH", tmp_path / "data" / "material_flow.db")
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
    c.commit()
    c.close()


def login(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owlco", "password": "Admin@2026", "deviceId": rid()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def seed_published_bom_and_model(*, include_model=True):
    c = backend.db()
    if include_model:
        c.execute(
            "INSERT INTO production_order_models(id,model_code,model_name,created_at) VALUES(?,?,?,?)",
            ("model-1", "M-001", "测试机型", backend.now()),
        )
    c.execute(
        "INSERT INTO bom_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("bom-pub", "M-001", 1, "PUBLISHED", "sha", 1, "u_admin", backend.now(), "u_admin", backend.now()),
    )
    c.commit()
    c.close()


def create_order(client, auth):
    operation_id = rid()
    response = client.post(
        "/api/v1/production-orders",
        json={
            "clientOperationId": operation_id,
            "orderNo": "SO-REMAINING",
            "productName": "产品",
            "plannedQuantity": 1,
            "models": [{
                "modelCode": "M-001",
                "modelName": "机型",
                "plannedQuantity": 1,
                "bomVersionId": "bom-pub",
            }],
        },
        headers={**auth, **headers(operation_id)},
    )
    assert response.status_code == 200, response.text


def create_device(client, auth, device_no):
    operation_id = rid()
    response = client.post(
        "/api/v1/devices",
        json={
            "clientOperationId": operation_id,
            "deviceNo": device_no,
            "deviceName": "测试机台",
            "workshop": "A",
            "modelCapability": "M-001",
        },
        headers={**auth, **headers(operation_id)},
    )
    assert response.status_code == 200, response.text
    return response.json()["deviceId"]


@pytest.mark.parametrize("device_status", ["DISABLED", "MAINTENANCE"])
def test_assign_rejects_each_non_active_device_without_half_finished_rows(
    tmp_path, monkeypatch, device_status
):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client)
        seed_published_bom_and_model(include_model=False)
        create_order(client, auth)
        device_id = create_device(client, auth, f"D-{device_status}")

        c = backend.db()
        c.execute("UPDATE devices SET status=? WHERE id=?", (device_status, device_id))
        c.commit()
        c.close()

        operation_id = rid()
        response = client.post(
            "/api/v1/production-orders/SO-REMAINING/models/M-001/assign-device",
            json={
                "clientOperationId": operation_id,
                "deviceId": device_id,
                "expectedVersion": 1,
            },
            headers={**auth, **headers(operation_id)},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "DEVICE_NOT_ACTIVE"

        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM production_model_devices").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM assembly_tasks").fetchone()[0] == 0
        c.close()


def bom_csv():
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
        "modelCode": "M-001", "materialCode": "MTR-001", "materialName": "轴承",
        "specification": "", "unit": "件", "quantity": "2", "scrapRate": "0",
        "substituteMaterialCodes": "",
    })
    return output.getvalue().encode()


def test_bom_commit_exception_rolls_back_version_items_and_idempotency_record(
    tmp_path, monkeypatch
):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app, raise_server_exceptions=False) as client:
        auth = login(client)
        seed_published_bom_and_model(include_model=False)
        c = backend.db()
        c.execute(
            "INSERT INTO production_order_models(id,model_code,model_name,created_at) VALUES(?,?,?,?)",
            ("model-1", "M-001", "测试机型", backend.now()),
        )
        c.commit()
        c.close()
        preview = client.post(
            "/api/v1/boms/import/preview",
            files={"file": ("bom.csv", bom_csv(), "text/csv")},
            data={"modelCode": "M-001"},
            headers=auth,
        )
        assert preview.status_code == 200, preview.text
        operation_id = rid()

        c = backend.db()
        c.execute("""
            CREATE TRIGGER fail_bom_commit_audit
            BEFORE INSERT ON audit_events
            WHEN NEW.event_type='BOM_IMPORT'
            BEGIN SELECT RAISE(ABORT, 'injected BOM commit failure'); END
        """)
        c.commit()
        c.close()
        response = client.post(
            "/api/v1/boms/import/commit",
            json={
                "previewId": preview.json()["previewId"],
                "clientOperationId": operation_id,
                "publish": True,
            },
            headers={**auth, **headers(operation_id)},
        )
        assert response.status_code == 500, response.text

        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM bom_versions").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM bom_items").fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type='BOM_IMPORT' AND client_operation_id=?",
            (operation_id,),
        ).fetchone()[0] == 0
        c.close()
