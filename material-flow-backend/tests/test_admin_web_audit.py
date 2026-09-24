"""S6 审计日志查询契约测试（docs/admin-web-contract.md §8.5）。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "管理员", "ADMIN", backend.hash_password("Admin@2026"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_wh", "wh-admin", "仓库管理员", "WAREHOUSE_ADMIN", backend.hash_password("Wh@2026x"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_audit_rows():
    c = backend.db()
    c.execute(
        "INSERT INTO audit_logs(operator_id,role,action,resource_type,resource_id,"
        "request_id,occurred_at,result) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "ADMIN", "LOGIN_SUCCESS", "SESSION", "sess_1", "req-log-1",
         "2026-09-24T01:00:00+00:00", "SUCCESS"),
    )
    c.execute(
        "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
        "request_id,client_operation_id,before_json,after_json,server_time,result)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("OUTBOUND_APPROVED", "TRANSFER_REQUEST", "tr_1", "u_wh", "WAREHOUSE_ADMIN",
         "req-ev-1", "op-ev-1", "{}", "{}", "2026-09-24T02:00:00+00:00", "SUCCESS"),
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


def test_audit_admin_ok_others_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        assert client.get("/admin/audit").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "wh-admin", "Wh@2026x").status_code == 303
        assert client2.get("/admin/audit").status_code == 403


def test_audit_shows_both_record_types():
    seed_audit_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/audit")
        assert r.status_code == 200
        assert "审计日志" in r.text and "审计事件" in r.text
        assert "LOGIN_SUCCESS" in r.text and "OUTBOUND_APPROVED" in r.text


def test_audit_event_type_filter():
    seed_audit_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        hit = client.get("/admin/audit?eventType=OUTBOUND_APPROVED")
        assert "OUTBOUND_APPROVED" in hit.text and "LOGIN_SUCCESS" not in hit.text
        miss = client.get("/admin/audit?eventType=NO_SUCH_EVENT")
        assert "暂无数据" in miss.text


def test_audit_time_validation():
    seed_audit_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        inverted = client.get("/admin/audit?from=2026-09-25T00:00:00Z&to=2026-09-01T00:00:00Z")
        assert inverted.status_code == 200
        assert "from 不能晚于 to" in inverted.text
        bad = client.get("/admin/audit?from=not-a-time")
        assert bad.status_code == 200
        assert "必须是合法 ISO-8601 时间" in bad.text
