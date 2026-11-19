"""Warehouse priority ordering: preserve results and avoid a full temporary sort."""
from __future__ import annotations

import sqlite3
import statistics
import time

import pytest

from app import main as backend
from app.admin_web import _inventory_list


@pytest.fixture
def catalog():
    c = backend.db()
    c.executemany(
        "INSERT INTO materials(id,code,name,unit,category,total_quantity,available_quantity) "
        "VALUES(?,?,?,?,?,?,?)",
        ((f"sort-{i}", f"MAT-{i:06}", f"物料{i}", "件", f"类{i % 3}",
          (i * 7919) % 1000, (i * 7919) % 1000) for i in range(20000)),
    )
    c.commit()
    yield c
    c.close()


@pytest.mark.parametrize("category", ["", "类1"])
def test_priority_sort_uses_index_and_preserves_full_paging_result(catalog, category, record_property):
    c = catalog
    index_names = ("idx_materials_available_code", "idx_materials_category_available_code")
    for name in index_names:
        c.execute(f"DROP INDEX IF EXISTS {name}")
    where = " WHERE category=?" if category else ""
    args = [category] if category else []
    sql = "SELECT code,name,available_quantity FROM materials" + where + " ORDER BY available_quantity,code LIMIT 20 OFFSET 0"
    before_plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args)]
    before_result = _inventory_list(c, "", category, 1)

    def sample():
        elapsed = []
        for _ in range(11):
            start = time.perf_counter()
            c.execute(sql, args).fetchall()
            elapsed.append(time.perf_counter() - start)
        return statistics.median(elapsed)

    before_time = sample()
    backend._ensure_performance_indexes(c)
    after_plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args)]
    after_result = _inventory_list(c, "", category, 1)
    after_time = sample()
    assert after_result == before_result
    record_property("category", category or "all")
    record_property("rows", c.execute("SELECT COUNT(*) FROM materials").fetchone()[0])
    record_property("before_plan", "; ".join(before_plan))
    record_property("after_plan", "; ".join(after_plan))
    record_property("before_median_ms", round(before_time * 1000, 4))
    record_property("after_median_ms", round(after_time * 1000, 4))
    assert any("TEMP B-TREE" in p for p in before_plan)
    assert not any("TEMP B-TREE" in p for p in after_plan)
    expected = index_names[1] if category else index_names[0]
    assert any(expected in p for p in after_plan)
    # Migration is idempotent; counts/status and page boundaries are unchanged.
    backend._ensure_performance_indexes(c)
    assert _inventory_list(c, "", category, 2)[0] != after_result[0]


def test_performance_indexes_skip_partial_legacy_schema_and_are_idempotent():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    try:
        c.execute("CREATE TABLE materials(id TEXT, code TEXT)")
        backend._ensure_performance_indexes(c)
        backend._ensure_performance_indexes(c)
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0] == 0
    finally:
        c.close()
