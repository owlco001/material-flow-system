"""0.14.0 物料勾选批量操作：退料（RETURN）/ 厂内调拨（TRANSFER）后端契约 + 管理台批量接口。"""
from __future__ import annotations

import os
import re
import tempfile
import uuid

from fastapi.testclient import TestClient

from app import main as backend

ROOT = tempfile.mkdtemp(prefix="mf_batch_ops_")
os.environ.update(MATERIAL_FLOW_DATA=f"{ROOT}/data", MATERIAL_FLOW_UPLOADS=f"{ROOT}/uploads", INITIAL_ADMIN_PASSWORD="Admin@2026")

PWD = "Worker@2026"


def add_user(uid, role):
    c = backend.db()
    c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
              (uid, uid, uid, role, backend.hash_password(PWD), 0, 1, backend.now()))
    c.commit(); c.close()


def login(client, name, password=PWD):
    r = client.post("/api/v1/auth/login", json={"username": name, "password": password, "deviceId": name})
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


def form_csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post("/admin/login", data={"username": username, "password": password,
                                             "csrf_token": form_csrf(page.text)}, follow_redirects=False)


def seed():
    for uid, role in (("op-b", "OPERATOR"), ("wh-b", "WAREHOUSE_ADMIN"), ("asm-b", "ASSEMBLER"),
                      ("sup-b", "WORKSHOP_SUPERVISOR"), ("plan-b", "PLANNER")):
        add_user(uid, role)
    c = backend.db(); now = backend.now()
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?)", ("ord-b", "BT-1", "产品B", "IN_PROGRESS", now, now))
    c.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?,?)", ("dev-b1", "BT-1-M1", "机台B1", "一车间", None, None, "ACTIVE", "u_admin", now, now))
    c.execute("INSERT INTO assembly_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("task-b1", "BT-1", "dev-b1", "BT-1-M1", "asm-b", "IN_PROGRESS", 1, 1, None, None, now, now))
    c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)", ("od-b1", "ord-b", "机台", "BT-1-M1", 1, now))
    for mid, code, qty in (("mat-b1", "MTR-B1", 100), ("mat-b2", "MTR-B2", 40)):
        c.execute("INSERT INTO materials(id, code, name, specification, unit, batch_no, expiry_date, total_quantity, available_quantity, version, category) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (mid, code, "物料" + code, None, "件", None, None, qty, qty, 1, None))
    c.execute("INSERT INTO order_material_requirements VALUES(?,?,?,?,?,?,?,?,?,?)", ("req-b1", "ord-b", "od-b1", "mat-b1", 10, 10, 10, "IN_STOCK", now, now))
    c.execute("INSERT INTO order_material_requirements VALUES(?,?,?,?,?,?,?,?,?,?)", ("req-b2", "ord-b", "od-b1", "mat-b2", 5, 5, 5, "IN_STOCK", now, now))
    for lid, code in (("loc-a", "LOC-A"), ("loc-b", "LOC-B")):
        c.execute("INSERT INTO locations VALUES(?,?,?)", (lid, code, code))
    c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("inv-b1a", "mat-b1", "loc-a", 100))
    c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("inv-b2a", "mat-b2", "loc-a", 40))
    c.commit(); c.close()


def write_headers(token, op):
    return {"Authorization": f"Bearer {token}", "X-Request-Id": str(uuid.uuid4()), "Idempotency-Key": op}


def create(client, token, type_, item, document_no="BT-1"):
    op = str(uuid.uuid4())
    return client.post("/api/v1/transfer-requests", headers=write_headers(token, op), json={
        "clientOperationId": op, "type": type_, "documentNo": document_no, "items": [item], "remark": "批量测试"})


def approve_and_execute(client, admin, wh, rid):
    op = str(uuid.uuid4())
    r = client.post(f"/api/v1/transfer-requests/{rid}/approve", headers=write_headers(admin, op),
                    json={"decision": "APPROVE", "comment": "", "clientOperationId": op})
    assert r.status_code == 200, r.text
    op = str(uuid.uuid4())
    r = client.post(f"/api/v1/transfer-requests/{rid}/execute", headers=write_headers(wh, op),
                    json={"clientOperationId": op})
    assert r.status_code == 200, r.text
    return r.json()


def material(mid):
    c = backend.db()
    m = c.execute("SELECT total_quantity, available_quantity, version FROM materials WHERE id=?", (mid,)).fetchone()
    locs = {r["code"]: r["quantity"] for r in c.execute(
        "SELECT l.code, i.quantity FROM inventory i JOIN locations l ON l.id=i.location_id WHERE i.material_id=?", (mid,))}
    c.close()
    return dict(m), locs


