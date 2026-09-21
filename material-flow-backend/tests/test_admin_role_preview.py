"""Admin-only role preview regression tests."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import uuid

from fastapi.testclient import TestClient


TEST_ROOT = tempfile.mkdtemp(prefix="mf_admin_role_preview_")
os.environ["MATERIAL_FLOW_DATA"] = f"{TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{TEST_ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "PreviewAdmin@2026"
os.environ.pop("ENABLE_ADMIN_ROLE_PREVIEW", None)

BACKEND_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(BACKEND_ROOT, "app"))
_spec = importlib.util.spec_from_file_location(
    "admin_role_preview_backend", os.path.join(BACKEND_ROOT, "app", "main.py")
)
assert _spec is not None and _spec.loader is not None
backend = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = backend
_spec.loader.exec_module(backend)


ADMIN_PASSWORD = "PreviewAdmin@2026"
USER_PASSWORD = "PreviewUser@2026"


def _add_user(user_id: str, username: str, role: str) -> None:
    c = backend.db()
    c.execute(
        "INSERT OR IGNORE INTO users(id, username, display_name, role, password_hash, "
        "must_change_password, active, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, username, username, role, backend.hash_password(USER_PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def _login(client: TestClient, username: str) -> str:
    password = ADMIN_PASSWORD if username == "owlco" else USER_PASSWORD
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": f"device-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _preview_headers(token: str, request_id: str | None = None, operation_id: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": request_id or str(uuid.uuid4()),
        "X-Client-Operation-Id": operation_id or str(uuid.uuid4()),
    }


def _enable_preview() -> None:
    backend.ENABLE_ADMIN_ROLE_PREVIEW = True


def test_role_preview_is_admin_only_read_only_and_audited_without_sensitive_data():
    with TestClient(backend.app) as client:
        backend.ENABLE_ADMIN_ROLE_PREVIEW = False
        _add_user("u_preview_operator", "preview_operator", "OPERATOR")
        admin_token = _login(client, "owlco")
        operator_token = _login(client, "preview_operator")

        assert backend.ENABLE_ADMIN_ROLE_PREVIEW is False
        disabled = client.get(
            "/api/v1/workspace/summary?viewRole=OPERATOR",
            headers=_preview_headers(admin_token),
        )
        assert disabled.status_code == 404
        assert disabled.json()["error"]["code"] == "ROLE_PREVIEW_DISABLED"

        _enable_preview()
        not_admin = client.get(
            "/api/v1/workspace/summary?viewRole=OPERATOR",
            headers=_preview_headers(operator_token),
        )
        assert not_admin.status_code == 403
        assert not_admin.json()["error"]["code"] == "ROLE_PREVIEW_ADMIN_ONLY"

        unauthenticated_write = client.post(
            "/api/v1/transfer-requests?viewRole=OPERATOR",
            json={"clientOperationId": str(uuid.uuid4()), "type": "OUTBOUND", "items": []},
            headers={"X-Request-Id": str(uuid.uuid4())},
        )
        assert unauthenticated_write.status_code == 401
        assert unauthenticated_write.json()["error"]["code"] == "UNAUTHORIZED"

        invalid_role = client.get(
            "/api/v1/workspace/summary?viewRole=NOT_A_ROLE",
            headers=_preview_headers(admin_token),
        )
        assert invalid_role.status_code == 400
        assert invalid_role.json()["error"]["code"] == "INVALID_VIEW_ROLE"

        missing_headers = client.get(
            "/api/v1/workspace/summary?viewRole=OPERATOR",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert missing_headers.status_code == 400
        assert missing_headers.json()["error"]["code"] == "INVALID_REQUEST_ID"

        operation_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        summary = client.get(
            "/api/v1/workspace/summary?viewRole=OPERATOR",
            headers=_preview_headers(admin_token, request_id, operation_id),
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["role"] == "OPERATOR"
        assert summary.json()["preview"] is True
        assert summary.json()["authenticatedRole"] == "ADMIN"

        repeated = client.get(
            "/api/v1/workspace/summary?viewRole=OPERATOR",
            headers=_preview_headers(admin_token, request_id, operation_id),
        )
        assert repeated.status_code == 200
        assert repeated.json()["role"] == "OPERATOR"

        c = backend.db()
        events = c.execute(
            "SELECT * FROM audit_events WHERE event_type='ADMIN_ROLE_PREVIEW' "
            "AND client_operation_id=?",
            (operation_id,),
        ).fetchall()
        assert len(events) == 1
        event = events[0]
        assert event["actor_role"] == "ADMIN"
        assert event["view_role"] == "OPERATOR"
        assert event["action"] == "QUERY"
        assert event["request_id"] == request_id
        assert event["source_ip"] is None
        assert event["device_id"] is None
        assert "Authorization" not in event["before_json"] + event["after_json"]
        assert "password" not in event["before_json"] + event["after_json"]
        assert admin_token not in event["before_json"] + event["after_json"]
        c.close()

        operation = str(uuid.uuid4())
        write = client.post(
            "/api/v1/transfer-requests?viewRole=OPERATOR",
            json={"clientOperationId": operation, "type": "OUTBOUND", "items": []},
            headers={
                **_preview_headers(admin_token),
                "Idempotency-Key": operation,
            },
        )
        assert write.status_code == 403
        assert write.json()["error"]["code"] == "ROLE_PREVIEW_READ_ONLY"


def test_all_preview_roles_return_read_projection_metadata_and_enter_exit_audits():
    with TestClient(backend.app) as client:
        _enable_preview()
        _add_user("u_preview_operator", "preview_operator", "OPERATOR")
        _add_user("u_preview_material", "preview_material", "MATERIAL")
        admin_token = _login(client, "owlco")
        c = backend.db()
        c.executemany(
            "INSERT INTO material_work_items(id, requirement_id, material_id, device_id, "
            "assigned_user_id, quantity) VALUES(?,?,?,?,?,?)",
            [
                (
                    "wi_preview_operator",
                    "omr_demo_dev_demo_HZ01_mat_ctl_cabinet",
                    "mat_ctl_cabinet", "dev_demo_HZ01", "u_preview_operator", 1,
                ),
                (
                    "wi_preview_material",
                    "omr_demo_dev_demo_HZ03_mat_ctl_cabinet",
                    "mat_ctl_cabinet", "dev_demo_HZ03", "u_preview_material", 1,
                ),
            ],
        )
        c.commit()
        c.close()

        for role in ("OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "ADMIN"):
            summary = client.get(
                f"/api/v1/workspace/summary?viewRole={role}",
                headers=_preview_headers(admin_token),
            )
            assert summary.status_code == 200, summary.text
            assert summary.json()["role"] == role
            assert summary.json()["preview"] is True
            assert summary.json()["authenticatedRole"] == "ADMIN"

            items = client.get(
                f"/api/v1/workspace/material-items?viewRole={role}&page=1&pageSize=20",
                headers=_preview_headers(admin_token),
            )
            assert items.status_code == 200, items.text
            assert items.json()["preview"] is True
            assert items.json()["authenticatedRole"] == "ADMIN"
            assert items.json()["role"] == role
            if role == "OPERATOR":
                assert items.json()["total"] == 1
                assert items.json()["items"][0]["id"] == "wi_preview_operator"
            elif role == "MATERIAL":
                assert items.json()["total"] == 1
                assert items.json()["items"][0]["id"] == "wi_preview_material"
            else:
                assert items.json()["total"] == 75

        enter_operation = str(uuid.uuid4())
        enter = client.post(
            "/api/v1/workspace/role-preview/enter?viewRole=MATERIAL",
            headers=_preview_headers(admin_token, operation_id=enter_operation),
        )
        assert enter.status_code == 200, enter.text
        assert enter.json() == {
            "role": "MATERIAL",
            "preview": True,
            "authenticatedRole": "ADMIN",
            "traceId": enter.json()["traceId"],
        }

        exit_operation = str(uuid.uuid4())
        leave = client.post(
            "/api/v1/workspace/role-preview/exit?viewRole=MATERIAL",
            headers=_preview_headers(admin_token, operation_id=exit_operation),
        )
        assert leave.status_code == 200, leave.text
        assert leave.json()["role"] == "ADMIN"
        assert leave.json()["preview"] is False
        assert leave.json()["authenticatedRole"] == "ADMIN"

        real_summary = client.get(
            "/api/v1/workspace/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert real_summary.status_code == 200
        assert real_summary.json()["role"] == "ADMIN"
        assert real_summary.json()["preview"] is False
        c = backend.db()
        assert c.execute("SELECT role FROM users WHERE id='u_admin'").fetchone()["role"] == "ADMIN"
        c.close()

        c = backend.db()
        actions = c.execute(
            "SELECT action,view_role,actor_role FROM audit_events "
            "WHERE event_type='ADMIN_ROLE_PREVIEW' ORDER BY id"
        ).fetchall()
        assert {row["action"] for row in actions} >= {"ENTER", "QUERY", "EXIT"}
        assert all(row["actor_role"] == "ADMIN" for row in actions)
        c.close()


def test_preview_configuration_treats_invalid_values_as_disabled():
    assert backend._env_flag("false") is False
    assert backend._env_flag("garbage") is False
    assert backend._env_flag("TRUE") is True
