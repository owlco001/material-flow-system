"""R02 precise write contracts: binding, stocktake, and exception flows."""
from __future__ import annotations

import os
import tempfile
import uuid

os.environ["MATERIAL_FLOW_DATA"] = os.path.join(tempfile.mkdtemp(prefix="mf_r02_"), "data")
os.environ["MATERIAL_FLOW_UPLOADS"] = os.path.join(tempfile.mkdtemp(prefix="mf_r02_uploads_"), "uploads")
os.environ["INITIAL_ADMIN_PASSWORD"] = "Admin@2026"

from fastapi.testclient import TestClient

from app import main as backend


def _headers(token: str, operation: str | None = None, request_id: str | None = None) -> dict[str, str]:
    operation = operation or str(uuid.uuid4())
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": request_id or str(uuid.uuid4()),
        "Idempotency-Key": operation,
    }


def _login(client: TestClient, username: str = "owlco", password: str = "Admin@2026") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": "r02-test"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def test_r02_models_validate_headers_permissions_and_idempotency():
    with TestClient(backend.app) as client:
        admin = _login(client)
        operation = str(uuid.uuid4())
        payload = {
            "materialCode": "MTR-001",
            "locationCode": "A-01-03",
            "quantity": 7,
            "evidenceIds": ["file-1"],
            "clientOperationId": operation,
        }
        first = client.post("/api/v1/location-bindings", json=payload, headers=_headers(admin, operation))
        assert first.status_code == 200, first.text
        repeated = client.post("/api/v1/location-bindings", json=payload, headers=_headers(admin, operation))
        assert repeated.status_code == 200
        assert repeated.json()["idempotent"] is True
        assert client.post(
            "/api/v1/location-bindings", json=payload,
            headers=_headers(admin, operation, request_id="not-a-uuid"),
        ).status_code == 400
        mismatched = str(uuid.uuid4())
        assert client.post(
            "/api/v1/location-bindings", json=payload,
            headers=_headers(admin, mismatched),
        ).json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"
        invalid = client.post(
            "/api/v1/location-bindings",
            json={"materialCode": "MTR-001", "locationCode": "A-01-03", "quantity": -1},
            headers=_headers(admin),
        )
        assert invalid.status_code == 422


def test_r02_stocktake_and_exception_repeat_writes_keep_audit_request_id():
    with TestClient(backend.app) as client:
        admin = _login(client)
        stock_op = str(uuid.uuid4())
        stock = client.post(
            "/api/v1/stocktakes",
            json={"materialCode": "MTR-001", "actualQuantity": 980, "clientOperationId": stock_op},
            headers=_headers(admin, stock_op),
        )
        assert stock.status_code == 200, stock.text
        repeated = client.post(
            "/api/v1/stocktakes",
            json={"materialCode": "MTR-001", "actualQuantity": 980, "clientOperationId": stock_op},
            headers=_headers(admin, stock_op),
        )
        assert repeated.json()["idempotent"] is True
        exception_op = str(uuid.uuid4())
        exception = client.post(
            "/api/v1/exceptions",
            json={
                "orderNo": "26B-013", "deviceId": "dev_demo_HZ01", "materialId": "mat_ctl_cabinet",
                "type": "SHORTAGE", "bookQuantity": 2, "actualQuantity": 1,
                "description": "R02", "clientOperationId": exception_op,
            },
            headers=_headers(admin, exception_op),
        )
        assert exception.status_code == 200, exception.text
        assert exception.json()["difference"] == -1
        repeated_exception = client.post(
            "/api/v1/exceptions",
            json={
                "orderNo": "26B-013", "deviceId": "dev_demo_HZ01", "materialId": "mat_ctl_cabinet",
                "bookQuantity": 2, "actualQuantity": 1, "clientOperationId": exception_op,
            },
            headers=_headers(admin, exception_op),
        )
        assert repeated_exception.json()["idempotent"] is True
        audit_rows = backend.db().execute(
            "SELECT request_id FROM audit_logs WHERE resource_type IN ('LOCATION', 'STOCKTAKE', 'EXCEPTION')"
        ).fetchall()
        assert audit_rows and all(row["request_id"] for row in audit_rows)