def test_return_type_follows_inbound_approval_rules():
    seed()
    with TestClient(backend.app) as client:
        admin = login(client, "owlco", "Admin@2026"); op = login(client, "op-b")
        wh = login(client, "wh-b"); asm = login(client, "asm-b")
        item = {"materialId": "mat-b1", "quantity": 3, "targetLocationCode": "LOC-B", "expectedInventoryVersion": 1}
        assert create(client, wh, "RETURN", item).status_code == 403
        assert create(client, asm, "RETURN", item).status_code == 403
        r = create(client, op, "RETURN", item)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PENDING_APPROVAL"
        rid = r.json()["requestId"]
        # 审批前库存不变
        assert material("mat-b1")[0]["available_quantity"] == 100
        assert approve_and_execute(client, admin, wh, rid)["status"] == "EXECUTED"
        summary, locs = material("mat-b1")
        assert summary["total_quantity"] == 103 and summary["available_quantity"] == 103
        assert locs == {"LOC-A": 100, "LOC-B": 3}
        listed = client.get("/api/v1/transfer-requests", headers={"Authorization": f"Bearer {admin}"}).json()
        assert any(i.get("type") == "RETURN" for i in listed["items"])
        # 列表不带明细但给条数，App 卡片「明细 N 条」用
        ret = next(i for i in listed["items"] if i["id"] == rid)
        assert ret["itemCount"] == 1 and "items" not in ret


def test_relocation_moves_between_locations_without_changing_totals():
    seed()
    with TestClient(backend.app) as client:
        admin = login(client, "owlco", "Admin@2026"); op = login(client, "op-b")
        wh = login(client, "wh-b"); asm = login(client, "asm-b")
        base = {"materialId": "mat-b1", "quantity": 30, "expectedInventoryVersion": 1}
        missing = create(client, op, "TRANSFER", {**base, "targetLocationCode": "LOC-B"})
        assert missing.status_code == 400 and "来源库位" in missing.json()["error"]["message"]
        same = create(client, op, "TRANSFER", {**base, "sourceLocationCode": "LOC-A", "targetLocationCode": "LOC-A"})
        assert same.status_code == 400
        good = {**base, "sourceLocationCode": "LOC-A", "targetLocationCode": "LOC-B"}
        assert create(client, asm, "TRANSFER", good).status_code == 403
        r = create(client, wh, "TRANSFER", good)
        assert r.status_code == 200, r.text
        # 仓库管理员发起 → 管理员审批 → 另一仓库/管理角色执行（执行人不能是审批人）
        op2 = str(uuid.uuid4())
        assert client.post(f"/api/v1/transfer-requests/{r.json()['requestId']}/approve", headers=write_headers(admin, op2),
                           json={"decision": "APPROVE", "comment": "", "clientOperationId": op2}).status_code == 200
        op3 = str(uuid.uuid4())
        ex = client.post(f"/api/v1/transfer-requests/{r.json()['requestId']}/execute", headers=write_headers(wh, op3),
                         json={"clientOperationId": op3})
        assert ex.status_code == 200, ex.text
        summary, locs = material("mat-b1")
        assert summary["total_quantity"] == 100 and summary["available_quantity"] == 100
        assert locs == {"LOC-A": 70, "LOC-B": 30}


def test_task_detail_exposes_ids_for_batch():
    seed()
    with TestClient(backend.app) as client:
        admin = login(client, "owlco", "Admin@2026")
        d = client.get("/api/v1/orders/BT-1/tasks/task-b1", headers={"Authorization": f"Bearer {admin}"}).json()
        first = d["materials"][0]
        assert first["requirementId"] == "req-b1" and first["materialId"] == "mat-b1" and first["deviceId"] == "od-b1"


def _batch(client, csrf, action, ids, **extra):
    data = {"csrf_token": csrf, "action": action, "requirementId": list(ids), **extra}
    return client.post("/admin/orders/BT-1/materials/batch", data=data)


