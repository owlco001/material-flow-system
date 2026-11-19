"""Behavior and query-budget coverage for the registered assembly task-list route."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


APP_DIR = Path(__file__).resolve().parents[1] / "app"
_TEST_ROOT = tempfile.mkdtemp(prefix="mf_assembly_list_")
_ISOLATION_ENV = ("MATERIAL_FLOW_DATA", "MATERIAL_FLOW_UPLOADS")
_PREVIOUS_ISOLATION_ENV = {key: os.environ.get(key) for key in _ISOLATION_ENV}
os.environ["MATERIAL_FLOW_DATA"] = f"{_TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{_TEST_ROOT}/uploads"
sys.path.insert(0, str(APP_DIR))
spec = importlib.util.spec_from_file_location("assembly_list_performance_backend", APP_DIR / "main.py")
assert spec and spec.loader
backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend
try:
    spec.loader.exec_module(backend)
finally:
    for _key, _value in _PREVIOUS_ISOLATION_ENV.items():
        if _value is None:
            os.environ.pop(_key, None)
        else:
            os.environ[_key] = _value

PASSWORD = "AssemblyList@2026"
ASSEMBLY_LIST_SELECT_BUDGET = 8


def _add_user(user_id: str, role: str) -> None:
    connection = backend.db()
    connection.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (user_id, user_id, user_id, role, backend.hash_password(PASSWORD), 0, 1, backend.now()),
    )
    connection.commit()
    connection.close()


def _login(client: TestClient, user_id: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": user_id, "password": PASSWORD, "deviceId": f"test-{user_id}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _insert_task(task_id: str, created_at: str, assigned_id: str | None = None) -> None:
    connection = backend.db()
    connection.execute(
        """INSERT INTO assembly_tasks
        (id,order_no,device_id,device_no,assigned_assembler_id,status,progress_stage,
         task_version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (task_id, f"WO-{task_id}", f"device-{task_id}", f"NO-{task_id}", assigned_id,
         "MATERIAL_ACCEPTED", 0, 1, created_at, created_at),
    )
    connection.commit()
    connection.close()


def _insert_member(
    task_id: str,
    assembler_id: str,
    role: str,
    assigned_at: str,
    assigned_by: str,
    removed_at: str | None = None,
) -> None:
    connection = backend.db()
    connection.execute(
        """INSERT INTO assembly_task_members
        (task_id,assembler_id,assignment_role,assigned_by,assigned_at,removed_at)
        VALUES(?,?,?,?,?,?)""",
        (task_id, assembler_id, role, assigned_by, assigned_at, removed_at),
    )
    connection.commit()
    connection.close()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_registered_list_route_preserves_permissions_desc_order_and_response_contract():
    backend.init_db()
    for user_id, role in (
        ("list-viewer", "ASSEMBLER"),
        ("list-outsider", "ASSEMBLER"),
        ("list-operator", "OPERATOR"),
        ("list-a", "ASSEMBLER"),
        ("list-z", "ASSEMBLER"),
        ("list-removed", "ASSEMBLER"),
        ("list-supervisor", "WORKSHOP_SUPERVISOR"),
    ):
        _add_user(user_id, role)

    # The application must expose exactly one GET implementation for this path.
    list_routes = [
        route for route in backend.app.routes
        if getattr(route, "path", None) == "/api/v1/assembly/tasks"
        and "GET" in getattr(route, "methods", set())
    ]
    assert len(list_routes) == 1

    _insert_task("task-multi", "2026-01-01T00:00:00", "list-viewer")
    _insert_member("task-multi", "list-viewer", "LEAD", "2026-01-01T00:00:01", "list-supervisor")
    _insert_member("task-multi", "list-a", "MEMBER", "2026-01-01T00:00:02", "list-supervisor")
    _insert_member("task-multi", "list-z", "MEMBER", "2026-01-01T00:00:03", "list-supervisor")
    _insert_member(
        "task-multi", "list-removed", "MEMBER", "2026-01-01T00:00:04", "list-supervisor",
        "2026-01-02T00:00:00",
    )

    # The effective main.py route returns active membership rows as stored; it does
    # not synthesize legacy assigned_assembler_id into `members`.
    _insert_task("task-legacy", "2026-01-02T00:00:00", "list-viewer")
    _insert_task("task-member", "2026-01-03T00:00:00")
    _insert_member("task-member", "list-viewer", "MEMBER", "2026-01-03T00:00:01", "list-supervisor")
    _insert_task("task-removed", "2026-01-04T00:00:00")
    _insert_member(
        "task-removed", "list-viewer", "MEMBER", "2026-01-04T00:00:01", "list-supervisor",
        "2026-01-05T00:00:00",
    )
    _insert_task("task-outsider", "2026-01-05T00:00:00", "list-outsider")

    with TestClient(backend.app) as client:
        viewer = _login(client, "list-viewer")
        outsider = _login(client, "list-outsider")
        operator = _login(client, "list-operator")
        supervisor = _login(client, "list-supervisor")

        first_page = client.get("/api/v1/assembly/tasks?page=1&pageSize=2", headers=_auth(viewer))
        assert first_page.status_code == 200, first_page.text
        body = first_page.json()
        assert body["total"] == 3
        assert body["totalPages"] == 2
        assert [task["id"] for task in body["items"]] == ["task-member", "task-legacy"]
        assert {"items", "page", "pageSize", "total", "totalPages", "serverTime", "generatedAt"} <= body.keys()

        # Main route member order is assignment_role then insertion rowid.
        multi_page = client.get("/api/v1/assembly/tasks?page=2&pageSize=2", headers=_auth(viewer))
        assert multi_page.status_code == 200, multi_page.text
        multi = multi_page.json()["items"][0]
        assert multi["id"] == "task-multi"
        assert [member["assembler_id"] for member in multi["members"]] == [
            "list-viewer", "list-a", "list-z",
        ]
        assert [member["assignment_role"] for member in multi["members"]] == ["LEAD", "MEMBER", "MEMBER"]
        assert all("assemblerId" in member and "assemblerName" in member for member in multi["members"])
        assert "list-removed" not in {member["assembler_id"] for member in multi["members"]}

        legacy_item = body["items"][1]
        assert legacy_item["assignedAssemblerId"] == "list-viewer"
        assert legacy_item["members"] == []

        empty_page = client.get("/api/v1/assembly/tasks?page=9&pageSize=2", headers=_auth(viewer))
        assert empty_page.status_code == 200, empty_page.text
        assert empty_page.json()["items"] == []
        assert empty_page.json()["total"] == 3

        outsider_list = client.get("/api/v1/assembly/tasks", headers=_auth(outsider))
        assert outsider_list.status_code == 200, outsider_list.text
        assert [task["id"] for task in outsider_list.json()["items"]] == ["task-outsider"]
        assert client.get("/api/v1/assembly/tasks", headers=_auth(operator)).status_code == 403

        supervisor_list = client.get("/api/v1/assembly/tasks?pageSize=100", headers=_auth(supervisor))
        assert supervisor_list.status_code == 200, supervisor_list.text
        assert [task["id"] for task in supervisor_list.json()["items"]] == [
            "task-outsider", "task-removed", "task-member", "task-legacy", "task-multi",
        ]


