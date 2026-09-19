import importlib
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MATERIAL_FLOW_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("MATERIAL_FLOW_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import app.main as main
    importlib.reload(main)
    with TestClient(main.app) as test_client:
        yield test_client, main


def admin_token(test_client: TestClient, main, password="A_valid_setup_password_123!"):
    test_client.post("/api/v1/setup/initialize-admin", json={
        "password": password, "confirmPassword": password,
        "idempotencyKey": "6f7e8d9c-" + uuid.uuid4().hex[:28],
    })
    login = test_client.post("/api/v1/auth/login", json={"username": "owlco", "password": password, "deviceId": "test-device"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    changed = test_client.post("/api/v1/auth/change-password", json={
        "currentPassword": password, "newPassword": "New_valid_password_789!",
        "confirmPassword": "New_valid_password_789!"}, headers=headers)
    assert changed.status_code == 200
    return headers


def insert_requirement(main, req_id="mr_test", in_stock=0, required=500):
    c = main.db()
    c.execute("INSERT INTO material_requirements VALUES(?,?,?,?,?,?,?)", (req_id, "pom_001", "mat_001", required, 0, in_stock, 0))
    c.commit()
    c.close()


def test_unauthenticated_returns_401(client):
    test_client, _ = client
    response = test_client.get("/api/v1/production-orders")
    assert response.status_code == 401


def test_list_pagination_bounds_and_total(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    c = main.db()
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?,?,?)", ("ord_002", "SO20260920", "电机总成", 100, "2026-10-10", "PLANNED", main.now(), main.now()))
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?,?,?)", ("ord_003", "PO20260921", "泵壳", 50, None, "COMPLETED", main.now(), main.now()))
    c.commit(); c.close()
    page1 = test_client.get("/api/v1/production-orders", params={"page": 1, "pageSize": 2}, headers=headers).json()
    assert page1["total"] == 3
    assert page1["page"] == 1 and page1["pageSize"] == 2
    assert [i["orderNo"] for i in page1["items"]] == ["PO20260921", "SO20260919"]
    out_of_range = test_client.get("/api/v1/production-orders", params={"page": 99, "pageSize": 2}, headers=headers)
    assert out_of_range.status_code == 200
    body = out_of_range.json()
    assert body["items"] == [] and body["total"] == 3 and body["page"] == 99
    assert body["serverTime"]
    demo = test_client.get("/api/v1/production-orders", params={"keyword": "SO20260919"}, headers=headers).json()
    assert demo["total"] == 1
    assert demo["items"][0]["modelCount"] == 1
    assert demo["items"][0]["materialCompletionRate"] == 100
    assert demo["items"][0]["shortageCount"] == 0
    assert demo["items"][0]["lastFlowAt"] is None


def test_keyword_and_status_filters(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    by_name = test_client.get("/api/v1/production-orders", params={"keyword": "轴承"}, headers=headers).json()
    assert by_name["total"] == 1 and by_name["items"][0]["orderNo"] == "SO20260919"
    by_status = test_client.get("/api/v1/production-orders", params={"status": "RELEASED"}, headers=headers).json()
    assert by_status["total"] == 1 and by_status["items"][0]["status"] == "RELEASED"
    none_match = test_client.get("/api/v1/production-orders", params={"status": "CLOSED"}, headers=headers)
    assert none_match.status_code == 200
    assert none_match.json() == {"items": [], "page": 1, "pageSize": 20, "total": 0, "serverTime": none_match.json()["serverTime"]}


def test_order_not_found_returns_404(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    response = test_client.get("/api/v1/production-orders/SO99999999", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "生产订单不存在"}


def test_detail_and_model_404(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    detail = test_client.get("/api/v1/production-orders/SO20260919", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["order"]["orderNo"] == "SO20260919"
    assert body["order"]["plannedQuantity"] == 200
    assert len(body["models"]) == 1
    m = body["models"][0]
    assert m["modelCode"] == "BDX-6205" and m["requiredMaterialCount"] == 1 and m["shortageMaterialCount"] == 0
    assert m["completionRate"] == 100
    missing_model = test_client.get("/api/v1/production-orders/SO20260919/models/NOPE-999", headers=headers)
    assert missing_model.status_code == 404


def test_model_detail_derived_quantities_shortage_path(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    url = "/api/v1/production-orders/SO20260919/models/BDX-6205"
    ok = test_client.get(url, headers=headers).json()
    req = ok["requirements"][0]
    assert req["materialCode"] == "MTR-001" and req["materialName"] == "工业轴承"
    assert req["unit"] == "件" and req["batchNo"] == "B20260912"
    assert req["availableQuantity"] == 986 and req["shortageQuantity"] == 0
    assert req["statusCode"] == "AVAILABLE" and req["label"] == "齐套" and req["colorToken"] == "status-green"
    insert_requirement(main, req_id="mr_short", in_stock=50, required=500)
    shortage = test_client.get(url, headers=headers).json()
    short_req = next(r for r in shortage["requirements"] if r["materialId"] == "mat_001" and r["availableQuantity"] == 50)
    assert short_req["shortageQuantity"] == 450
    assert short_req["statusCode"] == "SHORTAGE"
    assert short_req["label"] == "缺货"
    assert short_req["colorToken"] == "status-red"
    assert shortage["model"]["shortageMaterialCount"] == 1


def test_flow_records_empty_page_is_legal(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    response = test_client.get("/api/v1/production-orders/SO20260919/models/BDX-6205/flow-records", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [] and body["total"] == 0 and body["page"] == 1 and body["pageSize"] == 50
    assert body["serverTime"]


def test_flow_records_with_inbound_transfer(client):
    test_client, main = client
    headers = admin_token(test_client, main)
    created = test_client.post("/api/v1/transfer-requests", json={
        "clientOperationId": uuid.uuid4().hex, "type": "INBOUND", "documentNo": "SO20260919",
        "items": [{"materialId": "mat_001", "quantity": 120}, {"materialId": "mat_001", "quantity": 30}],
    }, headers=headers)
    assert created.status_code == 200
    base = "/api/v1/production-orders/SO20260919/models/BDX-6205/flow-records"
    records = test_client.get(base, headers=headers).json()
    assert records["total"] == 1
    item = records["items"][0]
    assert item["documentNo"] == "SO20260919" and item["type"] == "INBOUND"
    assert item["quantityTotal"] == 150
    assert item["materialCodes"] == ["MTR-001"]
    assert item["flowNo"] == created.json()["requestId"]
    filtered = test_client.get(base, params={"flowType": "OUTBOUND"}, headers=headers).json()
    assert filtered["total"] == 0 and filtered["items"] == []
