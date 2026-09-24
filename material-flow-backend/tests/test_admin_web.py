"""Web 管理界面契约测试（docs/admin-web-contract.md §8）。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_admin():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "管理员", "ADMIN", backend.hash_password("Admin@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_user(uid, username, role, password="User@2026"):
    c = backend.db()
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, username, username, role, backend.hash_password(password), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_business_rows():
    c = backend.db()
    c.execute(
        "INSERT INTO transfer_requests(id,client_operation_id,type,status,payload_json,"
        "created_by,created_at) VALUES(?,?,?,?,?,?,?)",
        ("tr_1", "op-tr-1", "OUTBOUND", "PENDING_APPROVAL", "{}", "u_admin", backend.now()),
    )
    c.execute(
        "INSERT INTO material_handovers(id,work_item_id,transfer_request_id,quantity,"
        "from_location,client_operation_id,status,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        ("ho_1", "wi_1", "tr_1", 1, "A-01", "op-ho-1", "PENDING", "u_admin", backend.now()),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_admin()
    yield


def form_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "登录表单必须携带 csrf_token hidden 字段"
    return match.group(1)


def web_login(client, username, password, csrf=None):
    page = client.get("/admin/login")
    token = csrf or form_csrf(page.text)
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def test_login_page_anonymous_shows_form_and_anon_csrf():
    with TestClient(backend.app) as client:
        r = client.get("/admin/login")
        assert r.status_code == 200
        assert 'name="username"' in r.text and 'name="password"' in r.text
        assert client.cookies.get("anon_csrf") == form_csrf(r.text)


def test_logged_in_login_page_redirects_to_home():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/login", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/"


def test_login_success_sets_httponly_cookie_and_session_row():
    with TestClient(backend.app) as client:
        r = web_login(client, "owlco", "Admin@2026")
        assert r.status_code == 303 and r.headers["location"] == "/admin/"
        cookies = r.headers.get_list("set-cookie")
        assert any("mf_session=" in h and "HttpOnly" in h for h in cookies)
        c = backend.db()
        row = c.execute("SELECT user_id FROM web_sessions").fetchone()
        c.close()
        assert row is not None and row["user_id"] == "u_admin"


def test_login_failure_redirects_error_and_leaves_no_session_or_password_echo():
    with TestClient(backend.app) as client:
        r = web_login(client, "owlco", "WrongPass@2026")
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/login?error=1"
        assert "WrongPass@2026" not in r.text
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 0
        c.close()
        page = client.get("/admin/login?error=1")
        assert "WrongPass@2026" not in page.text
        assert "用户名或密码错误" in page.text


def test_home_requires_session():
    with TestClient(backend.app) as client:
        r = client.get("/admin/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"


def test_non_admin_role_gets_403():
    seed_user("u_op", "operator1", "OPERATOR")
    with TestClient(backend.app) as client:
        assert web_login(client, "operator1", "User@2026").status_code == 303
        r = client.get("/admin/")
        assert r.status_code == 403


def test_logout_without_csrf_is_rejected_and_session_survives():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.post("/admin/logout", data={"csrf_token": ""}, follow_redirects=False)
        assert r.status_code == 403
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 1
        c.close()


def test_logout_deletes_session_and_clears_cookie():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        home = client.get("/admin/")
        csrf = form_csrf(home.text)
        r = client.post("/admin/logout", data={"csrf_token": csrf}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"
        assert any("mf_session=" in h and "Max-Age=0" in h for h in r.headers.get_list("set-cookie"))
        c = backend.db()
        assert c.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 0
        c.close()


def test_expired_session_redirects_to_login():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        c = backend.db()
        c.execute("UPDATE web_sessions SET expires_at=0")
        c.commit()
        c.close()
        r = client.get("/admin/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login"


def test_dashboard_shows_counts_and_version():
    seed_user("u_op", "operator1", "OPERATOR")
    seed_business_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/")
        assert r.status_code == 200
        assert "总数 2 · 启用 2" in r.text
        assert "待审批流转申请" in r.text and "待确认交接" in r.text
        assert f"服务版本 {backend.APP_VERSION}" in r.text


def test_no_password_echo_anywhere_in_web_flow():
    with TestClient(backend.app) as client:
        page = client.get("/admin/login")
        r1 = web_login(client, "owlco", "Admin@2026", csrf=form_csrf(page.text))
        home = client.get("/admin/")
        r2 = client.post(
            "/admin/logout", data={"csrf_token": form_csrf(home.text)}, follow_redirects=False
        )
        for response in (page, r1, home, r2):
            assert "Admin@2026" not in response.text
