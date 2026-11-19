"""Filtered workspaces must only decorate the requested page, preserving fact semantics."""
from __future__ import annotations

import pytest
import uuid

from app import main as backend

ORDER = "AUDIT-WORKSPACE"


@pytest.fixture
def workspace_sample(monkeypatch):
    monkeypatch.setattr(backend, "ENABLE_ADMIN_ROLE_PREVIEW", True)
    c = backend.db()
    ts = "2000-01-01T00:00:00+00:00"
    for uid, role in (("audit-admin", "ADMIN"), ("audit-operator", "OPERATOR")):
        c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (uid, uid, uid, role, "unused", 0, 1, ts))
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?)", (ORDER, ORDER, "Synthetic", "IN_PROGRESS", ts, ts))
    c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)", ("audit-device", ORDER, "TYPE", "AUDIT-DEVICE", 1, ts))
    for i in range(121):
        material = f"audit-material-{i:03}"
        requirement = f"audit-requirement-{i:03}"
        c.execute("INSERT INTO materials(id,code,name,unit,total_quantity,available_quantity) VALUES(?,?,?,?,?,?)", (material, material, material, "EA", 10, 10))
        c.execute("INSERT INTO order_material_requirements VALUES(?,?,?,?,?,?,?,?,?,?)", (
            requirement, ORDER, "audit-device", material, 1, 1 if i % 3 == 0 else 0,
            1 if i % 3 == 0 else 0, "IN_STOCK" if i % 3 == 0 else "OUT_OF_STOCK", ts, ts))
        if i % 2 == 0:
            c.execute("INSERT INTO material_work_items VALUES(?,?,?,?,?,?)", (
                f"audit-work-{i:03}", requirement, material, "audit-device", "audit-operator", 1))
    c.execute("INSERT INTO transfer_requests(id,client_operation_id,type,status,payload_json,created_by,created_at) VALUES(?,?,?,?,?,?,?)", (
        "audit-transfer", "audit-transfer-op", "OUTBOUND", "EXECUTED", '{"items":[]}', "audit-admin", ts))
    for i, status, device in ((0, "PENDING", None), (1, "CONFIRMED", "audit-device"), (2, "CONFIRMED", None), (3, "REJECTED", None)):
        work = f"audit-work-{i:03}" if i % 2 == 0 else f"audit-requirement-{i:03}"
        c.execute("INSERT INTO material_handovers(id,work_item_id,transfer_request_id,quantity,from_location,device_id,client_operation_id,status,created_by,created_at,confirmed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            f"audit-handover-{i}", work, "audit-transfer", 1, "A", device, f"audit-handover-op-{i}", status, "audit-admin", ts, ts))
    # A matching projection overrides computed state while preserving the confirmed handover facts.
    c.execute("INSERT INTO material_work_item_projections VALUES(?,?,?,?,?,?,?)", (
        "audit-requirement-001", "PICKED_UP", "audit-operator", "audit-handover-1", "audit-device", ts, ts))
    # Stale projection is ignored if it does not reference the latest handover.
    c.execute("INSERT INTO material_work_item_projections VALUES(?,?,?,?,?,?,?)", (
        "audit-work-002", "AT_STATION", "audit-operator", "not-the-latest", None, ts, ts))
    user = c.execute("SELECT * FROM users WHERE id='audit-admin'").fetchone()
    c.commit()
    c.close()
    return user


def listed(user, status, page=1, view_role=None):
    return backend.workspace_material_items(viewRole=view_role, status=status, orderNo=ORDER,
        page=page, pageSize=20, user=user, x_request_id=str(uuid.uuid4()) if view_role else None,
        x_client_operation_id=str(uuid.uuid4()) if view_role else None)


@pytest.mark.parametrize("status", ["IN_STOCK", "OUT_OF_STOCK", "PENDING", " in_stock ", "PICKED_UP", "REJECTED", "CANCELLED"])
@pytest.mark.parametrize("view_role", [None, "OPERATOR"])
def test_filtered_pages_match_full_fact_projection_without_decorating_all(workspace_sample, monkeypatch, status, view_role):
    c = backend.db()
    try:
        expected = backend._workspace_filter_status(backend._workspace_items(c, workspace_sample, ORDER, view_role), status)
    finally:
        c.close()
    builds = []
    original = backend._workspace_item
    def counted(row, *args, **kwargs):
        builds.append(row["requirement_id"])
        return original(row, *args, **kwargs)
    monkeypatch.setattr(backend, "_workspace_item", counted)
    for page in (1, 2, 9):
        builds.clear()
        response = listed(workspace_sample, status, page, view_role)
        assert response["total"] == len(expected)
        assert response["items"] == expected[(page - 1) * 20:page * 20]
        assert len(builds) == len(response["items"]), "Only visible page rows may be fully decorated"


def test_invalid_status_is_rejected_before_full_item_work(workspace_sample, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid status must not construct full workspace items")
    monkeypatch.setattr(backend, "_workspace_item", forbidden)
    with pytest.raises(backend.ApiError) as failure:
        listed(workspace_sample, "not-a-status")
    assert failure.value.code == backend.CODE_VALIDATION_ERROR


def test_admin_page_and_global_summary_share_one_read_snapshot(workspace_sample, monkeypatch):
    from app import admin_web as admin
    c = backend.db()
    try:
        expected_counts, _ = backend._workspace_light_counts(c, workspace_sample)
    finally:
        c.close()
    original = admin._workspace_light_counts
    def concurrent_change_then_count(connection, user):
        writer = backend.db()
        try:
            writer.execute("UPDATE order_material_requirements SET status_code='OUT_OF_STOCK' WHERE order_id=?", (ORDER,))
            writer.commit()
        finally:
            writer.close()
        return original(connection, user)
    monkeypatch.setattr(admin, "_workspace_light_counts", concurrent_change_then_count)
    summary, items, total = admin._workspace_snapshot(workspace_sample, ORDER, "IN_STOCK", limit=20)
    assert items and total > 20
    assert summary["statusCounts"] == expected_counts
