"""清除演示 / 测试数据（管理台「系统」页，管理员手动触发）。

清除范围（根）：
- app.seed 写入的测试夹具：所有表里 id 以 MF_TEST_SEED_V1 开头的行（物料、库位、订单、机台、用户、任务…）；
- 启动种子：26B-013 演示订单（ord_demo_26b013）及其三条演示物料（MTR-CTL-001/SEN-002/DRV-003）、
  空库初始物料 MTR-001 工业轴承（mat_001）与库位 A-01-03（loc_001，仅当无其它库存引用）。

从根出发只向“子”方向级联：引用了被删物料/订单/机台/任务/需求/领料项/交接/用户的行一并删除；
不会因为删库位而删别的物料的库存（库位只在无人引用时删）。日志类表（审计、*_operations、
U9 同步日志、智能体会话）不动。清除后写入 app_flags.demo_data_purged，启动时不再补种 26B-013。
"""
from __future__ import annotations

import sqlite3
from typing import Any

SEED_PREFIX = "MF_TEST_SEED_V1"
DEMO_MATERIAL_IDS = ("mat_ctl_cabinet", "mat_pos_sensor", "mat_drive_unit")
DEMO_ORDER_IDS = ("ord_demo_26b013",)
STARTER_MATERIAL = ("mat_001", "MTR-001")
STARTER_LOCATION = "loc_001"
FLAG = "demo_data_purged"

# 表 → 其主键所代表的“种类”（被删的行把 id 加入该种类集合，供子表级联）
OWN_KIND = {
    "materials": "material",
    "production_orders": "order",
    "order_devices": "device",
    "users": "user",
    "assembly_tasks": "task",
    "order_material_requirements": "requirement",
    "material_work_items": "work_item",
    "transfer_requests": "transfer",
    "labor_records": "labor",
    "material_handovers": "handover",
    "exceptions": "exception",
}
# 列名 → 引用的种类（子表通过这些列级联）
REF_KIND = {
    "material_id": "material",
    "order_id": "order",
    "order_no": "order_no",
    "device_id": "device",
    "target_device_id": "device",
    "task_id": "task",
    "source_task_id": "task",
    "requirement_id": "requirement",
    "work_item_id": "work_item",
    "transfer_request_id": "transfer",
    "labor_record_id": "labor",
    "last_handover_id": "handover",
    "exception_id": None,  # 只在日志表出现，不级联
    "user_id": "user",
    "worker_user_id": "user",
    "assembler_id": "user",
    "assigned_assembler_id": "user",
    "employee_id": "user",
    "manager_id": "user",
    "receiver_user_id": "user",
    "current_owner_user_id": "user",
}
SKIP_TABLES_PREFIX = ("audit_", "agent_", "u9_sync_logs", "sqlite_", "setup_", "login_attempts",
                      "material_categories", "material_master", "app_flags")


def _skip(table: str) -> bool:
    return table.endswith("_operations") or table.startswith(SKIP_TABLES_PREFIX)


