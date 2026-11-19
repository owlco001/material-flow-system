"""Transfer execution must use one optimistic-lock snapshot per material."""
from __future__ import annotations

import json
import uuid

import pytest

from app import main as backend


@pytest.fixture
def stock():
    c = backend.db()
    for mid in ("exec-m1", "exec-m2"):
        c.execute(
            "INSERT INTO materials(id,code,name,unit,total_quantity,available_quantity,version) "
            "VALUES(?,?,?,?,?,?,?)", (mid, mid, mid, "件", 100, 100, 1),
        )
    for code in ("EXEC-A", "EXEC-B"):
        c.execute("INSERT INTO locations VALUES(?,?,?)", (code, code, code))
    c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("exec-i1", "exec-m1", "EXEC-A", 60))
    c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("exec-i2", "exec-m1", "EXEC-B", 40))
    c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("exec-i3", "exec-m2", "EXEC-A", 100))
    c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
              ("exec-user", "exec-user", "测试执行人", "ADMIN", "unused-test-hash", 0, 1, backend.now()))
    user = c.execute("SELECT * FROM users WHERE id='exec-user'").fetchone()
    c.commit()
    c.close()
    return user


def item(quantity, *, material="exec-m1", version=1, source="EXEC-A", target=None):
    return {"materialId": material, "quantity": quantity, "expectedInventoryVersion": version,
            "sourceLocationCode": source, "targetLocationCode": target, "batchNo": "lot"}


def approved(kind, items):
    rid = "exec-" + uuid.uuid4().hex
    c = backend.db()
    c.execute(
        "INSERT INTO transfer_requests "
        "(id,client_operation_id,type,status,payload_json,created_by,created_at,approved_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (rid, rid, kind, "APPROVED", json.dumps({"type": kind, "items": items}),
         "u_admin", backend.now(), None),
    )
    c.commit()
    c.close()
    return rid


def snapshot(rid):
    c = backend.db()
    result = {
        "materials": [tuple(r) for r in c.execute(
            "SELECT id,total_quantity,available_quantity,version FROM materials "
            "WHERE id LIKE 'exec-%' ORDER BY id")],
        "locations": [tuple(r) for r in c.execute(
            "SELECT material_id,location_id,quantity FROM inventory "
            "WHERE material_id LIKE 'exec-%' ORDER BY material_id,location_id")],
        "status": c.execute("SELECT status FROM transfer_requests WHERE id=?", (rid,)).fetchone()[0],
        "audits": c.execute("SELECT COUNT(*) FROM audit_logs WHERE resource_id=?", (rid,)).fetchone()[0],
    }
    c.close()
    return result


@pytest.mark.parametrize("kind,items,total,locations", [
    ("OUTBOUND", [item(5), item(6, source="EXEC-B")], 89, [55, 34]),
    ("INBOUND", [item(5, target="EXEC-A"), item(6, target="EXEC-B")], 111, [65, 46]),
    ("RETURN", [item(5, target="EXEC-A"), item(6, target="EXEC-B")], 111, [65, 46]),
    ("TRANSFER", [item(5, target="EXEC-B"), item(6, source="EXEC-B", target="EXEC-A")], 100, [61, 39]),
])
def test_duplicate_material_rows_share_snapshot_and_update_version_once(stock, kind, items, total, locations):
    rid = approved(kind, items)
    result = backend._execute_transfer_items(rid, stock, "exec-test")
    assert result["status"] == "EXECUTED"
    current = snapshot(rid)
    assert current["materials"][0] == ("exec-m1", total, total, 2)
    assert [r[2] for r in current["locations"][:2]] == locations
    assert current["audits"] == 1


@pytest.mark.parametrize("items,code", [
    ([item(50), item(51, source="EXEC-B")], backend.CODE_INSUFFICIENT_INVENTORY),
    ([item(50), item(20)], backend.CODE_INSUFFICIENT_INVENTORY),
    ([item(5), item(6, version=2)], backend.CODE_INVENTORY_VERSION_CONFLICT),
    ([item(5, version=2)], backend.CODE_INVENTORY_VERSION_CONFLICT),
    ([item(5), item(6, material="missing")], backend.CODE_VALIDATION_ERROR),
])
def test_conflicts_and_cumulative_shortages_rollback_entire_request(stock, items, code):
    rid = approved("OUTBOUND", items)
    before = snapshot(rid)
    with pytest.raises(backend.ApiError) as failure:
        backend._execute_transfer_items(rid, stock, "exec-test")
    assert failure.value.code == code
    assert snapshot(rid) == before


@pytest.mark.parametrize("kind", ["STOCKTAKE", "UNRECOGNIZED"])
def test_generic_execution_rejects_non_movement_types_without_mutation(stock, kind):
    rid = approved(kind, [item(10)])
    before = snapshot(rid)
    with pytest.raises(backend.ApiError) as failure:
        backend._execute_transfer_items(rid, stock, "exec-test")
    assert failure.value.code == backend.CODE_VALIDATION_ERROR
    assert snapshot(rid) == before


def test_multiple_materials_and_idempotent_retry_do_not_double_apply(stock):
    rid = approved("OUTBOUND", [item(5), item(6, material="exec-m2")])
    operation = str(uuid.uuid4())
    payload = json.dumps({"clientOperationId": operation})
    backend._execute_transfer_items(rid, stock, "exec-first", operation, payload)
    before = snapshot(rid)
    result = backend._execute_transfer_items(rid, stock, "exec-retry", operation, payload)
    assert result["idempotent"] is True
    assert snapshot(rid) == before
    assert before["materials"] == [("exec-m1", 95, 95, 2), ("exec-m2", 94, 94, 2)]


def test_approver_cannot_execute_own_approval(stock):
    rid = approved("OUTBOUND", [item(5)])
    c = backend.db()
    c.execute("UPDATE transfer_requests SET approved_by=? WHERE id=?", (stock["id"], rid))
    c.commit()
    c.close()
    before = snapshot(rid)
    with pytest.raises(backend.ApiError) as failure:
        backend._execute_transfer_items(rid, stock, "exec-test")
    assert failure.value.code == backend.CODE_APPROVAL_EXECUTOR_SAME_USER
    assert snapshot(rid) == before
