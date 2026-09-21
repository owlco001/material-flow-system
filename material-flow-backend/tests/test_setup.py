import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

ROOT = tempfile.mkdtemp(prefix="mf_setup_")
os.environ["MATERIAL_FLOW_DATA"] = f"{ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "Admin@2026"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402


def headers(key):
    return {"X-Request-Id": str(uuid.uuid4()), "Idempotency-Key": key}


def reset():
    shutil.rmtree(f"{ROOT}/data", ignore_errors=True)
    shutil.rmtree(f"{ROOT}/uploads", ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_setup_database():
    reset()
    yield
    reset()


def clear_seeded_admin():
    c = backend.db()
    c.execute("DELETE FROM sessions")
    c.execute("DELETE FROM consumed_refresh_tokens")
    c.execute("DELETE FROM employee_managers")
    c.execute("DELETE FROM setup_operations")
    c.execute("DELETE FROM setup_state")
    c.execute("DELETE FROM users WHERE username='owlco'")
    c.commit()
    c.close()


def test_setup_status_uninitialized_and_success():
    with TestClient(backend.app) as client:
        clear_seeded_admin()
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json()["initialized"] is False
        key = str(uuid.uuid4())
        response = client.post("/api/v1/setup/initialize-admin", json={
            "password": "SetupAdmin@2026", "confirmPassword": "SetupAdmin@2026",
            "clientOperationId": key,
        }, headers=headers(key))
        assert response.status_code == 200
        body = response.json()
        assert body["initialized"] is True
        assert body["username"] == "owlco"
        assert body["mustChangePassword"] is True
        assert "SetupAdmin@2026" not in response.text
        assert "password_hash" not in response.text


def test_setup_idempotency_and_conflict_do_not_overwrite_hash():
    with TestClient(backend.app) as client:
        clear_seeded_admin()
        key = str(uuid.uuid4())
        payload = {"password": "SetupAdmin@2026", "confirmPassword": "SetupAdmin@2026", "clientOperationId": key}
        first = client.post("/api/v1/setup/initialize-admin", json=payload, headers=headers(key))
        second = client.post("/api/v1/setup/initialize-admin", json=payload, headers=headers(key))
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        conflict = client.post("/api/v1/setup/initialize-admin", json={
            **payload, "password": "DifferentAdmin@2026", "confirmPassword": "DifferentAdmin@2026"
        }, headers=headers(key))
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
        other = str(uuid.uuid4())
        already = client.post("/api/v1/setup/initialize-admin", json={
            "password": "OtherAdmin@2026", "confirmPassword": "OtherAdmin@2026", "clientOperationId": other
        }, headers=headers(other))
        assert already.status_code == 409
        assert already.json()["error"]["code"] == "SETUP_ALREADY_INITIALIZED"
        login = client.post("/api/v1/auth/login", json={"username": "owlco", "password": "SetupAdmin@2026", "deviceId": "test"})
        assert login.status_code == 200
        assert login.json()["mustChangePassword"] is True


def test_setup_rejects_mismatched_passwords_without_leaking_secret():
    with TestClient(backend.app) as client:
        clear_seeded_admin()
        key = str(uuid.uuid4())
        response = client.post("/api/v1/setup/initialize-admin", json={
            "password": "SetupAdmin@2026", "confirmPassword": "WrongAdmin@2026", "clientOperationId": key,
        }, headers=headers(key))
        assert response.status_code == 400
        assert "SetupAdmin@2026" not in response.text
        assert "WrongAdmin@2026" not in response.text


def test_setup_concurrent_initialization_has_one_winner():
    reset()
    with TestClient(backend.app) as client:
        clear_seeded_admin()
        import concurrent.futures
        key_values = [(str(uuid.uuid4()), f"SetupAdmin{i}@2026") for i in range(2)]

        def attempt(item):
            key, password = item
            return client.post("/api/v1/setup/initialize-admin", json={
                "password": password, "confirmPassword": password, "clientOperationId": key,
            }, headers=headers(key)).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, key_values))
    assert sorted(results) == [200, 409]
