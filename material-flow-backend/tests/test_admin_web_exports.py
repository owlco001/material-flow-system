"""S15 CSV 数据导出契约测试（docs/admin-web-contract.md §8.13）。"""
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
        ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
        ("u_op", "operator1", "OPERATOR", "Op@2026xx"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.execute(
        "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
        "request_id,client_operation_id,before_json,after_json,server_time,result)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("FLOW_TRANSFER_EXECUTED", "MATERIAL_FLOW", "mf_1", "u_admin", "ADMIN",
         str(uuid.uuid4()), str(uuid.uuid4()), "{}", "{}", backend.now(), "SUCCESS"),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed()
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


def test_audit_export_csv_for_admin_only():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/audit/export")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert r.text.startswith("\ufeff")
        lines = r.text.strip().splitlines()
        assert len(lines) == 3  # 表头 + demo 种子审计行 + 本次种子行
        assert "FLOW_TRANSFER_EXECUTED" in r.text
    with TestClient(backend.app) as client2:
        assert web_login(client2, "supervisor1", "Sup@2026x").status_code == 303
        assert client2.get("/admin/audit/export").status_code == 403


def test_workspace_export_csv():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/workspace/export")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "material-workspace.csv" in r.headers["content-disposition"]


def test_workspace_export_operator_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "operator1", "Op@2026xx").status_code == 303
        assert client.get("/admin/workspace/export").status_code == 403


def test_labor_and_machines_export_for_supervisor():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        labor = client.get("/admin/reports/labor/export")
        assert labor.status_code == 200
        assert labor.headers["content-type"].startswith("text/csv")
        assert "labor-summary.csv" in labor.headers["content-disposition"]
        machines = client.get("/admin/reports/machines/export")
        assert machines.status_code == 200
        assert "machine-progress.csv" in machines.headers["content-disposition"]


def test_reports_export_operator_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "operator1", "Op@2026xx").status_code == 303
        assert client.get("/admin/reports/labor/export").status_code == 403
        assert client.get("/admin/reports/machines/export").status_code == 403
