"""S8 物料—库位—库存查询契约测试（docs/admin-web-contract.md §8.7）。"""
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
        ("u_wh", "wh-admin", "仓库管理员", "WAREHOUSE_ADMIN", backend.hash_password("Wh@2026x"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_sup", "supervisor1", "车间主管", "WORKSHOP_SUPERVISOR", backend.hash_password("Sup@2026x"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_warehouse_rows():
    c = backend.db()
    c.execute("INSERT INTO locations(id,code,name) VALUES(?,?,?)", ("loc_1", "A-01", "一号库位"))
    c.execute(
        "INSERT INTO materials(id,code,name,specification,unit,batch_no,expiry_date,"
        "total_quantity,available_quantity,version) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("mat_1", "M-001", "轴承", "6204-2RS", "个", "B1", "2027-01-01", 100, 80, 3),
    )
    c.execute("INSERT INTO inventory(id,material_id,location_id,quantity) VALUES(?,?,?,?)",
              ("inv_1", "mat_1", "loc_1", 80))
    c.execute(
        "INSERT INTO stocktakes(id,material_id,location_id,book_quantity,actual_quantity,"
        "difference,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("st_1", "mat_1", "loc_1", 80, 78, -2, "PENDING_CONFIRM", "u_wh", backend.now()),
    )
    c.execute(
        "INSERT INTO exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ex_1", "mat_1", "DAMAGE", 80, 78, -2, "PENDING", "外箱破损", "[]", "u_wh",
         backend.now(), None, None, "SO-TEST", "dev_1"),
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


def test_warehouse_admin_ok_supervisor_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        assert client.get("/admin/warehouse").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "supervisor1", "Sup@2026x").status_code == 303
        assert client2.get("/admin/warehouse").status_code == 403


def test_warehouse_material_query_hit():
    seed_warehouse_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/warehouse?code=M-001")
        assert r.status_code == 200
        assert "轴承" in r.text and "6204-2RS" in r.text and "B1" in r.text
        assert "A-01" in r.text and "总量 100 · 可用 80" in r.text


def test_warehouse_material_not_found():
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/warehouse?code=NOPE")
        assert r.status_code == 200 and "物料不存在" in r.text


def test_warehouse_stocktake_and_exception_rows():
    seed_warehouse_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r = client.get("/admin/warehouse")
        assert r.status_code == 200
        assert "st_1" in r.text and "待确认" in r.text
        assert "ex_1" in r.text and "外箱破损" in r.text and "待处理" in r.text


def test_stocktake_confirm_flow():
    seed_warehouse_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/warehouse")
        r = client.post(
            "/admin/warehouse/stocktakes/st_1/confirm",
            data={"csrf_token": form_csrf(page.text), "clientOperationId": str(uuid.uuid4())},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/warehouse?notice=stocktake_confirmed"
        c = backend.db()
        row = c.execute("SELECT status, confirmed_by FROM stocktakes WHERE id='st_1'").fetchone()
        c.close()
        assert row["status"] == "CONFIRMED" and row["confirmed_by"] == "u_wh"
        again = client.post(
            "/admin/warehouse/stocktakes/st_1/confirm",
            data={"csrf_token": form_csrf(client.get("/admin/warehouse").text),
                  "clientOperationId": str(uuid.uuid4())},
        )
        assert again.status_code == 200 and "待确认盘点不存在或已处理" in again.text


def test_exception_review_approve_and_reject():
    seed_warehouse_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/warehouse")
        r = client.post(
            "/admin/warehouse/exceptions/ex_1/review",
            data={"csrf_token": form_csrf(page.text), "clientOperationId": str(uuid.uuid4()),
                  "decision": "APPROVE", "comment": "核实无误"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/warehouse?notice=exception_reviewed"
        c = backend.db()
        assert c.execute("SELECT status FROM exceptions WHERE id='ex_1'").fetchone()[0] == "APPROVED"
        c.execute("INSERT INTO exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("ex_2", "mat_1", "SHORTAGE", 10, 8, -2, "PENDING", "缺件", "[]", "u_wh",
                   backend.now(), None, None, None, None))
        c.commit()
        c.close()
        page2 = client.get("/admin/warehouse")
        r2 = client.post(
            "/admin/warehouse/exceptions/ex_2/review",
            data={"csrf_token": form_csrf(page2.text), "clientOperationId": str(uuid.uuid4()),
                  "decision": "REJECT", "comment": "证据不足"},
            follow_redirects=False,
        )
        assert r2.status_code == 303
        c = backend.db()
        assert c.execute("SELECT status FROM exceptions WHERE id='ex_2'").fetchone()[0] == "REJECTED"
        c.close()


def test_exception_review_state_conflict():
    seed_warehouse_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        page = client.get("/admin/warehouse")
        client.post(
            "/admin/warehouse/exceptions/ex_1/review",
            data={"csrf_token": form_csrf(page.text), "clientOperationId": str(uuid.uuid4()),
                  "decision": "APPROVE", "comment": ""},
            follow_redirects=False,
        )
        again = client.post(
            "/admin/warehouse/exceptions/ex_1/review",
            data={"csrf_token": form_csrf(client.get("/admin/warehouse").text),
                  "clientOperationId": str(uuid.uuid4()), "decision": "APPROVE", "comment": ""},
        )
        assert again.status_code == 200 and "异常状态不允许审批" in again.text


def test_actions_without_csrf_rejected():
    seed_warehouse_rows()
    with TestClient(backend.app) as client:
        assert web_login(client, "wh-admin", "Wh@2026x").status_code == 303
        r1 = client.post(
            "/admin/warehouse/stocktakes/st_1/confirm",
            data={"csrf_token": "", "clientOperationId": str(uuid.uuid4())},
        )
        r2 = client.post(
            "/admin/warehouse/exceptions/ex_1/review",
            data={"csrf_token": "", "clientOperationId": str(uuid.uuid4()),
                  "decision": "APPROVE", "comment": ""},
        )
        assert r1.status_code == 403 and r2.status_code == 403
        c = backend.db()
        assert c.execute("SELECT status FROM stocktakes WHERE id='st_1'").fetchone()[0] == "PENDING_CONFIRM"
        assert c.execute("SELECT status FROM exceptions WHERE id='ex_1'").fetchone()[0] == "PENDING"
        c.close()
