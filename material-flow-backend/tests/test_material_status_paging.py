"""订单物料状态（material-status）分页（0.21.14）。

手机端订单页的物料清单走的是这个接口，原来一次返回订单全部需求行
（本机 2 万行订单 9MB），翻页后每页只取一小段，齐套率仍按全量汇总。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as backend

H = lambda token: {"Authorization": f"Bearer {token}"}


def login(client, username, password):
    r = client.post("/api/v1/auth/login",
                    json={"username": username, "password": password, "deviceId": "t"})
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


def seed(rows: int = 5):
    c = backend.db()
    c.execute("PRAGMA foreign_keys=OFF")
    now = backend.now()
    c.execute("DELETE FROM order_material_requirements WHERE order_id='o-ms'")
    c.execute("INSERT OR REPLACE INTO users VALUES(?,?,?,?,?,?,?,?)",
              ("u_ms", "ms-admin", "管理员", "ADMIN", backend.hash_password("Pw@2026x"), 0, 1, now))
    c.execute("INSERT OR REPLACE INTO production_orders(id, order_no, product_name, status,"
              " created_at, updated_at) VALUES('o-ms','OM-MS','物料状态样机','IN_PROGRESS',?,?)", (now, now))
    c.execute("INSERT OR REPLACE INTO order_devices(id, order_id, device_type, device_no,"
              " sequence_no, created_at) VALUES('od-ms','o-ms','T1','DEV-MS',1,?)", (now,))
    for i in range(rows):
        mid, code = f"m-ms{i}", f"MMS-{i:03d}"
        c.execute("INSERT OR REPLACE INTO materials(id, code, name, specification, unit,"
                  " total_quantity, available_quantity, version)"
                  " VALUES(?,?,?,'S','个',100,100,1)", (mid, code, f"状态料{i}"))
        c.execute("INSERT INTO order_material_requirements(id, order_id, device_id, material_id,"
                  " required_quantity, arrived_quantity, in_stock_quantity, status_code,"
                  " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (f"r-ms{i}", "o-ms", "od-ms", mid, 10, 5, 5,
                   "IN_STOCK" if i % 2 == 0 else "OUT_OF_STOCK", now, now))
    c.commit()
    c.close()


def query(client, token, **extra):
    body = {"documentNo": "OM-MS", "documentType": "PRODUCTION_ORDER"}
    body.update(extra)
    return client.post("/api/v1/orders/material-status", json=body, headers=H(token))


def test_material_status_pages_without_changing_summary():
    seed(rows=5)
    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        full = query(client, token)
        assert full.status_code == 200, full.text
        full_body = full.json()
        assert len(full_body["items"]) == 5 and full_body["total"] == 5
        assert full_body["pageSize"] == 0 and full_body["hasMore"] is False

        p1 = query(client, token, page="1", pageSize="3").json()
        assert len(p1["items"]) == 3 and p1["total"] == 5 and p1["hasMore"] is True
        p2 = query(client, token, page="2", pageSize="3").json()
        assert len(p2["items"]) == 2 and p2["hasMore"] is False
        ids = [i["requirementId"] for i in p1["items"] + p2["items"]]
        assert len(set(ids)) == 5, "两页不重不漏"
        # 齐套率口径：汇总必须与全量一致，不能只统计当前页
        assert p1["materialSummary"] == full_body["materialSummary"]
        assert p2["materialSummary"] == full_body["materialSummary"]
        assert full_body["materialSummary"]["totalRequiredQuantity"] == 50


def test_material_status_can_filter_one_order_device_before_paging():
    seed(rows=6)
    c = backend.db()
    now = backend.now()
    c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)", ("od-ms2", "o-ms", "T2", "DEV-MS-2", 2, now))
    c.execute("UPDATE order_material_requirements SET device_id='od-ms2' WHERE id IN ('r-ms3','r-ms4','r-ms5')")
    c.commit(); c.close()

    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        p1 = query(client, token, deviceNo="DEV-MS-2", page="1", pageSize="2")
        assert p1.status_code == 200, p1.text
        first = p1.json()
        assert first["total"] == 3
        assert [row["requirementId"] for row in first["items"]] == ["r-ms3", "r-ms4"]
        assert all(row["deviceNo"] == "DEV-MS-2" for row in first["items"])
        assert first["materialSummary"]["totalRequiredQuantity"] == 30
        assert first["shortageLineCount"] == 2
        p2 = query(client, token, deviceNo="DEV-MS-2", page="2", pageSize="2").json()
        assert p2["total"] == 3 and [row["requirementId"] for row in p2["items"]] == ["r-ms5"]
        # Legacy unfiltered requests retain the complete order totals.
        full = query(client, token).json()
        assert full["total"] == 6 and full["materialSummary"]["totalRequiredQuantity"] == 60


def test_material_status_unknown_device_is_empty_without_falling_back_to_whole_order():
    seed(rows=5)
    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        body = query(client, token, deviceNo="' OR 1=1 --", page=1, pageSize=2).json()
        assert body["items"] == [] and body["total"] == 0 and body["hasMore"] is False
        assert body["materialSummary"]["totalRequiredQuantity"] == 0
        assert body["shortageLineCount"] == 0
        trimmed = query(client, token, deviceNo="  DEV-MS ", page=1, pageSize=2).json()
        assert trimmed["total"] == 5 and len(trimmed["items"]) == 2


def test_material_status_accepts_historical_imported_long_device_number():
    seed(rows=2)
    long_no = "IMPORTED-" + "机台" * 40
    c = backend.db()
    c.execute("UPDATE order_devices SET device_no=? WHERE id='od-ms'", (long_no,))
    c.commit(); c.close()
    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        response = query(client, token, deviceNo=long_no, page=1, pageSize=1)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        assert response.json()["items"][0]["deviceNo"] == long_no


def test_material_status_rejects_invalid_device_no():
    seed(rows=2)
    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        for value in (123, "", " "):
            assert query(client, token, deviceNo=value).status_code == 400


def test_material_status_rejects_bad_paging():
    seed(rows=2)
    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        assert query(client, token, page="0").status_code == 400
        assert query(client, token, pageSize="501").status_code == 400
        assert query(client, token, page="abc").status_code == 400


def test_material_status_page_out_of_range_is_empty():
    seed(rows=2)
    with TestClient(backend.app) as client:
        token = login(client, "ms-admin", "Pw@2026x")
        body = query(client, token, page="7", pageSize="10").json()
        assert body["items"] == [] and body["total"] == 2 and body["hasMore"] is False
