"""Server-authoritative order material summary contract."""
from __future__ import annotations

import os

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

from fastapi.testclient import TestClient

from app import main as backend


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owlco", "password": "Admin@2026", "deviceId": "summary-test"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _assert_summary(summary: dict, items: list[dict]) -> None:
    assert summary["totalMaterialTypes"] == 3
    assert summary["totalRequiredQuantity"] == 1500
    assert summary["totalArrivedQuantity"] == 980
    assert summary["totalInStockQuantity"] == 480
    assert summary["totalShortageQuantity"] == 1020
    assert len(summary["items"]) == 3
    assert {item["statusCode"] for item in summary["items"]} == {"OUT_OF_STOCK"}
    assert {item["statusLabel"] for item in summary["items"]} == {"缺货"}
    assert all(
        set(item) >= {
            "materialId", "materialCode", "materialName", "unit",
            "requiredQuantity", "arrivedQuantity", "inStockQuantity",
            "shortageQuantity", "statusCode", "statusLabel",
        }
        for item in summary["items"]
    )
    assert sum(item["requiredQuantity"] for item in items) == summary["totalRequiredQuantity"]


def test_material_status_and_order_detail_include_authoritative_summary():
    with TestClient(backend.app) as client:
        token = _login(client)
        headers = {"Authorization": f"Bearer {token}"}
        material_status = client.post(
            "/api/v1/orders/material-status",
            headers=headers,
            json={"documentType": "PRODUCTION_ORDER", "documentNo": "26B-013"},
        )
        assert material_status.status_code == 200, material_status.text
        status_body = material_status.json()
        _assert_summary(status_body["materialSummary"], status_body["items"])
        assert len(status_body["items"]) == 75  # legacy response remains intact

        detail = client.get("/api/v1/orders/26B-013/detail", headers=headers)
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()
        _assert_summary(detail_body["materialSummary"], detail_body["materials"])
        assert len(detail_body["materials"]) == 75  # legacy response remains intact
