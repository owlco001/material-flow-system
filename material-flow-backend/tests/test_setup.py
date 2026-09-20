import importlib
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MATERIAL_FLOW_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MATERIAL_FLOW_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client, main


def payload(password="A_valid_setup_password_123!", key="6f7e8d9c-1234-4abc-8def-123456789abc"):
    return {"password": password, "confirmPassword": password, "idempotencyKey": key}


def test_uninitialized_status_is_public_and_does_not_leak_password(client):
    response = client[0].get("/api/v1/setup/status")
    assert response.status_code == 200
    assert response.json() == {"initialized": False, "username": "owlco"}
    assert "password" not in response.text.lower()


def test_initialize_admin_uses_argon2id_and_requires_change(client):
    test_client, main = client
    response = test_client.post("/api/v1/setup/initialize-admin", json=payload())
    assert response.status_code == 201
    assert response.json() == {"initialized": True, "username": "owlco", "mustChangePassword": True}
    row = main.db().execute("SELECT password_hash,must_change_password FROM users WHERE username='owlco'").fetchone()
    assert row["password_hash"].startswith("$argon2id$")
    assert "A_valid_setup_password_123!" not in row["password_hash"]
    assert row["must_change_password"] == 1


def test_repeat_same_payload_is_idempotent_and_different_payload_conflicts(client):
    test_client, main = client
    first = test_client.post("/api/v1/setup/initialize-admin", json=payload())
    repeat = test_client.post("/api/v1/setup/initialize-admin", json=payload())
    conflict = test_client.post("/api/v1/setup/initialize-admin", json=payload("Different_password_456!"))
    assert first.status_code == 201
    assert repeat.status_code == 200
    assert repeat.json()["idempotent"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    assert main.db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_concurrent_initialization_creates_one_admin(client):
    test_client, main = client
    body = payload()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: test_client.post("/api/v1/setup/initialize-admin", json=body), range(4)))
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]
    assert main.db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_business_is_blocked_until_first_password_change_and_change_is_allowed(client):
    test_client, _ = client
    test_client.post("/api/v1/setup/initialize-admin", json=payload())
    login = test_client.post("/api/v1/auth/login", json={"username": "owlco", "password": payload()["password"], "deviceId": "test-device"})
    assert login.status_code == 200
    assert login.json()["mustChangePassword"] is True
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    blocked = test_client.post("/api/v1/orders/material-status", json={}, headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    changed = test_client.post("/api/v1/auth/change-password", json={"currentPassword": payload()["password"], "newPassword": "New_valid_password_789!", "confirmPassword": "New_valid_password_789!"}, headers=headers)
    assert changed.status_code == 200
    allowed = test_client.post("/api/v1/orders/material-status", json={}, headers=headers)
    assert allowed.status_code == 200
