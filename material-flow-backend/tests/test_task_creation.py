"""S24 装配任务创建契约测试（docs/admin-web-contract.md §6.19/§8.18）。"""
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
        ("u_planner", "planner1", "PLANNER", "Plan@2026"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.execute(
        "INSERT OR REPLACE INTO production_orders(id,order_no,product_name,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        ("ord_1", "PO-T1", "测试产品", "RELEASED", backend.now(), backend.now()),
    )
    c.execute(
        "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,assigned_assembler_id,status,"
        "progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("tsk_exist", "PO-T1", "dev-1", "MC-001", None, "WAITING_MATERIAL", 0, 1,
         backend.now(), backend.now()),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed()
    yield


def auth_login(client, username, password):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": "s24-device"},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def form_csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def task_body(**overrides):
    body = {
        "clientOperationId": str(uuid.uuid4()),
        "orderNo": "PO-T1",
        "deviceId": "dev-2",
        "deviceNo": "MC-002",
    }
    body.update(overrides)
    return body


def test_create_task_via_api_and_side_effects():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        r = client.post("/api/v1/assembly/tasks", json=task_body(), headers=headers)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "WAITING_MATERIAL" and data["idempotent"] is False
        c = backend.db()
        task = c.execute("SELECT * FROM assembly_tasks WHERE id=?", (data["taskId"],)).fetchone()
        audit = c.execute(
            "SELECT 1 FROM audit_events WHERE event_type='ASSEMBLY_TASK_CREATED' AND entity_id=?",
            (data["taskId"],),
        ).fetchone()
        c.close()
        assert task["order_no"] == "PO-T1" and task["device_no"] == "MC-002"
        assert task["status"] == "WAITING_MATERIAL" and task["progress_stage"] == 0
        assert audit is not None


def test_create_task_duplicate_device_conflict():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "supervisor1", "Sup@2026x")
        r = client.post(
            "/api/v1/assembly/tasks",
            json=task_body(deviceId="dev-1", deviceNo="MC-001"),
            headers=headers,
        )
        assert r.status_code == 409
        assert "机台任务已存在" in r.json()["error"]["message"]


def test_create_task_validation_and_roles():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        for bad in (
            task_body(orderNo=""),
            task_body(deviceId=""),
            task_body(deviceNo=""),
        ):
            r = client.post("/api/v1/assembly/tasks", json=bad, headers=headers)
            assert r.status_code == 422
    with TestClient(backend.app) as client2:
        headers = auth_login(client2, "planner1", "Plan@2026")
        r = client2.post("/api/v1/assembly/tasks", json=task_body(), headers=headers)
        assert r.status_code == 403


def test_create_task_idempotent_replay():
    with TestClient(backend.app) as client:
        headers = auth_login(client, "owlco", "Admin@2026")
        body = task_body()
        first = client.post("/api/v1/assembly/tasks", json=body, headers=headers)
        assert first.status_code == 201
        replay = client.post("/api/v1/assembly/tasks", json=body, headers=headers)
        assert replay.status_code == 201 and replay.json()["idempotent"] is True
        assert replay.json()["taskId"] == first.json()["taskId"]


def test_create_task_via_web_form():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/tasks")
        assert "新建任务" in page.text
        r = client.post(
            "/admin/tasks/create",
            data={
                "csrf_token": form_csrf(page.text),
                "orderNo": "PO-T1",
                "deviceId": "dev-3",
                "deviceNo": "MC-003",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/tasks?notice=task_created"
        c = backend.db()
        assert c.execute("SELECT 1 FROM assembly_tasks WHERE device_no='MC-003'").fetchone()
        c.close()
