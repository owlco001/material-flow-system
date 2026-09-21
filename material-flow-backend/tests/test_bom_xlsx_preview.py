from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402
from tests.test_xlsx_parser import make_xlsx_bytes  # noqa: E402


def rid() -> str:
    return str(uuid.uuid4())


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owlco", "password": "Admin@2026", "deviceId": rid()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def setup_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend, "DB_PATH", tmp_path / "data" / "material_flow.db")
    backend.BOM_PREVIEWS.clear()
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
    c.commit()
    c.close()


def post_xlsx_preview(client: TestClient, auth: dict[str, str], workbook: bytes):
    return client.post(
        "/api/v1/boms/import/preview",
        files={
            "file": (
                "bom.xlsx",
                workbook,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"modelCode": "M-001"},
        headers=auth,
    )


def test_xlsx_preview_maps_effective_rows_and_does_not_write_business_tables(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    workbook = make_xlsx_bytes([
        ["导出", "BOM"],
        ["料品编码", "料品名称", "规格", "单位名称", "实际用量", "是否生效"],
        ["MTR-001", "工业轴承", "6205-2RS", "件", "2.5", "是"],
        ["MTR-CTL-001", "控制柜", "GGD-800x600x2200", "台", "1", "否"],
        ["", "空编码", "", "件", "1", "是"],
        ["MTR-SEN-002", "位置传感器", "PNP-NO-M12", "只", "0", "是"],
    ])

    with TestClient(backend.app) as client:
        response = post_xlsx_preview(client, login(client), workbook)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totalRows"] == 3
    assert body["validRows"] == 1
    assert body["invalidRows"] == 2
    assert body["canCommit"] is False
    assert body["items"] == [
        {
            "lineNo": 3,
            "materialId": "mat_001",
            "materialCode": "MTR-001",
            "materialName": "工业轴承",
            "specification": "6205-2RS",
            "unit": "件",
            "quantity": 2.5,
            "scrapRate": 0.0,
            "substituteMaterialCodes": "",
        }
    ]
    assert {error["code"] for error in body["errors"]} == {
        "REQUIRED",
        "INVALID_QUANTITY",
        "UNKNOWN_MATERIAL",
    }
    assert {(error["lineNo"], error["field"]) for error in body["errors"]} == {
        (5, "materialCode"),
        (6, "quantity"),
    }
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM bom_versions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM bom_items").fetchone()[0] == 0
    finally:
        c.close()


def test_xlsx_preview_preview_id_commits_via_existing_idempotent_path(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    c = backend.db()
    try:
        c.execute(
            "INSERT INTO production_order_models(id,model_code,model_name,created_at) VALUES(?,?,?,?)",
            ("model-1", "M-001", "测试机型", backend.now()),
        )
        c.commit()
    finally:
        c.close()
    workbook = make_xlsx_bytes([
        ["导出", "BOM"],
        ["料品编码", "料品名称", "规格", "单位名称", "实际用量", "是否生效"],
        ["MTR-001", "工业轴承", "6205-2RS", "件", "2.5", "是"],
    ])

    with TestClient(backend.app) as client:
        auth = login(client)
        preview_response = post_xlsx_preview(client, auth, workbook)
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["canCommit"] is True
        assert preview["validRows"] == 1

        operation_id = rid()
        payload = {
            "previewId": preview["previewId"],
            "clientOperationId": operation_id,
            "publish": True,
        }
        headers = {**auth, "X-Request-Id": rid(), "Idempotency-Key": operation_id}
        first = client.post("/api/v1/boms/import/commit", json=payload, headers=headers)
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["status"] == "PUBLISHED"
        assert first_body["itemCount"] == 1
        assert first_body["idempotent"] is False

        c = backend.db()
        try:
            version = c.execute(
                "SELECT * FROM bom_versions WHERE id=?",
                (first_body["bomVersionId"],),
            ).fetchone()
            assert version is not None
            assert version["model_code"] == "M-001"
            assert version["status"] == "PUBLISHED"
            assert version["row_count"] == 1
            item = c.execute(
                "SELECT * FROM bom_items WHERE bom_version_id=?",
                (first_body["bomVersionId"],),
            ).fetchone()
            assert item is not None
            assert item["material_id"] == "mat_001"
            assert item["material_code"] == "MTR-001"
            assert item["quantity"] == 2.5
            assert item["line_no"] == 3
        finally:
            c.close()

        retry = client.post(
            "/api/v1/boms/import/commit",
            json=payload,
            headers={**auth, "X-Request-Id": rid(), "Idempotency-Key": operation_id},
        )
        assert retry.status_code == 200, retry.text
        retry_body = retry.json()
        assert retry_body["idempotent"] is True
        assert retry_body["bomVersionId"] == first_body["bomVersionId"]

        conflict = client.post(
            "/api/v1/boms/import/commit",
            json={**payload, "publish": False},
            headers={**auth, "X-Request-Id": rid(), "Idempotency-Key": operation_id},
        )
        assert conflict.status_code == 409, conflict.text


def test_xlsx_preview_missing_header_returns_422(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    workbook = make_xlsx_bytes([
        ["料品编码", "料品名称", "规格", "单位名称", "实际用量"],
        ["MTR-001", "工业轴承", "6205-2RS", "件", "2.5"],
    ])

    with TestClient(backend.app) as client:
        response = post_xlsx_preview(client, login(client), workbook)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "BOM_TEMPLATE_INVALID"
    assert body["error"]["details"]["missingFields"] == ["是否生效"]
