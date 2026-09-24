"""S9 物料状态工作台契约测试（docs/admin-web-contract.md §8.8）。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, role, pwd in (
        ("u_wh", "wh-admin", "WAREHOUSE_ADMIN", "Wh@2026x"),
        ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
        ("u_op", "operator1", "OPERATOR", "User@2026"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_users()
    yield


def form_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "表单必须携带 csrf_token"
    return match.group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def test_workspace_roles_ok_operator_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        assert client.get("/admin/workspace").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "supervisor1", "Sup@2026x").status_code == 303
        assert client2.get("/admin/workspace").status_code == 200
    with TestClient(backend.app) as client3:
        assert web_login(client3, "operator1", "User@2026").status_code == 303
        assert client3.get("/admin/workspace").status_code == 403


def test_workspace_summary_cards_and_role_view():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/workspace")
        assert r.status_code == 200
        assert "当前角色视图" in r.text
        assert "流转待办" in r.text and "物料状态" in r.text and "交接结果" in r.text


def test_workspace_items_show_demo_row_and_filter():
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/workspace")
        assert r.status_code == 200
        assert "MTR-CTL-001" in r.text and "控制柜" in r.text
        hit = client.get("/admin/workspace?orderNo=26B-013")
        assert "MTR-CTL-001" in hit.text
        miss = client.get("/admin/workspace?orderNo=NO-SUCH-ORDER")
        assert "暂无数据" in miss.text


def test_workspace_page_out_of_range_ok():
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/workspace?page=2")
        assert r.status_code == 200
