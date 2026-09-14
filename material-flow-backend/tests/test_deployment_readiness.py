"""Deployment-readiness tests for local schema, health and write serialization."""

from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(tempfile.mkdtemp(prefix="mf_deployment_readiness_"))
os.environ["MATERIAL_FLOW_DATA"] = str(ROOT / "data")
os.environ["MATERIAL_FLOW_UPLOADS"] = str(ROOT / "uploads")
os.environ["INITIAL_ADMIN_PASSWORD"] = "ReadinessAdmin@2026"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = BACKEND_ROOT / "app" / "main.py"
VERSION_PATH = BACKEND_ROOT / "VERSION"
SERVICE_PATH = BACKEND_ROOT / "material-flow.service"
DEPLOYMENT_PATH = BACKEND_ROOT / "docs" / "DEPLOYMENT.md"
MIGRATION_COMMAND = "ExecStartPre=/srv/material-flow/.venv/bin/python -m app.migrate"
SERVER_COMMAND = (
    "ExecStart=/srv/material-flow/.venv/bin/uvicorn app.main:app "
    "--host 127.0.0.1 --port 8000"
)
spec = importlib.util.spec_from_file_location("deployment_readiness_backend", APP_PATH)
assert spec is not None and spec.loader is not None
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
spec.loader.exec_module(backend)