def _assembly_list_select_count(statements: list[str]) -> int:
    """Count endpoint SELECTs, excluding auth/user lookups unrelated to list assembly."""
    endpoint_tables = (
        "assembly_tasks", "assembly_task_stages", "assembly_task_members", "labor_records",
    )
    count = 0
    for sql in statements:
        normalized = sql.lstrip().upper()
        if not normalized.startswith(("SELECT", "WITH")):
            continue
        lowered = sql.lower()
        if any(table in lowered for table in endpoint_tables) or "from users where id in" in lowered:
            count += 1
    return count


def test_registered_list_query_count_is_constant_for_synthetic_1_and_20_task_pages(monkeypatch, record_property):
    backend.init_db()
    _add_user("perf-supervisor", "WORKSHOP_SUPERVISOR")
    _add_user("perf-assembler", "ASSEMBLER")
    for index in range(20):
        task_id = f"perf-task-{index:02d}"
        timestamp = f"2026-02-{index + 1:02d}T00:00:00"
        _insert_task(task_id, timestamp, "perf-assembler")
        _insert_member(task_id, "perf-assembler", "LEAD", timestamp, "perf-supervisor")

    statements: list[str] = []
    real_connect = backend.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(backend.sqlite3, "connect", traced_connect)

    with TestClient(backend.app) as client:
        supervisor = _login(client, "perf-supervisor")
        statements.clear()
        one_task = client.get("/api/v1/assembly/tasks?pageSize=1", headers=_auth(supervisor))
        one_count = _assembly_list_select_count(statements)
        assert one_task.status_code == 200, one_task.text
        assert len(one_task.json()["items"]) == 1

        statements.clear()
        twenty_tasks = client.get("/api/v1/assembly/tasks?pageSize=100", headers=_auth(supervisor))
        twenty_count = _assembly_list_select_count(statements)
        assert twenty_tasks.status_code == 200, twenty_tasks.text
        assert len(twenty_tasks.json()["items"]) == 20

    record_property("assembly_list_current_1_task_selects", one_count)
    record_property("assembly_list_current_20_task_selects", twenty_count)
    assert one_count == twenty_count, (
        f"registered list query count grew with N: N=1 had {one_count}, N=20 had {twenty_count}"
    )
    assert 1 <= twenty_count <= ASSEMBLY_LIST_SELECT_BUDGET, (
        f"expected the registered route to remain within {ASSEMBLY_LIST_SELECT_BUDGET} SELECTs; got {twenty_count}"
    )
