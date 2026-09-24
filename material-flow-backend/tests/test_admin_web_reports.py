"""S4 工时汇总与车间概览契约测试（docs/admin-web-contract.md §8.3）。"""
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
        ("u_sup", "supervisor1", "车间主管", "WORKSHOP_SUPERVISOR", backend.hash_password("Sup@2026x"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_wh", "wh-admin", "仓库管理员", "WAREHOUSE_ADMIN", backend.hash_password("Wh@2026x"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_asm", "fitter1", "装配工甲", "ASSEMBLER", backend.hash_password("User@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_tasks_and_labor():
    c = backend.db()
    c.execute(
        "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,"
        "task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("task_1", "ORD-1001", "dev_1", "MC-001", "COMPLETED", 3, 1, backend.now(), backend.now()),
    )
    c.execute(
        "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,"
        "task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("task_2", "ORD-1002", "dev_1", "MC-001", "IN_PROGRESS", 1, 1, backend.now(), backend.now()),
    )
    c.execute(
        "INSERT INTO labor_records(id,task_id,worker_user_id,type,status,started_at,ended_at,"
        "duration_minutes,client_operation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("lab_1", "task_1", "u_asm", "ASSEMBLY", "COMPLETED", backend.now(), backend.now(),
         30, "op-lab-1", backend.now()),
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


def test_reports_supervisor_ok_warehouse_admin_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/reports").status_code == 200
        assert client.get("/admin/reports/labor").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "wh-admin", "Wh@2026x").status_code == 303
        assert client2.get("/admin/reports").status_code == 403


def test_overview_shows_summary_and_machine_row():
    seed_tasks_and_labor()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/reports")
        assert r.status_code == 200
        assert "总任务 2 · 完成 1" in r.text
        assert "MC-001" in r.text
        assert "装配 30 · 借调 0 · 合计 30" in r.text


def test_labor_summary_rows_and_order_filter():
    seed_tasks_and_labor()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/reports/labor")
        assert r.status_code == 200
        assert "装配工甲" in r.text and "ORD-1001" in r.text
        assert "共 1 行" in r.text
        hit = client.get("/admin/reports/labor?orderNo=ORD-1001")
        assert "ORD-1001" in hit.text and "暂无数据" not in hit.text
        miss = client.get("/admin/reports/labor?orderNo=NOPE")
        assert "暂无数据" in miss.text


def test_labor_page_out_of_range_ok():
    seed_tasks_and_labor()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/reports/labor?page=99")
        assert r.status_code == 200 and "暂无数据" in r.text
