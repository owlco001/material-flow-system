import os, tempfile, uuid, sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = tempfile.mkdtemp(prefix="mf_closure_")
os.environ.update(MATERIAL_FLOW_DATA=f"{ROOT}/data", MATERIAL_FLOW_UPLOADS=f"{ROOT}/uploads", INITIAL_ADMIN_PASSWORD="Admin@2026")
from app import main as backend


def auth(client, username, password="Worker@2026"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password, "deviceId": username})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["accessToken"]}


def test_scan_uses_real_resource_ids_and_missing_order_is_not_success():
    with TestClient(backend.app) as client:
        admin = auth(client, "owlco", "Admin@2026")
        r = client.post("/api/v1/scan/resolve", json={"rawValue": "26B-013", "clientOperationId": str(uuid.uuid4())}, headers=admin)
        assert r.status_code == 200
        assert r.json() == {"type": "PRODUCTION_ORDER", "normalizedValue": "26B-013", "resourceId": "ord_demo_26b013"}
        missing = client.post("/api/v1/orders/material-status", json={"documentType":"PRODUCTION_ORDER", "documentNo":"26B-999"}, headers=admin)
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
        old = client.post("/api/v1/scan/resolve", json={"rawValue": "26B-999", "clientOperationId": str(uuid.uuid4())}, headers=admin)
        assert old.status_code == 404


def test_admin_add_employee_accepts_employee_no_and_manager_and_is_idempotent():
    with TestClient(backend.app) as client:
        admin = auth(client, "owlco", "Admin@2026")
        payload = {"employeeNo":"E-100", "displayName":"员工一", "role":"OPERATOR", "password":"Temp@2026", "managerId":"u_admin"}
        r = client.post("/api/v1/admin/users", json=payload, headers=admin)
        assert r.status_code == 200 and r.json()["mustChangePassword"] is True
        assert "password" not in r.text and "password_hash" not in r.text
        again = client.post("/api/v1/admin/users", json=payload, headers=admin)
        assert again.status_code == 200 and again.json()["id"] == r.json()["id"]
        login = client.post("/api/v1/auth/login", json={"employeeNo":"E-100", "password":"Temp@2026", "deviceId":"pda"})
        assert login.status_code == 200 and login.json()["user"]["username"] == "E-100"


def test_exception_requires_order_device_material_and_direct_manager_review():
    with TestClient(backend.app) as client:
        admin = auth(client, "owlco", "Admin@2026")
        client.post("/api/v1/admin/users", json={"employeeNo":"E-200","displayName":"申请人","role":"OPERATOR","password":"Temp@2026","managerId":"u_admin"}, headers=admin)
        submit = auth(client, "E-200", "Temp@2026")
        r = client.post("/api/v1/exceptions", json={"orderNo":"26B-013","deviceId":"dev_demo_HZ01","materialId":"mat_ctl_cabinet","type":"SHORTAGE","bookQuantity":1,"actualQuantity":0}, headers=submit)
        assert r.status_code == 200
        eid = r.json()["exceptionId"]
        review = client.post(f"/api/v1/exceptions/{eid}/review", json={"decision":"APPROVE"}, headers=admin)
        assert review.status_code == 200
        bad = client.post("/api/v1/exceptions", json={"materialId":"mat_ctl_cabinet","actualQuantity":0,"bookQuantity":1}, headers=submit)
        assert bad.status_code == 400