def _add_user(user_id: str, username: str, role: str, password: str = "ReadinessUser@2026") -> None:
    c = backend.db()
    c.execute(
        """INSERT INTO users(id,username,display_name,role,password_hash,
                              must_change_password,active,created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
        (user_id, username, username, role, backend.hash_password(password), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def _login(client: TestClient, username: str, password: str = "ReadinessUser@2026") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": f"device-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _write_headers(token: str, operation: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-Id": str(uuid.uuid4()),
        "Idempotency-Key": operation,
    }


def _legacy_database() -> None:
    backend.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(backend.DB_PATH)
    c.executescript(
        """
        CREATE TABLE users(
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL, role TEXT NOT NULL,
            password_hash TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE TABLE materials(
            id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            specification TEXT, unit TEXT NOT NULL, batch_no TEXT, expiry_date TEXT,
            total_quantity INTEGER NOT NULL DEFAULT 0, available_quantity INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE locations(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
        CREATE TABLE inventory(
            id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity >= 0), UNIQUE(material_id, location_id)
        );
        CREATE TABLE transfer_requests(
            id TEXT PRIMARY KEY, client_operation_id TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
            document_no TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT,
            approved_at TEXT, executed_at TEXT
        );
        CREATE TABLE audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, operator_id TEXT, role TEXT,
            action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT,
            request_id TEXT, occurred_at TEXT NOT NULL, result TEXT NOT NULL
        );
        CREATE TABLE production_orders(
            id TEXT PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, model_name TEXT,
            status TEXT NOT NULL DEFAULT 'IN_PROGRESS', created_at TEXT NOT NULL
        );
        CREATE TABLE order_material_requirements(
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL, material_id TEXT NOT NULL,
            required_quantity INTEGER NOT NULL CHECK(required_quantity >= 0),
            arrived_quantity INTEGER NOT NULL DEFAULT 0 CHECK(arrived_quantity >= 0),
            UNIQUE(order_id, material_id)
        );
        CREATE TABLE login_attempts(
            username TEXT PRIMARY KEY, failed_count INTEGER NOT NULL DEFAULT 0,
            first_failed_at INTEGER NOT NULL, locked_until INTEGER
        );
        """
    )
    timestamp = backend.now()
    c.execute(
        "INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
        ("u_legacy_admin", "legacy_admin", "遗留管理员", "MATERIAL_CLERK",
         backend.hash_password("LegacyAdmin@2026"), 0, 1, timestamp),
    )
    c.execute(
        "INSERT INTO materials VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("mat_legacy", "MTR-LEGACY-001", "遗留物料", "SPEC", "件", None, None, 20, 20, 1),
    )
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?)",
              ("ord_legacy", "SO-LEGACY-001", "旧型号", "IN_PROGRESS", timestamp))
    c.execute(
        "INSERT INTO order_material_requirements VALUES(?,?,?,?,?)",
        ("omr_legacy", "ord_legacy", "mat_legacy", 20, 5),
    )
    c.commit()
    c.close()


def test_legacy_schema_migrates_and_repeated_initialization_is_idempotent():
    _legacy_database()

    backend.init_db()
    c = backend.db()
    columns = {
        row["name"] for row in c.execute("PRAGMA table_info(order_material_requirements)").fetchall()
    }
    migrated = c.execute(
        "SELECT * FROM order_material_requirements WHERE id='omr_legacy'"
    ).fetchone()
    order = c.execute(
        "SELECT product_name,updated_at FROM production_orders WHERE id='ord_legacy'"
    ).fetchone()
    legacy_table = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='order_material_requirements_legacy'"
    ).fetchone()
    assert {
        "device_id", "in_stock_quantity", "status_code", "created_at", "updated_at",
    } <= columns
    assert migrated is not None
    assert migrated["device_id"] is None
    assert migrated["required_quantity"] == 20
    assert migrated["arrived_quantity"] == 5
    assert migrated["in_stock_quantity"] == 0
    assert migrated["status_code"] == "ARRIVED"
    assert order["product_name"] == "旧型号"
    assert order["updated_at"]
    assert legacy_table is None
    c.close()

    backend.init_db()
    c = backend.db()
    assert c.execute("SELECT COUNT(*) FROM production_orders WHERE id='ord_legacy'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM order_material_requirements WHERE id='omr_legacy'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='26B-013'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM order_devices WHERE order_id='ord_demo_26b013'").fetchone()[0] == 25
    assert c.execute(
        "SELECT COUNT(*) FROM order_material_requirements WHERE order_id='ord_demo_26b013'"
    ).fetchone()[0] == 75
    c.close()


def test_two_startup_processes_can_initialize_the_same_database_once():
    data_dir = Path(tempfile.mkdtemp(prefix="mf_parallel_startup_")) / "data"
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import main
main.init_db()
"""
    child_env = os.environ.copy()
    child_env["MATERIAL_FLOW_DATA"] = str(data_dir)
    child_env["MATERIAL_FLOW_UPLOADS"] = str(data_dir.parent / "uploads")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(APP_PATH.parent)],
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]
    assert all(process.returncode == 0 for process in processes), results

    c = sqlite3.connect(data_dir / "material_flow.db")
    assert c.execute("SELECT COUNT(*) FROM users WHERE username='owlco'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM production_orders WHERE order_no='26B-013'").fetchone()[0] == 1
    assert c.execute(
        "SELECT COUNT(*) FROM order_material_requirements "
        "WHERE order_id='ord_demo_26b013'"
    ).fetchone()[0] == 75
    c.close()


def test_health_check_reports_database_and_never_exposes_storage_details():
    with TestClient(backend.app) as client:
        healthy = client.get("/healthz")
        assert healthy.status_code == 200, healthy.text
        assert healthy.json()["status"] == "ok"
        assert healthy.json()["database"] == "ok"
        assert healthy.json()["version"] == backend.APP_VERSION
        assert str(backend.DB_PATH) not in healthy.text

        original_db = backend.db

        def broken_db():
            raise sqlite3.OperationalError("private database path")

        backend.db = broken_db
        try:
            unhealthy = client.get("/healthz")
        finally:
            backend.db = original_db
        assert unhealthy.status_code == 503
        assert unhealthy.json() == {
            "status": "unhealthy",
            "service": "material-flow",
            "version": backend.APP_VERSION,
        }
        assert "private database path" not in unhealthy.text
        assert str(backend.DB_PATH) not in unhealthy.text


