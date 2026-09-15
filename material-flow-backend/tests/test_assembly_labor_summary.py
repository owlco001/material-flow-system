"""C14 labor-summary coverage with an isolated dynamically loaded backend."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


ROOT = tempfile.mkdtemp(prefix="mf_c14_")
os.environ["MATERIAL_FLOW_DATA"] = f"{ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "Admin@2026"
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app")
sys.path.insert(0, APP_DIR)
spec = importlib.util.spec_from_file_location("c14_backend", os.path.join(APP_DIR, "main.py"))
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)

PASSWORD = "Worker@2026"


def add_user(uid: str, username: str, role: str) -> None:
    c = backend.db()
    c.execute(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
        (uid, username, username, role, backend.hash_password(PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD, "deviceId": username},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-Id": str(uuid.uuid4())}


def insert_task() -> None:
    c = backend.db()
    ts = backend.now()
    c.execute(
        """INSERT INTO assembly_tasks
        (id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,
         task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("c14-task", "C14-WO-001", "C14-DEVICE-001", "C14-NO-001", "c14-asm-1",
         "IN_PROGRESS", 1, 2, ts, ts),
    )
    c.execute(
        """INSERT INTO assembly_task_members
        (task_id,assembler_id,assignment_role,assigned_by,assigned_at)
        VALUES(?,?,?,?,?)""",
        ("c14-task", "c14-asm-1", "LEAD", "c14-admin", ts),
    )
    c.execute(
        """INSERT INTO assembly_task_members
        (task_id,assembler_id,assignment_role,assigned_by,assigned_at)
        VALUES(?,?,?,?,?)""",
        ("c14-task", "c14-asm-2", "MEMBER", "c14-admin", ts),
    )
    base = datetime.now(timezone.utc).replace(microsecond=0)
    assembly_rows = [
        ("c14-assembly-1", "c14-asm-1", base - timedelta(minutes=31), 31),
        ("c14-assembly-2", "c14-asm-2", base - timedelta(minutes=17), 17),
    ]
    for rid, worker, started, minutes in assembly_rows:
        started_text = started.isoformat().replace("+00:00", "Z")
        ended_text = base.isoformat().replace("+00:00", "Z")
        c.execute(
            """INSERT INTO labor_records
            (id,task_id,worker_user_id,type,status,started_at,ended_at,duration_minutes,
             remark,client_operation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "c14-task", worker, "ASSEMBLY", "COMPLETED", started_text, ended_text,
             minutes, "C14 assembly", f"op-{rid}", ended_text),
        )
    transfer_started = (base - timedelta(minutes=13)).isoformat().replace("+00:00", "Z")
    ended_text = base.isoformat().replace("+00:00", "Z")
    c.execute(
        """INSERT INTO labor_records
        (id,task_id,worker_user_id,type,status,started_at,ended_at,duration_minutes,
         remark,client_operation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("c14-transfer", None, "c14-asm-2", "TEMPORARY_TRANSFER", "COMPLETED",
         transfer_started, ended_text, 13, "C14 transfer", "op-c14-transfer", ended_text),
    )
    c.execute(
        """INSERT INTO temporary_transfers
        (id,worker_user_id,source_task_id,labor_record_id,status,remark,started_at,ended_at,
         client_operation_id,device_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("c14-tt", "c14-asm-2", "c14-task", "c14-transfer", "COMPLETED", "C14 transfer",
         transfer_started, ended_text, "op-c14-transfer", "C14-DEVICE-001"),
    )
    active_started = (base - timedelta(minutes=9)).isoformat().replace("+00:00", "Z")
    c.execute(
        """INSERT INTO labor_records
        (id,task_id,worker_user_id,type,status,started_at,ended_at,duration_minutes,
         remark,client_operation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        ("c14-active", "c14-task", "c14-asm-1", "ASSEMBLY", "ACTIVE", active_started,
         None, None, "C14 active", "op-c14-active", ended_text),
    )
    c.commit()
    c.close()


def test_c14_task_summary_permissions_details_active_and_no_secrets():
    backend.init_db()
    for user in (
        ("c14-admin", "c14admin", "ADMIN"),
        ("c14-supervisor", "c14supervisor", "WORKSHOP_SUPERVISOR"),
        ("c14-asm-1", "c14asm1", "ASSEMBLER"),
        ("c14-asm-2", "c14asm2", "ASSEMBLER"),
        ("c14-outsider", "c14outsider", "ASSEMBLER"),
    ):
        add_user(*user)
    insert_task()

    with TestClient(backend.app) as client:
        tokens = {name: login(client, name) for name in ("c14admin", "c14supervisor", "c14asm1", "c14asm2", "c14outsider")}
        for role in ("c14admin", "c14supervisor", "c14asm1", "c14asm2"):
            response = client.get("/api/v1/assembly/tasks/c14-task/labor-summary", headers=headers(tokens[role]))
            assert response.status_code == 200, response.text
        denied = client.get("/api/v1/assembly/tasks/c14-task/labor-summary", headers=headers(tokens["c14outsider"]))
        assert denied.status_code == 403

        detail = client.get(
            "/api/v1/assembly/tasks/c14-task/labor-summary?assemblerId=c14-asm-2",
            headers=headers(tokens["c14admin"]),
        )
        assert detail.status_code == 200
        assert [(x["assemblerId"], x["assemblyLaborMinutes"], x["temporaryTransferLaborMinutes"], x["totalLaborMinutes"]) for x in detail.json()["items"]] == [("c14-asm-2", 17, 13, 30)]
        all_items = client.get("/api/v1/assembly/tasks/c14-task/labor-summary", headers=headers(tokens["c14admin"])).json()["items"]
        by_assembler = {x["assemblerId"]: x for x in all_items}
        assert by_assembler["c14-asm-1"]["assemblyLaborMinutes"] > 31
        assert by_assembler["c14-asm-1"]["temporaryTransferLaborMinutes"] == 0
        assert (by_assembler["c14-asm-2"]["assemblyLaborMinutes"], by_assembler["c14-asm-2"]["temporaryTransferLaborMinutes"]) == (17, 13)

        workshop_url = "/api/v1/workshop/labor-summary?deviceId=C14-DEVICE-001&orderNo=C14-WO-001&assemblerId=c14-asm-2"
        for role in ("c14admin", "c14supervisor"):
            response = client.get(workshop_url, headers=headers(tokens[role]))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["pageSize"] == 20 and body["total"] == 1
            assert body["items"][0]["totalLaborMinutes"] == 30
        assert client.get("/api/v1/workshop/labor-summary?pageSize=10", headers=headers(tokens["c14admin"])).status_code == 422

        active = next(x for x in client.get("/api/v1/assembly/tasks/c14-task/labor-summary", headers=headers(tokens["c14admin"])).json()["items"] if x["assemblerId"] == "c14-asm-1")
        assert active["assemblyLaborMinutes"] > 31
        c = backend.db()
        row = c.execute("SELECT duration_minutes FROM labor_records WHERE id='c14-active'").fetchone()
        assert row["duration_minutes"] is None
        c.close()

        serialized = response.text
        assert "Worker@2026" not in serialized and "accessToken" not in serialized and "password_hash" not in serialized
