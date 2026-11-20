"""R6: repeated documentNo filtering stays scoped and paginated."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


from app import main as backend  # noqa: E402

INITIAL_ADMIN_PASSWORD = "ScopeAdmin@2026"
ADMIN_PASSWORD = INITIAL_ADMIN_PASSWORD
USER_PASSWORD = "ScopeUser@2026"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_user(uid: str, username: str, role: str) -> None:
    c = backend.db()
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (uid, username, username, role, backend.hash_password(USER_PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def _login(client: TestClient, username: str) -> str:
    password = ADMIN_PASSWORD if username == "owlco" else USER_PASSWORD
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": f"scope-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def _insert_transfer(rid: str, document_no: str, status: str, created_by: str) -> None:
    c = backend.db()
    stamp = f"2026-11-20T00:00:{int(rid[-2:]):02d}+00:00"
    c.execute(
        """INSERT INTO transfer_requests
           (id,client_operation_id,type,document_no,status,payload_json,created_by,created_at,
            approved_by,approved_at,executed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, str(uuid.uuid4()), "OUTBOUND", document_no, status,
         '{"items":[]}', created_by, stamp, None, None, None),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    data = tmp_path / "scope-data"
    uploads = tmp_path / "scope-uploads"
    monkeypatch.setattr(backend, "DATA_DIR", data)
    monkeypatch.setattr(backend, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(backend, "DB_PATH", data / "material_flow.db")
    monkeypatch.setattr(backend, "INITIAL_ADMIN_PASSWORD", ADMIN_PASSWORD)
    backend.init_db()
    yield
    backend.app.dependency_overrides.clear()


def test_document_no_repeated_exact_filter_applies_to_count_rows_and_status():
    _add_user("scope-operator", "scope-operator", "OPERATOR")
    _add_user("scope-other", "scope-other", "OPERATOR")
    for i, (doc, status, owner) in enumerate([
        ("ORDER-A", "PENDING_APPROVAL", "scope-operator"),
        ("ORDER-B", "APPROVED", "scope-other"),
        ("ORDER-C", "REJECTED", "scope-operator"),
        ("ORDER-A", "EXECUTED", "scope-other"),
        ("A+B?[]", "PENDING_APPROVAL", "scope-operator"),
        ("L" * 256, "APPROVED", "scope-operator"),
    ], 1):
        _insert_transfer(f"scope-{i:02d}", doc, status, owner)

    with TestClient(backend.app) as client:
        admin = _login(client, "owlco")
        operator = _login(client, "scope-operator")
        unfiltered = client.get("/api/v1/transfer-requests", headers=_headers(admin))
        assert unfiltered.status_code == 200
        assert unfiltered.json()["total"] == 6
        assert len(unfiltered.json()["items"]) == 6
        filtered = client.get(
            "/api/v1/transfer-requests",
            params=[("documentNo", "ORDER-A"), ("documentNo", "ORDER-B"),
                    ("documentNo", "ORDER-A"), ("status", "APPROVED")],
            headers=_headers(admin),
        )
        assert filtered.status_code == 200, filtered.text
        assert [x["document_no"] for x in filtered.json()["items"]] == ["ORDER-B"]
        assert filtered.json()["total"] == 1

        exact = client.get(
            "/api/v1/transfer-requests",
            params=[("documentNo", "A+B?[]")], headers=_headers(admin),
        )
        assert exact.status_code == 200
        assert [x["document_no"] for x in exact.json()["items"]] == ["A+B?[]"]
        long_no = "L" * 256
        long_result = client.get("/api/v1/transfer-requests", params=[("documentNo", long_no)], headers=_headers(admin))
        assert long_result.status_code == 200
        assert [x["document_no"] for x in long_result.json()["items"]] == [long_no]

        # Existing internal/admin callers omit the optional filter and retain the old result set.
        legacy_conn = backend.db()
        try:
            legacy_user = legacy_conn.execute("SELECT * FROM users WHERE id='u_admin'").fetchone()
            legacy = backend._list_transfers(status=None, page=1, page_size=100, user=legacy_user)
        finally:
            legacy_conn.close()
        assert legacy["total"] == 6

        # Unknown document numbers produce an empty result, never an unfiltered fallback.
        unknown = client.get(
            "/api/v1/transfer-requests",
            params=[("documentNo", "DOES-NOT-EXIST")], headers=_headers(admin),
        )
        assert unknown.status_code == 200
        assert unknown.json()["items"] == [] and unknown.json()["total"] == 0

        restricted = client.get(
            "/api/v1/transfer-requests",
            params=[("documentNo", "ORDER-A"), ("documentNo", "ORDER-B")],
            headers=_headers(operator),
        )
        assert restricted.status_code == 200
        assert {x["id"] for x in restricted.json()["items"]} == {"scope-01"}
        assert restricted.json()["total"] == 1


def test_document_no_validation_and_pagination_are_stable():
    with TestClient(backend.app) as client:
        token = _login(client, "owlco")
        for i in range(1, 104):
            _insert_transfer(f"page-{i:02d}", f"P-{i:03d}", "APPROVED", "owlco")
        blank = client.get("/api/v1/transfer-requests?documentNo=   ", headers=_headers(token))
        assert blank.status_code == 400
        too_many = [("documentNo", f"P-{i:03d}") for i in range(101)]
        assert client.get("/api/v1/transfer-requests", params=too_many,
                          headers=_headers(token)).status_code == 422
        page_one = client.get("/api/v1/transfer-requests", params=[("documentNo", "P-001"), ("documentNo", "P-002"), ("pageSize", "1")], headers=_headers(token))
        page_two = client.get("/api/v1/transfer-requests", params=[("documentNo", "P-001"), ("documentNo", "P-002"), ("pageSize", "1"), ("page", "2")], headers=_headers(token))
        assert page_one.status_code == page_two.status_code == 200
        assert page_one.json()["total"] == page_two.json()["total"] == 2
        first_id = page_one.json()["items"][0]["id"]
        second_id = page_two.json()["items"][0]["id"]
        assert [first_id, second_id] == ["page-02", "page-01"]
        assert {first_id, second_id} == {"page-01", "page-02"}


def test_document_filter_limits_large_result_set_and_query_payload(record_property):
    with TestClient(backend.app) as client:
        token = _login(client, "owlco")
        stamp = backend.now()
        c = backend.db()
        c.executemany(
            """INSERT INTO transfer_requests
               (id,client_operation_id,type,document_no,status,payload_json,created_by,created_at)
               VALUES(?,?,'OUTBOUND',?,'APPROVED',?,?,?)""",
            ((f"bulk-{i:04d}", str(uuid.uuid4()), f"BULK-{i:04d}", '{"items":[]}', "owlco", stamp)
             for i in range(1000)),
        )
        c.executemany("UPDATE transfer_requests SET document_no=?, payload_json=? WHERE id=?", [
            (doc, '{"items":[{"materialId":"target"},{"materialId":"target"}]}', f"bulk-{i:04d}")
            for i, doc in enumerate(("TARGET-A", "TARGET-B"), 1)
        ])
        c.commit()
        c.close()

        c = backend.db()
        plan = [row[3] for row in c.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM transfer_requests WHERE document_no IN (?,?)",
            ("TARGET-A", "TARGET-B"),
        )]
        c.close()

        statements = []
        open_db = backend._open_db

        def traced_open_db(**kwargs):
            connection = open_db(**kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        backend._open_db = traced_open_db
        try:
            old_responses = [client.get(
                "/api/v1/transfer-requests",
                params={"page": page, "pageSize": 100}, headers=_headers(token),
            ) for page in range(1, 11)]
            result = client.get(
                "/api/v1/transfer-requests",
                params=[("documentNo", "TARGET-A"), ("documentNo", "TARGET-B")],
                headers=_headers(token),
            )
        finally:
            backend._open_db = open_db
        assert all(response.status_code == 200 for response in old_responses), [r.text for r in old_responses]
        assert result.status_code == 200, result.text
        old_rows = sum(len(response.json()["items"]) for response in old_responses)
        old_json_bytes = sum(len(response.content) for response in old_responses)
        body = result.json()
        new_rows = len(body["items"])
        new_json_bytes = len(result.content)
        record_property("unfiltered_page_requests", len(old_responses))
        record_property("unfiltered_returned_rows", old_rows)
        record_property("unfiltered_total_json_bytes", old_json_bytes)
        record_property("filtered_page_requests", 1)
        record_property("filtered_returned_rows", new_rows)
        record_property("filtered_json_bytes", new_json_bytes)
        assert old_rows == 1000
        assert body["total"] == 2 and new_rows == 2
        assert new_json_bytes * 20 < old_json_bytes, (new_json_bytes, old_json_bytes)
        assert {item["document_no"] for item in body["items"]} == {"TARGET-A", "TARGET-B"}
        assert {item["itemCount"] for item in body["items"]} == {2}
        page_selects = [sql for sql in statements if "FROM transfer_requests t WHERE" in sql]
        assert len(page_selects) == 22, page_selects
        assert all("document_no IN" not in sql for sql in page_selects[:20])
        assert all("document_no IN" in sql for sql in page_selects[20:]), page_selects


def test_internal_list_reuses_existing_shared_read_transaction(monkeypatch):
    connection = backend._open_db()
    try:
        connection.execute("BEGIN")
        admin = connection.execute("SELECT * FROM users WHERE id='u_admin'").fetchone()
        shared = backend._SharedConnection(connection)
        monkeypatch.setattr(backend, "db", lambda: shared)
        result = backend._list_transfers(status=None, page=1, page_size=100, user=admin)
        assert result["total"] == 0
    finally:
        connection.close()
