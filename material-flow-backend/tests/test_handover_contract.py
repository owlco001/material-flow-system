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


def test_handover_scope_and_decision_idempotency():
    with TestClient(main.app) as client:
        login = client.post("/api/v1/auth/login", json={
            "username": "owlco", "password": "TestAdmin@2026", "deviceId": "test",
        })
        assert login.status_code == 200
        token = login.json()["accessToken"]
        op = str(uuid.uuid4())
        transfer = client.post("/api/v1/transfer-requests", json={
            "clientOperationId": op, "type": "OUTBOUND", "items": [{
                "materialId": "mat_ctl_cabinet", "quantity": 1,
                "expectedInventoryVersion": 1,
            }],
        }, headers=headers(token, op))
        assert transfer.status_code == 200
        rid = transfer.json()["requestId"]
        approved = client.post(f"/api/v1/transfer-requests/{rid}/approve",
                               json={"decision": "APPROVE"},
                               headers={**headers(token), "X-Request-Id": str(uuid.uuid4())})
        assert approved.status_code == 200

        work_item = "omr_demo_dev_demo_CC01_mat_ctl_cabinet"
        create_op = str(uuid.uuid4())
        handover = client.post("/api/v1/handovers", json={
            "workItemId": work_item, "transferRequestId": rid, "quantity": 1,
            "fromLocation": "A-01-03", "clientOperationId": create_op,
        }, headers=headers(token, create_op))
        assert handover.status_code == 200, handover.text
        hid = handover.json()["handoverId"]

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
        assert audit_count == 2
