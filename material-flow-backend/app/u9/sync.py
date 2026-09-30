"""U9 同步编排（Phase 1：只读拉取）。

入口 run_sync(entity, dry_run=True, conn=None)：
  entity ∈ {"items", "boms", "inventory", "orders"}

行为：
  - dry_run=True（默认）：只拉取 + 映射 + 统计，不写任何业务表；结果记一条 DRY_RUN 日志
  - dry_run=False：幂等 upsert（按自然键：materials.code / production_orders.order_no 等）
  - 每次运行写 u9_sync_logs（含 run_id，便于追溯）；U9NotImplemented 记为 PENDING_DOC
    （待实施商文档），不视为失败，不触发重试告警

调用方（routes.py）负责：权限校验（管理员）、传 sqlite3 连接、返回结果。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

from . import mappers
from .client import U9NotImplemented, get_client
from .config import U9Config

ENTITIES = ("items", "boms", "inventory", "orders")

CREATE_U9_SYNC_LOGS = """
CREATE TABLE IF NOT EXISTS u9_sync_logs(
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    dry_run INTEGER NOT NULL DEFAULT 1,
    total INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_u9_sync_logs_run ON u9_sync_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_u9_sync_logs_entity ON u9_sync_logs(entity, started_at);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _log(conn: sqlite3.Connection, run_id: str, entity: str, dry_run: bool, *,
         status: str, total: int = 0, inserted: int = 0, updated: int = 0,
         skipped: int = 0, error: str = "", detail: dict | None = None,
         started_at: str | None = None) -> None:
    conn.executescript(CREATE_U9_SYNC_LOGS)
    conn.execute(
        """INSERT INTO u9_sync_logs(id, run_id, entity, started_at, finished_at, status,
               dry_run, total, inserted, updated, skipped, error, detail_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"log-{run_id}-{entity}", run_id, entity, started_at or _now(), _now(), status,
         1 if dry_run else 0, total, inserted, updated, skipped, error,
         json.dumps(detail or {}, ensure_ascii=False)),
    )


# ---------------------------------------------------------------- 各实体同步
def _sync_items(conn: sqlite3.Connection, cfg: U9Config, dry_run: bool) -> dict:
    client = get_client(cfg)
    raw = client.fetch_items()
    mapped, skipped = [], 0
    for r in raw:
        try:
            mapped.append(mappers.map_item(r))
        except ValueError:
            skipped += 1
    inserted = updated = 0
    if not dry_run:
        for m in mapped:
            cur = conn.execute("SELECT id FROM materials WHERE code=?", (m["code"],)).fetchone()
            if cur:
                conn.execute(
                    "UPDATE materials SET name=?, specification=?, unit=?, version=version+1 WHERE code=?",
                    (m["name"], m["specification"], m["unit"], m["code"]))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO materials(id, code, name, specification, unit,"
                    " total_quantity, available_quantity, version)"
                    " VALUES(?,?,?,?,?,0,0,1)",
                    (m["id"], m["code"], m["name"], m["specification"], m["unit"]))
                inserted += 1
    return {"total": len(raw), "mapped": len(mapped), "inserted": inserted,
            "updated": updated, "skipped": skipped}


def _sync_boms(conn: sqlite3.Connection, cfg: U9Config, dry_run: bool,
               product_codes: list[str] | None = None) -> dict:
    client = get_client(cfg)
    codes = product_codes or []
    total_lines = inserted_batches = inserted_lines = skipped = 0
    sample: list[dict] = []
    for pcode in codes:
        bom = client.fetch_bom(pcode)
        lines = bom.get("lines", [])
        total_lines += len(lines)
        batch = mappers.map_bom_batch(pcode, bom.get("product_name", ""))
        n = 0
        for i, ln in enumerate(lines, start=1):
            try:
                row = mappers.map_bom_line(batch["id"], i, ln)
            except (ValueError, TypeError):
                skipped += 1
                continue
            n += 1
            if not dry_run:
                conn.execute(
                    """INSERT INTO agg_bom_items(id, batch_id, device_code, device_name, level_no,
                           parent_code, material_code, material_name, specification, unit,
                           quantity, material_form, line_no)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["id"], row["batch_id"], row["device_code"], row["device_name"],
                     row["level_no"], row["parent_code"], row["material_code"],
                     row["material_name"], row["specification"], row["unit"],
                     row["quantity"], row["material_form"], row["line_no"]))
            elif len(sample) < 5:
                sample.append(row)
        if not dry_run and n:
            conn.execute(
                "INSERT INTO agg_bom_batches(id, product_code, product_name, source, created_at)"
                " VALUES(?,?,?,?,?)",
                (batch["id"], batch["product_code"], batch["product_name"],
                 batch["source"], batch["created_at"]))
            inserted_batches += 1
        inserted_lines += 0 if dry_run else n
    return {"products": len(codes), "total_lines": total_lines,
            "inserted_batches": inserted_batches, "inserted_lines": inserted_lines,
            "skipped": skipped, "sample": sample}


