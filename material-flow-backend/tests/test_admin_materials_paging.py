"""Web material filters must run before SQL paging, including literal/case-sensitive search."""
from __future__ import annotations

from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app import main as backend
from app import admin_web as admin

ORDER = "AUDIT-WEB-MATERIALS"


@pytest.fixture
def material_page(monkeypatch):
    c = backend.db()
    ts = "2000-01-01T00:00:00+00:00"
    c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", ("web-audit", "web-audit", "Synthetic", "ADMIN", "unused", 0, 1, ts))
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?)", (ORDER, ORDER, "Synthetic", "IN_PROGRESS", ts, ts))
    c.execute("INSERT INTO order_devices VALUES(?,?,?,?,?,?)", ("web-device", ORDER, "TYPE", "AUDIT-WEB-DEVICE", 1, ts))
    for i in range(121):
        mid = f"web-material-{i:03}"
        name = {0: "100% Valve", 4: "motor lower", 5: "Motor Upper"}.get(i, f"Part {i}")
        category = ("电气", "机械", None)[i % 3]
        c.execute("INSERT INTO materials(id,code,name,unit,category) VALUES(?,?,?,?,?)", (mid, f"SKU-{i:03}", name, "EA", category))
        c.execute("INSERT INTO order_material_requirements VALUES(?,?,?,?,?,?,?,?,?,?)", (f"web-req-{i:03}", ORDER, "web-device", mid, 1, 1, 1, "IN_STOCK", ts, ts))
    user = dict(c.execute("SELECT * FROM users WHERE id='web-audit'").fetchone())
    user["csrf_token"] = "synthetic-csrf"
    c.commit()
    c.close()
    monkeypatch.setattr(admin, "_report_or_403", lambda request: (user, None))
    return user


def request(**params):
    query = {"order_no": ORDER, "partial": "materials", **params}
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": "/admin/orders",
                    "root_path": "", "query_string": urlencode(query).encode(), "headers": [],
                    "app": backend.app, "server": ("testserver", 80), "client": ("testclient", 1)})


@pytest.mark.parametrize("query,category,page,size", [
    ("", "", 1, 20), ("", "", 2, 20), ("", "机械", 2, 10),
    ("%", "", 1, 20), ("motor", "", 1, 20), ("Motor", "", 1, 20),
    ("SKU-0", "电气", 999, 20), ("missing", "", 1, 20),
])
def test_partial_filters_then_pages_and_preserves_material_payload(material_page, query, category, page, size):
    full = backend.order_detail(ORDER, page=1, pageSize=500, materialsPage=1, materialsPageSize=0, user=material_page)["materials"]
    expected = [m for m in full if (not query or query in m["materialCode"] or query in m["materialName"])
                and (not category or category == (m["materialCategory"] or ""))]
    pages = (len(expected) + size - 1) // size
    effective = min(max(page, 1), pages) if pages else max(page, 1)
    response = admin.admin_orders(request(mat_q=query, mat_cat=category, mat_page=page, mat_size=size))
    assert response.status_code == 200
    assert response.context["detail"]["materials"] == expected[(effective - 1) * size:effective * size]
    assert f"共 {len(expected)} 条" in response.body.decode()


def test_partial_does_not_build_unrelated_order_task_labor_or_timeline(material_page, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("materials partial must not build the complete order aggregate")
    monkeypatch.setattr(admin, "api_order_detail", forbidden)
    response = admin.admin_orders(request())
    assert response.status_code == 200
    assert len(response.context["detail"]["materials"]) == 20


@pytest.mark.parametrize("page", [0, -1, 10 ** 100])
def test_empty_filtered_result_has_safe_first_page_even_for_extreme_page_input(material_page, page):
    response = admin.admin_orders(request(mat_q="not-present", mat_page=page))
    assert response.status_code == 200
    assert response.context["detail"]["materials"] == []
    assert response.context["detail"]["materialsPage"] == 1


def test_partial_material_row_query_is_bounded_in_sql(material_page, monkeypatch):
    statements = []
    real_connect = backend.sqlite3.connect
    def traced(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(backend.sqlite3, "connect", traced)
    response = admin.admin_orders(request())
    assert response.status_code == 200
    material_queries = [sql for sql in statements if "AS requirement_id" in sql
                        and not sql.lstrip().upper().startswith("SELECT COUNT")]
    assert material_queries
    assert all("LIMIT 20" in sql.upper() for sql in material_queries), material_queries
