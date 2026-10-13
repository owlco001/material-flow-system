"""U9 同步编排（Phase 1：只读拉取 U9 → 本地）。

入口 run_sync(entity, conn, cfg, dry_run=True, **kwargs)：
  items      料品档案：默认取本地 materials 全部编码 + codes 参数，按编码批量查 U9
  boms       BOM：product_codes 参数（母件/设备编码），多层展开后写一个 agg_bom 批次
  inventory  库存：codes 参数（料号）或按组织 + 存储地点（wh / U9_DEFAULT_WH）全量
  orders     生产订单：doc_nos 参数 + 本地已有 production_orders.order_no

行为：
  - dry_run=True（默认）：只拉取 + 映射 + 统计 + 样例行，不写任何业务表；记一条日志
  - dry_run=False：幂等 upsert（materials.code / production_orders.order_no / inventory 料号+库位；
    BOM 内容未变则不重复建批次）
  - 每次运行写 u9_sync_logs（detail_json 含统计与样例，管理页据此展示预览）
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid

from . import mappers
from .client import U9NotImplemented, get_client
from .config import U9Config

ENTITIES = ("items", "boms", "inventory", "orders")
SAMPLE_SIZE = 20

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


def log_id(run_id: str, entity: str) -> str:
    return f"log-{run_id}-{entity}"


def _log(conn: sqlite3.Connection, run_id: str, entity: str, dry_run: bool, *,
         status: str, total: int = 0, inserted: int = 0, updated: int = 0,
         skipped: int = 0, error: str = "", detail: dict | None = None,
         started_at: str | None = None) -> None:
    conn.executescript(CREATE_U9_SYNC_LOGS)
    conn.execute(
        """INSERT INTO u9_sync_logs(id, run_id, entity, started_at, finished_at, status,
               dry_run, total, inserted, updated, skipped, error, detail_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (log_id(run_id, entity), run_id, entity, started_at or _now(), _now(), status,
         1 if dry_run else 0, total, inserted, updated, skipped, error[:2000],
         json.dumps(detail or {}, ensure_ascii=False, default=str)),
    )


