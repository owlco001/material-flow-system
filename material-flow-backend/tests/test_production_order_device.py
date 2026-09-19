import os
import sys
import uuid
from pathlib import Path
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend


def rid(): return str(uuid.uuid4())
def hdr(op): return {"X-Request-Id": rid(), "Idempotency-Key": op}

def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend, "DB_PATH", tmp_path / "data" / "material_flow.db")
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
    c.execute("INSERT INTO production_order_models(id,model_code,model_name,created_at) VALUES(?,?,?,?)", ("legacy-model", "OTHER", "其他", backend.now()))
    c.execute("INSERT INTO bom_versions VALUES(?,?,?,?,?,?,?,?,?,?)", ("bom-pub", "M-001", 1, "PUBLISHED", "sha", 1, "u_admin", backend.now(), "u_admin", backend.now()))
    c.commit(); c.close()

def login(client):
    r = client.post("/api/v1/auth/login", json={"username":"owlco","password":"Admin@2026","deviceId":rid()})
    assert r.status_code == 200, r.text
    return {"Authorization":"Bearer " + r.json()["accessToken"]}

def test_order_device_assignment_and_idempotency(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client)
        op = rid(); order = {"clientOperationId":op,"orderNo":"SO-1","productName":"产品","plannedQuantity":10,"models":[{"modelCode":"M-001","modelName":"机型","plannedQuantity":10,"bomVersionId":"bom-pub"}]}
        r = client.post("/api/v1/production-orders", json=order, headers={**auth, **hdr(op)})
        assert r.status_code == 200, r.text
        assert client.post("/api/v1/production-orders", json=order, headers={**auth, **hdr(op)}).json()["idempotent"] is True
        dop = rid(); dr = client.post("/api/v1/devices", json={"clientOperationId":dop,"deviceNo":"D-1","deviceName":"一号机","workshop":"A","modelCapability":"M-001"}, headers={**auth, **hdr(dop)})
        assert dr.status_code == 200, dr.text
        aop = rid(); payload = {"clientOperationId":aop,"deviceId":dr.json()["deviceId"],"expectedVersion":1}
        ar = client.post("/api/v1/production-orders/SO-1/models/M-001/assign-device", json=payload, headers={**auth, **hdr(aop)})
        assert ar.status_code == 200, ar.text
        assert client.post("/api/v1/production-orders/SO-1/models/M-001/assign-device", json=payload, headers={**auth, **hdr(aop)}).json()["idempotent"] is True
        c = backend.db(); assert c.execute("SELECT COUNT(*) FROM assembly_tasks WHERE order_no='SO-1'").fetchone()[0] == 1; c.close()

def test_order_requires_published_bom_and_version_conflict(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client); op = rid()
        bad = {"clientOperationId":op,"orderNo":"SO-2","productName":"产品","plannedQuantity":1,"models":[{"modelCode":"M-001","modelName":"机型","plannedQuantity":1,"bomVersionId":"missing"}]}
        assert client.post("/api/v1/production-orders", json=bad, headers={**auth, **hdr(op)}).status_code == 422
