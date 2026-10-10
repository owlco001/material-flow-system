"""智慧工厂 Agent 分析工具集（只读）。

本模块定义供 LLM 通过 function calling 调用的数据分析工具。
所有工具均为只读：run() 仅执行 SELECT 语句，不修改任何数据；
返回值为可 JSON 序列化的数据（dict / list / str / int / float / None）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable


class ToolError(Exception):
    """工具参数错误或执行失败时抛出。"""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str          # 中文描述，给 LLM 看
    parameters: dict          # JSON Schema
    run: Callable[[sqlite3.Connection, dict], Any]  # 只读，返回可 JSON 序列化结果


_MAX_LIMIT = 50


def _clamp_offset(args: dict) -> int:
    """offset 参数钳制到 >=0。"""
    raw = args.get("offset", 0)
    if raw is None:
        return 0
    try:
        offset = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, offset)


def _clamp_limit(args: dict, default: int = 20) -> int:
    """limit 参数钳制到 1..50。"""
    raw = args.get("limit", default)
    if raw is None:
        return default
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"limit 必须是整数，收到: {raw!r}")
    return max(1, min(_MAX_LIMIT, limit))


def _require(args: dict, key: str) -> Any:
    """取必填参数，缺失或为空时抛 ToolError。"""
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolError(f"缺少必填参数: {key}")
    return value


def _fetch_dicts(cur: sqlite3.Cursor) -> list[dict]:
    """把 cursor 结果转成 dict 列表（不改动 connection 的 row_factory）。"""
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_one(cur: sqlite3.Cursor) -> dict | None:
    rows = _fetch_dicts(cur)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# 工具实现（全部只读）
# ---------------------------------------------------------------------------

def _order_detail(conn: sqlite3.Connection, args: dict) -> dict:
    order_no = _require(args, "order_no")
    order = _fetch_one(conn.execute(
        "SELECT * FROM production_orders WHERE order_no = ?", (order_no,)))
    if order is None:
        raise ToolError(f"订单不存在: {order_no}")
    models = _fetch_dicts(conn.execute(
        """SELECT model_code, model_name, planned_quantity, bom_version_id, created_at
           FROM production_order_models WHERE order_id = ? ORDER BY created_at""",
        (order["id"],)))
    task_summary = _fetch_dicts(conn.execute(
        """SELECT status, COUNT(*) AS count FROM assembly_tasks
           WHERE order_no = ? GROUP BY status ORDER BY status""",
        (order_no,)))
    return {
        "order": order,
        "model_count": len(models),
        "models": models,
        "task_status_summary": task_summary,
    }


def _list_orders(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT order_no, product_name, status, created_at FROM production_orders
               WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (status, limit, offset))
    else:
        cur = conn.execute(
            """SELECT order_no, product_name, status, created_at FROM production_orders
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset))
    return _fetch_dicts(cur)


def _material_inventory(conn: sqlite3.Connection, args: dict) -> dict:
    code = _require(args, "code")
    material = _fetch_one(conn.execute(
        "SELECT * FROM materials WHERE code = ?", (code,)))
    if material is None:
        raise ToolError(f"物料不存在: {code}")
    locations = _fetch_dicts(conn.execute(
        """SELECT l.code AS location_code, l.name AS location_name, i.quantity
           FROM inventory i JOIN locations l ON l.id = i.location_id
           WHERE i.material_id = ? ORDER BY l.code""",
        (material["id"],)))
    return {
        "material": material,
        "locations": locations,
        "location_count": len(locations),
    }


def _low_stock(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    return _fetch_dicts(conn.execute(
        """SELECT code, name, specification, unit, total_quantity, available_quantity
           FROM materials ORDER BY available_quantity ASC LIMIT ? OFFSET ?""",
        (limit, offset)))


def _workspace_summary(conn: sqlite3.Connection, args: dict) -> dict:
    def _count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "pending_transfer_requests": _count(
            "SELECT COUNT(*) FROM transfer_requests WHERE status = 'PENDING_APPROVAL'"),
        "pending_handovers": _count(
            "SELECT COUNT(*) FROM material_handovers WHERE status = 'PENDING'"),
        "open_tasks": _count(
            "SELECT COUNT(*) FROM assembly_tasks WHERE status <> 'COMPLETED'"),
        "open_exceptions": _count(
            "SELECT COUNT(*) FROM exceptions WHERE status = 'PENDING'"),
    }


def _list_transfer_requests(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT id, type, document_no, status, created_at FROM transfer_requests
               WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (status, limit, offset))
    else:
        cur = conn.execute(
            """SELECT id, type, document_no, status, created_at FROM transfer_requests
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset))
    return _fetch_dicts(cur)


def _list_handovers(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT id, work_item_id, quantity, status, created_at FROM material_handovers
               WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (status, limit, offset))
    else:
        cur = conn.execute(
            """SELECT id, work_item_id, quantity, status, created_at FROM material_handovers
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset))
    return _fetch_dicts(cur)


