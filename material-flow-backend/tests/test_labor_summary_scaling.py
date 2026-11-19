"""Workshop labor attribution and SQLite work budgets against the real handler."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import re

import pytest


REPORT = "/api/v1/workshop/labor-summary"
ADMIN = {"id": "fixture-admin", "role": "ADMIN"}
STAMP = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def labor_backend(monkeypatch, tmp_path):
    data = tmp_path / "data"
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("MATERIAL_FLOW_DATA", str(data))
    monkeypatch.setenv("MATERIAL_FLOW_UPLOADS", str(uploads))
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "")
    from app import main

    # In the full suite, another module may already have imported app.main.
    monkeypatch.setattr(main, "DATA_DIR", data)
    monkeypatch.setattr(main, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(main, "DB_PATH", data / "labor.db")
    monkeypatch.setattr(main, "INITIAL_ADMIN_PASSWORD", "")
    main.init_db()
    return main


def _endpoint(backend):
    return next(route.endpoint for route in backend.app.routes
                if getattr(route, "path", None) == REPORT and "GET" in route.methods)


def _report(backend, *, page=1, deviceId=None, orderNo=None, assemblerId=None):
    return _endpoint(backend)(page=page, pageSize=20, deviceId=deviceId,
                              orderNo=orderNo, assemblerId=assemblerId, user=ADMIN)


def _worker(db, worker, name):
    db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
               (worker, worker, name, "ASSEMBLER", "unused", 0, 1, STAMP))


def _task(db, task, order, device, number):
    db.execute("""INSERT INTO assembly_tasks
        (id,order_no,device_id,device_no,status,progress_stage,task_version,created_at,updated_at)
        VALUES(?,?,?,?,'IN_PROGRESS',1,1,?,?)""", (task, order, device, number, STAMP, STAMP))


def _labor(db, record, task, worker, minutes, *, kind="ASSEMBLY", started=STAMP):
    status = "ACTIVE" if minutes is None else "COMPLETED"
    ended = None if minutes is None else STAMP
    db.execute("""INSERT INTO labor_records
        (id,task_id,worker_user_id,type,status,started_at,ended_at,duration_minutes,client_operation_id,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (record, task, worker, kind, status, started, ended, minutes, record, STAMP))


def _transfer(db, record, source, worker):
    db.execute("""INSERT INTO temporary_transfers
        (id,worker_user_id,source_task_id,labor_record_id,status,started_at,ended_at,remark,client_operation_id)
        VALUES(?,?,?,?,'COMPLETED',?,?,?,?)""",
               (record, worker, source, record, STAMP, STAMP, "fixture", record))


def _expected(task, order, device, number, worker, name, assembly, transfer):
    return {
        "taskId": task, "orderNo": order, "deviceId": device, "deviceNo": number,
        "assemblerId": worker, "assemblerName": name,
        "assemblyLaborMinutes": assembly, "temporaryTransferLaborMinutes": transfer,
        "totalLaborMinutes": assembly + transfer,
    }


