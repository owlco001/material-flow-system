"""S3 流转审批与交接留痕契约测试（docs/admin-web-contract.md §8.2）。"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "管理员", "ADMIN", backend.hash_password("Admin@2026"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_wh", "wh-admin", "仓库管理员", "WAREHOUSE_ADMIN", backend.hash_password("Wh@2026x"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_op", "operator1", "操作员", "OPERATOR", backend.hash_password("User@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_transfer(rid="tr_1", status="PENDING_APPROVAL", created_by="u_op", op="op-tr-1"):
    c = backend.db()
    c.execute(
        "INSERT INTO transfer_requests(id,client_operation_id,type,status,payload_json,"
        "created_by,created_at) VALUES(?,?,?,?,?,?,?)",
        (rid, op, "OUTBOUND", status, '{"items": []}', created_by, backend.now()),
    )
    c.commit()
    c.close()


def seed_handover_and_event():
    c = backend.db()
    c.execute(
        "INSERT INTO material_handovers(id,work_item_id,transfer_request_id,quantity,"
        "from_location,client_operation_id,status,created_by,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        ("ho_1", "wi_1", "tr_1", 3, "A-01", "op-ho-1", "PENDING", "u_op", backend.now()),
    )
    c.execute(
        "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
        "request_id,client_operation_id,before_json,after_json,server_time,result)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("OUTBOUND_APPROVED", "TRANSFER_REQUEST", "tr_1", "u_wh", "WAREHOUSE_ADMIN",
         "req-1", "op-ev-1", "{}", "{}", backend.now(), "SUCCESS"),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_users()
    yield


def form_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "表单必须携带 csrf_token"
    return match.group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def test_flows_list_manager_ok_operator_forbidden():
    seed_transfer()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        assert client.get("/admin/flows").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "operator1", "User@2026").status_code == 303
        assert client2.get("/admin/flows").status_code == 403


def test_flows_list_shows_pending_with_label():
    seed_transfer()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/flows?status=PENDING_APPROVAL")
        assert r.status_code == 200
        assert "tr_1" in r.text and "待审批" in r.text and "OUTBOUND" in r.text


def test_flow_detail_shows_payload_and_approve_form_only_when_pending():
    seed_transfer()
    seed_transfer("tr_2", status="APPROVED", op="op-tr-2")
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        pending = client.get("/admin/flows/tr_1")
        assert pending.status_code == 200
        assert "审批" in pending.text and 'name="decision"' in pending.text
        approved = client.get("/admin/flows/tr_2")
        assert approved.status_code == 200
        assert 'name="decision"' not in approved.text


def test_approve_success_updates_status():
    seed_transfer()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/flows/tr_1")
        r = client.post(
            "/admin/flows/tr_1/approve",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "decision": "APPROVE",
                "comment": "现场核对无误",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/flows/tr_1?notice=approved"
        c = backend.db()
        row = c.execute("SELECT status, approved_by FROM transfer_requests WHERE id='tr_1'").fetchone()
        c.close()
        assert row["status"] == "APPROVED" and row["approved_by"] == "u_wh"


def test_reject_requires_comment():
    seed_transfer()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/flows/tr_1")
        r = client.post(
            "/admin/flows/tr_1/approve",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "decision": "REJECT",
                "comment": "",
            },
        )
        assert r.status_code == 200
        assert "拒绝时必须填写原因（1-500 字）" in r.text
        c = backend.db()
        assert c.execute("SELECT status FROM transfer_requests WHERE id='tr_1'").fetchone()[0] == "PENDING_APPROVAL"
        c.close()


def test_self_approve_forbidden():
    seed_transfer(created_by="u_wh", op="op-tr-self")
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/flows/tr_1")
        r = client.post(
            "/admin/flows/tr_1/approve",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "decision": "APPROVE",
                "comment": "自己批自己",
            },
        )
        assert r.status_code == 200
        assert "申请人不能审批本人申请" in r.text
        c = backend.db()
        assert c.execute("SELECT status FROM transfer_requests WHERE id='tr_1'").fetchone()[0] == "PENDING_APPROVAL"
        c.close()


def test_state_conflict_on_second_approval():
    seed_transfer(status="APPROVED", op="op-tr-x")
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/flows/tr_1")
        assert 'name="decision"' not in page.text
        r = client.post(
            "/admin/flows/tr_1/approve",
            data={
                "csrf_token": form_csrf(page.text),
                "clientOperationId": str(uuid.uuid4()),
                "decision": "APPROVE",
                "comment": "",
            },
        )
        assert r.status_code == 200
        assert "申请状态不允许审批" in r.text


def test_handover_timeline_query_and_404():
    seed_transfer()
    seed_handover_and_event()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/handovers?query=ho_1")
        assert r.status_code == 200
        assert "ho_1" in r.text and "wi_1" in r.text
        assert "OUTBOUND_APPROVED" in r.text
        missing = client.get("/admin/handovers?query=ho_missing")
        assert missing.status_code == 200
        assert "404" in missing.text


def test_approve_without_csrf_rejected_no_change():
    seed_transfer()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.post(
            "/admin/flows/tr_1/approve",
            data={
                "csrf_token": "",
                "clientOperationId": str(uuid.uuid4()),
                "decision": "APPROVE",
                "comment": "",
            },
        )
        assert r.status_code == 403
        c = backend.db()
        assert c.execute("SELECT status FROM transfer_requests WHERE id='tr_1'").fetchone()[0] == "PENDING_APPROVAL"
        c.close()
