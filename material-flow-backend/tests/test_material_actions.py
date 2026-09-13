"""阶段 3 业务动作验收：提交、审批、出库、领取/到机台和工作台投影。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import uuid

from fastapi.testclient import TestClient


ROOT = "/tmp/mf_material_actions"
shutil.rmtree(ROOT, ignore_errors=True)
os.environ["MATERIAL_FLOW_DATA"] = f"{ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "ActionsAdmin@2026"

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "main.py")
spec = importlib.util.spec_from_file_location("material_actions_backend", APP_PATH)
assert spec is not None and spec.loader is not None
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)


ADMIN_PASSWORD = "ActionsAdmin@2026"
USER_PASSWORD = "ActionsUser@2026"


def _headers(token: str, operation: str | None = None) -> dict[str, str]:
    operation = operation or str(uuid.uuid4())
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": str(uuid.uuid4()),
        "Idempotency-Key": operation,
    }


def _add_user(user_id: str, username: str, role: str) -> None:
    c = backend.db()
    c.execute(
        """INSERT INTO users(id,username,display_name,role,password_hash,
                              must_change_password,active,created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
        (user_id, username, username, role, backend.hash_password(USER_PASSWORD),
         0, 1, backend.now()),
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


def _create_outbound(
    client: TestClient,
    token: str,
    quantity: int = 1,
    material_id: str = "mat_ctl_cabinet",
    expected_version: int = 1,
) -> tuple[str, str]:
    operation = str(uuid.uuid4())
    response = client.post(
        "/api/v1/transfer-requests",
        json={
            "clientOperationId": operation,
            "type": "OUTBOUND",
            "documentNo": "26B-013",
            "items": [{
                "materialId": material_id,
                "quantity": quantity,
                "sourceLocationCode": "A-01-03",
                "expectedInventoryVersion": expected_version,
            }],
        },
        headers=_headers(token, operation),
    )
    assert response.status_code == 200, response.text
    return response.json()["requestId"], operation


def test_complete_actions_permissions_idempotency_inventory_audit_and_workspace():
    with TestClient(backend.app) as client:
        _add_user("u_operator_actions", "operator_actions", "OPERATOR")
        _add_user("u_material_actions", "material_actions", "MATERIAL")
        _add_user("u_warehouse_actions", "warehouse_actions", "WAREHOUSE_ADMIN")
        _add_user("u_other_actions", "other_actions", "OPERATOR")

        operator = _login(client, "operator_actions")
        material = _login(client, "material_actions")
        warehouse = _login(client, "warehouse_actions")
        other_operator = _login(client, "other_actions")
        admin = _login(client, "owlco")

        c = backend.db()
        c.execute(
            "UPDATE materials SET available_quantity=5,total_quantity=5,version=1 WHERE id='mat_ctl_cabinet'"
        )
        c.execute(
            "INSERT INTO inventory(id,material_id,location_id,quantity) VALUES(?,?,?,?)",
            ("inv_actions_ctl", "mat_ctl_cabinet", "loc_001", 5),
        )
        c.commit()
        c.close()

        # 物料员提交出库；仓管不能伪造物料员提交范围，操作员也不得确认审批。
        request_id, _ = _create_outbound(client, material, quantity=2)
        assert client.get(
            f"/api/v1/transfer-requests/{request_id}",
            headers={"Authorization": f"Bearer {other_operator}"},
        ).status_code == 403
        denied_submit_operation = str(uuid.uuid4())
        denied_submit = client.post(
            "/api/v1/transfer-requests",
            json={
                "clientOperationId": denied_submit_operation,
                "type": "OUTBOUND",
                "documentNo": "26B-013",
                "items": [{"materialId": "mat_ctl_cabinet", "quantity": 1,
                           "expectedInventoryVersion": 1}],
            },
            headers=_headers(warehouse, denied_submit_operation),
        )
        assert denied_submit.status_code == 403

        approval_operation = str(uuid.uuid4())
        approval_payload = {"decision": "APPROVE", "clientOperationId": approval_operation}
        denied_approval = client.post(
            f"/api/v1/transfer-requests/{request_id}/approve",
            json=approval_payload,
            headers=_headers(operator, approval_operation),
        )
        assert denied_approval.status_code == 403

        approved = client.post(
            f"/api/v1/transfer-requests/{request_id}/approve",
            json=approval_payload,
            headers=_headers(warehouse, approval_operation),
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED"
        repeated_approval = client.post(
            f"/api/v1/transfer-requests/{request_id}/approve",
            json=approval_payload,
            headers=_headers(warehouse, approval_operation),
        )
        assert repeated_approval.status_code == 200
        assert repeated_approval.json()["idempotent"] is True

        # 审批通过但尚未执行时，库存不能变化。
        before = backend.db().execute(
            "SELECT available_quantity,total_quantity,version FROM materials WHERE id=?",
            ("mat_ctl_cabinet",),
        ).fetchone()
        assert tuple(before) == (5, 5, 1)

        # 执行需要独立的幂等动作，且审批人与执行人不能相同。
        execute_operation = str(uuid.uuid4())
        execute_payload = {"clientOperationId": execute_operation}
        same_user_execute = client.post(
            f"/api/v1/transfer-requests/{request_id}/execute",
            json=execute_payload,
            headers=_headers(warehouse, execute_operation),
        )
        assert same_user_execute.status_code == 409
        assert same_user_execute.json()["error"]["code"] == "APPROVAL_EXECUTOR_SAME_USER"

        executed = client.post(
            f"/api/v1/transfer-requests/{request_id}/execute",
            json=execute_payload,
            headers=_headers(admin, execute_operation),
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "EXECUTED"
        after_execute = backend.db().execute(
            "SELECT available_quantity,total_quantity,version FROM materials WHERE id=?",
            ("mat_ctl_cabinet",),
        ).fetchone()
        assert tuple(after_execute) == (3, 3, 2)
        transfer_audit = backend.db().execute(
            """SELECT action,operator_id,request_id FROM audit_logs
                WHERE resource_type='TRANSFER_REQUEST' AND resource_id=? ORDER BY id""",
            (request_id,),
        ).fetchall()
        assert [row["action"] for row in transfer_audit] == ["CREATE", "APPROVE", "EXECUTE"]
        assert all(row["request_id"] for row in transfer_audit)

        # 再用正式的 mat_001 库存快照验证扣减路径与库位明细同步。
        c = backend.db()
        c.execute("UPDATE materials SET available_quantity=10,total_quantity=10,version=1 WHERE id='mat_001'")
        c.execute("UPDATE inventory SET quantity=10 WHERE material_id='mat_001'")
        c.commit()
        c.close()
        stock_request, _ = _create_outbound(client, material, quantity=2, material_id="mat_001")
        stock_approval_op = str(uuid.uuid4())
        stock_approval = client.post(
            f"/api/v1/transfer-requests/{stock_request}/approve",
            json={"decision": "APPROVE", "clientOperationId": stock_approval_op},
            headers=_headers(warehouse, stock_approval_op),
        )
        assert stock_approval.status_code == 200, stock_approval.text
        stock_execute_op = str(uuid.uuid4())
        stock_execute = client.post(
            f"/api/v1/transfer-requests/{stock_request}/execute",
            json={"clientOperationId": stock_execute_op},
            headers=_headers(admin, stock_execute_op),
        )
        assert stock_execute.status_code == 200, stock_execute.text
        stock_after = backend.db().execute(
            "SELECT available_quantity,total_quantity,version FROM materials WHERE id='mat_001'"
        ).fetchone()
        location_after = backend.db().execute(
            """SELECT i.quantity FROM inventory i JOIN locations l ON l.id=i.location_id
                WHERE i.material_id='mat_001' AND l.code='A-01-03'"""
        ).fetchone()
        assert tuple(stock_after) == (8, 8, 2)
        assert location_after["quantity"] == 8

        # 已审批但库存不足时整单回滚：状态停在 APPROVED，不能写入出库确认事件。
        short_request, _ = _create_outbound(
            client, material, quantity=99, material_id="mat_001", expected_version=2
        )
        short_approval_op = str(uuid.uuid4())
        short_approval = client.post(
            f"/api/v1/transfer-requests/{short_request}/approve",
            json={"decision": "APPROVE", "clientOperationId": short_approval_op},
            headers=_headers(warehouse, short_approval_op),
        )
        assert short_approval.status_code == 200, short_approval.text
        short_execute_op = str(uuid.uuid4())
        short_execute = client.post(
            f"/api/v1/transfer-requests/{short_request}/execute",
            json={"clientOperationId": short_execute_op},
            headers=_headers(admin, short_execute_op),
        )
        assert short_execute.status_code == 409
        assert short_execute.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"
        short_row = backend.db().execute(
            "SELECT status FROM transfer_requests WHERE id=?", (short_request,)
        ).fetchone()
        assert short_row["status"] == "APPROVED"
        assert backend.db().execute(
            "SELECT COUNT(*) FROM audit_events WHERE entity_id=? AND event_type='OUTBOUND_CONFIRMED'",
            (short_request,),
        ).fetchone()[0] == 0
        assert backend.db().execute(
            "SELECT available_quantity,version FROM materials WHERE id='mat_001'"
        ).fetchone()["available_quantity"] == 8

        repeated_execute = client.post(
            f"/api/v1/transfer-requests/{stock_request}/execute",
            json={"clientOperationId": stock_execute_op},
            headers=_headers(admin, stock_execute_op),
        )
        assert repeated_execute.status_code == 200
        assert repeated_execute.json()["idempotent"] is True
        assert backend.db().execute(
            "SELECT available_quantity,version FROM materials WHERE id='mat_001'"
        ).fetchone()["available_quantity"] == 8

        # 交接正式关联工作项、订单机台和已执行 OUTBOUND；创建仍不扣库存。
        work_item = "omr_demo_dev_demo_HZ01_mat_ctl_cabinet"
        handover_operation = str(uuid.uuid4())
        handover_payload = {
            "workItemId": work_item,
            "transferRequestId": request_id,
            "quantity": 1,
            "fromLocation": "A-01-03",
            "deviceId": "dev_demo_HZ01",
            "receiverUserId": "u_operator_actions",
            "clientOperationId": handover_operation,
        }
        handover = client.post(
            "/api/v1/handovers",
            json=handover_payload,
            headers=_headers(material, handover_operation),
        )
        assert handover.status_code == 200, handover.text
        handover_id = handover.json()["handoverId"]
        handover_retry = client.post(
            "/api/v1/handovers",
            json=handover_payload,
            headers=_headers(material, handover_operation),
        )
        assert handover_retry.status_code == 200
        assert handover_retry.json()["idempotent"] is True

        wrong_operator_operation = str(uuid.uuid4())
        wrong_operator = client.post(
            f"/api/v1/handovers/{handover_id}/confirm",
            json={"clientOperationId": wrong_operator_operation, "requestId": str(uuid.uuid4())},
            headers=_headers(other_operator, wrong_operator_operation),
        )
        assert wrong_operator.status_code == 403

        confirm_operation = str(uuid.uuid4())
        confirm_payload = {"clientOperationId": confirm_operation, "requestId": str(uuid.uuid4())}
        confirmed = client.post(
            f"/api/v1/handovers/{handover_id}/confirm",
            json=confirm_payload,
            headers=_headers(operator, confirm_operation),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["eventTypes"] == [
            "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]
        confirm_retry = client.post(
            f"/api/v1/handovers/{handover_id}/confirm",
            json=confirm_payload,
            headers=_headers(operator, confirm_operation),
        )
        assert confirm_retry.status_code == 200
        assert confirm_retry.json()["idempotent"] is True

        # 仓库管理员也可以执行仓库侧交接确认，但仍必须走同一幂等动作和状态机。
        second_work_item = "omr_demo_dev_demo_HZ02_mat_ctl_cabinet"
        second_handover_operation = str(uuid.uuid4())
        second_handover = client.post(
            "/api/v1/handovers",
            json={
                **handover_payload,
                "workItemId": second_work_item,
                "deviceId": "dev_demo_HZ02",
                "receiverUserId": "u_operator_actions",
                "clientOperationId": second_handover_operation,
            },
            headers=_headers(material, second_handover_operation),
        )
        assert second_handover.status_code == 200, second_handover.text
        second_handover_id = second_handover.json()["handoverId"]
        warehouse_confirm_operation = str(uuid.uuid4())
        warehouse_confirm = client.post(
            f"/api/v1/handovers/{second_handover_id}/confirm",
            json={"clientOperationId": warehouse_confirm_operation, "requestId": str(uuid.uuid4())},
            headers=_headers(warehouse, warehouse_confirm_operation),
        )
        assert warehouse_confirm.status_code == 200, warehouse_confirm.text
        assert warehouse_confirm.json()["eventTypes"] == [
            "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]

        # 工作台状态和数量来自正式事实；交接确认不改订单需求数量。
        item = client.get(
            "/api/v1/workspace/material-items?status=AT_STATION&orderNo=26B-013",
            headers={"Authorization": f"Bearer {operator}"},
        )
        assert item.status_code == 200, item.text
        item_body = next(entry for entry in item.json()["items"] if entry["id"] == work_item)
        assert item_body["statusCode"] == "AT_STATION"
        assert item_body["requiredQuantity"] == 20
        assert item_body["pickedQuantity"] == 1
        assert item_body["currentOwnerUserId"] == "u_operator_actions"

        timeline = client.get(
            f"/api/v1/handovers/{handover_id}/timeline",
            headers={"Authorization": f"Bearer {operator}"},
        )
        assert timeline.status_code == 200
        assert [event["event_type"] for event in timeline.json()["items"]] == [
            "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED", "HANDOVER_CREATED",
            "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]
        assert all(
            key in event
            for event in timeline.json()["items"]
            for key in ("actor_user_id", "actor_role", "request_id", "client_operation_id", "server_time")
        )

        events = backend.db().execute(
            "SELECT event_type,entity_type FROM audit_events WHERE entity_id IN (?,?) ORDER BY id",
            (request_id, handover_id),
        ).fetchall()
        assert [event["event_type"] for event in events] == [
            "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED", "HANDOVER_CREATED",
            "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]
        assert events[0]["entity_type"] == "TRANSFER_REQUEST"
        assert events[1]["entity_type"] == "TRANSFER_REQUEST"


def test_action_state_conflict_payload_mismatch_and_missing_idempotency():
    with TestClient(backend.app) as client:
        _add_user("u_material_conflict", "material_conflict", "MATERIAL")
        _add_user("u_warehouse_conflict", "warehouse_conflict", "WAREHOUSE_ADMIN")
        material = _login(client, "material_conflict")
        warehouse = _login(client, "warehouse_conflict")

        request_id, _ = _create_outbound(client, material)
        approval_operation = str(uuid.uuid4())
        approval_headers = _headers(warehouse, approval_operation)
        approved = client.post(
            f"/api/v1/transfer-requests/{request_id}/approve",
            json={"decision": "APPROVE", "clientOperationId": approval_operation},
            headers=approval_headers,
        )
        assert approved.status_code == 200, approved.text
        changed_approval = client.post(
            f"/api/v1/transfer-requests/{request_id}/approve",
            json={"decision": "REJECT", "comment": "改变动作", "clientOperationId": approval_operation},
            headers=approval_headers,
        )
        assert changed_approval.status_code == 409
        assert changed_approval.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

        missing_execute_key = client.post(
            f"/api/v1/transfer-requests/{request_id}/execute",
            json={"clientOperationId": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {warehouse}", "X-Request-Id": str(uuid.uuid4())},
        )
        assert missing_execute_key.status_code == 400
        assert missing_execute_key.json()["error"]["code"] == "VALIDATION_ERROR"

        execute_operation = str(uuid.uuid4())
        executed = client.post(
            f"/api/v1/transfer-requests/{request_id}/execute",
            json={"clientOperationId": execute_operation},
            headers=_headers(warehouse, execute_operation),
        )
        assert executed.status_code == 409
        assert executed.json()["error"]["code"] == "APPROVAL_EXECUTOR_SAME_USER"
