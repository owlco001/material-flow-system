"""Coverage for assembly-task member assignment and access control."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient


ROOT = tempfile.mkdtemp(prefix="mf_assembly_members_")
os.environ["MATERIAL_FLOW_DATA"] = f"{ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "Admin@2026"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "app"))
spec = importlib.util.spec_from_file_location(
    "assembly_members_backend",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "main.py"),
)
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)


def add_user(uid: str, username: str, role: str, active: int = 1) -> None:
    c = backend.db()
    c.execute(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
        (
            uid,
            username,
            username,
            role,
            backend.hash_password("Worker@2026"),
            0,
            active,
            backend.now(),
        ),
    )
    c.commit()
    c.close()


def login(client: TestClient, username: str) -> str:
    result = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Worker@2026", "deviceId": username},
    )
    assert result.status_code == 200, result.text
    return result.json()["accessToken"]


def request_headers(token: str, operation: str | None = None) -> dict[str, str]:
    operation = operation or str(uuid.uuid4())
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": operation,
        "X-Request-Id": str(uuid.uuid4()),
    }


def insert_task(task_id: str, device_id: str) -> None:
    c = backend.db()
    c.execute(
        """INSERT INTO assembly_tasks
        (id, order_no, device_id, device_no, assigned_assembler_id, status,
         progress_stage, task_version, created_at, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            task_id,
            f"WO-{task_id}",
            device_id,
            f"NO-{device_id}",
            None,
            "MATERIAL_ACCEPTED",
            0,
            1,
            backend.now(),
            backend.now(),
        ),
    )
    c.commit()
    c.close()


def test_assignment_initializes_schema_assigns_two_members_and_lists_by_device():
    backend.init_db()
    c = backend.db()
    tables = {
        row["name"]
        for row in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"assembly_task_members", "assembly_assignment_operations"} <= tables
    c.close()

    add_user("asm-member-1", "member1", "ASSEMBLER")
    add_user("asm-member-2", "member2", "ASSEMBLER")
    add_user("asm-disabled", "disabled", "ASSEMBLER", active=0)
    add_user("supervisor", "supervisor", "WORKSHOP_SUPERVISOR")
    add_user("outsider", "outsider", "ASSEMBLER")
    insert_task("member-task", "device-member")
    insert_task("other-task", "device-other")

    with TestClient(backend.app) as client:
        supervisor = login(client, "supervisor")
        member1 = login(client, "member1")
        member2 = login(client, "member2")
        outsider = login(client, "outsider")

        operation = str(uuid.uuid4())
        payload = {"clientOperationId": operation, "assemblerIds": ["asm-member-1", "asm-member-2"]}
        assigned = client.post(
            "/api/v1/assembly/tasks/member-task/assignments",
            json=payload,
            headers=request_headers(supervisor, operation),
        )
        assert assigned.status_code == 200, assigned.text
        assert {member["assembler_id"] for member in assigned.json()["members"]} == {
            "asm-member-1",
            "asm-member-2",
        }
        assert [member["assignment_role"] for member in assigned.json()["members"]] == [
            "LEAD",
            "MEMBER",
        ]

        invalid_operation = str(uuid.uuid4())
        invalid = client.post(
            "/api/v1/assembly/tasks/member-task/assignments",
            json={"clientOperationId": invalid_operation, "assemblerIds": ["asm-disabled"]},
            headers=request_headers(supervisor, invalid_operation),
        )
        assert invalid.status_code == 422

        for token in (member1, member2):
            listed = client.get(
                "/api/v1/assembly/tasks?deviceId=device-member",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert listed.status_code == 200, listed.text
            assert listed.json()["total"] == 1
            task = listed.json()["items"][0]
            assert task["id"] == "member-task"
            assert task["deviceId"] == "device-member"
            assert {member["assembler_id"] for member in task["members"]} == {
                "asm-member-1",
                "asm-member-2",
            }

        assert client.get(
            "/api/v1/assembly/tasks?deviceId=device-other",
            headers={"Authorization": f"Bearer {member1}"},
        ).json()["items"] == []

        denied = client.post(
            "/api/v1/assembly/tasks/member-task/accept-material",
            json={"clientOperationId": str(uuid.uuid4())},
            headers=request_headers(outsider),
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "ASSEMBLY_TASK_FORBIDDEN"

        remove_operation = str(uuid.uuid4())
        removed = client.request(
            "DELETE",
            "/api/v1/assembly/tasks/member-task/assignments/asm-member-2",
            json={"clientOperationId": remove_operation},
            headers=request_headers(supervisor, remove_operation),
        )
        assert removed.status_code == 200, removed.text
        assert [member["assembler_id"] for member in removed.json()["members"]] == ["asm-member-1"]

        after_remove = client.post(
            "/api/v1/assembly/tasks/member-task/accept-material",
            json={"clientOperationId": str(uuid.uuid4())},
            headers=request_headers(member2),
        )
        assert after_remove.status_code == 403


def test_assignment_replay_is_stable_and_does_not_duplicate_members_or_audit():
    backend.init_db()
    add_user("replay-1", "replay1", "ASSEMBLER")
    add_user("replay-2", "replay2", "ASSEMBLER")
    add_user("replay-supervisor", "replay-supervisor", "WORKSHOP_SUPERVISOR")
    insert_task("replay-task", "device-replay")

    with TestClient(backend.app) as client:
        supervisor = login(client, "replay-supervisor")
        operation = str(uuid.uuid4())
        payload = {"clientOperationId": operation, "assemblerIds": ["replay-1", "replay-2"]}
        headers = request_headers(supervisor, operation)
        first = client.post(
            "/api/v1/assembly/tasks/replay-task/assignments",
            json=payload,
            headers=headers,
        )
        replay = client.post(
            "/api/v1/assembly/tasks/replay-task/assignments",
            json=payload,
            headers=headers,
        )
        assert first.status_code == replay.status_code == 200
        first_body = first.json()
        replay_body = replay.json()
        assert {key: value for key, value in replay_body.items() if key != "idempotent"} == {
            key: value for key, value in first_body.items() if key != "idempotent"
        }
        assert first_body["idempotent"] is False
        assert replay_body["idempotent"] is True

        c = backend.db()
        assert c.execute(
            "SELECT COUNT(*) FROM assembly_task_members WHERE task_id=? AND removed_at IS NULL",
            ("replay-task",),
        ).fetchone()[0] == 2
        assert c.execute(
            "SELECT COUNT(*) FROM assembly_assignment_operations WHERE client_operation_id=?",
            (operation,),
        ).fetchone()[0] == 1
        assert c.execute(
            "SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND event_type='ASSEMBLY_TASK_ASSIGNED'",
            ("replay-task",),
        ).fetchone()[0] == 1
        c.close()
