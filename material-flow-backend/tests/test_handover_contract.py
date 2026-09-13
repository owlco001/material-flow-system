import os
import shutil
import sys
import uuid
import importlib.util

ROOT = "/tmp/mf_handover_contract"
shutil.rmtree(ROOT, ignore_errors=True)
os.environ["MATERIAL_FLOW_DATA"] = f"{ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{ROOT}/uploads"
os.environ["INITIAL_ADMIN_PASSWORD"] = "TestAdmin@2026"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "app"))

from fastapi.testclient import TestClient  # noqa: E402

# Load the application under a test-specific module name.  The workspace
# contract test uses a different database and also imports ``main``; relying
# on Python's shared module cache makes the first collected test leak its
# environment and database into the other file.
_spec = importlib.util.spec_from_file_location(
    "handover_contract_backend", os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "main.py")
)
assert _spec is not None
main = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = main
_spec.loader.exec_module(main)


def headers(token, operation=None):
    operation = operation or str(uuid.uuid4())
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": str(uuid.uuid4()),
        "Idempotency-Key": operation,
    }


def _add_user(user_id, username, role, password="TestUser@2026"):
    c = main.db()
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, username, username, role, main.hash_password(password), 0, 1, main.now()),
    )
    c.commit()
    c.close()


def _login(client, username, password):
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": password, "deviceId": f"device-{username}",
    })
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _approved_transfer(client, token, document_no="26B-013", material_id="mat_ctl_cabinet", quantity=1):
    operation = str(uuid.uuid4())
    response = client.post("/api/v1/transfer-requests", json={
        "clientOperationId": operation, "type": "OUTBOUND", "documentNo": document_no,
        "items": [{"materialId": material_id, "quantity": quantity,
                   "expectedInventoryVersion": 1}],
    }, headers=headers(token, operation))
    assert response.status_code == 200, response.text
    request_id = response.json()["requestId"]
    response = client.post(
        f"/api/v1/transfer-requests/{request_id}/approve",
        json={"decision": "APPROVE"},
        headers={"Authorization": f"Bearer {token}", "X-Request-Id": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    return request_id


def _create_handover(client, token, transfer_id, work_item, **overrides):
    operation = str(uuid.uuid4())
    payload = {
        "workItemId": work_item,
        "transferRequestId": transfer_id,
        "quantity": 1,
        "fromLocation": "A-01-03",
        "deviceId": "dev_demo_CC01",
        "receiverUserId": "u_operator",
        "clientOperationId": operation,
    }
    payload.update(overrides)
    response = client.post("/api/v1/handovers", json=payload, headers=headers(token, operation))
    assert response.status_code == 200, response.text
    return response.json()["handoverId"], payload


def test_handover_confirm_projection_events_inventory_and_timeline():
    with TestClient(main.app) as client:
        _add_user("u_operator", "handover_operator", "OPERATOR")
        _add_user("u_material", "handover_material", "MATERIAL")
        _add_user("u_warehouse", "handover_warehouse", "WAREHOUSE_ADMIN")
        operator = _login(client, "handover_operator", "TestUser@2026")
        material = _login(client, "handover_material", "TestUser@2026")
        warehouse = _login(client, "handover_warehouse", "TestUser@2026")

        # 创建交接必须由物料员发起；操作员只有确认权。
        transfer_id = _approved_transfer(client, warehouse)
        work_item = "omr_demo_dev_demo_CC01_mat_ctl_cabinet"
        before = main.db().execute(
            "SELECT available_quantity,version FROM materials WHERE id='mat_ctl_cabinet'"
        ).fetchone()
        denied_create_op = str(uuid.uuid4())
        denied_create = client.post("/api/v1/handovers", json={
            "workItemId": work_item, "transferRequestId": transfer_id, "quantity": 1,
            "fromLocation": "A-01-03", "deviceId": "dev_demo_CC01",
            "receiverUserId": "u_operator", "clientOperationId": denied_create_op,
        }, headers=headers(operator, denied_create_op))
        assert denied_create.status_code == 403

        hid, create_payload = _create_handover(client, material, transfer_id, work_item)
        after_create = main.db().execute(
            "SELECT available_quantity,version FROM materials WHERE id='mat_ctl_cabinet'"
        ).fetchone()
        assert tuple(after_create) == tuple(before)
        assert main.db().execute(
            "SELECT status_code FROM material_work_item_projections WHERE work_item_id=?", (work_item,)
        ).fetchone()["status_code"] == "PENDING"
        material_confirm_op = str(uuid.uuid4())
        material_confirm = client.post(
            f"/api/v1/handovers/{hid}/confirm",
            json={"clientOperationId": material_confirm_op, "requestId": str(uuid.uuid4())},
            headers=headers(material, material_confirm_op),
        )
        assert material_confirm.status_code == 403

        decision_op = str(uuid.uuid4())
        decision = {"clientOperationId": decision_op, "requestId": str(uuid.uuid4())}
        first = client.post(f"/api/v1/handovers/{hid}/confirm", json=decision,
                            headers=headers(operator, decision_op))
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "CONFIRMED"
        assert first.json()["eventTypes"] == [
            "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]
        second = client.post(f"/api/v1/handovers/{hid}/confirm", json=decision,
                             headers=headers(operator, decision_op))
        assert second.status_code == 200
        assert second.json()["idempotent"] is True

        projection = main.db().execute(
            "SELECT * FROM material_work_item_projections WHERE work_item_id=?", (work_item,)
        ).fetchone()
        assert projection["status_code"] == "AT_STATION"
        assert projection["last_handover_id"] == hid
        assert projection["target_device_id"] == "dev_demo_CC01"
        after_confirm = main.db().execute(
            "SELECT available_quantity,version FROM materials WHERE id='mat_ctl_cabinet'"
        ).fetchone()
        assert tuple(after_confirm) == tuple(before)

        events = main.db().execute(
            "SELECT event_type,actor_user_id,actor_role,client_operation_id FROM audit_events WHERE entity_id=? ORDER BY id",
            (hid,),
        ).fetchall()
        assert [event["event_type"] for event in events] == [
            "HANDOVER_CREATED", "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]
        assert all(event["actor_user_id"] == "u_operator" for event in events[1:])
        assert all(event["actor_role"] == "OPERATOR" for event in events[1:])
        assert events[2]["client_operation_id"] == decision_op

        timeline = client.get(f"/api/v1/handovers/{hid}/timeline",
                              headers={"Authorization": f"Bearer {operator}"})
        assert timeline.status_code == 200
        assert [event["event_type"] for event in timeline.json()["items"]] == [
            "HANDOVER_CREATED", "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP", "MATERIAL_AT_STATION",
        ]
        assert timeline.json()["status"] == "CONFIRMED"
        assert timeline.json()["workspaceStatus"] == "AT_STATION"

        workspace = client.get(
            "/api/v1/workspace/material-items?status=AT_STATION",
            headers={"Authorization": f"Bearer {operator}"},
        )
        assert workspace.status_code == 200
        assert workspace.json()["items"][0]["statusCode"] == "AT_STATION"
        assert workspace.json()["items"][0]["requiredQuantity"] == 20
        # CC01 的订单需求库存事实仍由正式需求表返回（该演示行当前为缺货）；
        # 交接确认不能把领取数量写回需求数量。
        assert workspace.json()["items"][0]["inStockQuantity"] == 0


def test_handover_reject_cancel_roles_states_and_idempotency():
    with TestClient(main.app) as client:
        _add_user("u_operator2", "handover_operator2", "OPERATOR")
        _add_user("u_material2", "handover_material2", "MATERIAL")
        _add_user("u_warehouse2", "handover_warehouse2", "WAREHOUSE_ADMIN")
        operator = _login(client, "handover_operator2", "TestUser@2026")
        material = _login(client, "handover_material2", "TestUser@2026")
        warehouse = _login(client, "handover_warehouse2", "TestUser@2026")

        transfer_id = _approved_transfer(client, warehouse)
        work_item = "omr_demo_dev_demo_CC02_mat_ctl_cabinet"
        hid, _ = _create_handover(client, material, transfer_id, work_item,
                                   deviceId="dev_demo_CC02", receiverUserId=None)
        reject_op = str(uuid.uuid4())
        reject_payload = {"clientOperationId": reject_op, "requestId": str(uuid.uuid4()), "reason": "数量核验不通过"}
        material_reject_op = str(uuid.uuid4())
        assert client.post(
            f"/api/v1/handovers/{hid}/reject",
            json={"clientOperationId": material_reject_op, "requestId": str(uuid.uuid4()), "reason": "无驳回权限"},
            headers=headers(material, material_reject_op),
        ).status_code == 403
        rejected = client.post(f"/api/v1/handovers/{hid}/reject", json=reject_payload,
                               headers=headers(warehouse, reject_op))
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "REJECTED"
        repeated = client.post(f"/api/v1/handovers/{hid}/reject", json=reject_payload,
                               headers=headers(warehouse, reject_op))
        assert repeated.status_code == 200
        assert repeated.json()["idempotent"] is True
        events = main.db().execute(
            "SELECT event_type FROM audit_events WHERE entity_id=? ORDER BY id", (hid,)
        ).fetchall()
        assert [row["event_type"] for row in events] == ["HANDOVER_CREATED", "HANDOVER_REJECTED"]
        item = client.get("/api/v1/workspace/material-items?status=REJECTED",
                          headers={"Authorization": f"Bearer {warehouse}"})
        assert item.status_code == 200
        assert any(entry["id"] == work_item for entry in item.json()["items"])
        invalid_confirm_op = str(uuid.uuid4())
        assert client.post(f"/api/v1/handovers/{hid}/confirm",
                           json={"clientOperationId": invalid_confirm_op, "requestId": str(uuid.uuid4())},
                           headers=headers(operator, invalid_confirm_op)).status_code == 403
        terminal_confirm_op = str(uuid.uuid4())
        assert client.post(f"/api/v1/handovers/{hid}/confirm",
                           json={"clientOperationId": terminal_confirm_op, "requestId": str(uuid.uuid4())},
                           headers=headers(warehouse, terminal_confirm_op)).status_code == 409

        transfer_id = _approved_transfer(client, warehouse)
        cancel_item = "omr_demo_dev_demo_CC03_mat_ctl_cabinet"
        cancel_hid, _ = _create_handover(client, material, transfer_id, cancel_item,
                                         deviceId="dev_demo_CC03", receiverUserId=None)
        operator_cancel_op = str(uuid.uuid4())
        assert client.post(
            f"/api/v1/handovers/{cancel_hid}/cancel",
            json={"clientOperationId": operator_cancel_op, "requestId": str(uuid.uuid4()), "reason": "无取消权限"},
            headers=headers(operator, operator_cancel_op),
        ).status_code == 403
        cancel_op = str(uuid.uuid4())
        cancel_payload = {"clientOperationId": cancel_op, "requestId": str(uuid.uuid4()), "reason": "现场取消"}
        cancelled = client.post(f"/api/v1/handovers/{cancel_hid}/cancel", json=cancel_payload,
                                headers=headers(material, cancel_op))
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"
        assert client.post(f"/api/v1/handovers/{cancel_hid}/cancel", json=cancel_payload,
                           headers=headers(material, cancel_op)).json()["idempotent"] is True
        cancel_events = main.db().execute(
            "SELECT event_type FROM audit_events WHERE entity_id=? ORDER BY id", (cancel_hid,)
        ).fetchall()
        assert [row["event_type"] for row in cancel_events] == ["HANDOVER_CREATED", "HANDOVER_CANCELLED"]
        cancel_timeline = client.get(
            f"/api/v1/handovers/{cancel_hid}/timeline",
            headers={"Authorization": f"Bearer {warehouse}"},
        )
        assert cancel_timeline.status_code == 200
        assert cancel_timeline.json()["status"] == "CANCELLED"
        assert cancel_timeline.json()["workspaceStatus"] == "CANCELLED"
        summary = client.get("/api/v1/workspace/summary",
                             headers={"Authorization": f"Bearer {warehouse}"})
        assert summary.status_code == 200
        assert summary.json()["handoverStatusCounts"]["REJECTED"] >= 1
        assert summary.json()["handoverStatusCounts"]["CANCELLED"] >= 1
        assert summary.json()["statusCounts"]["REJECTED"] >= 1
        assert summary.json()["statusCounts"]["CANCELLED"] >= 1
        assert summary.json()["rejectedCount"] >= 1
        assert summary.json()["cancelledCount"] >= 1
        terminal_cancel_op = str(uuid.uuid4())
        assert client.post(f"/api/v1/handovers/{cancel_hid}/cancel",
                           json={"clientOperationId": terminal_cancel_op, "requestId": str(uuid.uuid4()), "reason": "再次取消"},
                           headers=headers(material, terminal_cancel_op)).status_code == 409


def test_handover_requires_approved_related_transfer_and_target_device():
    with TestClient(main.app) as client:
        _add_user("u_material3", "handover_material3", "MATERIAL")
        login = client.post("/api/v1/auth/login", json={
            "username": "owlco", "password": "TestAdmin@2026", "deviceId": "test",
        })
        assert login.status_code == 200
        token = login.json()["accessToken"]
        material_token = _login(client, "handover_material3", "TestUser@2026")
        pending_op = str(uuid.uuid4())
        pending_transfer = client.post("/api/v1/transfer-requests", json={
            "clientOperationId": pending_op, "type": "OUTBOUND",
            "documentNo": "26B-013",
            "items": [{"materialId": "mat_ctl_cabinet", "quantity": 1,
                       "expectedInventoryVersion": 1}],
        }, headers=headers(token, pending_op))
        assert pending_transfer.status_code == 200
        pending_handover_op = str(uuid.uuid4())
        pending_handover = client.post("/api/v1/handovers", json={
            "workItemId": "omr_demo_dev_demo_HZ04_mat_ctl_cabinet",
            "transferRequestId": pending_transfer.json()["requestId"],
            "quantity": 1, "fromLocation": "A-01-03",
            "deviceId": "dev_demo_HZ04", "clientOperationId": pending_handover_op,
        }, headers=headers(material_token, pending_handover_op))
        assert pending_handover.status_code == 409
        assert pending_handover.json()["error"]["code"] == "TRANSFER_STATE_CONFLICT"

        op = str(uuid.uuid4())
        transfer = client.post("/api/v1/transfer-requests", json={
            "clientOperationId": op, "type": "OUTBOUND", "items": [{
                "materialId": "mat_ctl_cabinet", "quantity": 1,
                "expectedInventoryVersion": 1,
            }], "documentNo": "26B-013",
        }, headers=headers(token, op))
        assert transfer.status_code == 200
        rid = transfer.json()["requestId"]
        approved = client.post(f"/api/v1/transfer-requests/{rid}/approve",
                               json={"decision": "APPROVE"},
                               headers={**headers(token), "X-Request-Id": str(uuid.uuid4())})
        assert approved.status_code == 200

        work_item = "omr_demo_dev_demo_HZ04_mat_ctl_cabinet"
        create_op = str(uuid.uuid4())
        handover = client.post("/api/v1/handovers", json={
            "workItemId": work_item, "transferRequestId": rid, "quantity": 1,
            "fromLocation": "A-01-03", "deviceId": "dev_demo_HZ04",
            "clientOperationId": create_op,
        }, headers=headers(material_token, create_op))
        assert handover.status_code == 200, handover.text
        hid = handover.json()["handoverId"]

        mismatch_op = str(uuid.uuid4())
        mismatch = client.post("/api/v1/handovers", json={
            "workItemId": work_item, "transferRequestId": rid, "quantity": 1,
            "fromLocation": "A-01-03", "deviceId": "dev_demo_HZ99",
            "clientOperationId": mismatch_op,
        }, headers=headers(material_token, mismatch_op))
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["code"] == "VALIDATION_ERROR"

        aggregate_op = str(uuid.uuid4())
        aggregate = client.post("/api/v1/handovers", json={
            "workItemId": "omr_demo_dev_demo_HZ05_mat_ctl_cabinet",
            "transferRequestId": rid, "quantity": 1,
            "fromLocation": "A-01-03", "deviceId": "dev_demo_HZ05",
            "clientOperationId": aggregate_op,
        }, headers=headers(material_token, aggregate_op))
        assert aggregate.status_code == 409
        assert aggregate.json()["error"]["code"] == "TRANSFER_STATE_CONFLICT"

        decision_op = str(uuid.uuid4())
        decision = {"clientOperationId": decision_op, "requestId": str(uuid.uuid4())}
        first = client.post(f"/api/v1/handovers/{hid}/confirm", json=decision,
                            headers=headers(token, decision_op))
        second = client.post(f"/api/v1/handovers/{hid}/confirm", json=decision,
                             headers=headers(token, decision_op))
        assert first.status_code == second.status_code == 200
        assert second.json()["idempotent"] is True
        assert second.json()["status"] == "CONFIRMED"

        changed = {**decision, "reason": "different payload"}
        mismatch = client.post(f"/api/v1/handovers/{hid}/confirm", json=changed,
                               headers=headers(token, decision_op))
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

        timeline = client.get(f"/api/v1/handovers/{work_item}/timeline",
                              headers={"Authorization": f"Bearer {token}"})
        assert timeline.status_code == 200
        assert timeline.json()["handoverId"] == hid
        assert timeline.json()["workItemId"] == work_item

        audit_count = main.db().execute(
            "SELECT COUNT(*) FROM audit_events WHERE entity_id=?", (hid,)
        ).fetchone()[0]
        assert audit_count == 4
