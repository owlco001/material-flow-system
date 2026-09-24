"""S23 会话强制下线契约测试（docs/admin-web-contract.md §6.18/§8.17）。"""
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
        ("u_op", "operator1", "OPERATOR", "Op@2026xx"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.execute(
        "INSERT INTO sessions(token,user_id,expires_at,token_type,device_id) VALUES(?,?,?,?,?)",
        ("tok-op-1", "u_op", 4102444800, "ACCESS", "dev-1"),
    )
    c.execute(
        "INSERT INTO web_sessions(id,user_id,csrf_token,created_at,expires_at,last_seen_at)"
        " VALUES(?,?,?,?,?,?)",
        ("ws_op", "u_op", "csrf-op", backend.now(), 4102444800, 4102444800),
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
        json={"username": username, "password": password, "deviceId": "s23-device"},
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


def session_counts(user_id: str):
    c = backend.db()
    app_n = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user_id,)).fetchone()[0]
    web_n = c.execute("SELECT COUNT(*) FROM web_sessions WHERE user_id=?", (user_id,)).fetchone()[0]
    c.close()
    return app_n, web_n


def test_revoke_sessions_via_api():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        r = client.post(
            "/api/v1/users/u_op/sessions/revoke",
            json={"clientOperationId": str(uuid.uuid4())},
            headers=headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["revokedSessions"] == 1 and data["revokedWebSessions"] == 1
        assert session_counts("u_op") == (0, 0)
        c = backend.db()
        audit = c.execute(
            "SELECT 1 FROM audit_events WHERE event_type='SESSIONS_REVOKED' AND entity_id='u_op'"
        ).fetchone()
        c.close()
        assert audit is not None


def test_revoke_sessions_idempotent_replay():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        body = {"clientOperationId": str(uuid.uuid4())}
        first = client.post("/api/v1/users/u_op/sessions/revoke", json=body, headers=headers)
        assert first.status_code == 200 and first.json()["idempotent"] is False
        replay = client.post("/api/v1/users/u_op/sessions/revoke", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json()["idempotent"] is True
        assert replay.json()["revokedSessions"] == 1


def test_revoke_sessions_roles_and_missing_user():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        missing = client.post(
            "/api/v1/users/nope/sessions/revoke",
            json={"clientOperationId": str(uuid.uuid4())},
            headers=headers,
        )
        assert missing.status_code == 404
    with TestClient(backend.app) as client2:
        r = client2.post(
            "/api/v1/auth/login",
            json={"username": "operator1", "password": "Op@2026xx", "deviceId": "s23-dev2"},
        )
        headers = {"Authorization": f"Bearer {r.json()['accessToken']}"}
        forbidden = client2.post(
            "/api/v1/users/u_admin/sessions/revoke",
            json={"clientOperationId": str(uuid.uuid4())},
            headers=headers,
        )
        assert forbidden.status_code == 403


def test_revoke_sessions_via_web_form():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/users")
        assert "强制下线" in page.text
        r = client.post(
            "/admin/users/u_op/revoke-sessions",
            data={"csrf_token": form_csrf(page.text)},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/users?notice=sessions_revoked"
        assert session_counts("u_op") == (0, 0)
