"""The real application must document and dispatch the same workshop routes."""
from collections import defaultdict

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest


SUMMARY = "/api/v1/workshop/summary"
MACHINE_PROGRESS = "/api/v1/workshop/machine-progress"


@pytest.fixture
def workshop_backend(monkeypatch, tmp_path):
    data = tmp_path / "data"
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("MATERIAL_FLOW_DATA", str(data))
    monkeypatch.setenv("MATERIAL_FLOW_UPLOADS", str(uploads))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "")
    from app import main

    monkeypatch.setattr(main, "DATA_DIR", data)
    monkeypatch.setattr(main, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(main, "DB_PATH", data / "workshop.db")
    monkeypatch.setattr(main, "INITIAL_ADMIN_PASSWORD", "")
    return main


def test_application_http_routes_are_unique(workshop_backend):
    registrations = defaultdict(list)
    for route in workshop_backend.app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                registrations[(method, route.path)].append(route.endpoint)
    duplicates = {key: endpoints for key, endpoints in registrations.items() if len(endpoints) > 1}
    assert not duplicates
    assert registrations[("GET", SUMMARY)] == [workshop_backend.workshop_summary]
    assert registrations[("GET", MACHINE_PROGRESS)] == [workshop_backend.workshop_machine_progress]


def test_workshop_openapi_matches_main_route_validation_and_permissions(workshop_backend, monkeypatch):
    backend = workshop_backend
    backend.init_db()
    role = "PLANNER"
    # Override authentication only. Route authorization and validation still run.
    monkeypatch.setitem(backend.app.dependency_overrides, backend.current_user,
                        lambda: {"id": "fixture-user", "role": role})
    # Requests use the real ASGI app. DB setup is explicit so startup jobs stay out of scope.
    client = TestClient(backend.app)
    schema = client.get("/openapi.json").json()["paths"]
    assert schema[SUMMARY]["get"]["operationId"].startswith("workshop_summary_")
    machine = schema[MACHINE_PROGRESS]["get"]
    assert machine["operationId"].startswith("workshop_machine_progress_")
    params = {p["name"]: p["schema"] for p in machine["parameters"]}
    assert params["page"]["minimum"] == params["pageSize"]["minimum"] == 1
    assert params["pageSize"]["maximum"] == 100
    assert any(s.get("maxLength") == 64 for s in params["deviceId"]["anyOf"])
    assert client.get(MACHINE_PROGRESS, params={"pageSize": 101}).status_code == 422
    assert client.get(MACHINE_PROGRESS, params={"page": 0}).status_code == 422
    assert client.get(MACHINE_PROGRESS, params={"deviceId": "x" * 65}).status_code == 422
    assert client.get(MACHINE_PROGRESS, params={"pageSize": 100}).status_code == 200
    assert client.get(SUMMARY).status_code == 200
    role = "ASSEMBLER"
    assert client.get(SUMMARY).status_code == 403
    assert client.get(MACHINE_PROGRESS).status_code == 403
    client.close()