def test_attribution_filters_and_export_match_all_pages(labor_backend, monkeypatch):
    backend = labor_backend
    instant = datetime(2000, 1, 1, 0, 9, 30, tzinfo=timezone.utc)

    class ReportClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    # register() captured backend.now, whose module still resolves datetime at call time.
    monkeypatch.setattr(backend, "datetime", ReportClock)
    with closing(backend.db()) as db:
        _worker(db, "w1", "Worker One")
        _worker(db, "w2", "Worker Two")
        _task(db, "a", "ORDER-A", "dev-a", "01")
        _task(db, "b", "ORDER-B", "dev-b", "02")
        _task(db, "empty", "ORDER-A", "dev-a", "01")
        _labor(db, "assembly", "a", "w1", 11)
        _labor(db, "active", "a", "w1", None)
        _labor(db, "equal-1", "a", "w2", 3)
        _labor(db, "equal-2", "a", "w2", 3)
        _labor(db, "another-worker", "b", "w2", 2)
        for record, target, source, worker, minutes in (
            ("same-task", "a", "a", "w1", 7),
            ("cross-order", "b", "a", "w1", 13),
            ("null-source", "a", None, "w2", 5),
            ("source-only", None, "a", "w2", 17),
            ("unattributed", None, None, "w2", 19),
        ):
            _labor(db, record, target, worker, minutes, kind="TEMPORARY_TRANSFER")
            _transfer(db, record, source, worker)
        for i in range(21):
            task = f"extra-{i:02}"
            _task(db, task, "ORDER-A", "dev-extra", "03")
            _labor(db, task, task, "w1", 1)
        db.commit()

    expected = [
        _expected("a", "ORDER-A", "dev-a", "01", "w1", "Worker One", 20, 20),
        _expected("a", "ORDER-A", "dev-a", "01", "w2", "Worker Two", 6, 22),
        _expected("b", "ORDER-B", "dev-b", "02", "w1", "Worker One", 0, 13),
        _expected("b", "ORDER-B", "dev-b", "02", "w2", "Worker Two", 2, 0),
        *(_expected(f"extra-{i:02}", "ORDER-A", "dev-extra", "03", "w1", "Worker One", 1, 0)
          for i in range(21)),
    ]
    export = backend.app.state.workshop_labor_summary_export
    for filters in (
        {}, {"deviceId": "dev-a"}, {"deviceId": "dev-b"},
        {"orderNo": "ORDER-A"}, {"orderNo": "ORDER-B"}, {"assemblerId": "w2"},
        {"deviceId": "dev-a", "orderNo": "ORDER-A", "assemblerId": "w1"},
        {"deviceId": "dev-b", "orderNo": "ORDER-A"},
    ):
        filtered = [item for item in expected if all(item[key] == value for key, value in filters.items())]
        pages = (len(filtered) + 19) // 20
        combined = []
        for page in range(1, pages + 2):  # Include the first empty page.
            expected_items = filtered[(page - 1) * 20:page * 20]
            result = _report(backend, page=page, **filters)
            assert result == {
                "items": expected_items, "page": page, "pageSize": 20,
                "total": len(filtered), "totalPages": pages,
                "totalLaborMinutes": sum(item["totalLaborMinutes"] for item in expected_items),
            }
            combined.extend(result["items"])
        assert export(ADMIN, **filters) == combined == filtered

    instant += timedelta(minutes=5)
    updated = _report(backend, deviceId="dev-a", assemblerId="w1")
    assert updated["items"] == [dict(expected[0], assemblyLaborMinutes=25, totalLaborMinutes=45)]
    assert updated["totalLaborMinutes"] == 45
    assert export(ADMIN, deviceId="dev-a", assemblerId="w1") == updated["items"]
    with closing(backend.db()) as db:
        active = db.execute("SELECT status, ended_at, duration_minutes FROM labor_records WHERE id='active'").fetchone()
        assert tuple(active) == ("ACTIVE", None, None)


def test_workshop_labor_summary_sqlite_vm_scales_subquadratically(labor_backend, monkeypatch):
    backend = labor_backend
    original_open = backend._open_db
    vm_counts = []
    measured_queries = []
    for size in (100, 1000):
        monkeypatch.setattr(backend, "DB_PATH", backend.DATA_DIR / f"labor-scale-{size}.db")
        backend.init_db()
        with closing(backend.db()) as db:
            _worker(db, "worker", "Synthetic worker")
            for i in range(size):
                _task(db, f"task-{i:04}", "AUDIT-LABOR", f"device-{i}", f"D{i}")
            for i in range(size):
                for j in range(4):
                    record = f"labor-{i}-{j}"
                    transfer = j == 3 and i % 10 == 0
                    _labor(db, record, f"task-{i:04}", "worker", 15,
                           kind="TEMPORARY_TRANSFER" if transfer else "ASSEMBLY")
                    if transfer:
                        _transfer(db, record, f"task-{(i+1) % size:04}", "worker")
            db.commit()

        statements = []
        steps = 0

        def progress():
            nonlocal steps
            steps += 1000
            return 0

        def instrumented_open(**kwargs):
            db = original_open(**kwargs)
            db.set_trace_callback(statements.append)
            db.set_progress_handler(progress, 1000)
            return db

        with monkeypatch.context() as patch:
            patch.setattr(backend, "_open_db", instrumented_open)
            result = _report(backend, orderNo="AUDIT-LABOR")
        assert result["total"] == size
        assert result["totalPages"] == size // 20
        assert len(result["items"]) == 20
        assert result["totalLaborMinutes"] == 20 * 60 + 2 * 15
        queries = [sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert len(queries) == 2
        vm_counts.append(steps)
        measured_queries.extend(queries)

    small, large = vm_counts
    # Baseline audit: ~716k / ~67.3m VM instructions. Allow room for SQLite
    # planner/version variation, while rejecting quadratic growth and large fixed work.
    assert 0 < small and large <= 25 * small and large <= 5_000_000, vm_counts
    counts = [sql for sql in measured_queries if re.search(r"\bCOUNT\s*\(", sql, re.I)]
    assert len(counts) == 2
    for sql in counts:
        assert not re.search(r"\b(?:SUM\s*\(|ORDER\s+BY\b)", sql, re.I), sql
