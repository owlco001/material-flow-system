"""S16 移动端响应式契约测试（docs/admin-web-contract.md §1 S16）。"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app import main as backend


def test_responsive_base_present_on_admin_pages():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "admin", "ADMIN", backend.hash_password("Admin@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        page = client.get("/admin/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        assert client.post(
            "/admin/login",
            data={"username": "owlco", "password": "Admin@2026", "csrf_token": token},
            follow_redirects=False,
        ).status_code == 303
        r = client.get("/admin/users")
        assert r.status_code == 200
        assert '<meta name="viewport"' in r.text
        assert "@media (max-width: 720px)" in r.text
        assert "overflow-x: auto" in r.text
