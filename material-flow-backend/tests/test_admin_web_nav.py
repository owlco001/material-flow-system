"""S18 全局统一导航契约测试（docs/admin-web-contract.md §1 S18）。"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app import main as backend


def seed():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, role, pwd in (
        ("u_admin", "owlco", "ADMIN", "Admin@2026"),
        ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
        ("u_op", "operator1", "OPERATOR", "Op@2026xx"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.commit()
    c.close()


def form_csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def test_quicknav_admin_sees_everything():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/users")
        assert r.status_code == 200
        for href in ("/admin/", "/admin/workspace", "/admin/reports", "/admin/tasks",
                     "/admin/flows", "/admin/handovers", "/admin/warehouse", "/admin/models",
                     "/admin/boms", "/admin/users", "/admin/orders", "/admin/audit"):
            assert f'href="{href}"' in r.text


def test_quicknav_supervisor_scoped():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/tasks")
        assert r.status_code == 200
        assert 'href="/admin/tasks"' in r.text and 'href="/admin/boms"' in r.text
        assert 'href="/admin/users"' not in r.text
        assert 'href="/admin/audit"' not in r.text
        assert 'href="/admin/flows"' not in r.text


def test_reports_progress_bars_render():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/reports")
        assert r.status_code == 200
        assert "bar-bg" in r.text and "bar-fg" in r.text
