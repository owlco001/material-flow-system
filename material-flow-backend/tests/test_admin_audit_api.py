"""Administrator audit query and transfer read-scope contract tests."""

import importlib.util
import os
import sys
import tempfile
import uuid


TEST_ROOT = tempfile.mkdtemp(prefix="mf_admin_audit_api_")
_ENV_KEYS = ("MATERIAL_FLOW_DATA", "MATERIAL_FLOW_UPLOADS", "INITIAL_ADMIN_PASSWORD")
_PREVIOUS_ENV = {key: os.environ.get(key) for key in _ENV_KEYS}
os.environ["MATERIAL_FLOW_DATA"] = f"{TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{TEST_ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "AuditAdmin@2026"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "app"))

from fastapi.testclient import TestClient  # noqa: E402


_spec = importlib.util.spec_from_file_location(
    "admin_audit_backend", os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "main.py")
)
assert _spec is not None
backend = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = backend
_spec.loader.exec_module(backend)

for _key, _value in _PREVIOUS_ENV.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value


ADMIN_PASSWORD = "AuditAdmin@2026"
USER_PASSWORD = "AuditUser@2026"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_user(user_id: str, username: str, role: str) -> None:
    c = backend.db()
    c.execute(
        "INSERT INTO users(id, username, display_name, role, password_hash, "
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


def _insert_audit_facts(suffix: str = "") -> tuple[str, str, str]:
    transfer_id = f"tr_audit_scope{suffix}"
    handover_id = f"ho_audit_scope{suffix}"
    work_item_id = f"wi_audit_scope{suffix}"
    operator_id = f"u_operator{suffix}"
    material_id = f"u_material{suffix}"
    device_id = "dev_demo_HZ01" if not suffix else "dev_demo_HZ02"
    requirement_id = f"omr_demo_{device_id}_mat_ctl_cabinet"
    operation_id = str(uuid.uuid4())
    event_time = "2026-09-14T08:00:00+00:00"
    c = backend.db()
    c.execute(
        """INSERT INTO transfer_requests
           (id, client_operation_id, type, document_no, status, payload_json,
            created_by, created_at, approved_by, approved_at, executed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            transfer_id, operation_id, "OUTBOUND", "26B-013", "EXECUTED",
            '{"items":[{"materialId":"mat_ctl_cabinet","quantity":1,"expectedInventoryVersion":1}]}',
            material_id, event_time, "u_admin", event_time, event_time,
        ),
    )
    c.execute(
        """INSERT INTO material_work_items
           (id, requirement_id, material_id, device_id, assigned_user_id, quantity)
           VALUES(?,?,?,?,?,?)""",
        (
            work_item_id, requirement_id, "mat_ctl_cabinet", device_id, operator_id, 1,
        ),
    )
    c.execute(
        """INSERT INTO material_handovers
           (id, work_item_id, transfer_request_id, quantity, from_location, device_id,
            receiver_user_id, remark, client_operation_id, status, created_by, created_at,
            confirmed_by, confirmed_at, decision_reason)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            handover_id, work_item_id, transfer_id, 1, "A-01-03", device_id,
            operator_id, "已送达", str(uuid.uuid4()), "CONFIRMED", material_id,
            event_time, operator_id, event_time, None,
        ),
    )
    c.execute(
        """INSERT INTO material_work_item_projections
           (work_item_id, status_code, current_owner_user_id, last_handover_id,
            target_device_id, status_updated_at, updated_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            work_item_id, "AT_STATION", operator_id, handover_id,
            device_id, event_time, event_time,
        ),
    )
    c.execute(
        """INSERT INTO audit_logs
           (operator_id, role, action, resource_type, resource_id, request_id,
            occurred_at, result)
           VALUES(?,?,?,?,?,?,?,?)""",
        ("u_admin", "ADMIN", "CREATE", "TRANSFER_REQUEST", transfer_id, "req-log", event_time, "SUCCESS"),
    )
    c.execute(
        """INSERT INTO audit_events
           (event_type, entity_type, entity_id, actor_user_id, actor_role, request_id,
            client_operation_id, before_json, after_json, server_time, device_id,
            source_ip, result)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "OUTBOUND_CONFIRMED", "TRANSFER_REQUEST", transfer_id, "u_admin", "ADMIN",
            "req-event", operation_id, '{"status":"APPROVED"}',
            '{"status":"EXECUTED"}', event_time, "device-admin", "203.0.113.77", "SUCCESS",
        ),
    )
    c.execute(
        """INSERT INTO audit_events
           (event_type, entity_type, entity_id, actor_user_id, actor_role, request_id,
            client_operation_id, before_json, after_json, server_time, device_id,
            source_ip, result)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "HANDOVER_CONFIRMED", "MATERIAL_HANDOVER", handover_id, operator_id, "OPERATOR",
            "req-handover", str(uuid.uuid4()), '{"status":"PENDING"}',
            '{"status":"CONFIRMED"}', event_time, "device-operator", "203.0.113.78", "SUCCESS",
        ),
    )
    c.commit()
    c.close()
    return transfer_id, handover_id, work_item_id


def test_audit_query_is_admin_only_filtered_paginated_stable_and_redacted():
    with TestClient(backend.app) as client:
        _add_user("u_operator", "audit_operator", "OPERATOR")
        _add_user("u_material", "audit_material", "MATERIAL")
        _add_user("u_warehouse", "audit_warehouse", "WAREHOUSE_ADMIN")
        transfer_id, _, _ = _insert_audit_facts()
        admin = _login(client, "owlco")
        operator = _login(client, "audit_operator")
        warehouse = _login(client, "audit_warehouse")

        assert client.get("/api/v1/audit-logs").status_code == 401
        assert client.get("/api/v1/audit-logs", headers=_headers(operator)).status_code == 403
        assert client.get("/api/v1/audit-logs", headers=_headers(warehouse)).status_code == 403

        first = client.get(
            "/api/v1/audit-logs?page=1&pageSize=2",
            headers=_headers(admin),
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["page"] == 1
        assert first_body["pageSize"] == 2
        assert first_body["total"] >= 3
        assert first_body["totalPages"] >= 2
        assert len(first_body["items"]) == 2
        assert first.text.count("source_ip") == 0
        assert "203.0.113.77" not in first.text
        assert "203.0.113.78" not in first.text

        repeated = client.get(
            "/api/v1/audit-logs?page=1&pageSize=2",
            headers=_headers(admin),
        )
        assert repeated.json()["items"] == first_body["items"]

        event_filter = client.get(
            "/api/v1/audit-logs?eventType=OUTBOUND_CONFIRMED&entityType=TRANSFER_REQUEST"
            f"&operatorId=u_admin&entityId={transfer_id}",
            headers=_headers(admin),
        )
        assert event_filter.status_code == 200
        assert event_filter.json()["total"] == 1
        assert event_filter.json()["items"][0]["event_type"] == "OUTBOUND_CONFIRMED"
        assert event_filter.json()["items"][0]["entity_id"] == transfer_id

        time_filter = client.get(
            "/api/v1/audit-logs?from=2026-09-14T07:59:59Z&to=2026-09-14T08:00:01Z",
            headers=_headers(admin),
        )
        assert time_filter.status_code == 200
        assert time_filter.json()["total"] >= 3

        empty = client.get(
            "/api/v1/audit-logs?eventType=NO_SUCH_EVENT",
            headers=_headers(admin),
        )
        assert empty.status_code == 200
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0
        assert empty.json()["totalPages"] == 0

        assert client.get("/api/v1/audit-logs?page=0", headers=_headers(admin)).status_code == 422
        assert client.get("/api/v1/audit-logs?pageSize=0", headers=_headers(admin)).status_code == 422
        assert client.get("/api/v1/audit-logs?pageSize=101", headers=_headers(admin)).status_code == 422
        assert client.get(
            "/api/v1/audit-logs?from=not-a-time", headers=_headers(admin)
        ).status_code == 400
        assert client.get(
            "/api/v1/audit-logs?from=2026-09-15&to=2026-09-14", headers=_headers(admin)
        ).status_code == 400


def test_transfer_list_detail_scope_and_status_match_workspace_and_timeline():
    with TestClient(backend.app) as client:
        _add_user("u_operator_2", "transfer_operator", "OPERATOR")
        _add_user("u_material_2", "transfer_material", "MATERIAL")
        _add_user("u_warehouse_2", "transfer_warehouse", "WAREHOUSE_ADMIN")
        _add_user("u_other_operator", "transfer_other_operator", "OPERATOR")
        transfer_id, handover_id, work_item_id = _insert_audit_facts("_2")
        admin = _login(client, "owlco")
        operator = _login(client, "transfer_operator")
        other_operator = _login(client, "transfer_other_operator")
        material = _login(client, "transfer_material")
        warehouse = _login(client, "transfer_warehouse")

        listed = client.get("/api/v1/transfer-requests", headers=_headers(operator))
        assert listed.status_code == 200, listed.text
        transfer = next(item for item in listed.json()["items"] if item["id"] == transfer_id)
        assert transfer["status"] == "EXECUTED"
        assert transfer["statusCode"] == "EXECUTED"
        assert transfer["transferStatus"] == "EXECUTED"
        assert transfer["workspaceStatus"] == "AT_STATION"
        assert transfer["handoverStatus"] == "CONFIRMED"

        detail = client.get(f"/api/v1/transfer-requests/{transfer_id}", headers=_headers(operator))
        assert detail.status_code == 200, detail.text
        assert detail.json()["workspaceStatus"] == transfer["workspaceStatus"]
        assert detail.json()["statusCode"] == transfer["statusCode"]

        workspace = client.get(
            "/api/v1/workspace/material-items?orderNo=26B-013",
            headers=_headers(operator),
        )
        assert workspace.status_code == 200
        workspace_item = next(
            item for item in workspace.json()["items"] if item["id"] == work_item_id
        )
        assert workspace_item["transferRequestId"] == transfer_id
        assert workspace_item["transferStatus"] == "EXECUTED"
        assert workspace_item["statusCode"] == "AT_STATION"

        other_list = client.get("/api/v1/transfer-requests", headers=_headers(other_operator))
        assert other_list.status_code == 200
        assert all(item["id"] != transfer_id for item in other_list.json()["items"])
        assert client.get(
            f"/api/v1/transfer-requests/{transfer_id}", headers=_headers(other_operator)
        ).status_code == 403

        timeline = client.get(
            f"/api/v1/handovers/{handover_id}/timeline",
            headers=_headers(operator),
        )
        assert timeline.status_code == 200
        assert timeline.json()["workspaceStatus"] == "AT_STATION"
        assert "source_ip" not in timeline.text
        assert "203.0.113.78" not in timeline.text

        # 创建人、仓库管理员和管理员均在共享范围内；无关角色不会越权。
        assert any(item["id"] == transfer_id for item in client.get(
            "/api/v1/transfer-requests", headers=_headers(material)
        ).json()["items"])
        assert any(item["id"] == transfer_id for item in client.get(
            "/api/v1/transfer-requests", headers=_headers(warehouse)
        ).json()["items"])
        assert any(item["id"] == transfer_id for item in client.get(
            "/api/v1/transfer-requests", headers=_headers(admin)
        ).json()["items"])
