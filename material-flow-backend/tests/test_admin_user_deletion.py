"""管理员停用删除用户的 API、幂等和业务阻断契约测试。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = tempfile.mkdtemp(prefix="mf_admin_user_delete_")
os.environ.update(
    MATERIAL_FLOW_DATA=f"{ROOT}/data",
    MATERIAL_FLOW_UPLOADS=f"{ROOT}/uploads",
    INITIAL_ADMIN_PASSWORD="DeleteAdmin@2026",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as backend  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database():
    shutil.rmtree(f"{ROOT}/data", ignore_errors=True)
    shutil.rmtree(f"{ROOT}/uploads", ignore_errors=True)
    yield


def _login(client: TestClient, username: str, password: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": f"device-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _admin_session(client: TestClient) -> dict:
    initial = _login(client, "owlco", "DeleteAdmin@2026")
    changed = client.post(
        "/api/v1/auth/change-password",
        json={"oldPassword": "DeleteAdmin@2026", "newPassword": "DeleteAdminChanged@2026"},
        headers={"Authorization": f"Bearer {initial['accessToken']}"},
    )
    assert changed.status_code == 200, changed.text
    return _login(client, "owlco", "DeleteAdminChanged@2026")


def _insert_user(user_id: str, username: str, role: str = "OPERATOR") -> None:
    c = backend.db()
    c.execute(
        """INSERT INTO users
           (id,username,display_name,role,password_hash,must_change_password,active,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (user_id, username, username, role, backend.hash_password("User@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def _delete_headers(admin: dict, operation: str, request_id: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {admin['accessToken']}",
        "X-Request-Id": request_id or str(uuid.uuid4()),
        "Idempotency-Key": operation,
    }


def test_admin_delete_disables_user_revokes_sessions_and_replays_without_new_audit():
    with TestClient(backend.app) as client:
        admin = _admin_session(client)
        _insert_user("u_delete", "delete-user")
        target = _login(client, "delete-user", "User@2026")
        operation = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        payload = {"clientOperationId": operation}

        first = client.request(
            "DELETE",
            "/api/v1/admin/users/u_delete",
            json=payload,
            headers=_delete_headers(admin, operation, request_id),
        )
        assert first.status_code == 200, first.text
        assert first.json()["userId"] == "u_delete"
        assert first.json()["status"] == "DELETED"
        assert first.json()["idempotent"] is False
        assert first.json()["traceId"] == request_id
        assert "User@2026" not in first.text
        assert "accessToken" not in first.text

        c = backend.db()
        assert c.execute("SELECT active FROM users WHERE id='u_delete'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM sessions WHERE user_id='u_delete'").fetchone()[0] == 0
        audit_count = c.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='DELETE' AND resource_type='USER' AND resource_id='u_delete'"
        ).fetchone()[0]
        assert audit_count == 1
        operation_row = c.execute(
            "SELECT payload_json FROM admin_user_delete_operations WHERE client_operation_id=?",
            (operation,),
        ).fetchone()
        assert operation_row and "User@2026" not in operation_row["payload_json"]
        c.close()

        assert client.get(
            "/api/v1/materials/MTR-001/inventory",
            headers={"Authorization": f"Bearer {target['accessToken']}"},
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login",
            json={"username": "delete-user", "password": "User@2026", "deviceId": "new-device"},
        ).status_code == 401

        replay_request_id = str(uuid.uuid4())
        replay = client.request(
            "DELETE",
            "/api/v1/admin/users/u_delete",
            json=payload,
            headers=_delete_headers(admin, operation, replay_request_id),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent"] is True
        assert replay.json()["traceId"] == replay_request_id
        c = backend.db()
        assert c.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='DELETE' AND resource_type='USER' AND resource_id='u_delete'"
        ).fetchone()[0] == 1
        c.close()


def test_delete_requires_admin_uuid_headers_and_matching_operation_id():
    with TestClient(backend.app) as client:
        admin = _admin_session(client)
        _insert_user("u_headers", "headers-user")
        operation = str(uuid.uuid4())
        payload = {"clientOperationId": operation}
        valid = _delete_headers(admin, operation)

        missing_request_id = dict(valid)
        missing_request_id.pop("X-Request-Id")
        response = client.request(
            "DELETE", "/api/v1/admin/users/u_headers", json=payload, headers=missing_request_id
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REQUEST_ID"

        invalid_key = dict(valid, **{"Idempotency-Key": "not-a-uuid"})
        response = client.request(
            "DELETE", "/api/v1/admin/users/u_headers", json=payload, headers=invalid_key
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

        mismatch = dict(valid, **{"Idempotency-Key": str(uuid.uuid4())})
        response = client.request(
            "DELETE", "/api/v1/admin/users/u_headers", json=payload, headers=mismatch
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"

        first_delete = client.request(
            "DELETE",
            "/api/v1/admin/users/u_headers",
            json=payload,
            headers=valid,
        )
        assert first_delete.status_code == 200, first_delete.text

        _insert_user("u_other", "other-user")
        _insert_user("u_nonadmin", "non-admin-user")
        conflict = client.request(
            "DELETE",
            "/api/v1/admin/users/u_other",
            json=payload,
            headers=valid,
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

        non_admin = _login(client, "non-admin-user", "User@2026")
        response = client.request(
            "DELETE",
            "/api/v1/admin/users/u_other",
            json={"clientOperationId": str(uuid.uuid4())},
            headers=_delete_headers(non_admin, str(uuid.uuid4())),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"

        operation = str(uuid.uuid4())
        response = client.request(
            "DELETE",
            "/api/v1/admin/users/u_other",
            json={"clientOperationId": operation},
            headers=_delete_headers(non_admin, operation),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("business", ["assembly", "transfer", "stocktake", "exception", "handover"])
def test_delete_is_blocked_by_unfinished_business(business: str):
    with TestClient(backend.app) as client:
        admin = _admin_session(client)
        _insert_user("u_busy", "busy-user")
        c = backend.db()
        ts = backend.now()
        if business == "assembly":
            c.execute(
                """INSERT INTO assembly_tasks
                   (id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,task_version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                ("task_busy", "WO-BUSY", "device-busy", "BUSY-01", "u_busy", "IN_PROGRESS", 1, 1, ts, ts),
            )
        elif business == "transfer":
            c.execute(
                """INSERT INTO transfer_requests
                   (id,client_operation_id,type,document_no,status,payload_json,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("tr_busy", str(uuid.uuid4()), "OUTBOUND", "WO-BUSY", "PENDING_APPROVAL", "{}", "u_busy", ts),
            )
        elif business == "stocktake":
            c.execute(
                """INSERT INTO stocktakes
                   (id,material_id,location_id,book_quantity,actual_quantity,difference,status,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("st_busy", "mat_001", "loc_001", 1, 0, -1, "PENDING_CONFIRM", "u_busy", ts),
            )
        elif business == "exception":
            c.execute(
                """INSERT INTO exceptions
                   (id,material_id,type,book_quantity,actual_quantity,difference,status,description,evidence_ids,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("ex_busy", "mat_001", "SHORTAGE", 1, 0, -1, "PENDING", "busy", "[]", "u_busy", ts),
            )
        else:
            c.execute(
                """INSERT INTO transfer_requests
                   (id,client_operation_id,type,document_no,status,payload_json,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("tr_handover", str(uuid.uuid4()), "OUTBOUND", "WO-BUSY", "EXECUTED", "{}", "u_admin", ts),
            )
            c.execute(
                """INSERT INTO material_handovers
                   (id,work_item_id,transfer_request_id,quantity,from_location,client_operation_id,status,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                ("ho_busy", "work-busy", "tr_handover", 1, "A-01-03", str(uuid.uuid4()), "PENDING", "u_admin", ts),
            )
            c.execute(
                "UPDATE material_handovers SET receiver_user_id=? WHERE id='ho_busy'", ("u_busy",)
            )
        c.commit()
        c.close()

        operation = str(uuid.uuid4())
        response = client.request(
            "DELETE",
            "/api/v1/admin/users/u_busy",
            json={"clientOperationId": operation},
            headers=_delete_headers(admin, operation),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "USER_HAS_ACTIVE_BUSINESS"
        c = backend.db()
        assert c.execute("SELECT active FROM users WHERE id='u_busy'").fetchone()[0] == 1
        assert c.execute(
            "SELECT COUNT(*) FROM admin_user_delete_operations WHERE client_operation_id=?", (operation,)
        ).fetchone()[0] == 0
        c.close()


def test_delete_self_and_missing_user_are_unified_errors():
    with TestClient(backend.app) as client:
        admin = _admin_session(client)
        operation = str(uuid.uuid4())
        response = client.request(
            "DELETE",
            "/api/v1/admin/users/u_admin",
            json={"clientOperationId": operation},
            headers=_delete_headers(admin, operation),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "USER_CANNOT_DELETE_SELF"

        operation = str(uuid.uuid4())
        response = client.request(
            "DELETE",
            "/api/v1/admin/users/does-not-exist",
            json={"clientOperationId": operation},
            headers=_delete_headers(admin, operation),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "USER_NOT_FOUND"