def ensure_flags_table(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS app_flags(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")


def demo_purged(c: sqlite3.Connection) -> bool:
    try:
        return c.execute("SELECT 1 FROM app_flags WHERE key=?", (FLAG,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _tables(c) -> dict[str, list[str]]:
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if _skip(t):
            continue
        out[t] = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")').fetchall()]
    return out


def plan(c: sqlite3.Connection) -> dict[str, Any]:
    """计算要删除的行（不写库）。返回 {table: [rowid,...]}, 各表计数与物料样例。"""
    tables = _tables(c)
    kinds: dict[str, set] = {k: set() for k in set(OWN_KIND.values()) | {"order_no"}}
    doomed: dict[str, set] = {t: set() for t in tables}

    def mark(table, rows):
        cols = tables[table]
        new = False
        for r in rows:
            rid = r[0]
            if rid in doomed[table]:
                continue
            doomed[table].add(rid)
            new = True
            kind = OWN_KIND.get(table)
            if kind and "id" in cols:
                kinds[kind].add(r[1])
            if table == "production_orders":
                kinds["order_no"].add(r[2])
        return new

    def sel(table, where, args=()):
        cols = tables[table]
        extra = ", id" if "id" in cols else ", NULL"
        extra += ", order_no" if table == "production_orders" else ""
        return c.execute(f'SELECT rowid{extra} FROM "{table}" WHERE {where}', args).fetchall()

    like = SEED_PREFIX + "%"
    # 根：所有带 id 列的表里 seed 前缀的行（库位单独处理）
    for t, cols in tables.items():
        if "id" in cols and t != "locations":
            mark(t, sel(t, "id LIKE ?", (like,)))
    if "materials" in tables:
        q = ",".join("?" * len(DEMO_MATERIAL_IDS))
        mark("materials", sel("materials", f"id IN ({q})", DEMO_MATERIAL_IDS))
        mark("materials", sel("materials", "id=? AND code=?", STARTER_MATERIAL))
    if "production_orders" in tables:
        mark("production_orders", sel("production_orders", "id IN (?)", DEMO_ORDER_IDS))

    # 级联到不动点
    changed = True
    while changed:
        changed = False
        for t, cols in tables.items():
            for col in cols:
                kind = REF_KIND.get(col)
                if not kind or not kinds.get(kind):
                    continue
                if col == "id":
                    continue
                vals = list(kinds[kind])
                for i in range(0, len(vals), 500):
                    chunk = vals[i:i + 500]
                    rows = sel(t, f'"{col}" IN ({",".join("?" * len(chunk))})', chunk)
                    if mark(t, rows):
                        changed = True

    # 库位：seed 前缀 / 初始库位，且删除后无库存、绑定、盘点引用
    if "locations" in tables:
        cand = sel("locations", "id LIKE ? OR id=?", (like, STARTER_LOCATION))
        for r in cand:
            lid = r[1]
            used = False
            for t in ("inventory", "location_bindings", "stocktakes"):
                if t not in tables:
                    continue
                for (rid,) in c.execute(f'SELECT rowid FROM "{t}" WHERE location_id=?', (lid,)).fetchall():
                    if rid not in doomed[t]:
                        used = True
                        break
                if used:
                    break
            if not used:
                doomed["locations"].add(r[0])

    counts = {t: len(v) for t, v in doomed.items() if v}
    sample = []
    if doomed.get("materials"):
        ids = list(doomed["materials"])[:20]
        sample = [f"{r[0]} {r[1]}" for r in c.execute(
            f'SELECT code, name FROM materials WHERE rowid IN ({",".join("?" * len(ids))}) ORDER BY code', ids)]
    return {"rows": {t: sorted(v) for t, v in doomed.items() if v}, "counts": counts,
            "total": sum(counts.values()), "material_sample": sample,
            "order_nos": sorted(x for x in kinds["order_no"] if x)}


def purge(c: sqlite3.Connection, now: str) -> dict[str, Any]:
    """执行清除（单事务；外键延迟到提交时检查，失败整体回滚）。"""
    p = plan(c)
    ensure_flags_table(c)
    c.execute("PRAGMA defer_foreign_keys=ON")
    try:
        for t, rowids in p["rows"].items():
            for i in range(0, len(rowids), 500):
                chunk = rowids[i:i + 500]
                c.execute(f'DELETE FROM "{t}" WHERE rowid IN ({",".join("?" * len(chunk))})', chunk)
        c.execute("INSERT INTO app_flags(key, value, updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE"
                  " SET value=excluded.value, updated_at=excluded.updated_at", (FLAG, "1", now))
        bad = c.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"清除后存在外键悬挂：{[tuple(b) for b in bad[:5]]}")
        c.commit()
    except Exception:
        c.rollback()
        raise
    p.pop("rows")
    return p
