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
        assert "组长" in r.text and "fitter1" in r.text  # 角色已本地化显示


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


def _seed_many():
    c = backend.db()
    t = backend.now()
    rows = [("tk_a1", "PRJ-A", "26A-P5-10", "WAITING_MATERIAL"), ("tk_a2", "PRJ-A", "26A-P5-9", "COMPLETED"),
            ("tk_a3", "PRJ-A", "26A-C5-1", "WAITING_MATERIAL"), ("tk_b1", "PRJ-B", "26B-X1", "IN_PROGRESS")]
    for tid, order, dev, st in rows:
        c.execute("INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,"
                  "task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                  (tid, order, "d_" + tid, dev, st, 3 if st == "COMPLETED" else 0, 1, t, t))
    c.execute("INSERT INTO assembly_task_members(task_id,assembler_id,assignment_role,assigned_by,"
              "assigned_at,removed_at) VALUES('tk_a2','u_a1','LEAD','u_sup',?,NULL)", (t,))
    c.commit()
    c.close()


def test_tasks_filter_by_order_status_chips_and_unassigned():
    _seed_many()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/tasks?order=PRJ-A")
        assert r.status_code == 200
        assert "26A-P5-10" in r.text and "26B-X1" not in r.text
        assert "PRJ-A（1/3 台完成）" in r.text
        assert 'href="/admin/orders/PRJ-A/tasks/tk_a1"' in r.text  # 点机台进机台详情页
        # 按机台号自然排序：P5-9 在 P5-10 前
        assert r.text.index("26A-P5-9") < r.text.index("26A-P5-10")
        assert re.search(r"未分配 <b>2</b>", r.text)
        r = client.get("/admin/tasks?order=PRJ-A&assigned=no&status=WAITING_MATERIAL")
        assert "26A-C5-1" in r.text and "26A-P5-9" not in r.text
        r = client.get("/admin/tasks?size=50&sort=created")
        assert r.status_code == 200 and "26B-X1" in r.text


def test_batch_assign_keeps_filters_and_reports_counts():
    _seed_many()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/tasks?order=PRJ-A")
        assert 'action="/admin/tasks/batch-assign"' in page.text
        r = client.post("/admin/tasks/batch-assign", data={
            "csrf_token": form_csrf(page.text), "next": "/admin/tasks?order=PRJ-A",
            "taskIds": ["tk_a1", "tk_a3", "nope"], "assemblerIds": ["u_a2", "u_a1"]}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/tasks?order=PRJ-A&notice=batch_assigned&ok=2&fail=1"
        c = backend.db()
        got = {(x[0], x[1]): x[2] for x in c.execute(
            "SELECT task_id, assembler_id, assignment_role FROM assembly_task_members WHERE removed_at IS NULL")}
        c.close()
        assert got[("tk_a1", "u_a2")] == "LEAD" and got[("tk_a3", "u_a1")] == "MEMBER"
        r = client.get(r.headers["location"])
        assert "成功 2 台，失败 1 台" in r.text
        # 缺参数
        r = client.post("/admin/tasks/batch-assign", data={"csrf_token": form_csrf(page.text), "taskIds": ["tk_a1"]})
        assert "请先勾选机台并选择装配工" in r.text
        # CSRF
        r = client.post("/admin/tasks/batch-assign", data={"taskIds": ["tk_a1"], "assemblerIds": ["u_a1"]})
        assert r.status_code == 403
