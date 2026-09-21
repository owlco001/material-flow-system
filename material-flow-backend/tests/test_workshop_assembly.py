"""Regression coverage for workshop supervisor and assembler vertical slice."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient

ROOT = tempfile.mkdtemp(prefix="mf_workshop_assembly_")
os.environ["MATERIAL_FLOW_DATA"] = ROOT + "/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = ROOT + "/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "Admin@2026"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "app"))
spec = importlib.util.spec_from_file_location("workshop_backend", os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "main.py"))
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)
backend.init_db()


def add_user(uid, username, role):
    c = backend.db()
    c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (uid, username, username, role, backend.hash_password("Worker@2026"), 0, 1, backend.now()))
    c.commit(); c.close()


def login(client, username):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "Worker@2026", "deviceId": username})
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


def headers(token, op=None):
    op = op or str(uuid.uuid4())
    return {"Authorization": f"Bearer {token}", "X-Request-Id": str(uuid.uuid4()), "Idempotency-Key": op}


def setup_task():
    c = backend.db()
    c.execute("INSERT INTO assembly_tasks(id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("task-1", "WO-1", "machine-1", "M-01", "assembler-1", "WAITING_MATERIAL", 0, 1, backend.now(), backend.now()))
    c.commit(); c.close()


def test_assembler_lifecycle_idempotency_permissions_and_audit():
    add_user("assembler-1", "asm", "ASSEMBLER")
    add_user("assembler-2", "other", "ASSEMBLER")
    add_user("supervisor-1", "sup", "WORKSHOP_SUPERVISOR")
    setup_task()
    with TestClient(backend.app) as client:
        asm = login(client, "asm")
        other = login(client, "other")
        sup = login(client, "sup")
        mine = client.get("/api/v1/assembly/tasks", headers={"Authorization": f"Bearer {asm}"})
        assert mine.status_code == 200 and [x["id"] for x in mine.json()["items"]] == ["task-1"]
        assert client.get("/api/v1/assembly/tasks", headers={"Authorization": f"Bearer {other}"}).json()["items"] == []
        op = str(uuid.uuid4())
        denied = client.post("/api/v1/assembly/tasks/task-1/accept-material", json={"clientOperationId": op}, headers=headers(other, op))
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "ASSEMBLY_TASK_FORBIDDEN"
        accepted = client.post("/api/v1/assembly/tasks/task-1/accept-material", json={"clientOperationId": op}, headers=headers(asm, op))
        assert accepted.status_code == 200 and accepted.json()["status"] == "MATERIAL_ACCEPTED"
        assert client.post("/api/v1/assembly/tasks/task-1/accept-material", json={"clientOperationId": op}, headers=headers(asm, op)).json() == accepted.json()
        op2 = str(uuid.uuid4())
        started = client.post("/api/v1/assembly/tasks/task-1/start", json={"clientOperationId": op2, "expectedVersion": 2}, headers=headers(asm, op2))
        assert started.status_code == 200 and started.json()["type"] == "ASSEMBLY"
        op3 = str(uuid.uuid4())
        progress = client.post("/api/v1/assembly/tasks/task-1/progress", json={"clientOperationId": op3, "expectedVersion": 3, "stage": 1}, headers=headers(asm, op3))
        assert progress.status_code == 200 and progress.json()["progressStage"] == 1
        stale = client.post("/api/v1/assembly/tasks/task-1/progress", json={"clientOperationId": str(uuid.uuid4()), "expectedVersion": 3, "stage": 2}, headers=headers(asm))
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "TASK_VERSION_CONFLICT"
        version = progress.json()["taskVersion"]
        for stage in (2, 3):
            opx = str(uuid.uuid4())
            r = client.post("/api/v1/assembly/tasks/task-1/progress", json={"clientOperationId": opx, "expectedVersion": version, "stage": stage}, headers=headers(asm, opx))
            assert r.status_code == 200; version = r.json()["taskVersion"]
        op4 = str(uuid.uuid4())
        done = client.post("/api/v1/assembly/tasks/task-1/complete", json={"clientOperationId": op4, "expectedVersion": version}, headers=headers(asm, op4))
        assert done.status_code == 200 and done.json()["status"] == "COMPLETED"
        assert client.get("/api/v1/workshop/summary", headers={"Authorization": f"Bearer {sup}"}).status_code == 200
        assert client.get("/api/v1/workshop/summary", headers={"Authorization": f"Bearer {asm}"}).status_code == 403
        c = backend.db()
        assert {r["event_type"] for r in c.execute("SELECT event_type FROM audit_events WHERE entity_id='task-1'")} >= {"MATERIAL_ACCEPTED_FOR_ASSEMBLY", "ASSEMBLY_STARTED", "ASSEMBLY_PROGRESS_UPDATED", "ASSEMBLY_COMPLETED"}
        assert c.execute("SELECT status FROM labor_records WHERE task_id='task-1'").fetchone()["status"] == "COMPLETED"
        c.close()


def test_temporary_transfer_is_separate_and_requires_remark():
    add_user("assembler-3", "asm3", "ASSEMBLER")
    add_user("supervisor-3", "sup3", "WORKSHOP_SUPERVISOR")
    c = backend.db()
    c.execute("INSERT INTO assembly_tasks(id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("task-3", "WO-3", "machine-3", "M-03", "assembler-3", "MATERIAL_ACCEPTED", 0, 1, backend.now(), backend.now()))
    c.commit(); c.close()
    with TestClient(backend.app) as client:
        token = login(client, "asm3")
        op = str(uuid.uuid4())
        started = client.post("/api/v1/assembly/tasks/task-3/start", json={"clientOperationId": op, "expectedVersion": 1}, headers=headers(token, op))
        assert started.status_code == 200
        bad = client.post("/api/v1/assembly/temporary-transfers/start", json={"clientOperationId": str(uuid.uuid4()), "taskId": "task-3", "remark": ""}, headers=headers(token))
        assert bad.status_code == 422
        op2 = str(uuid.uuid4())
        transfer = client.post("/api/v1/assembly/temporary-transfers/start", json={"clientOperationId": op2, "taskId": "task-3", "deviceId": "machine-3", "remark": "搬运工装"}, headers=headers(token, op2))
        assert transfer.status_code == 200 and transfer.json()["type"] == "TEMPORARY_TRANSFER"
        tid = transfer.json()["temporaryTransferId"]
        op3 = str(uuid.uuid4())
        finished = client.post(f"/api/v1/assembly/temporary-transfers/{tid}/complete", json={"clientOperationId": op3, "remark": "已完成搬运"}, headers=headers(token, op3))
        assert finished.status_code == 200 and finished.json()["status"] == "COMPLETED"
        c = backend.db()
        rows = c.execute("SELECT type,task_id FROM labor_records WHERE worker_user_id='assembler-3'").fetchall()
        assert {(r["type"], r["task_id"]) for r in rows} == {("ASSEMBLY", "task-3"), ("TEMPORARY_TRANSFER", None)}
        c.close()
