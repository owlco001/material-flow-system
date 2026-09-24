"""S2 用户管理契约测试（docs/admin-web-contract.md §8.1）。"""
from __future__ import annotations

import re
import uuid

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


def seed_user(uid="u_target", username="target-user", role="OPERATOR", display_name="目标用户"):
    c = backend.db()
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, username, display_name, role, backend.hash_password("User@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_admin()
    yield


def form_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "表单必须携带 csrf_token hidden 字段"
    return match.group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def op_id_for(html: str, action_suffix: str) -> str:
    match = re.search(
        rf'action="/admin/users/[a-zA-Z0-9_]+/{action_suffix}".*?name="clientOperationId" value="([^"]+)"',
        html,
        re.S,
    )
    assert match, f"未找到 {action_suffix} 表单的 clientOperationId"
    return match.group(1)


def test_users_list_shows_seed_users():
    seed_user()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/users")
        assert r.status_code == 200
        assert "target-user" in r.text and "目标用户" in r.text
        assert "操作员" in r.text and "在职" in r.text


def test_create_user_success_and_password_never_echoed():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = form_csrf(client.get("/admin/users/new").text)
        r = client.post(
            "/admin/users/new",
            data={
                "csrf_token": csrf,
                "employeeNo": "emp-001",
                "displayName": "张三",
                "role": "MATERIAL",
                "password": "Init@2026x",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/users?notice=created"
        assert "Init@2026x" not in r.text
        c = backend.db()
        row = c.execute("SELECT * FROM users WHERE username='emp-001'").fetchone()
        c.close()
        assert row is not None
        assert row["must_change_password"] == 1
        assert row["password_hash"].startswith("$argon2id$")
        page = client.get("/admin/users?notice=created")
        assert "Init@2026x" not in page.text and "$argon2id$" not in page.text


def test_create_duplicate_employee_mismatch_shows_api_message():
    seed_user()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = form_csrf(client.get("/admin/users/new").text)
        r = client.post(
            "/admin/users/new",
            data={
                "csrf_token": csrf,
                "employeeNo": "target-user",
                "displayName": "别人",
                "role": "OPERATOR",
                "password": "Init@2026x",
            },
        )
        assert r.status_code == 200
        assert "工号已存在但员工信息不一致" in r.text


def test_create_admin_role_rejected():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = form_csrf(client.get("/admin/users/new").text)
        r = client.post(
            "/admin/users/new",
            data={
                "csrf_token": csrf,
                "employeeNo": "evil",
                "displayName": "X",
                "role": "ADMIN",
                "password": "Init@2026x",
            },
        )
        assert r.status_code == 200 and "角色无效" in r.text


def test_edit_updates_display_name():
    seed_user()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/users/u_target/edit")
        r = client.post(
            "/admin/users/u_target/edit",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": re.search(
                    r'name="clientOperationId" value="([^"]+)"', page.text
                ).group(1),
                "employeeNo": "target-user",
                "displayName": "新名",
                "role": "OPERATOR",
                "active": "on",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/users?notice=updated"
        c = backend.db()
        assert c.execute("SELECT display_name FROM users WHERE id='u_target'").fetchone()[0] == "新名"
        c.close()


def test_edit_cannot_disable_self():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/users/u_admin/edit")
        r = client.post(
            "/admin/users/u_admin/edit",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "displayName": "管理员",
                "role": "OPERATOR",
            },
        )
        assert r.status_code == 200
        assert "不能停用当前登录用户" in r.text


def test_password_reset_revokes_sessions_and_never_echoes():
    seed_user()
    c = backend.db()
    c.execute(
        "INSERT INTO sessions(token, user_id, expires_at, token_type, device_id) VALUES(?,?,?,?,?)",
        ("tok_target", "u_target", 9999999999, "ACCESS", "dev"),
    )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        listing = client.get("/admin/users")
        r = client.post(
            "/admin/users/u_target/password-reset",
            data={
                "csrf_token": form_csrf(listing.text),
                "clientOperationId": op_id_for(listing.text, "password-reset"),
                "newPassword": "Reset@2026x",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/users?notice=reset"
        assert "Reset@2026x" not in r.text
        c = backend.db()
        row = c.execute(
            "SELECT password_hash, must_change_password FROM users WHERE id='u_target'"
        ).fetchone()
        assert row["password_hash"].startswith("$argon2id$")
        assert row["must_change_password"] == 1
        assert c.execute("SELECT COUNT(*) FROM sessions WHERE user_id='u_target'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM web_sessions WHERE user_id='u_target'").fetchone()[0] == 0
        c.close()
        page = client.get("/admin/users?notice=reset")
        assert "Reset@2026x" not in page.text and "$argon2id$" not in page.text


def test_delete_disables_and_keeps_history():
    seed_user()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        listing = client.get("/admin/users")
        r = client.post(
            "/admin/users/u_target/delete",
            data={
                "csrf_token": form_csrf(listing.text),
                "clientOperationId": op_id_for(listing.text, "delete"),
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/users?notice=disabled"
        c = backend.db()
        row = c.execute("SELECT active, username FROM users WHERE id='u_target'").fetchone()
        c.close()
        assert row["active"] == 0 and row["username"] == "target-user"


def test_operator_forbidden():
    seed_user()
    with TestClient(backend.app) as client:
        assert web_login(client, "target-user", "User@2026").status_code == 303
        assert client.get("/admin/users").status_code == 403


def test_edit_without_csrf_rejected_no_change():
    seed_user()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.post(
            "/admin/users/u_target/edit",
            data={
                "csrf_token": "",
                "clientOperationId": str(uuid.uuid4()),
                "displayName": "HACK",
                "role": "OPERATOR",
                "active": "on",
            },
        )
        assert r.status_code == 403
        c = backend.db()
        assert c.execute("SELECT display_name FROM users WHERE id='u_target'").fetchone()[0] == "目标用户"
        c.close()


def test_create_with_manager_id_links_row():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/users/new")
        r = client.post(
            "/admin/users/new",
            data={
                "csrf_token": form_csrf(page.text),
                "employeeNo": "emp-mgr",
                "displayName": "带领导",
                "role": "OPERATOR",
                "password": "Init@2026x",
                "managerId": "u_admin",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        c = backend.db()
        row = c.execute(
            "SELECT manager_id FROM employee_managers WHERE employee_id="
            "(SELECT id FROM users WHERE username='emp-mgr')"
        ).fetchone()
        c.close()
        assert row["manager_id"] == "u_admin"
        assert "管理员" in client.get("/admin/users").text


def test_create_with_unknown_manager_rejected():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/users/new")
        r = client.post(
            "/admin/users/new",
            data={
                "csrf_token": form_csrf(page.text),
                "employeeNo": "emp-badmgr",
                "displayName": "坏领导",
                "role": "OPERATOR",
                "password": "Init@2026x",
                "managerId": "u_nobody",
            },
        )
        assert r.status_code == 200 and "直属领导不存在" in r.text
        c = backend.db()
        count = c.execute("SELECT COUNT(*) FROM users WHERE username='emp-badmgr'").fetchone()[0]
        c.close()
        assert count == 0
