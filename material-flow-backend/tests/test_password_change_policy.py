import os
import tempfile
from pathlib import Path
import sys
import shutil

import pytest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = tempfile.mkdtemp(prefix="mf_password_policy_")
os.environ.update(
    MATERIAL_FLOW_DATA=f"{ROOT}/data",
    MATERIAL_FLOW_UPLOADS=f"{ROOT}/uploads",
    INITIAL_ADMIN_PASSWORD="Admin@2026",
)

from app import main as backend  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database():
    shutil.rmtree(f"{ROOT}/data", ignore_errors=True)
    shutil.rmtree(f"{ROOT}/uploads", ignore_errors=True)
    yield


def login(client, username, password, device=None):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": device or username},
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth(session):
    return {"Authorization": f"Bearer {session['accessToken']}"}


def admin_session(client):
    session = login(client, "owlco", "Admin@2026")
    assert session["mustChangePassword"] is True
    changed = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": "Admin@2026", "newPassword": "AdminChanged@2026"},
        headers=auth(session),
    )
    assert changed.status_code == 200
    return login(client, "owlco", "AdminChanged@2026")


def test_initial_admin_requires_password_change_before_business_access():
    with TestClient(backend.app) as client:
        session = login(client, "owlco", "Admin@2026")
        assert session["mustChangePassword"] is True
        assert session["user"]["mustChangePassword"] is True
        blocked = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(session))
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"oldPassword": "Admin@2026", "newPassword": "AdminChanged@2026"},
            headers=auth(session),
        )
        assert changed.status_code == 200
        fresh = login(client, "owlco", "AdminChanged@2026")
        assert fresh["mustChangePassword"] is False
        assert client.get("/api/v1/materials/MTR-001/inventory", headers=auth(fresh)).status_code == 200


def test_first_login_is_consistent_and_blocks_business_until_password_change():
    with TestClient(backend.app) as client:
        admin = admin_session(client)
        created = client.post(
            "/api/v1/admin/users",
            json={"employeeNo": "TEMP-1", "displayName": "临时用户", "role": "OPERATOR", "password": "Temp@2026"},
            headers=auth(admin),
        )
        assert created.status_code == 200
        session = login(client, "TEMP-1", "Temp@2026")
        assert session["mustChangePassword"] is True
        assert session["user"]["mustChangePassword"] is True
        blocked = client.get("/api/v1/materials/MTR-001/inventory", headers=auth(session))
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert client.get("/healthz").status_code == 200


def test_login_response_and_session_state_preserve_must_change_flag():
    with TestClient(backend.app) as client:
        admin = admin_session(client)
        client.post(
            "/api/v1/admin/users",
            json={"employeeNo": "TEMP-STATE", "displayName": "状态用户", "role": "OPERATOR", "password": "Temp@2026"},
            headers=auth(admin),
        )
        session = login(client, "TEMP-STATE", "Temp@2026")
        assert session["mustChangePassword"] is True
        assert session["user"]["mustChangePassword"] is True


def test_change_password_requires_old_password_and_clears_flag():
    with TestClient(backend.app) as client:
        admin = admin_session(client)
        client.post(
            "/api/v1/admin/users",
            json={"employeeNo": "TEMP-2", "displayName": "临时用户", "role": "OPERATOR", "password": "Temp@2026"},
            headers=auth(admin),
        )
        session = login(client, "TEMP-2", "Temp@2026")
        wrong = client.post(
            "/api/v1/auth/change-password",
            json={"oldPassword": "Wrong@2026", "newPassword": "New@2026"},
            headers=auth(session),
        )
        assert wrong.status_code == 401
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"oldPassword": "Temp@2026", "newPassword": "New@2026"},
            headers=auth(session),
        )
        assert changed.status_code == 200
        fresh = login(client, "TEMP-2", "New@2026")
        assert fresh["mustChangePassword"] is False
        assert fresh["user"]["mustChangePassword"] is False
        assert client.get("/api/v1/materials/MTR-001/inventory", headers=auth(fresh)).status_code == 200


def test_user_admin_endpoints_are_admin_only_and_password_is_argon2_only():
    with TestClient(backend.app) as client:
        admin = admin_session(client)
        created = client.post(
            "/api/v1/admin/users",
            json={"employeeNo": "TEMP-3", "displayName": "临时用户", "role": "OPERATOR", "password": "Temp@2026"},
            headers=auth(admin),
        )
        assert created.status_code == 200
        temp = login(client, "TEMP-3", "Temp@2026")
        assert client.get("/api/v1/users", headers=auth(temp)).status_code == 403
        assert client.post(
            "/api/v1/admin/users",
            json={"employeeNo": "TEMP-4", "displayName": "临时用户", "role": "OPERATOR", "password": "Temp@2026"},
            headers=auth(temp),
        ).status_code == 403
        listing = client.get("/api/v1/users", headers=auth(admin))
        assert listing.status_code == 200
        assert all("password" not in item and "password_hash" not in item for item in listing.json()["items"])
        row = backend.db().execute("SELECT password_hash FROM users WHERE username=?", ("TEMP-3",)).fetchone()
        assert row["password_hash"].startswith("$argon2id$")
