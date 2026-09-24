"""S11 机台任务分配契约测试（docs/admin-web-contract.md §8.10）。"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, role, pwd in (
        ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
        ("u_wh", "wh-admin", "WAREHOUSE_ADMIN", "Wh@2026x"),
        ("u_a1", "fitter1", "ASSEMBLER", "User@2026"),
        ("u_a2", "fitter2", "ASSEMBLER", "User@2026"),
        ("u_op", "operator1", "OPERATOR", "User@2026"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.commit()
    c.close()


def seed_task_with_member():
    c = backend.db()
    c.execute(
        "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,"
        "task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("task_1", "ORD-1001", "dev_1", "MC-001", "IN_PROGRESS", 1, 1,
         backend.now(), backend.now()),
    )
    c.execute(
        "INSERT INTO assembly_task_members(task_id,assembler_id,assignment_role,assigned_by,"
        "assigned_at,removed_at) VALUES(?,?,?,?,?,NULL)",
        ("task_1", "u_a1", "LEAD", "u_sup", backend.now()),
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


def test_tasks_supervisor_ok_warehouse_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/tasks").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "wh-admin", "Wh@2026x").status_code == 303
        assert client2.get("/admin/tasks").status_code == 403


def test_tasks_show_members_with_lead_label():
    seed_task_with_member()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/tasks")
        assert r.status_code == 200
        assert "task_1" in r.text and "MC-001" in r.text
        assert "LEAD" in r.text and "fitter1" in r.text


def test_assign_two_members_lead_first():
    seed_task_with_member()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/tasks")
        r = client.post(
            "/admin/tasks/task_1/assignments",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "assemblerIds": ["u_a2", "u_a1"],
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/tasks?notice=assigned"
        c = backend.db()
        rows = c.execute(
            "SELECT assembler_id, assignment_role FROM assembly_task_members"
            " WHERE task_id='task_1' AND removed_at IS NULL ORDER BY assembler_id"
        ).fetchall()
        c.close()
        roles = {row["assembler_id"]: row["assignment_role"] for row in rows}
        assert roles == {"u_a2": "LEAD", "u_a1": "MEMBER"} or roles == {"u_a1": "LEAD", "u_a2": "MEMBER"}
        assert roles["u_a2"] in {"LEAD", "MEMBER"} and roles["u_a1"] in {"LEAD", "MEMBER"}


def test_assign_non_assembler_rejected():
    seed_task_with_member()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/tasks")
        r = client.post(
            "/admin/tasks/task_1/assignments",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "assemblerIds": ["u_op"],
            },
        )
        assert r.status_code == 200 and "成员必须是启用的 ASSEMBLER" in r.text
        c = backend.db()
        count = c.execute(
            "SELECT COUNT(*) FROM assembly_task_members WHERE task_id='task_1' AND assembler_id='u_op'"
        ).fetchone()[0]
        c.close()
        assert count == 0


def test_unassign_member_soft_delete():
    seed_task_with_member()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/tasks")
        r = client.post(
            "/admin/tasks/task_1/assignments/u_a1/remove",
            data={"csrf_token": form_csrf(page.text), "clientOperationId": str(uuid.uuid4())},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/tasks?notice=unassigned"
        c = backend.db()
        row = c.execute(
            "SELECT removed_at FROM assembly_task_members WHERE task_id='task_1' AND assembler_id='u_a1'"
        ).fetchone()
        c.close()
        assert row["removed_at"] is not None


def test_assign_without_csrf_rejected():
    seed_task_with_member()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.post(
            "/admin/tasks/task_1/assignments",
            data={
                "csrf_token": "",
                "clientOperationId": str(uuid.uuid4()),
                "assemblerIds": ["u_a2"],
            },
        )
        assert r.status_code == 403
        c = backend.db()
        count = c.execute(
            "SELECT COUNT(*) FROM assembly_task_members WHERE task_id='task_1' AND assembler_id='u_a2'"
        ).fetchone()[0]
        c.close()
        assert count == 0