def test_package_version_is_the_health_check_version_identifier():
    package_version = VERSION_PATH.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", package_version)
    assert backend.APP_VERSION == package_version
    assert backend.app.version == package_version

    with TestClient(backend.app) as client:
        response = client.get("/healthz")
        openapi = client.get("/openapi.json")
    assert response.json()["version"] == package_version
    assert openapi.json()["info"]["version"] == package_version

    deployment = DEPLOYMENT_PATH.read_text(encoding="utf-8")
    assert "`FastAPI.version`, the `/openapi.json` document" in deployment
    assert "contents of `VERSION`" in deployment


def test_release_documentation_replays_version_and_systemd_contract():
    package_version = VERSION_PATH.read_text(encoding="ascii").strip()
    deployment = DEPLOYMENT_PATH.read_text(encoding="ascii")
    service = SERVICE_PATH.read_text(encoding="ascii")

    with TestClient(backend.app) as client:
        health = client.get("/healthz")

    assert health.status_code == 200, health.text
    assert health.json()["version"] == package_version
    assert health.json()["service"] == "material-flow"
    normalized_deployment = " ".join(deployment.split())
    assert package_version not in normalized_deployment
    assert "`VERSION` is the only release version source" in normalized_deployment
    assert "The systemd unit does not define a separate version" in normalized_deployment
    assert "# app.main reads VERSION from this package; keep the unit versionless." in service
    assert package_version not in service
    assert MIGRATION_COMMAND in service
    assert SERVER_COMMAND in service
    assert MIGRATION_COMMAND in deployment
    assert SERVER_COMMAND in deployment


def test_explicit_migration_entrypoint_is_repeatable():
    data_dir = Path(tempfile.mkdtemp(prefix="mf_explicit_migration_")) / "data"
    child_env = os.environ.copy()
    child_env["MATERIAL_FLOW_DATA"] = str(data_dir)
    child_env["MATERIAL_FLOW_UPLOADS"] = str(data_dir.parent / "uploads")
    child_env["INITIAL_ADMIN_PASSWORD"] = "MigrationAdmin@2026"
    results = [
        subprocess.run(
            [sys.executable, "-m", "app.migrate"],
            cwd=BACKEND_ROOT,
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )
        for _ in range(2)
    ]
    assert all(result.returncode == 0 for result in results), [
        result.stderr or result.stdout for result in results
    ]

    connection = sqlite3.connect(data_dir / "material_flow.db")
    try:
        for table in (
            "users", "production_orders", "order_devices", "order_material_requirements"
        ):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE username='owlco'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM production_orders WHERE order_no='26B-013'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM order_devices WHERE order_id='ord_demo_26b013'"
        ).fetchone()[0] == 25
        assert connection.execute(
            "SELECT COUNT(*) FROM order_material_requirements "
            "WHERE order_id='ord_demo_26b013'"
        ).fetchone()[0] == 75
    finally:
        connection.close()


def test_deployment_inputs_are_pinned_and_service_uses_canonical_entries():
    requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    test_requirements = (BACKEND_ROOT / "requirements-test.txt").read_text(encoding="utf-8")
    runtime_lines = [
        line for line in requirements.splitlines()
        if line and not line.startswith("#")
    ]
    test_lines = [
        line for line in test_requirements.splitlines()
        if line and not line.startswith("#") and not line.startswith("-r ")
    ]
    assert all("==" in line for line in runtime_lines)
    assert all("==" in line for line in test_lines)
    assert {"fastapi", "uvicorn", "python-multipart", "argon2-cffi"} <= {
        line.split("==", 1)[0].split("[", 1)[0]
        for line in runtime_lines
    }
    assert "-r requirements.txt" in test_requirements

    service = SERVICE_PATH.read_text(encoding="ascii")
    service_lines = service.splitlines()
    migration_line = next(
        i for i, line in enumerate(service_lines) if line.startswith("ExecStartPre=")
    )
    start_line = next(i for i, line in enumerate(service_lines) if line.startswith("ExecStart="))
    assert migration_line < start_line
    assert sum(line.startswith("ExecStartPre=") for line in service_lines) == 1
    assert sum(line.startswith("ExecStart=") for line in service_lines) == 1
    assert (
        MIGRATION_COMMAND
        in service
    )
    assert (
        SERVER_COMMAND
    ) in service
    assert "--host 0.0.0.0" not in service


