import csv
import io
import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402


def rid():
    return str(uuid.uuid4())


def headers(key=None):
    return {"X-Request-Id": rid(), "Idempotency-Key": key or rid()}


def login(client, username="owlco", password="Admin@2026"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password, "deviceId": rid()})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def setup_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend, "DB_PATH", tmp_path / "data" / "material_flow.db")
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
    c.execute("INSERT INTO production_order_models(id,model_code,model_name,created_at) VALUES(?,?,?,?)", ("model-1", "M-001", "测试机型", backend.now()))
    c.commit(); c.close()


def csv_bytes(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=["modelCode", "materialCode", "materialName", "specification", "unit", "quantity", "scrapRate", "substituteMaterialCodes"])
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode()


def test_preview_commit_idempotency_archive_and_list(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client)
        rows = [{"modelCode": "M-001", "materialCode": "MTR-001", "materialName": "轴承", "specification": "", "unit": "件", "quantity": "2.5", "scrapRate": "0.1", "substituteMaterialCodes": ""}]
        response = client.post("/api/v1/boms/import/preview", files={"file": ("bom.csv", csv_bytes(rows), "text/csv")}, data={"modelCode": "M-001"}, headers=auth)
        assert response.status_code == 200
        preview = response.json(); assert preview["canCommit"] and preview["validRows"] == 1
        c = backend.db(); assert c.execute("SELECT COUNT(*) FROM bom_versions").fetchone()[0] == 0; c.close()
        key = rid(); payload = {"previewId": preview["previewId"], "clientOperationId": key, "publish": True}
        first = client.post("/api/v1/boms/import/commit", json=payload, headers={**auth, **headers(key)})
        assert first.status_code == 200 and first.json()["status"] == "PUBLISHED"
        second = client.post("/api/v1/boms/import/commit", json=payload, headers={**auth, **headers(key)})
        assert second.status_code == 200 and second.json()["idempotent"] is True
        conflict = client.post("/api/v1/boms/import/commit", json={**payload, "publish": False}, headers={**auth, **headers(key)})
        assert conflict.status_code == 409
        preview2 = client.post("/api/v1/boms/import/preview", files={"file": ("bom.csv", csv_bytes(rows), "text/csv")}, data={"modelCode": "M-001"}, headers=auth).json()
        key2 = rid(); second_version = client.post("/api/v1/boms/import/commit", json={"previewId": preview2["previewId"], "clientOperationId": key2, "publish": True}, headers={**auth, **headers(key2)})
        assert second_version.status_code == 200
        versions = client.get("/api/v1/boms/versions?modelCode=M-001&page=1&pageSize=1", headers=auth).json()
        assert versions["total"] == 2 and len(versions["items"]) == 1
        c = backend.db(); assert c.execute("SELECT COUNT(*) FROM bom_versions WHERE status='ARCHIVED'").fetchone()[0] == 1; c.close()


def test_preview_row_errors_and_auth(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        rows = [
            {"modelCode": "M-001", "materialCode": "NOPE", "materialName": "x", "specification": "", "unit": "件", "quantity": "bad", "scrapRate": "2", "substituteMaterialCodes": ""},
            {"modelCode": "M-001", "materialCode": "MTR-001", "materialName": "x", "specification": "", "unit": "件", "quantity": "1", "scrapRate": "0", "substituteMaterialCodes": ""},
            {"modelCode": "M-001", "materialCode": "MTR-001", "materialName": "x", "specification": "", "unit": "件", "quantity": "1", "scrapRate": "0", "substituteMaterialCodes": ""},
        ]
        no_auth = client.post("/api/v1/boms/import/preview", files={"file": ("bom.csv", csv_bytes(rows), "text/csv")}, data={"modelCode": "M-001"})
        assert no_auth.status_code == 401
        auth = login(client)
        response = client.post("/api/v1/boms/import/preview", files={"file": ("bom.csv", csv_bytes(rows), "text/csv")}, data={"modelCode": "M-001"}, headers=auth)
        assert response.status_code == 200
        body = response.json(); codes = {e["code"] for e in body["errors"]}
        assert {"UNKNOWN_MATERIAL", "INVALID_QUANTITY", "INVALID_SCRAP_RATE", "DUPLICATE"} <= codes
        c = backend.db(); assert c.execute("SELECT COUNT(*) FROM bom_versions").fetchone()[0] == 0; c.close()