def test_admin_batch_outbound_partial_failure_and_audit():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/orders?order_no=BT-1")
        assert page.status_code == 200
        assert 'class="mb-row"' in page.text and 'value="req-b1"' in page.text
        for label in ("出库申请", "退料", "厂内调拨", "异常提报"):
            assert label in page.text
        assert "material-batch.js" in page.text
        csrf = form_csrf(page.text)
        r = _batch(client, csrf, "OUTBOUND", ["req-b1", "req-b2", "req-nope"], **{"qty_req-b2": "2", "remark": "急用"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and len(body["succeeded"]) == 2 and len(body["failed"]) == 1
        assert body["failed"][0]["requirementId"] == "req-nope"
        assert "失败 1 条" in body["message"]
        c = backend.db()
        rows = c.execute("SELECT type, document_no, payload_json, created_by FROM transfer_requests WHERE document_no='BT-1' ORDER BY created_at").fetchall()
        audit_row = c.execute("SELECT result FROM audit_logs WHERE action='BATCH_OUTBOUND' AND resource_id='BT-1'").fetchone()
        c.close()
        assert {r["type"] for r in rows} == {"OUTBOUND"} and len(rows) == 2
        qtys = sorted(backend.json.loads(r["payload_json"])["items"][0]["quantity"] for r in rows)
        assert qtys == [2, 10]
        assert all("机台：BT-1-M1" in backend.json.loads(r["payload_json"])["remark"] for r in rows)
        assert audit_row["result"] == "PARTIAL"


def test_admin_batch_transfer_return_and_validation():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = form_csrf(client.get("/admin/orders/BT-1/tasks/task-b1").text)
        assert _batch(client, csrf, "TRANSFER", ["req-b1"]).status_code == 400
        assert _batch(client, csrf, "TRANSFER", ["req-b1"], targetLocation="NOPE").status_code == 400
        ok = _batch(client, csrf, "TRANSFER", ["req-b1", "req-b2"], targetLocation="LOC-B")
        assert ok.status_code == 200 and len(ok.json()["succeeded"]) == 2, ok.text
        ret = _batch(client, csrf, "RETURN", ["req-b1"], **{"qty_req-b1": "4"})
        assert ret.status_code == 200 and len(ret.json()["succeeded"]) == 1, ret.text
        bad_qty = _batch(client, csrf, "RETURN", ["req-b1"], **{"qty_req-b1": "-1"})
        assert bad_qty.status_code == 400
        assert _batch(client, "wrong", "OUTBOUND", ["req-b1"]).status_code == 403
        assert _batch(client, csrf, "BOGUS", ["req-b1"]).status_code == 400
        assert _batch(client, csrf, "OUTBOUND", []).status_code == 400
        c = backend.db()
        payloads = [backend.json.loads(r["payload_json"]) for r in c.execute(
            "SELECT payload_json FROM transfer_requests WHERE type='TRANSFER'")]
        c.close()
        assert all(p["items"][0]["sourceLocationCode"] == "LOC-A" and p["items"][0]["targetLocationCode"] == "LOC-B" for p in payloads)


def test_admin_batch_rbac_supervisor_exception_only():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "sup-b", PWD).status_code == 303
        page = client.get("/admin/orders?order_no=BT-1")
        assert page.status_code == 200
        assert 'data-action="EXCEPTION"' in page.text and 'data-action="OUTBOUND"' not in page.text
        csrf = form_csrf(page.text)
        assert _batch(client, csrf, "OUTBOUND", ["req-b1"]).status_code == 403
        assert _batch(client, csrf, "EXCEPTION", ["req-b1"], exceptionType="DAMAGE").status_code == 400  # 描述必填
        r = _batch(client, csrf, "EXCEPTION", ["req-b1", "req-b2"], exceptionType="DAMAGE",
                   remark="外箱破损", **{"qty_req-b1": "8"})
        assert r.status_code == 200 and len(r.json()["succeeded"]) == 2, r.text
        c = backend.db()
        ex = c.execute("SELECT material_id, book_quantity, actual_quantity, difference, order_no, device_id, created_by FROM exceptions WHERE material_id='mat-b1'").fetchone()
        audit_row = c.execute("SELECT result FROM audit_logs WHERE action='BATCH_EXCEPTION'").fetchone()
        c.close()
        assert (ex["book_quantity"], ex["actual_quantity"], ex["difference"]) == (10, 8, -2)
        assert ex["order_no"] == "BT-1" and ex["device_id"] == "od-b1" and ex["created_by"] == "sup-b"
        assert audit_row["result"] == "SUCCESS"
    with TestClient(backend.app) as client:
        assert web_login(client, "plan-b", PWD).status_code == 303
        page = client.get("/admin/orders?order_no=BT-1")
        assert page.status_code == 200 and "mb-bar" not in page.text
    with TestClient(backend.app) as anon:
        assert anon.post("/admin/orders/BT-1/materials/batch", data={"action": "OUTBOUND"}).status_code == 401


def test_admin_batch_retry_same_batch_id_is_idempotent_and_zero_qty_rejected():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        csrf = form_csrf(client.get("/admin/orders?order_no=BT-1").text)
        bid = "0b8f2c1e-6d1a-4c7e-9a55-1f2d3c4b5a69"
        r1 = _batch(client, csrf, "OUTBOUND", ["req-b1"], batchId=bid)
        r2 = _batch(client, csrf, "OUTBOUND", ["req-b1"], batchId=bid)
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["succeeded"][0]["id"] == r2.json()["succeeded"][0]["id"]
        c = backend.db()
        n = c.execute("SELECT COUNT(*) FROM transfer_requests WHERE document_no='BT-1' AND type='OUTBOUND'").fetchone()[0]
        c.close()
        assert n == 1
        # 显式填 0 不再被悄悄换成需求数
        z = _batch(client, csrf, "OUTBOUND", ["req-b2"], **{"qty_req-b2": "0"})
        body = z.json()
        assert not body["succeeded"] and "正整数" in body["failed"][0]["error"]
