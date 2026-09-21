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


INVENTORY_HEADERS = [
    "存储地点名称",
    "料号",
    "品名",
    "库存单位名称",
    "库位编码",
    "库位名称",
    "库存可用量(库存单位)",
    "现存量(库存单位)",
]


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
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0 WHERE username='owlco'")
    c.commit()
    c.close()


def post_inventory_preview(client: TestClient, auth: dict[str, str] | None, workbook: bytes):
    headers = auth or {}
    return client.post(
        "/api/v1/inventory/import/preview",
        files={
            "file": (
                "inventory.xlsx",
                workbook,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers,
    )


def inventory_workbook(data_rows: list[list[str]], headers: list[str] | None = None) -> bytes:
    return make_xlsx_bytes([
        ["库存快照"],
        ["导出时间", "2026-09-21"],
        headers or INVENTORY_HEADERS,
        *data_rows,
    ])


def test_inventory_xlsx_preview_maps_third_row_header_and_does_not_write_inventory(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    workbook = inventory_workbook([
        ["一号仓", "MTR-001", "工业轴承", "件", "A-01", "主库位", "12.500", "20"],
        ["二号仓", "MTR-002", "位置传感器", "只", "B-02", "备料位", "0", "3.75"],
    ])

    with TestClient(backend.app) as client:
        response = post_inventory_preview(client, login(client), workbook)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batch"]["type"] == "INVENTORY_SNAPSHOT_XLSX"
    assert body["batch"]["headerRow"] == 3
    assert body["preview"] == {"totalRows": 2, "validRows": 2, "invalidRows": 0, "canCommit": False}
    assert body["errors"] == []
    assert body["rows"] == [
        {
            "lineNo": 4,
            "storageLocationName": "一号仓",
            "materialCode": "MTR-001",
            "materialName": "工业轴承",
            "unit": "件",
            "locationCode": "A-01",
            "locationName": "主库位",
            "availableQuantity": "12.500",
            "onHandQuantity": "20",
        },
        {
            "lineNo": 5,
            "storageLocationName": "二号仓",
            "materialCode": "MTR-002",
            "materialName": "位置传感器",
            "unit": "只",
            "locationCode": "B-02",
            "locationName": "备料位",
            "availableQuantity": "0",
            "onHandQuantity": "3.75",
        },
    ]
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM inventory WHERE material_id IN ('MTR-001', 'MTR-002')").fetchone()[0] == 0
    finally:
        c.close()


def test_inventory_xlsx_preview_reports_invalid_quantity_and_blank_material_code(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    workbook = inventory_workbook([
        ["一号仓", "", "工业轴承", "件", "A-01", "主库位", "12", "20"],
        ["二号仓", "MTR-002", "位置传感器", "只", "B-02", "备料位", "abc", "3.75"],
        ["三号仓", "MTR-003", "控制柜", "台", "C-03", "暂存位", "1", "bad"],
    ])

    with TestClient(backend.app) as client:
        response = post_inventory_preview(client, login(client), workbook)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preview"] == {"totalRows": 3, "validRows": 0, "invalidRows": 3, "canCommit": False}
    assert body["rows"] == []
    assert body["errors"] == [
        {"lineNo": 4, "field": "materialCode", "code": "REQUIRED", "message": "料号不能为空"},
        {"lineNo": 5, "field": "availableQuantity", "code": "INVALID_QUANTITY", "message": "数量必须为合法 Decimal"},
        {"lineNo": 6, "field": "onHandQuantity", "code": "INVALID_QUANTITY", "message": "数量必须为合法 Decimal"},
    ]


def test_inventory_xlsx_preview_missing_required_column_returns_422(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    headers = [header for header in INVENTORY_HEADERS if header != "库位名称"]
    workbook = inventory_workbook([
        ["一号仓", "MTR-001", "工业轴承", "件", "A-01", "12", "20"],
    ], headers=headers)

    with TestClient(backend.app) as client:
        response = post_inventory_preview(client, login(client), workbook)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "INVENTORY_TEMPLATE_INVALID"
    assert body["error"]["details"]["missingFields"] == ["库位名称"]


def test_inventory_xlsx_preview_requires_authentication(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    workbook = inventory_workbook([
        ["一号仓", "MTR-001", "工业轴承", "件", "A-01", "主库位", "12", "20"],
    ])

    with TestClient(backend.app) as client:
        response = post_inventory_preview(client, None, workbook)

    assert response.status_code == 401