def _list_exceptions(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT id, type, difference, status, description, created_at FROM exceptions
               WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (status, limit, offset))
    else:
        cur = conn.execute(
            """SELECT id, type, difference, status, description, created_at FROM exceptions
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset))
    return _fetch_dicts(cur)


def _task_progress(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    order_no = args.get("order_no")
    if order_no:
        cur = conn.execute(
            """SELECT order_no, device_no, status, progress_stage, updated_at
               FROM assembly_tasks WHERE order_no = ?
               ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
            (order_no, limit))
    else:
        cur = conn.execute(
            """SELECT order_no, device_no, status, progress_stage, updated_at
               FROM assembly_tasks ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
            (limit, offset))
    return _fetch_dicts(cur)


def _labor_summary(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    return _fetch_dicts(conn.execute(
        """SELECT worker_user_id, type,
                  SUM(COALESCE(duration_minutes, 0)) AS total_minutes,
                  COUNT(*) AS record_count
           FROM labor_records
           WHERE started_at >= date('now', '-30 days')
           GROUP BY worker_user_id, type
           ORDER BY total_minutes DESC LIMIT ? OFFSET ?""",
        (limit, offset)))


# ---------------------------------------------------------------------------
# 物料主档 / BOM（只读）
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _require_table(conn: sqlite3.Connection, name: str, hint: str) -> None:
    if not _table_exists(conn, name):
        raise ToolError(hint)


_MM_BRIEF = ("code", "name", "specification", "drawing_no", "unit_name", "item_form",
             "main_category_name", "storage_location", "device_no", "project_no")


def _search_material_master(conn: sqlite3.Connection, args: dict) -> dict:
    _require_table(conn, "material_master", "物料主档尚未导入")
    limit = _clamp_limit(args)
    offset = _clamp_offset(args)
    where, params = [], []
    kw = str(args.get("keyword") or "").strip()
    if kw:
        like = f"%{kw}%"
        where.append("(code LIKE ? OR name LIKE ? OR specification LIKE ? OR drawing_no LIKE ? OR t6_code LIKE ?)")
        params += [like] * 5
    for key in ("device_no", "project_no", "item_form", "storage_location", "main_category_name"):
        v = str(args.get(key) or "").strip()
        if v:
            where.append(f"{key} LIKE ?")
            params.append(f"%{v}%")
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM material_master{sql_where}", params).fetchone()[0]
    rows = _fetch_dicts(conn.execute(
        f"SELECT {', '.join(_MM_BRIEF)} FROM material_master{sql_where} ORDER BY code LIMIT ? OFFSET ?",
        (*params, limit, offset)))
    return {"total": total, "offset": offset, "count": len(rows), "items": rows}


def _material_master_detail(conn: sqlite3.Connection, args: dict) -> dict:
    _require_table(conn, "material_master", "物料主档尚未导入")
    code = str(_require(args, "code")).strip()
    row = _fetch_one(conn.execute("SELECT * FROM material_master WHERE code = ?", (code,)))
    if row is None:
        raise ToolError(f"物料主档中没有料号: {code}（可先用 search_material_master 模糊查找）")
    try:
        from app.material_master import FIELD_LABELS
    except Exception:  # noqa: BLE001
        FIELD_LABELS = {}
    skip = {"extra_json", "row_hash", "source_batch_id"}
    detail = {FIELD_LABELS.get(k, k): v for k, v in row.items()
              if k not in skip and v not in (None, "")}
    out: dict = {"material": detail}
    if _table_exists(conn, "materials"):
        inv = _fetch_one(conn.execute("SELECT * FROM materials WHERE code = ?", (code,)))
        if inv:
            out["inventory"] = inv
    return out


_MM_GROUPS = {"main_category": "main_category_name", "item_form": "item_form",
              "storage_location": "storage_location", "project_no": "project_no",
              "device_no": "device_no", "buyer": "buyer_name", "unit": "unit_name"}


def _material_master_stats(conn: sqlite3.Connection, args: dict) -> dict:
    _require_table(conn, "material_master", "物料主档尚未导入")
    group = str(args.get("group_by") or "main_category")
    col = _MM_GROUPS.get(group)
    if col is None:
        raise ToolError(f"group_by 仅支持: {', '.join(_MM_GROUPS)}")
    limit = _clamp_limit(args, default=30)
    total = conn.execute("SELECT COUNT(*) FROM material_master").fetchone()[0]
    rows = _fetch_dicts(conn.execute(
        f"""SELECT COALESCE(NULLIF({col}, ''), '(空)') AS value, COUNT(*) AS count
            FROM material_master GROUP BY value ORDER BY count DESC LIMIT ?""", (limit,)))
    return {"total": total, "group_by": group, "groups": rows}


def _bom_items(conn: sqlite3.Connection, args: dict) -> dict:
    """设备/机型的 BOM 明细：优先已发布的机型 BOM，其次最新一批汇总 BOM。"""
    code = str(_require(args, "code")).strip()
    limit = _clamp_limit(args, default=50)
    offset = _clamp_offset(args)
    if _table_exists(conn, "bom_versions"):
        ver = _fetch_one(conn.execute(
            """SELECT id, model_code, version_no, published_at FROM bom_versions
               WHERE model_code = ? AND status = 'PUBLISHED' ORDER BY version_no DESC LIMIT 1""",
            (code,)))
        if ver:
            total = conn.execute("SELECT COUNT(*) FROM bom_items WHERE bom_version_id=?",
                                 (ver["id"],)).fetchone()[0]
            items = _fetch_dicts(conn.execute(
                """SELECT line_no, material_code, material_name, specification, unit, quantity, scrap_rate
                   FROM bom_items WHERE bom_version_id = ? ORDER BY line_no LIMIT ? OFFSET ?""",
                (ver["id"], limit, offset)))
            return {"source": "机型BOM(已发布)", "bom": ver, "total": total, "items": items}
    if _table_exists(conn, "agg_bom_items"):
        batch = _fetch_one(conn.execute(
            """SELECT b.id, b.project_name, b.file_name, b.created_at FROM agg_bom_batches b
               JOIN agg_bom_items i ON i.batch_id = b.id
               WHERE i.device_code = ? OR i.parent_code = ?
               ORDER BY b.created_at DESC LIMIT 1""", (code, code)))
        if batch:
            cond = "batch_id = ? AND (device_code = ? OR parent_code = ?)"
            total = conn.execute(f"SELECT COUNT(*) FROM agg_bom_items WHERE {cond}",
                                 (batch["id"], code, code)).fetchone()[0]
            items = _fetch_dicts(conn.execute(
                f"""SELECT line_no, level_no, parent_code, material_code, material_name, specification,
                           unit, quantity, material_form
                    FROM agg_bom_items WHERE {cond} ORDER BY line_no LIMIT ? OFFSET ?""",
                (batch["id"], code, code, limit, offset)))
            return {"source": "汇总BOM(最新导入批次)", "batch": batch, "total": total, "items": items}
    raise ToolError(f"没有找到 {code} 的 BOM（已发布机型 BOM 与汇总 BOM 中均无）")


def _bom_where_used(conn: sqlite3.Connection, args: dict) -> dict:
    """物料反查：哪些机型/设备的 BOM 用到了它。"""
    code = str(_require(args, "material_code")).strip()
    limit = _clamp_limit(args, default=30)
    out: dict = {"material_code": code, "model_boms": [], "agg_boms": []}
    if _table_exists(conn, "bom_items"):
        out["model_boms"] = _fetch_dicts(conn.execute(
            """SELECT v.model_code, v.version_no, v.status, i.quantity, i.unit
               FROM bom_items i JOIN bom_versions v ON v.id = i.bom_version_id
               WHERE i.material_code = ? ORDER BY v.model_code, v.version_no DESC LIMIT ?""",
            (code, limit)))
    if _table_exists(conn, "agg_bom_items"):
        out["agg_boms"] = _fetch_dicts(conn.execute(
            """SELECT b.project_name, i.device_code, i.device_name, i.parent_code, i.quantity, i.unit
               FROM agg_bom_items i JOIN agg_bom_batches b ON b.id = i.batch_id
               WHERE i.material_code = ? ORDER BY b.created_at DESC, i.device_code LIMIT ?""",
            (code, limit)))
    if not out["model_boms"] and not out["agg_boms"]:
        raise ToolError(f"没有 BOM 用到物料 {code}")
    return out


# ---------------------------------------------------------------------------
# 缺料分析：需求（订单需求 / 机型 BOM / 汇总 BOM）× 库存
# ---------------------------------------------------------------------------

def _stock_by_code(conn: sqlite3.Connection, codes: list[str]) -> tuple[dict[str, float], str]:
    """可用库存：优先 materials.available_quantity；系统里没有的料号再看最新一次库存快照（U9 导入）。"""
    stock: dict[str, float] = {}
    sources = []
    codes = list(dict.fromkeys(c for c in codes if c))
    for i in range(0, len(codes), 500):
        chunk = codes[i:i + 500]
        ph = ",".join("?" * len(chunk))
        if _table_exists(conn, "materials"):
            for code, qty in conn.execute(
                    f"SELECT code, available_quantity FROM materials WHERE code IN ({ph})", chunk):
                stock[code] = float(qty or 0)
    if stock:
        sources.append("系统库存")
    missing = [c for c in codes if c not in stock]
    if missing and _table_exists(conn, "inventory_snapshots"):
        snap = conn.execute(
            "SELECT id, snapshot_name FROM inventory_snapshots ORDER BY created_at DESC LIMIT 1").fetchone()
        if snap:
            hit = False
            for i in range(0, len(missing), 500):
                chunk = missing[i:i + 500]
                ph = ",".join("?" * len(chunk))
                for code, qty in conn.execute(
                        f"""SELECT material_code, SUM(CAST(available_quantity_decimal AS REAL))
                            FROM inventory_snapshot_rows WHERE snapshot_id = ? AND material_code IN ({ph})
                            GROUP BY material_code""", (snap[0], *chunk)):
                    stock[code] = float(qty or 0)
                    hit = True
            if hit:
                sources.append(f"库存快照「{snap[1]}」")
    return stock, "、".join(sources) or "无库存记录"


def _shortage_rows(conn: sqlite3.Connection, need: dict[str, dict], only_short: bool, limit: int) -> dict:
    stock, stock_src = _stock_by_code(conn, list(need))
    rows = []
    for code, n in need.items():
        req = round(n["required"], 4)
        avail = round(stock.get(code, 0.0), 4)
        short = round(max(req - avail, 0.0), 4)
        rows.append({"material_code": code, "material_name": n.get("name", ""), "unit": n.get("unit", ""),
                     "required": req, "available": avail, "shortage": short,
                     "no_stock_record": code not in stock})
    rows.sort(key=lambda r: (-r["shortage"], r["material_code"]))
    short_rows = [r for r in rows if r["shortage"] > 0]
    shown = short_rows if only_short else rows
    return {
        "stock_source": stock_src,
        "material_types": len(rows),
        "short_types": len(short_rows),
        "fully_covered": not short_rows,
        "total": len(shown),
        "items": shown[:limit],
        "truncated": len(shown) > limit,
    }


def _add_need(need: dict, code: str, name: str, unit: str, qty: float) -> None:
    if not code or not qty or qty <= 0:
        return
    n = need.setdefault(code, {"name": name or "", "unit": unit or "", "required": 0.0})
    n["required"] += float(qty)


def _material_shortage(conn: sqlite3.Connection, args: dict) -> dict:
    order_no = str(args.get("order_no") or "").strip()
    model_code = str(args.get("model_code") or "").strip()
    dev = args.get("device_codes") or []
    device_codes = [str(x).strip() for x in (dev.split(",") if isinstance(dev, str) else dev) if str(x).strip()]
    try:
        qty = float(args.get("quantity") or 1)
    except (TypeError, ValueError):
        raise ToolError("quantity 必须是数字")
    if qty <= 0 or qty > 100000:
        raise ToolError("quantity 需在 0-100000 之间")
    only_short = args.get("only_short", True) is not False
    limit = _clamp_limit(args, default=30)
    need: dict[str, dict] = {}

    if order_no:
        _require_table(conn, "production_orders", "生产订单")
        order = _fetch_one(conn.execute(
            "SELECT id, order_no, product_name, status FROM production_orders WHERE order_no = ?", (order_no,)))
        if not order:
            raise ToolError(f"订单 {order_no} 不存在")
        # 1) 订单上已维护的物料需求（含已到货、在库口径）——直接用订单自己的数
        if _table_exists(conn, "order_material_requirements"):
            reqs = _fetch_dicts(conn.execute(
                """SELECT m.code AS material_code, m.name AS material_name, m.unit,
                          SUM(r.required_quantity) AS required, SUM(r.arrived_quantity) AS arrived,
                          SUM(r.in_stock_quantity) AS in_stock
                   FROM order_material_requirements r JOIN materials m ON m.id = r.material_id
                   WHERE r.order_id = ? GROUP BY m.code ORDER BY m.code""", (order["id"],)))
            if reqs:
                items = []
                for r in reqs:
                    short = max((r["required"] or 0) - (r["in_stock"] or 0), 0)
                    items.append({"material_code": r["material_code"], "material_name": r["material_name"],
                                  "unit": r["unit"], "required": r["required"], "arrived": r["arrived"],
                                  "in_stock": r["in_stock"], "shortage": short,
                                  "not_arrived": max((r["required"] or 0) - (r["arrived"] or 0), 0)})
                items.sort(key=lambda x: (-x["shortage"], x["material_code"]))
                short_items = [x for x in items if x["shortage"] > 0]
                shown = short_items if only_short else items
                return {"source": "订单物料需求", "order": order, "material_types": len(items),
                        "short_types": len(short_items), "fully_covered": not short_items,
                        "total": len(shown), "items": shown[:limit], "truncated": len(shown) > limit,
                        "note": "shortage = 需求 − 在库；not_arrived = 需求 − 已到货"}
        # 2) 订单没维护需求：按订单机型的已发布 BOM × 计划台数展开
        models = _fetch_dicts(conn.execute(
            "SELECT model_code, planned_quantity, bom_version_id FROM production_order_models WHERE order_id = ?",
            (order["id"],))) if _table_exists(conn, "production_order_models") else []
        if not models:
            raise ToolError(f"订单 {order_no} 既没有物料需求，也没有关联机型，无法计算缺料")
        no_bom = []
        for m in models:
            vid = m["bom_version_id"] or _latest_bom_version(conn, m["model_code"])
            if not vid:
                no_bom.append(m["model_code"])
                continue
            _explode_model_bom(conn, need, vid, float(m["planned_quantity"] or 0))
        if not need:
            raise ToolError(f"订单 {order_no} 的机型都没有已发布 BOM：{'、'.join(no_bom)}")
        out = _shortage_rows(conn, need, only_short, limit)
        out.update({"source": "订单机型 BOM × 计划台数", "order": order,
                    "models": [{"model_code": m["model_code"], "planned_quantity": m["planned_quantity"]} for m in models],
                    "models_without_bom": no_bom})
        return out

    if model_code:
        vid = _latest_bom_version(conn, model_code)
        if not vid:
            raise ToolError(f"机型 {model_code} 没有已发布 BOM")
        _explode_model_bom(conn, need, vid, qty)
        out = _shortage_rows(conn, need, only_short, limit)
        out.update({"source": f"机型 BOM（已发布）× {qty:g} 台", "model_code": model_code})
        return out

    if device_codes:
        _require_table(conn, "agg_bom_items", "汇总 BOM")
        batch_id = str(args.get("batch_id") or "").strip()
        if not batch_id:
            ph = ",".join("?" * len(device_codes))
            row = conn.execute(
                f"""SELECT b.id FROM agg_bom_batches b JOIN agg_bom_items i ON i.batch_id = b.id
                    WHERE i.device_code IN ({ph}) ORDER BY b.created_at DESC LIMIT 1""", device_codes).fetchone()
            if not row:
                raise ToolError(f"汇总 BOM 中没有设备：{'、'.join(device_codes)}")
            batch_id = row[0]
        ph = ",".join("?" * len(device_codes))
        found = {r[0] for r in conn.execute(
            f"SELECT DISTINCT device_code FROM agg_bom_items WHERE batch_id = ? AND device_code IN ({ph})",
            (batch_id, *device_codes))}
        for r in conn.execute(
                f"""SELECT material_code, MAX(material_name), MAX(unit), SUM(quantity) FROM agg_bom_items
                    WHERE batch_id = ? AND device_code IN ({ph}) AND quantity > 0 GROUP BY material_code""",
                (batch_id, *device_codes)):
            _add_need(need, r[0], r[1], r[2], (r[3] or 0) * qty)
        if not need:
            raise ToolError("这些设备在汇总 BOM 中没有物料行")
        out = _shortage_rows(conn, need, only_short, limit)
        out.update({"source": f"汇总 BOM × {qty:g} 套", "batch_id": batch_id, "devices": sorted(found),
                    "devices_not_found": [d for d in device_codes if d not in found]})
        return out

    # 不指定：所有进行中订单的物料需求汇总
    _require_table(conn, "order_material_requirements", "订单物料需求")
    rows = _fetch_dicts(conn.execute(
        """SELECT o.order_no, m.code AS material_code, m.name AS material_name, m.unit,
                  SUM(r.required_quantity) AS required, SUM(r.in_stock_quantity) AS in_stock
           FROM order_material_requirements r
           JOIN production_orders o ON o.id = r.order_id
           JOIN materials m ON m.id = r.material_id
           WHERE o.status NOT IN ('COMPLETED','CLOSED','CANCELLED')
           GROUP BY o.order_no, m.code HAVING SUM(r.required_quantity) > SUM(r.in_stock_quantity)
           ORDER BY (SUM(r.required_quantity) - SUM(r.in_stock_quantity)) DESC"""))
    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r["material_code"], {"material_code": r["material_code"], "material_name": r["material_name"],
                                                "unit": r["unit"], "shortage": 0, "orders": []})
        a["shortage"] += (r["required"] or 0) - (r["in_stock"] or 0)
        a["orders"].append(r["order_no"])
    items = sorted(agg.values(), key=lambda x: (-x["shortage"], x["material_code"]))
    return {"source": "进行中订单的物料需求（需求 − 在库）", "short_types": len(items),
            "orders_affected": len({r["order_no"] for r in rows}), "total": len(items),
            "items": items[:limit], "truncated": len(items) > limit}


def _latest_bom_version(conn: sqlite3.Connection, model_code: str) -> str | None:
    if not _table_exists(conn, "bom_versions"):
        return None
    row = conn.execute(
        """SELECT id FROM bom_versions WHERE model_code = ? AND status = 'PUBLISHED'
           ORDER BY version_no DESC LIMIT 1""", (model_code,)).fetchone()
    return row[0] if row else None


def _explode_model_bom(conn: sqlite3.Connection, need: dict, version_id: str, qty: float) -> None:
    for code, name, unit, q, scrap in conn.execute(
            "SELECT material_code, material_name, unit, quantity, scrap_rate FROM bom_items WHERE bom_version_id = ?",
            (version_id,)):
        _add_need(need, code, name, unit, (q or 0) * qty / (1 - (scrap or 0)))


# ---------------------------------------------------------------------------
# 趋势与对比（按北京时间切日/周/月，和上一个等长周期对比）
# ---------------------------------------------------------------------------

# metric -> (表, 时间列, 值表达式, 额外条件, 中文名, 单位)
TREND_METRICS: dict[str, tuple[str, str, str, str, str, str]] = {
    "exceptions": ("exceptions", "created_at", "COUNT(*)", "", "异常上报", "条"),
    "labor_minutes": ("labor_records", "started_at", "SUM(COALESCE(duration_minutes,0))", "", "工时", "分钟"),
    "handovers": ("material_handovers", "created_at", "COUNT(*)", "", "物料交接", "笔"),
    "handover_quantity": ("material_handovers", "created_at", "SUM(quantity)", "AND status='CONFIRMED'", "已确认交接数量", "件"),
    "transfers": ("transfer_requests", "created_at", "COUNT(*)", "", "流转申请", "笔"),
    "tasks_completed": ("assembly_tasks", "completed_at", "COUNT(*)", "AND completed_at IS NOT NULL", "完工装配任务", "个"),
    "stocktakes": ("stocktakes", "created_at", "COUNT(*)", "", "盘点", "次"),
}
_GRAN = {"day": ("%Y-%m-%d", 1), "week": ("%Y-W%W", 7), "month": ("%Y-%m", 30)}


def _trend_compare(conn: sqlite3.Connection, args: dict) -> dict:
    from datetime import datetime, timedelta, timezone
    metric = str(_require(args, "metric")).strip()
    if metric not in TREND_METRICS:
        raise ToolError(f"metric 只能是：{'、'.join(TREND_METRICS)}")
    table, col, expr, extra, label, unit = TREND_METRICS[metric]
    _require_table(conn, table, label)
    gran = str(args.get("granularity") or "day").strip()
    if gran not in _GRAN:
        raise ToolError("granularity 只能是 day / week / month")
    fmt, unit_days = _GRAN[gran]
    try:
        periods = int(args.get("periods") or {"day": 14, "week": 8, "month": 6}[gran])
    except (TypeError, ValueError):
        raise ToolError("periods 必须是整数")
    periods = max(1, min(periods, 60))
    group_by = str(args.get("group_by") or "").strip()
    group_cols = {"exceptions": {"type", "status"}, "labor_minutes": {"type", "worker_user_id"},
                  "transfers": {"type", "status"}, "handovers": {"status"}, "handover_quantity": set(),
                  "tasks_completed": {"assigned_assembler_id"}, "stocktakes": {"status"}}[metric]
    if group_by and group_by not in group_cols:
        raise ToolError(f"{metric} 的 group_by 只能是：{'、'.join(sorted(group_cols)) or '（不支持分组）'}")

    bj = timezone(timedelta(hours=8))
    today = datetime.now(bj).date()
    if gran == "day":
        start = today - timedelta(days=periods - 1)
    elif gran == "week":
        start = today - timedelta(days=today.weekday()) - timedelta(weeks=periods - 1)
    else:
        y, m = today.year, today.month - (periods - 1)
        while m <= 0:
            y, m = y - 1, m + 12
        start = today.replace(year=y, month=m, day=1)
    span = (today - start).days + 1
    prev_start = start - timedelta(days=span)
    # 存的是 UTC ISO 时间；换成北京时间再切桶
    local = f"datetime(substr({col},1,19), '+8 hours')"
    where = f"{col} IS NOT NULL AND date({local}) >= ? {extra}"

    rows = conn.execute(
        f"SELECT strftime('{fmt}', {local}) AS bucket, {expr} FROM {table} "
        f"WHERE {where} AND date({local}) <= ? GROUP BY bucket ORDER BY bucket",
        (start.isoformat(), today.isoformat())).fetchall()
    by_bucket = {r[0]: (r[1] or 0) for r in rows}
    series, d = [], start
    seen = set()
    while d <= today:
        b = d.strftime(fmt)
        if b not in seen:
            seen.add(b)
            series.append({"period": b, "value": by_bucket.get(b, 0)})
        d += timedelta(days=1)

    def total(a, b):
        return conn.execute(
            f"SELECT {expr} FROM {table} WHERE {where} AND date({local}) <= ?",
            (a.isoformat(), b.isoformat())).fetchone()[0] or 0

    cur = total(start, today)
    prev = total(prev_start, start - timedelta(days=1))
    out = {
        "metric": metric, "label": label, "unit": unit, "granularity": gran,
        "range": {"from": start.isoformat(), "to": today.isoformat(), "timezone": "Asia/Shanghai"},
        "previous_range": {"from": prev_start.isoformat(), "to": (start - timedelta(days=1)).isoformat()},
        "series": series, "current_total": cur, "previous_total": prev,
        "change": cur - prev,
        "change_pct": round((cur - prev) * 100.0 / prev, 1) if prev else None,
        "note": "本期含今天（未过完）；change_pct 为 null 表示上期为 0",
    }
    if group_by:
        out["breakdown"] = _fetch_dicts(conn.execute(
            f"SELECT {group_by} AS key, {expr} AS value FROM {table} WHERE {where} AND date({local}) <= ? "
            f"GROUP BY {group_by} ORDER BY value DESC LIMIT 20", (start.isoformat(), today.isoformat())))
    return out


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

ANALYSIS_TOOLS: list[Tool] = [
    Tool(
        name="order_detail",
        description=(
            "查询单个生产订单的详情。返回订单整行记录、关联的产品模型数量与明细、"
            "以及该订单下装配任务按状态的汇总。参数 order_no 为必填的订单号。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "生产订单号，例如 PO-2026-001"},
            },
            "required": ["order_no"],
        },
        run=_order_detail,
    ),
    Tool(
        name="list_orders",
        description=(
            "列出生产订单，按创建时间倒序列出。返回订单号、产品名称、状态、创建时间。"
            "可选按状态过滤（如 IN_PROGRESS / COMPLETED）；limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "订单状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_list_orders,
    ),
    Tool(
        name="material_inventory",
        description=(
            "查询单个物料的库存详情。返回物料主数据整行，以及该物料在各库位的"
            "分布数量（库位编码、库位名称、数量）。参数 code 为必填的物料编码。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "物料编码，例如 MTR-001"},
            },
            "required": ["code"],
        },
        run=_material_inventory,
    ),
    Tool(
        name="low_stock",
        description=(
            "查询低库存物料。按可用数量 available_quantity 升序排列，返回物料编码、"
            "名称、规格、单位、总数量、可用数量。limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_low_stock,
    ),
    Tool(
        name="workspace_summary",
        description=(
            "工作台待办汇总，一次性返回四个关键数字：待审批的流转申请数、"
            "待确认的交接数、未完成的装配任务数、未处理的异常数。无参数。"
        ),
        parameters={"type": "object", "properties": {}},
        run=_workspace_summary,
    ),
    Tool(
        name="list_transfer_requests",
        description=(
            "列出流转申请单，按创建时间倒序。返回 id、类型、单据号、状态、创建时间。"
            "可选按状态过滤（如 PENDING_APPROVAL / APPROVED / REJECTED / EXECUTED）；"
            "limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "单据状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_list_transfer_requests,
    ),
    Tool(
        name="list_handovers",
        description=(
            "列出物料交接记录，按创建时间倒序。返回 id、工单项 id、数量、状态、创建时间。"
            "可选按状态过滤（如 PENDING / CONFIRMED / REJECTED / CANCELLED）；"
            "limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "交接状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_list_handovers,
    ),
    Tool(
        name="list_exceptions",
        description=(
            "列出异常记录，按创建时间倒序。返回 id、异常类型、差异数量、状态、"
            "描述、创建时间。可选按状态过滤；limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "异常状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_list_exceptions,
    ),
    Tool(
        name="task_progress",
        description=(
            "查询装配任务进度，按更新时间倒序。返回订单号、机台号、状态、"
            "进度阶段（0-3）、更新时间。可选按订单号过滤；limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "生产订单号过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_task_progress,
    ),
    Tool(
        name="labor_summary",
        description=(
            "工时汇总：按工人 + 工时类型（ASSEMBLY 装配 / TEMPORARY_TRANSFER 临调）"
            "聚合最近 30 天的工时，返回工人 id、类型、总分钟数、记录数，"
            "按总分钟数降序。limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
                "offset": {"type": "integer", "description": "分页偏移，默认 0。数据多时可用 offset 翻页取全量", "default": 0},
            },
        },
        run=_labor_summary,
    ),
    Tool(
        name="search_material_master",
        description=(
            "在 U9 物料主档（约 3.7 万料号）中模糊搜索。keyword 同时匹配料号、品名、规格、U9图号、T6料号；"
            "可再按设备编号、项目号、形态属性、存储地点、主分类过滤。返回 total 总数与分页结果。"
            "用户只说了物料名称或图号时，先用它找到准确料号。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "料号/品名/规格/图号关键词"},
                "device_no": {"type": "string"}, "project_no": {"type": "string"},
                "item_form": {"type": "string", "description": "形态属性，如 采购件、自制件"},
                "storage_location": {"type": "string"}, "main_category_name": {"type": "string"},
                "limit": {"type": "integer", "description": "每页条数，1-50"},
                "offset": {"type": "integer", "description": "分页偏移"},
            },
        },
        run=_search_material_master,
    ),
    Tool(
        name="material_master_detail",
        description="按准确料号查询物料主档全部字段（中文字段名），若有库存记录一并返回。",
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "料号"}},
            "required": ["code"],
        },
        run=_material_master_detail,
    ),
    Tool(
        name="material_master_stats",
        description=(
            "物料主档分组统计：返回总料号数及按某字段分组的数量（降序）。"
            "group_by 可选 main_category / item_form / storage_location / project_no / device_no / buyer / unit。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["main_category", "item_form", "storage_location",
                                                        "project_no", "device_no", "buyer", "unit"]},
                "limit": {"type": "integer", "description": "返回分组数，1-50"},
            },
        },
        run=_material_master_stats,
    ),
    Tool(
        name="bom_items",
        description=(
            "查询机型或设备的 BOM 明细。优先返回已发布的机型 BOM，没有则返回最新导入的汇总 BOM 中该设备/母项的子件。"
            "code 为机型编码、设备编码或母项料号。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "机型编码 / 设备编码 / 母项料号"},
                "limit": {"type": "integer", "description": "每页条数，1-50"},
                "offset": {"type": "integer", "description": "分页偏移"},
            },
            "required": ["code"],
        },
        run=_bom_items,
    ),
    Tool(
        name="bom_where_used",
        description="物料反查：列出用到某料号的机型 BOM 与汇总 BOM（设备、母项、用量）。",
        parameters={
            "type": "object",
            "properties": {
                "material_code": {"type": "string", "description": "料号"},
                "limit": {"type": "integer", "description": "条数，1-50"},
            },
            "required": ["material_code"],
        },
        run=_bom_where_used,
    ),
    Tool(
        name="material_shortage",
        description=(
            "缺料分析：需求 × 可用库存，算出缺哪些料、各缺多少。四种用法（只给一种）："
            "order_no=某订单（优先用订单维护的物料需求，否则按订单机型 BOM × 计划台数展开）；"
            "model_code + quantity=某机型做 N 台；device_codes(+quantity)=汇总 BOM 里的设备做 N 套；"
            "都不给=所有进行中订单的缺料汇总。数字只能引用本工具返回值。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "生产订单号"},
                "model_code": {"type": "string", "description": "机型编码（用已发布 BOM）"},
                "device_codes": {"type": "array", "items": {"type": "string"}, "description": "汇总 BOM 中的设备编码"},
                "batch_id": {"type": "string", "description": "汇总 BOM 批次，可省略（默认最新含这些设备的批次）"},
                "quantity": {"type": "number", "description": "台数/套数，默认 1"},
                "only_short": {"type": "boolean", "description": "只返回缺料行，默认 true"},
                "limit": {"type": "integer", "description": "返回行数，1-50"},
            },
        },
        run=_material_shortage,
    ),
    Tool(
        name="trend_compare",
        description=(
            "趋势与对比：按北京时间按天/周/月统计某指标，并与上一个等长周期对比（环比）。"
            "metric：exceptions 异常上报数、labor_minutes 工时分钟、handovers 物料交接笔数、"
            "handover_quantity 已确认交接数量、transfers 流转申请数、tasks_completed 完工装配任务数、stocktakes 盘点次数。"
            "可选 group_by 看构成（如异常按 type、工时按 worker_user_id）。问“最近/本周/上周/趋势/同比环比/变多变少”时用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": list(TREND_METRICS)},
                "granularity": {"type": "string", "enum": ["day", "week", "month"], "description": "默认 day"},
                "periods": {"type": "integer", "description": "统计多少个周期（含当前），默认 天14/周8/月6，最多 60"},
                "group_by": {"type": "string", "description": "分组字段，如 type / status / worker_user_id"},
            },
            "required": ["metric"],
        },
        run=_trend_compare,
    ),
]


def openai_tools_schema(tools: list[Tool]) -> list[dict]:
    """把工具列表转成 OpenAI function calling 格式的 tools 参数。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def get_tool(tools: list[Tool], name: str) -> Tool | None:
    """按名称查找工具，找不到返回 None。"""
    for t in tools:
        if t.name == name:
            return t
    return None
