"""Measured query-plan and VM budget for document-scoped transfer lists."""
from __future__ import annotations

from contextlib import closing
from statistics import median
from time import perf_counter
import uuid

import pytest

from app import main as backend


INITIAL_ADMIN_PASSWORD = "IndexBudget@2026"
INDEX_NAME = "idx_transfer_requests_document_status_created"
ALT_INDEX_NAME = "idx_transfer_requests_document_created_trial"


@pytest.fixture
def seeded_transfers(monkeypatch, tmp_path):
    data = tmp_path / "index-data"
    uploads = tmp_path / "index-uploads"
    monkeypatch.setattr(backend, "DATA_DIR", data)
    monkeypatch.setattr(backend, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(backend, "DB_PATH", data / "material_flow.db")
    monkeypatch.setattr(backend, "INITIAL_ADMIN_PASSWORD", INITIAL_ADMIN_PASSWORD)
    backend.init_db()
    with closing(backend.db()) as c:
        admin = c.execute("SELECT * FROM users WHERE username='owlco'").fetchone()
        assert admin is not None
        rows = []
        for i in range(20_000):
            document = f"DOC-{i // 10:04d}"
            status = ("PENDING_APPROVAL", "APPROVED", "REJECTED", "EXECUTED")[i % 4]
            # Equal timestamps exercise the id tie-breaker. IDs sort in the
            # opposite direction to creation order, making page ordering visible.
            created = f"2023-11-{(i // 86400) % 28 + 1:02d}T00:00:{i % 60:02d}+00:00"
            rows.append((
                f"index-transfer-{i:05d}", str(uuid.uuid4()), "OUTBOUND", document,
                status, '{"items":[{"materialId":"synthetic","quantity":2}]}',
                admin["id"], created,
            ))
        c.executemany(
            """INSERT INTO transfer_requests
               (id,client_operation_id,type,document_no,status,payload_json,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            rows,
        )
        c.commit()
    return admin


def _statements(document_nos, status=None):
    clauses = []
    args = []
    if status:
        clauses.append("t.status=?")
        args.append(status)
    placeholders = ",".join("?" for _ in document_nos)
    clauses.append(f"t.document_no IN ({placeholders})")
    args.extend(document_nos)
    where = " AND ".join(clauses)
    count_sql = f"SELECT COUNT(*) FROM transfer_requests t WHERE {where}"
    rows_sql = f"""SELECT t.id,t.client_operation_id,t.type,t.document_no,t.status,
                            t.payload_json,t.created_by,t.created_at,t.approved_by,t.approved_at,t.executed_at
                       FROM transfer_requests t WHERE {where}
                       ORDER BY t.created_at DESC, t.id DESC LIMIT 7 OFFSET 0"""
    return count_sql, rows_sql, args


def _measure(connection, document_nos, status=None):
    count_sql, rows_sql, args = _statements(document_nos, status)
    plans = {
        "count": [r[3] for r in connection.execute("EXPLAIN QUERY PLAN " + count_sql, args)],
        "rows": [r[3] for r in connection.execute("EXPLAIN QUERY PLAN " + rows_sql, args)],
    }
    samples = []
    elapsed = []
    outcomes = None

    def run_queries():
        total = connection.execute(count_sql, args).fetchone()[0]
        rows = connection.execute(rows_sql, args).fetchall()
        return total, [row["id"] for row in rows]

    for _ in range(3):
        vm_steps = 0

        def progress():
            nonlocal vm_steps
            vm_steps += 1
            return 0

        # One-instruction progress callbacks give an exact deterministic VM count.
        connection.set_progress_handler(progress, 1)
        try:
            value = run_queries()
        finally:
            connection.set_progress_handler(None, 0)
        samples.append(vm_steps)
        started = perf_counter()
        timed_value = run_queries()
        elapsed.append((perf_counter() - started) * 1000)
        assert timed_value == value
        if outcomes is not None:
            assert value == outcomes
        outcomes = value
    return {
        "total": outcomes[0], "ids": outcomes[1], "plans": plans,
        "vm_steps": int(median(samples)), "median_ms": round(median(elapsed), 3),
    }


def test_document_filter_index_has_measurable_budget_and_is_idempotent(seeded_transfers, record_property):
    admin = seeded_transfers
    cases = {
        "single": (("DOC-0001",), None),
        "multiple": (("DOC-0001", "DOC-1999"), None),
        "status": (("DOC-0001", "DOC-1999"), "APPROVED"),
        "empty": (("NO-SUCH-DOCUMENT",), None),
    }
    connection = backend.db()
    before_rows = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(payload_json)),0) FROM transfer_requests"
    ).fetchone()
    # Drop only the candidate index to capture a genuine no-new-index baseline.
    connection.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    connection.commit()
    baseline = {name: _measure(connection, docs, status)
                for name, (docs, status) in cases.items()}
    baseline_second_pages = {}
    for name, (docs, status) in cases.items():
        _, rows_sql, args = _statements(docs, status)
        baseline_second_pages[name] = [row["id"] for row in connection.execute(
            rows_sql.replace("OFFSET 0", "OFFSET 7"), args,
        )]
    connection.execute(
        f"CREATE INDEX {ALT_INDEX_NAME} ON transfer_requests(document_no, created_at DESC, id DESC)"
    )
    connection.commit()
    created_candidate = {name: _measure(connection, docs, status)
                        for name, (docs, status) in cases.items()}
    connection.execute(f"DROP INDEX {ALT_INDEX_NAME}")
    connection.commit()
    connection.close()

    backend.init_db()
    connection = backend.db()  # Refresh the schema cache after init_db adds the index.
    indexes = {row["name"] for row in connection.execute("PRAGMA index_list(transfer_requests)")}
    assert INDEX_NAME in indexes, f"init_db did not create {INDEX_NAME}; baseline={baseline}"
    indexed = {name: _measure(connection, docs, status)
               for name, (docs, status) in cases.items()}
    index_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,),
    ).fetchone()["sql"]
    after_rows = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(payload_json)),0) FROM transfer_requests"
    ).fetchone()
    connection.close()

    backend.init_db()
    connection = backend.db()
    indexes_after = {row["name"] for row in connection.execute("PRAGMA index_list(transfer_requests)")}
    after_idempotent = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(length(payload_json)),0) FROM transfer_requests"
    ).fetchone()
    connection.close()
    assert indexes_after == indexes
    assert tuple(after_rows) == tuple(before_rows) == tuple(after_idempotent)
    assert before_rows[0] == 20_000

    record_property("baseline_explain", repr({k: v["plans"] for k, v in baseline.items()}))
    record_property("indexed_explain", repr({k: v["plans"] for k, v in indexed.items()}))
    record_property("created_candidate_explain", repr({k: v["plans"] for k, v in created_candidate.items()}))
    record_property("baseline_vm_steps", repr({k: v["vm_steps"] for k, v in baseline.items()}))
    record_property("created_candidate_vm_steps", repr({k: v["vm_steps"] for k, v in created_candidate.items()}))
    record_property("indexed_vm_steps", repr({k: v["vm_steps"] for k, v in indexed.items()}))
    record_property("baseline_median_ms_3runs", repr({k: v["median_ms"] for k, v in baseline.items()}))
    record_property("created_candidate_median_ms_3runs", repr({k: v["median_ms"] for k, v in created_candidate.items()}))
    record_property("indexed_median_ms_3runs", repr({k: v["median_ms"] for k, v in indexed.items()}))
    record_property("index_sql", index_sql)

    for name in cases:
        assert indexed[name]["total"] == baseline[name]["total"]
        assert indexed[name]["ids"] == baseline[name]["ids"]
        assert created_candidate[name]["total"] == baseline[name]["total"]
        assert created_candidate[name]["ids"] == baseline[name]["ids"]
    for name in ("single", "multiple", "empty"):
        assert indexed[name]["vm_steps"] < baseline[name]["vm_steps"]
        assert any(INDEX_NAME in detail for detail in indexed[name]["plans"]["count"])
        assert any(INDEX_NAME in detail for detail in indexed[name]["plans"]["rows"])
        assert indexed[name]["vm_steps"] <= 0.25 * baseline[name]["vm_steps"]
    for name in ("single", "multiple", "empty"):
        assert created_candidate[name]["vm_steps"] < baseline[name]["vm_steps"]
        assert any(ALT_INDEX_NAME in detail for detail in created_candidate[name]["plans"]["count"])
        assert any(ALT_INDEX_NAME in detail for detail in created_candidate[name]["plans"]["rows"])
        assert created_candidate[name]["vm_steps"] <= 0.25 * baseline[name]["vm_steps"]
    assert not any("TEMP B-TREE" in detail for detail in created_candidate["single"]["plans"]["rows"])
    # The created-at-first candidate does not improve status-filtered scans; the
    # selected doc/status index does, while preserving the existing row ordering plan.
    assert indexed["status"]["vm_steps"] < baseline["status"]["vm_steps"], (
        "status+multi-document result budget", baseline["status"], indexed["status"]
    )
    assert any(INDEX_NAME in detail for detail in indexed["status"]["plans"]["count"])
    assert indexed["status"]["vm_steps"] <= 0.75 * baseline["status"]["vm_steps"]
    assert sum(v["vm_steps"] for v in indexed.values()) < sum(v["vm_steps"] for v in created_candidate.values())

    # Composite index has status before timestamps: row order remains exact even if
    # SQLite needs a small temporary sort when status is unconstrained or multiple docs match.
    for name, (docs, status) in cases.items():
        result = backend._list_transfers(
            status=status, page=1, page_size=7, user=admin, document_nos=list(docs),
        )
        assert result["total"] == indexed[name]["total"]
        assert [row["id"] for row in result["items"]] == indexed[name]["ids"]
        second = backend._list_transfers(
            status=status, page=2, page_size=7, user=admin, document_nos=list(docs),
        )
        assert second["total"] == result["total"]
        assert [row["id"] for row in second["items"]] == baseline_second_pages[name]
        assert not set(indexed[name]["ids"]) & set(baseline_second_pages[name])