def _codes(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = v.replace("\n", ",").replace("，", ",").replace(";", ",").split(",")
    out, seen = [], set()
    for c in v:
        c = str(c).strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ---------------------------------------------------------------- 料品档案
def _sync_items(conn, cfg, dry_run, *, codes=None, include_local=True, client=None, **_):
    client = client or get_client(cfg)
    extra = _codes(codes)
    local = [r[0] for r in conn.execute("SELECT code FROM materials ORDER BY code")] if include_local else []
    wanted = _codes(local + extra)
    errors: list[dict] = []
    raw = client.fetch_items(wanted, errors=errors) if wanted else []
    mapped, skipped = {}, 0
    for r in raw:
        try:
            m = mappers.map_item(r)
        except ValueError:
            skipped += 1
            continue
        mapped[m["code"]] = m
    not_found = [c for c in wanted if c not in mapped]
    inserted = updated = unchanged = 0
    sample = []
    for m in mapped.values():
        cur = conn.execute("SELECT name, specification, unit FROM materials WHERE code=?",
                           (m["code"],)).fetchone()
        action = "新增" if not cur else (
            "不变" if (cur[0] or "", cur[1] or "", cur[2] or "") == (m["name"], m["specification"], m["unit"])
            else "更新")
        if len(sample) < SAMPLE_SIZE:
            sample.append({"动作": action, "料号": m["code"], "品名": m["name"], "规格": m["specification"],
                           "单位": m["unit"], "形态": m["item_form"], "主分类": m["category"],
                           **({"原品名": cur[0], "原规格": cur[1], "原单位": cur[2]} if cur and action == "更新" else {})})
        if action == "不变":
            unchanged += 1
            continue
        if action == "更新":
            updated += 1
            if not dry_run:
                conn.execute("UPDATE materials SET name=?, specification=?, unit=?, version=version+1"
                             " WHERE code=?", (m["name"], m["specification"], m["unit"], m["code"]))
        else:
            inserted += 1
            if not dry_run:
                conn.execute("INSERT INTO materials(id, code, name, specification, unit,"
                             " total_quantity, available_quantity, version) VALUES(?,?,?,?,?,0,0,1)",
                             (f"u9-{m['code']}", m["code"], m["name"], m["specification"], m["unit"]))
    return {"requested": len(wanted), "total": len(raw), "mapped": len(mapped),
            "inserted": inserted, "updated": updated, "unchanged": unchanged, "skipped": skipped,
            "not_found": not_found[:200], "not_found_count": len(not_found),
            "errors": errors[:50], "sample": sample}


# ---------------------------------------------------------------- BOM
def _sync_boms(conn, cfg, dry_run, *, product_codes=None, user_id="u9-sync", client=None, **_):
    client = client or get_client(cfg)
    codes = _codes(product_codes)
    results, sample = [], []
    total_lines = inserted_batches = inserted_lines = unchanged = skipped = 0
    errors: list[dict] = []
    for pcode in codes:
        bom = client.fetch_bom(pcode)
        errors.extend(bom.get("errors", [])[:50])
        if not bom.get("found"):
            skipped += 1
            results.append({"product_code": pcode, "found": False, "lines": 0})
            continue
        device_name = bom.get("product_name") or pcode
        lines = [{"level": 1, "parent_code": "", "material_code": pcode, "material_name": device_name,
                  "spec": bom.get("product_spec", ""), "unit": bom.get("product_unit", ""),
                  "qty": 1, "material_form": bom.get("product_form", "")}] + bom.get("lines", [])
        rows = [mappers.map_bom_line("PENDING", i, ln, pcode, device_name)
                for i, ln in enumerate(lines, start=1)]
        total_lines += len(rows)
        sig = json.dumps([[r["level_no"], r["parent_code"], r["material_code"], r["quantity"],
                           r["unit"], r["material_form"], r["specification"]] for r in rows],
                         ensure_ascii=False)
        sha = hashlib.sha256(f"u9c:{pcode}:{sig}".encode("utf-8")).hexdigest()
        file_name = f"U9C BOM {pcode}" + (f" v{bom['bom_version']}" if bom.get("bom_version") else "")
        exists = conn.execute("SELECT id FROM agg_bom_batches WHERE file_sha256=? AND file_name=?",
                              (sha, file_name)).fetchone()
        info = {"product_code": pcode, "product_name": device_name, "bom_version": bom.get("bom_version", ""),
                "found": True, "lines": len(rows), "truncated": bom.get("truncated", False),
                "max_level": max(r["level_no"] for r in rows), "unchanged": bool(exists)}
        results.append(info)
        for r in rows:
            if len(sample) >= SAMPLE_SIZE:
                break
            sample.append({"设备": pcode, "层级": r["level_no"], "父项": r["parent_code"],
                           "料号": r["material_code"], "品名": r["material_name"], "规格": r["specification"],
                           "单位": r["unit"], "用量": r["quantity"], "形态": r["material_form"]})
        if exists:
            unchanged += 1
            continue
        if dry_run:
            continue
        batch_id = f"u9bom-{uuid.uuid4().hex[:12]}"
        now = _now()
        mat_codes = {r["material_code"] for r in rows}
        conn.execute("INSERT INTO agg_bom_batches(id, project_name, file_name, file_sha256, row_count,"
                     " device_count, material_count, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (batch_id, f"U9:{device_name}", file_name, sha, len(rows), 1, len(mat_codes),
                      user_id or "u9-sync", now))
        for r in rows:
            conn.execute(
                """INSERT INTO agg_bom_items(id, batch_id, device_code, device_name, level_no,
                       parent_code, material_code, material_name, specification, unit,
                       quantity, material_form, line_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"{batch_id}-{r['line_no']}", batch_id, r["device_code"], r["device_name"],
                 r["level_no"], r["parent_code"], r["material_code"], r["material_name"],
                 r["specification"], r["unit"], r["quantity"], r["material_form"], r["line_no"]))
        inserted_batches += 1
        inserted_lines += len(rows)
        info["batch_id"] = batch_id
    return {"products": len(codes), "total": total_lines, "total_lines": total_lines,
            "inserted": inserted_lines, "inserted_batches": inserted_batches, "inserted_lines": inserted_lines,
            "unchanged": unchanged, "skipped": skipped, "results": results,
            "errors": errors[:50], "sample": sample}


# ---------------------------------------------------------------- 库存
def _sync_inventory(conn, cfg, dry_run, *, codes=None, wh=None, update_totals=True, client=None, **_):
    client = client or get_client(cfg)
    item_codes = _codes(codes)
    raw = client.fetch_inventory(item_codes or None, wh=(wh or None))
    agg: dict[tuple[str, str], dict] = {}
    skipped = 0
    for r in raw:
        try:
            m = mappers.map_inventory_row(r, cfg.inventory_qty_field)
        except ValueError:
            skipped += 1
            continue
        k = (m["material_code"], m["location_code"])
        a = agg.setdefault(k, {**m, "qty": 0.0, "store_qty": 0.0, "available_qty": 0.0, "lots": 0})
        a["qty"] += m["qty"]
        a["store_qty"] += m["store_qty"]
        a["available_qty"] += m["available_qty"]
        a["lots"] += 1
    inserted = updated = unchanged = 0
    unknown: list[str] = []
    totals: dict[str, list[float]] = {}
    sample = []
    fractional = 0
    for (code, loc_code), a in agg.items():
        mat = conn.execute("SELECT id, total_quantity, available_quantity FROM materials WHERE code=?",
                           (code,)).fetchone()
        if not mat:
            skipped += 1
            if code not in unknown:
                unknown.append(code)
            continue
        qty = max(0, int(round(a["qty"])))
        if abs(a["qty"] - round(a["qty"])) > 1e-9:
            fractional += 1
        t = totals.setdefault(code, [mat[0], 0.0, 0.0, mat[1], mat[2]])
        t[1] += max(0.0, a["store_qty"])
        t[2] += max(0.0, a["available_qty"])
        loc = conn.execute("SELECT id FROM locations WHERE code=?", (loc_code,)).fetchone()
        cur = conn.execute("SELECT quantity FROM inventory WHERE material_id=? AND location_id=?",
                           (mat[0], loc[0])).fetchone() if loc else None
        action = "新增" if cur is None else ("不变" if cur[0] == qty else "更新")
        if len(sample) < SAMPLE_SIZE:
            sample.append({"动作": action, "料号": code, "品名": a["material_name"], "库位": loc_code,
                           "库位名称": a["location_name"], "现存量": a["store_qty"],
                           "可用量": a["available_qty"], "写入数量": qty,
                           **({"原数量": cur[0]} if cur is not None and action == "更新" else {})})
        if action == "不变":
            unchanged += 1
            continue
        if action == "新增":
            inserted += 1
        else:
            updated += 1
        if dry_run:
            continue
        if not loc:
            loc_id = f"u9loc-{loc_code}"
            conn.execute("INSERT OR IGNORE INTO locations(id, code, name) VALUES(?,?,?)",
                         (loc_id, loc_code, a["location_name"] or loc_code))
            loc = (loc_id,)
        if cur is None:
            conn.execute("INSERT INTO inventory(id, material_id, location_id, quantity) VALUES(?,?,?,?)",
                         (f"inv-u9-{mat[0]}-{loc[0]}", mat[0], loc[0], qty))
        else:
            conn.execute("UPDATE inventory SET quantity=? WHERE material_id=? AND location_id=?",
                         (qty, mat[0], loc[0]))
    totals_changed = 0
    for code, (mid, store, avail, old_total, old_avail) in totals.items():
        nt, na = int(round(store)), int(round(avail))
        if (nt, na) != (old_total, old_avail):
            totals_changed += 1
            if not dry_run and update_totals:
                conn.execute("UPDATE materials SET total_quantity=?, available_quantity=?, version=version+1"
                             " WHERE id=?", (nt, na, mid))
    return {"total": len(raw), "rows": len(agg), "inserted": inserted, "updated": updated,
            "unchanged": unchanged, "skipped": skipped, "unknown_materials": unknown[:200],
            "unknown_count": len(unknown), "fractional_rounded": fractional,
            "materials_totals_changed": totals_changed, "update_totals": bool(update_totals),
            "qty_field": cfg.inventory_qty_field, "wh": wh or cfg.default_wh or "",
            "sample": sample}


# ---------------------------------------------------------------- 生产订单
def _sync_orders(conn, cfg, dry_run, *, doc_nos=None, include_local=True, client=None, **_):
    client = client or get_client(cfg)
    local = [r[0] for r in conn.execute("SELECT order_no FROM production_orders ORDER BY order_no")] \
        if include_local else []
    wanted = _codes(_codes(doc_nos) + local)
    errors: list[dict] = []
    raw = client.fetch_production_orders(wanted, errors=errors) if wanted else []
    inserted = updated = unchanged = skipped = 0
    sample, seen = [], set()
    now = _now()
    for r in raw:
        try:
            m = mappers.map_production_order(r)
        except ValueError:
            skipped += 1
            continue
        if m["order_no"] in seen:
            continue
        seen.add(m["order_no"])
        cur = conn.execute("SELECT product_name, status FROM production_orders WHERE order_no=?",
                           (m["order_no"],)).fetchone()
        new_status = None
        if cur is None:
            new_status = m["status"] or "IN_PROGRESS"
            action = "新增"
        else:
            if m["status"] and mappers.STATUS_RANK.get(m["status"], 0) > mappers.STATUS_RANK.get(cur[1], 0):
                new_status = m["status"]
            changed = (m["product_name"] and m["product_name"] != cur[0]) or new_status
            action = "更新" if changed else "不变"
        if len(sample) < SAMPLE_SIZE:
            sample.append({"动作": action, "单号": m["order_no"], "产品料号": m["product_code"],
                           "产品": m["product_name"], "数量": m["qty"], "单位": m["uom"],
                           "开工日": m["start_date"], "完工日": m["complete_date"],
                           "U9状态": m["u9_state"] or "（接口未返回）",
                           "本地状态": new_status or (cur[1] if cur else "")})
        if action == "不变":
            unchanged += 1
            continue
        if action == "新增":
            inserted += 1
            if not dry_run:
                conn.execute("INSERT INTO production_orders(id, order_no, product_name, status,"
                             " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                             (f"u9mo-{m['order_no']}", m["order_no"], m["product_name"], new_status, now, now))
        else:
            updated += 1
            if not dry_run:
                conn.execute("UPDATE production_orders SET product_name=?, status=?, updated_at=?"
                             " WHERE order_no=?",
                             (m["product_name"] or cur[0], new_status or cur[1], now, m["order_no"]))
    not_found = [d for d in wanted if d not in seen]
    return {"requested": len(wanted), "total": len(raw), "inserted": inserted, "updated": updated,
            "unchanged": unchanged, "skipped": skipped, "not_found": not_found[:200],
            "not_found_count": len(not_found), "errors": errors[:50], "sample": sample}


_SYNC_FNS = {
    "items": _sync_items,
    "boms": _sync_boms,
    "inventory": _sync_inventory,
    "orders": _sync_orders,
}


# ---------------------------------------------------------------- 入口
def run_sync(entity: str, conn: sqlite3.Connection, cfg: U9Config,
             dry_run: bool = True, **kwargs) -> dict:
    """执行一次同步。返回 {run_id, log_id, entity, dry_run, status, stats} 并写日志表。"""
    if entity not in _SYNC_FNS:
        raise ValueError(f"未知同步实体: {entity}，可选 {ENTITIES}")
    run_id = uuid.uuid4().hex[:12]
    started = _now()
    try:
        stats = _SYNC_FNS[entity](conn, cfg, dry_run, **kwargs)
    except U9NotImplemented as e:
        conn.rollback()
        _log(conn, run_id, entity, dry_run, status="PENDING_DOC", error=str(e), started_at=started)
        conn.commit()
        return {"run_id": run_id, "log_id": log_id(run_id, entity), "entity": entity,
                "dry_run": dry_run, "status": "PENDING_DOC",
                "message": "U9 接口占位：经典 U9 待实施商提供 WSDL/字段文档后实现", "detail": str(e)}
    except Exception as e:  # noqa: BLE001 - 记日志后抛给上层转中文报错
        conn.rollback()
        _log(conn, run_id, entity, dry_run, status="FAILED", error=str(e), started_at=started)
        conn.commit()
        raise
    if dry_run:
        conn.rollback()  # 双保险：预览绝不留下业务写入
    _log(conn, run_id, entity, dry_run, status="OK", total=stats.get("total", 0),
         inserted=stats.get("inserted", 0), updated=stats.get("updated", 0),
         skipped=stats.get("skipped", 0), detail=stats, started_at=started)
    conn.commit()
    return {"run_id": run_id, "log_id": log_id(run_id, entity), "entity": entity,
            "dry_run": dry_run, "status": "OK", "stats": stats}