def test_missing_initial_admin_configuration_allows_setup_initialization():
    data_dir = Path(tempfile.mkdtemp(prefix="mf_missing_admin_")) / "data"
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import main
from fastapi.testclient import TestClient
import uuid
main.init_db()
with TestClient(main.app) as client:
    status = client.get('/api/v1/setup/status')
    assert status.status_code == 200 and status.json()['initialized'] is False
    key = str(uuid.uuid4())
    response = client.post('/api/v1/setup/initialize-admin', json={
        'password': 'SetupAdmin@2026', 'confirmPassword': 'SetupAdmin@2026',
        'clientOperationId': key,
    }, headers={'X-Request-Id': str(uuid.uuid4()), 'Idempotency-Key': key})
    assert response.status_code == 200 and response.json()['mustChangePassword'] is True
    final_status = client.get('/api/v1/setup/status')
    assert final_status.json()['initialized'] is True
    assert final_status.json()['adminUsername'] == 'owlco'
    assert final_status.json()['mustChangePassword'] is True
"""
    child_env = os.environ.copy()
    child_env.pop("INITIAL_ADMIN_PASSWORD", None)
    child_env["MATERIAL_FLOW_DATA"] = str(data_dir)
    child_env["MATERIAL_FLOW_UPLOADS"] = str(data_dir.parent / "uploads")
    result = subprocess.run(
        [sys.executable, "-c", script, str(APP_PATH.parent)],
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_concurrent_transfer_and_handover_retries_commit_one_audit_path():
    with TestClient(backend.app) as client:
        suffix = uuid.uuid4().hex[:10]
        material_user = f"readiness_material_{suffix}"
        warehouse_user = f"readiness_warehouse_{suffix}"
        operator_user = f"readiness_operator_{suffix}"
        _add_user(f"u_material_{suffix}", material_user, "MATERIAL")
        _add_user(f"u_warehouse_{suffix}", warehouse_user, "WAREHOUSE_ADMIN")
        _add_user(f"u_warehouse_2_{suffix}", f"{warehouse_user}_2", "WAREHOUSE_ADMIN")
        _add_user(f"u_operator_{suffix}", operator_user, "OPERATOR")
        material_token = _login(client, material_user)
        warehouse_token = _login(client, warehouse_user)
        warehouse_2_token = _login(client, f"{warehouse_user}_2")
        operator_token = _login(client, operator_user)

        c = backend.db()
        c.execute(
            "UPDATE materials SET available_quantity=5,total_quantity=5,version=1 "
            "WHERE id='mat_ctl_cabinet'"
        )
        c.commit()
        c.close()

        transfer_operation = str(uuid.uuid4())
        transfer_payload = {
            "clientOperationId": transfer_operation,
            "type": "OUTBOUND",
            "documentNo": "26B-013",
            "items": [{
                "materialId": "mat_ctl_cabinet",
                "quantity": 1,
                "expectedInventoryVersion": 1,
            }],
        }

        def create_transfer():
            return client.post(
                "/api/v1/transfer-requests", json=transfer_payload,
                headers=_write_headers(material_token, transfer_operation),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            transfer_responses = list(pool.map(lambda _: create_transfer(), range(2)))
        assert all(response.status_code == 200 for response in transfer_responses), [
            response.text for response in transfer_responses
        ]
        transfer_ids = {response.json()["requestId"] for response in transfer_responses}
        assert len(transfer_ids) == 1
        transfer_id = transfer_ids.pop()

        approval_operation = str(uuid.uuid4())
        approval = client.post(
            f"/api/v1/transfer-requests/{transfer_id}/approve",
            json={"decision": "APPROVE", "clientOperationId": approval_operation},
            headers=_write_headers(warehouse_token, approval_operation),
        )
        assert approval.status_code == 200, approval.text

        c = backend.db()
        assert c.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE resource_type='TRANSFER_REQUEST' "
            "AND resource_id=? AND action='CREATE'", (transfer_id,)
        ).fetchone()[0] == 1
        c.close()

        execute_operations = [str(uuid.uuid4()), str(uuid.uuid4())]

        def execute_transfer(operation: str, token: str):
            return client.post(
                f"/api/v1/transfer-requests/{transfer_id}/execute",
                json={"clientOperationId": operation},
                headers=_write_headers(token, operation),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            execute_responses = list(pool.map(
                execute_transfer,
                execute_operations,
                [warehouse_2_token, _login(client, "owlco", "ReadinessAdmin@2026")],
            ))
        assert sorted(response.status_code for response in execute_responses) == [200, 409], [
            response.text for response in execute_responses
        ]
        c = backend.db()
        assert tuple(c.execute(
            "SELECT available_quantity,version FROM materials WHERE id='mat_ctl_cabinet'"
        ).fetchone()) == (4, 2)
        assert c.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE resource_type='TRANSFER_REQUEST' "
            "AND resource_id=? AND action='EXECUTE'", (transfer_id,)
        ).fetchone()[0] == 1
        c.close()

        handover_operation = str(uuid.uuid4())
        handover_payload = {
            "workItemId": "omr_demo_dev_demo_HZ01_mat_ctl_cabinet",
            "transferRequestId": transfer_id,
            "quantity": 1,
            "fromLocation": "A-01-03",
            "deviceId": "dev_demo_HZ01",
            "receiverUserId": f"u_operator_{suffix}",
            "clientOperationId": handover_operation,
        }

        def create_handover():
            return client.post(
                "/api/v1/handovers", json=handover_payload,
                headers=_write_headers(material_token, handover_operation),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            handover_responses = list(pool.map(lambda _: create_handover(), range(2)))
        assert all(response.status_code == 200 for response in handover_responses), [
            response.text for response in handover_responses
        ]
        handover_ids = {response.json()["handoverId"] for response in handover_responses}
        assert len(handover_ids) == 1
        handover_id = handover_ids.pop()

        decision_operation = str(uuid.uuid4())
        decision_payload = {
            "clientOperationId": decision_operation,
            "requestId": str(uuid.uuid4()),
        }

        def confirm_handover():
            return client.post(
                f"/api/v1/handovers/{handover_id}/confirm",
                json=decision_payload,
                headers=_write_headers(operator_token, decision_operation),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            decision_responses = list(pool.map(lambda _: confirm_handover(), range(2)))
        assert all(response.status_code == 200 for response in decision_responses), [
            response.text for response in decision_responses
        ]
        assert sum(response.json().get("idempotent", False) for response in decision_responses) == 1

        c = backend.db()
        assert c.execute(
            "SELECT COUNT(*) FROM material_handovers WHERE id=?", (handover_id,)
        ).fetchone()[0] == 1
        assert c.execute(
            "SELECT status FROM material_handovers WHERE id=?", (handover_id,)
        ).fetchone()[0] == "CONFIRMED"
        assert c.execute(
            "SELECT COUNT(*) FROM handover_operations WHERE client_operation_id=?",
            (decision_operation,),
        ).fetchone()[0] == 1
        assert [row[0] for row in c.execute(
            "SELECT event_type FROM audit_events WHERE entity_id=? ORDER BY id", (handover_id,)
        ).fetchall()] == [
            "HANDOVER_CREATED", "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP",
            "MATERIAL_AT_STATION",
        ]
        c.close()


def test_validation_errors_do_not_echo_secrets_or_raw_input():
    with TestClient(backend.app) as client:
        secret = "do-not-echo-this-secret"
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "safe", "password": secret, "deviceId": secret + "x" * 200},
            headers={"X-Request-Id": "not-a-uuid"},
        )
        assert response.status_code == 422
        assert secret not in response.text
        assert "input" not in response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