def _sync_inventory(conn: sqlite3.Connection, cfg: U9Config, dry_run: bool) -> dict:
    client = get_client(cfg)
    raw = client.fetch_inventory()
    total = len(raw)
    inserted = updated = skipped = 0
    sample: list[dict] = []
    for r in raw:
        try:
            m = mappers.map_inventory_row(r)
        except ValueError:
            skipped += 1
            continue
        mat = conn.execute("SELECT id FROM materials WHERE code=?", (m["material_code"],)).fetchone()
        if not mat:
            skipped += 1
            continue
        if not dry_run:
            loc = conn.execute("SELECT id FROM locations WHERE id=?", (m["location_id"],)).fetchone()
            if not loc:
                conn.execute("INSERT OR IGNORE INTO locations(id, name) VALUES(?,?)",
                             (m["location_id"], m["location_id"]))
            cur = conn.execute("SELECT quantity FROM inventory WHERE material_id=? AND location_id=?",
                               (mat["id"], m["location_id"])).fetchone()
            if cur:
                conn.execute("UPDATE inventory SET quantity=? WHERE material_id=? AND location_id=?",
                             (m["quantity"], mat["id"], m["location_id"]))
                updated += 1
            else:
                conn.execute("INSERT INTO inventory(id, material_id, location_id, quantity)"
                             " VALUES(?,?,?,?)",
                             (f"inv-u9-{mat['id']}-{m['location_id']}", mat["id"],
                              m["location_id"], m["quantity"]))
                inserted += 1
        elif len(sample) < 5:
            sample.append({**m, "material_id": mat["id"]})
    return {"total": total, "inserted": inserted, "updated": updated,
            "skipped": skipped, "sample": sample}


def _sync_orders(conn: sqlite3.Connection, cfg: U9Config, dry_run: bool) -> dict:
    client = get_client(cfg)
    raw = client.fetch_production_orders()
    inserted = updated = skipped = 0
    for r in raw:
        try:
            m = mappers.map_production_order(r)
        except ValueError:
            skipped += 1
            continue
        if not dry_run:
            cur = conn.execute("SELECT id FROM production_orders WHERE order_no=?",
                               (m["order_no"],)).fetchone()
            if cur:
                conn.execute("UPDATE production_orders SET product_name=?, status=?, updated_at=?"
                             " WHERE order_no=?",
                             (m["product_name"], m["status"], m["updated_at"], m["order_no"]))
                updated += 1
            else:
                conn.execute("INSERT INTO production_orders(id, order_no, product_name, status,"
                             " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                             (m["id"], m["order_no"], m["product_name"], m["status"],
                              m["created_at"], m["updated_at"]))
                inserted += 1
    return {"total": len(raw), "inserted": inserted, "updated": updated, "skipped": skipped}


_SYNC_FNS = {
    "items": _sync_items,
    "boms": _sync_boms,
    "inventory": _sync_inventory,
    "orders": _sync_orders,
}


# ---------------------------------------------------------------- 入口
def run_sync(entity: str, conn: sqlite3.Connection, cfg: U9Config,
             dry_run: bool = True, **kwargs) -> dict:
    """执行一次同步。返回 {run_id, entity, dry_run, status, stats} 并写日志表。"""
    if entity not in _SYNC_FNS:
        raise ValueError(f"未知同步实体: {entity}，可选 {ENTITIES}")
    run_id = uuid.uuid4().hex[:12]
    started = _now()
    try:
        stats = _SYNC_FNS[entity](conn, cfg, dry_run, **kwargs)
    except U9NotImplemented as e:
        _log(conn, run_id, entity, dry_run, status="PENDING_DOC",
             error=str(e), started_at=started)
        conn.commit()
        return {"run_id": run_id, "entity": entity, "dry_run": dry_run,
                "status": "PENDING_DOC",
                "message": "U9 接口占位：待实施商提供 WSDL/字段文档后实现",
                "detail": str(e)}
    except Exception as e:  # noqa: BLE001 - 同步异常要记日志后抛给上层转中文报错
        _log(conn, run_id, entity, dry_run, status="FAILED", error=str(e), started_at=started)
        conn.commit()
        raise
    _log(conn, run_id, entity, dry_run, status="OK", total=stats.get("total", 0),
         inserted=stats.get("inserted", stats.get("inserted_lines", 0)),
         updated=stats.get("updated", 0), skipped=stats.get("skipped", 0),
         detail=stats, started_at=started)
    conn.commit()
    return {"run_id": run_id, "entity": entity, "dry_run": dry_run,
            "status": "OK", "stats": stats}
