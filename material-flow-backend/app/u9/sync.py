"""U9 同步编排（Phase 1：只读拉取 U9 → 本地）。

入口 run_sync(entity, conn, cfg, dry_run=True, **kwargs)：
  items      料品档案：默认取本地 materials 全部编码 + codes 参数，按编码批量查 U9
  boms       BOM：product_codes 参数（母件/设备编码），多层展开后写一个 agg_bom 批次
  inventory  库存：默认按本地 materials 全部料号逐个查（scope=local）；codes 参数指定料号；
             scope=all 才按组织 + 存储地点整体查询（现场 U9 整体查询会报 SQL CLR 错误）
  orders     生产订单：doc_nos 参数 + 本地已有 production_orders.order_no

行为：
  - dry_run=True（默认）：只拉取 + 映射 + 统计 + 样例行，不写任何业务表；记一条日志
  - dry_run=False：幂等 upsert（materials.code / production_orders.order_no / inventory 料号+库位；
    BOM 内容未变则不重复建批次）
  - 每次运行写 u9_sync_logs（detail_json 含统计与样例，管理页据此展示预览）
"""
from __future__ import annotations

from datetime import datetime, timezone

import hashlib
import json
import sqlite3
import threading
import time
import uuid

from . import mappers
from .client import U9Error, U9NotImplemented, get_client
from .config import U9Config

ENTITIES = ("items", "categories", "boms", "inventory", "orders", "projects")

# 同一进程内同一时间只跑一个同步：U9 同步是长耗时阻塞调用，重复点击只会排队拖垮服务
_SYNC_LOCK = threading.Lock()
_RUNNING: dict = {}


class U9SyncBusy(U9Error):
    """已有同步在运行。"""


def running_sync() -> dict | None:
    """当前正在运行的同步（entity/started_at），没有则 None。"""
    return dict(_RUNNING) if _RUNNING else None


def _check_keys(cfg: U9Config, keys: list, label: str) -> None:
    limit = getattr(cfg, "sync_max_keys", 0) or 0
    if limit and len(keys) > limit:
        raise U9Error(f"{label}数量 {len(keys)} 超过单次上限 {limit}（U9_SYNC_MAX_KEYS），请分批指定后重试")
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
    # 与主程序一致存 UTC ISO（带 +00:00）；管理台 local_dt 再转北京时间显示。
    # 0.8.2 及以前存的是无时区的本地时间，被当作 UTC 再 +8，显示晚 8 小时。
    return datetime.now(timezone.utc).isoformat()


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
    _check_keys(cfg, wanted, "料号")
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
        cur = conn.execute("SELECT name, specification, unit, category, category_code FROM materials"
                           " WHERE code=?", (m["code"],)).fetchone()
        new_cat = (m["category"] or (cur[3] if cur else "") or "", m.get("category_code") or (cur[4] if cur else "") or "")
        action = "新增" if not cur else (
            "不变" if ((cur[0] or "", cur[1] or "", cur[2] or "") == (m["name"], m["specification"], m["unit"])
                     and (cur[3] or "", cur[4] or "") == new_cat)
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
                conn.execute("UPDATE materials SET name=?, specification=?, unit=?, category=?, category_code=?,"
                             " version=version+1 WHERE code=?",
                             (m["name"], m["specification"], m["unit"], new_cat[0] or None, new_cat[1] or None,
                              m["code"]))
        else:
            inserted += 1
            if not dry_run:
                conn.execute("INSERT INTO materials(id, code, name, specification, unit, category, category_code,"
                             " total_quantity, available_quantity, version) VALUES(?,?,?,?,?,?,?,0,0,1)",
                             (f"u9-{m['code']}", m["code"], m["name"], m["specification"], m["unit"],
                              new_cat[0] or None, new_cat[1] or None))
    return {"requested": len(wanted), "total": len(raw), "mapped": len(mapped),
            "inserted": inserted, "updated": updated, "unchanged": unchanged, "skipped": skipped,
            "not_found": not_found[:200], "not_found_count": len(not_found),
            "errors": errors[:50], "sample": sample}


# ---------------------------------------------------------------- 物料分类
def _sync_categories(conn, cfg, dry_run, *, client=None, **_):
    """U9 主分类全集 → material_categories；本地已有物料按料号回填 category / category_code。
    只改分类字段，不新增物料、不动数量。"""
    client = client or get_client(cfg)
    cat_rows, item_map = client.fetch_categories()
    tree = mappers.build_category_tree(cat_rows)
    names = {t["code"]: t["name"] for t in tree}
    now = _now()
    existing = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT code, name, parent_code FROM material_categories")}
    ins = upd = same = 0
    for t in tree:
        cur = existing.get(t["code"])
        if cur == (t["name"], t["parent_code"]):
            same += 1
            continue
        if cur is None:
            ins += 1
        else:
            upd += 1
        if not dry_run:
            conn.execute("INSERT INTO material_categories(code, name, parent_code, level, updated_at)"
                         " VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
                         " parent_code=excluded.parent_code, level=excluded.level, updated_at=excluded.updated_at",
                         (t["code"], t["name"], t["parent_code"], t["level"], now))
    mat_upd = mat_same = 0
    not_found = []
    sample = []
    for code, cat, cat_code in conn.execute(
            "SELECT code, category, category_code FROM materials ORDER BY code").fetchall():
        cc = item_map.get(code)
        if not cc:
            not_found.append(code)
            continue
        name = names.get(cc, cat or "")
        if (cat or "", cat_code or "") == (name, cc):
            mat_same += 1
            continue
        mat_upd += 1
        if len(sample) < SAMPLE_SIZE:
            sample.append({"料号": code, "分类编码": cc, "分类": name, "原分类": cat or ""})
        if not dry_run:
            conn.execute("UPDATE materials SET category=?, category_code=? WHERE code=?", (name, cc, code))
    top = [t for t in tree if t["level"] == 1]
    return {"requested": len(tree), "total": len(tree), "mapped": len(tree),
            "inserted": ins, "updated": upd + mat_upd, "unchanged": same + mat_same, "skipped": 0,
            "categories": len(tree), "top_categories": [f"{t['code']} {t['name']}" for t in top],
            "materials_updated": mat_upd, "not_found": not_found[:200], "not_found_count": len(not_found),
            "errors": [], "sample": sample}


# ---------------------------------------------------------------- BOM
def _sync_boms(conn, cfg, dry_run, *, product_codes=None, user_id="u9-sync", client=None, **_):
    client = client or get_client(cfg)
    codes = _codes(product_codes)
    limit = getattr(cfg, "bom_max_products", 0) or 0
    if limit and len(codes) > limit:
        raise U9Error(f"一次最多展开 {limit} 个设备 BOM（U9_BOM_MAX_PRODUCTS），当前 {len(codes)} 个，请分批")
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
def _sync_inventory(conn, cfg, dry_run, *, codes=None, wh=None, update_totals=True, scope="local",
                    client=None, **_):
    client = client or get_client(cfg)
    item_codes = _codes(codes)
    errors: list[dict] = []
    if not item_codes and scope != "all":
        item_codes = [r[0] for r in conn.execute("SELECT code FROM materials ORDER BY code")]
        if not item_codes:
            return {"total": 0, "rows": 0, "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0,
                    "requested": 0, "scope": scope, "sample": [], "errors": [],
                    "message": "本地没有物料编码可查询：请先同步/导入料品，或指定料号"}
    if item_codes:
        raw = client.fetch_inventory(item_codes, wh=(wh or None), errors=errors)
    else:
        raw = client.fetch_inventory(None, wh=(wh or None))
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
            "requested": len(item_codes), "scope": "codes" if codes else scope,
            "errors": errors[:50], "sample": sample}


# ---------------------------------------------------------------- 生产订单
def _sync_orders(conn, cfg, dry_run, *, doc_nos=None, include_local=True, client=None, **_):
    client = client or get_client(cfg)
    local = [r[0] for r in conn.execute("SELECT order_no FROM production_orders ORDER BY order_no")] \
        if include_local else []
    wanted = _codes(_codes(doc_nos) + local)
    _check_keys(cfg, wanted, "生产订单号")
    errors: list[dict] = []
    raw = client.fetch_production_orders(wanted, errors=errors) if wanted else []
    states: dict[str, dict] = {}
    if raw and getattr(cfg, "mo_state_sql", False) and hasattr(client, "fetch_mo_states"):
        nos = [str(r.get("m_docNo") or "").strip() for r in raw if isinstance(r, dict)]
        try:
            states = client.fetch_mo_states([n for n in nos if n], errors=errors)
        except U9Error as e:  # 状态查不到不影响订单头/备料同步
            errors.append({"key": "（订单状态）", "error": str(e)})
    for r in raw:
        if isinstance(r, dict):
            st = states.get(str(r.get("m_docNo") or "").strip())
            if st and st.get("doc_state") is not None and r.get("m_docState") in (None, ""):
                r["m_docState"] = st["doc_state"]
                r["_complete_qty"] = st.get("complete_qty")
    inserted = updated = unchanged = skipped = 0
    req_inserted = req_updated = mat_created = 0
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
        cur = conn.execute("SELECT product_name, status, id FROM production_orders WHERE order_no=?",
                           (m["order_no"],)).fetchone()
        new_status = None
        if cur is None:
            # MODoc/Query 不返回单据状态时按「已下达」入库，不臆断为生产中/已完成
            new_status = m["status"] or "RELEASED"
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
                           "U9状态": m["u9_state"] or "（未查到）",
                           "完工数量": r.get("_complete_qty", ""),
                           "备料行": len(m["picklist"]),
                           "本地状态": new_status or (cur[1] if cur else "")})
        order_id = cur[2] if cur is not None else f"u9mo-{m['order_no']}"
        if action == "不变":
            unchanged += 1
        elif action == "新增":
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
        if not dry_run and m["picklist"]:
            a, b, c = _upsert_mo_requirements(conn, order_id, m["picklist"], now)
            req_inserted += a
            req_updated += b
            mat_created += c
    not_found = [d for d in wanted if d not in seen]
    return {"requested": len(wanted), "total": len(raw), "inserted": inserted, "updated": updated,
            "unchanged": unchanged, "skipped": skipped, "not_found": not_found[:200],
            "requirements_inserted": req_inserted, "requirements_updated": req_updated,
            "materials_created": mat_created,
            "not_found_count": len(not_found), "errors": errors[:50], "sample": sample}



def _upsert_mo_requirements(conn, order_id: str, picklist: list[dict], now: str) -> tuple[int, int, int]:
    """把 U9 备料表写成订单级物料需求（device_id 为空）。

    - 本地没有的料号先建物料档案；
    - 已有订单级需求：需求量按 U9 更新（不低于已到货量，避免违反约束）；
    - 不删除本地多出来的需求（可能是人工维护的）。
    """
    ins = upd = mats = 0
    # 已有按机台拆分的需求（人工导入 BOM 生成）时不再叠加订单级需求，避免重复
    if conn.execute("SELECT 1 FROM order_material_requirements WHERE order_id=? AND device_id IS NOT NULL"
                    " LIMIT 1", (order_id,)).fetchone():
        return 0, 0, 0
    for p in picklist:
        row = conn.execute("SELECT id FROM materials WHERE code=?", (p["material_code"],)).fetchone()
        if row:
            mat_id = row[0]
        else:
            mat_id = f"u9mat-{uuid.uuid4().hex[:12]}"
            conn.execute("INSERT INTO materials(id, code, name, specification, unit) VALUES(?,?,?,?,?)",
                         (mat_id, p["material_code"], p["material_name"], "", p["unit"]))
            mats += 1
        cur = conn.execute("SELECT id, required_quantity, arrived_quantity FROM order_material_requirements"
                           " WHERE order_id=? AND device_id IS NULL AND material_id=?",
                           (order_id, mat_id)).fetchone()
        req = max(1, int(p["required_quantity"]))
        if cur is None:
            conn.execute("INSERT INTO order_material_requirements(id, order_id, device_id, material_id,"
                         " required_quantity, arrived_quantity, in_stock_quantity, status_code,"
                         " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (f"u9req-{uuid.uuid4().hex[:12]}", order_id, None, mat_id, req, 0, 0,
                          "OUT_OF_STOCK", now, now))
            ins += 1
        else:
            req = max(req, int(cur[2] or 0))
            if req != cur[1]:
                conn.execute("UPDATE order_material_requirements SET required_quantity=?, updated_at=?"
                             " WHERE id=?", (req, now, cur[0]))
                upd += 1
    return ins, upd, mats

# ---------------------------------------------------------------- 项目 → 订单，MO → 机台
# 业务口径（用户 2026-10-17 确认）：U9 项目号（CBO_Project.Code，如 JSBY-26B-001）对应本地生产订单，
# 项目下每张生产订单 MO-xxx 对应一台机台（devices + order_devices + assembly_tasks），
# 机台物料需求取该 MO 的备料表（U9 已按 BOM×数量展开，含替代料）。没挂项目号的 MO 不导入。
PROJECT_ORDER_PREFIX = "u9prj-"


def project_short(code: str) -> str:
    """项目号去掉公司前缀：JSBY-26B-012 → 26B-012（没有前缀则原样返回）。"""
    code = (code or "").strip()
    head, sep, rest = code.partition("-")
    return rest if sep and rest and head.isalpha() else code


def device_no_for(project_code: str, machine_no: str, mo: str) -> str:
    """机台号口径（用户 2026-10-17 确认）：项目短号-机台编号，如 26B-012-P5-12；
    U9 料品私有段 2 没填机台编号时退回 MO 单号。机台编号跨项目会重复，故带项目前缀。"""
    m = str(machine_no or "").strip()
    return f"{project_short(project_code)}-{m}" if m else mo


def _project_status(states: list) -> str:
    vals = []
    for s in states:
        try:
            vals.append(int(s))
        except (TypeError, ValueError):
            pass
    if vals and all(v >= 3 for v in vals):
        return "COMPLETED"
    if any(v >= 2 for v in vals):
        return "IN_PROGRESS"
    return "RELEASED"


def _upsert_device_requirements(conn, order_id: str, od_id: str, picklist: list[dict],
                                now: str) -> tuple[int, int, int]:
    """把一张 MO 的备料表写成该机台的物料需求（device_id=order_devices.id）。

    本地没有的料号先建物料档案；已有需求按 U9 更新需求量（不低于已到货量）；不删本地多出来的行。
    """
    ins = upd = mats = 0
    for p in picklist:
        row = conn.execute("SELECT id FROM materials WHERE code=?", (p["material_code"],)).fetchone()
        if row:
            mat_id = row[0]
        else:
            mat_id = f"u9mat-{uuid.uuid4().hex[:12]}"
            conn.execute("INSERT INTO materials(id, code, name, specification, unit) VALUES(?,?,?,?,?)",
                         (mat_id, p["material_code"], p["material_name"] or p["material_code"], "",
                          p["unit"] or "件"))
            mats += 1
        req = max(1, int(p["required_quantity"]))
        cur = conn.execute("SELECT id, required_quantity, arrived_quantity FROM order_material_requirements"
                           " WHERE order_id=? AND device_id=? AND material_id=?",
                           (order_id, od_id, mat_id)).fetchone()
        if cur is None:
            conn.execute("INSERT INTO order_material_requirements(id, order_id, device_id, material_id,"
                         " required_quantity, arrived_quantity, in_stock_quantity, status_code,"
                         " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (f"u9req-{uuid.uuid4().hex[:12]}", order_id, od_id, mat_id, req, 0, 0,
                          "OUT_OF_STOCK", now, now))
            ins += 1
        else:
            req = max(req, int(cur[2] or 0))
            if req != cur[1]:
                conn.execute("UPDATE order_material_requirements SET required_quantity=?, updated_at=?"
                             " WHERE id=?", (req, now, cur[0]))
                upd += 1
    return ins, upd, mats


def _upsert_mo_bom(conn, order_id: str, mo: str, dno: str, pname: str, mo_qty, picklist: list[dict],
                   creator: str, now: str) -> str:
    """一张 MO 的备料表 = 该机台的 BOM 清单：机型编码=机台号，挂到项目订单（production_order_models）。

    用量按「每台」：备料表需求量 ÷ MO 生产数量；计划台数=MO 生产数量。
    备料表没变则不动；变了就新建一个已发布版本、旧发布版本归档。返回 新建/更新/未变/无备料表。
    """
    try:
        planned = max(1, int(round(float(mo_qty or 1))))
    except (TypeError, ValueError):
        planned = 1
    pom_id = f"u9pom-{mo}"
    pom = conn.execute("SELECT model_code FROM production_order_models WHERE id=?", (pom_id,)).fetchone()
    old_code = pom[0] if pom else None
    if old_code and old_code != dno:  # 机台改号：BOM 跟着改机型编码
        if not conn.execute("SELECT 1 FROM bom_versions WHERE model_code=? AND id NOT LIKE ?",
                            (dno, f"u9bomv-{mo}-%")).fetchone():
            conn.execute("UPDATE bom_versions SET model_code=? WHERE id LIKE ?", (dno, f"u9bomv-{mo}-%"))
    if not picklist:
        result = "无备料表"
    else:
        lines = sorted(picklist, key=lambda p: p["material_code"])
        sig = json.dumps([[p["material_code"], round(p["qty"] / planned, 6), p["unit"]] for p in lines],
                         ensure_ascii=False)
        sha = hashlib.sha256(f"u9mo:{mo}:{sig}".encode("utf-8")).hexdigest()
        cur = conn.execute("SELECT id, version_no, source_file_sha256 FROM bom_versions WHERE model_code=?"
                           " AND status='PUBLISHED' ORDER BY version_no DESC LIMIT 1", (dno,)).fetchone()
        if cur and cur[2] == sha:
            result = "未变"
        else:
            vno = (conn.execute("SELECT COALESCE(MAX(version_no),0) FROM bom_versions WHERE model_code=?",
                                (dno,)).fetchone()[0] or 0) + 1
            vid = f"u9bomv-{mo}-{vno}"
            conn.execute("UPDATE bom_versions SET status='ARCHIVED' WHERE model_code=? AND status='PUBLISHED'",
                         (dno,))
            conn.execute("INSERT INTO bom_versions(id, model_code, version_no, status, source_file_sha256,"
                         " row_count, created_by, created_at, published_by, published_at)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (vid, dno, vno, "PUBLISHED", sha, len(lines), creator, now, creator, now))
            for i, p in enumerate(lines, start=1):
                mat = conn.execute("SELECT id, specification FROM materials WHERE code=?",
                                   (p["material_code"],)).fetchone()
                conn.execute("INSERT INTO bom_items(id, bom_version_id, material_id, material_code,"
                             " material_name, specification, unit, quantity, scrap_rate,"
                             " substitute_material_codes, line_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (f"{vid}-{i}", vid, mat[0], p["material_code"], p["material_name"],
                              (mat[1] or "") if mat else "", p["unit"] or "件",
                              round(p["qty"] / planned, 6), 0, "[]", i))
            result = "更新" if cur else "新建"
    vid_row = conn.execute("SELECT id FROM bom_versions WHERE model_code=? AND status='PUBLISHED'"
                           " ORDER BY version_no DESC LIMIT 1", (dno,)).fetchone()
    vid = vid_row[0] if vid_row else None
    if pom is None:
        if not conn.execute("SELECT 1 FROM production_order_models WHERE model_code=?", (dno,)).fetchone():
            conn.execute("INSERT INTO production_order_models(id, order_id, model_code, model_name,"
                         " bom_version_id, planned_quantity, created_at) VALUES(?,?,?,?,?,?,?)",
                         (pom_id, order_id, dno, pname[:200], vid, planned, now))
    else:
        clash = conn.execute("SELECT 1 FROM production_order_models WHERE model_code=? AND id<>?",
                             (dno, pom_id)).fetchone()
        conn.execute("UPDATE production_order_models SET model_code=?, model_name=?, bom_version_id=?,"
                     " planned_quantity=? WHERE id=?",
                     (old_code if clash else dno, pname[:200], vid, planned, pom_id))
    return result


def _rename_device(conn, od_id: str, mo: str, order_no: str, old: str, new: str, now: str) -> None:
    """已同步的机台换成新机台号（如 0.17.0 用 MO 单号建的机台）：订单机台、装配任务、U9 建的机台档案一起改。"""
    conn.execute("UPDATE order_devices SET device_no=? WHERE id=?", (new, od_id))
    conn.execute("UPDATE assembly_tasks SET device_no=?, updated_at=? WHERE order_no=? AND device_no=?",
                 (new, now, order_no, old))
    if not conn.execute("SELECT 1 FROM devices WHERE device_no=?", (new,)).fetchone():
        conn.execute("UPDATE devices SET device_no=?, updated_at=? WHERE id=?", (new, now, f"u9dev-{mo}"))


def _sync_projects(conn, cfg, dry_run, *, projects=None, include_local=True, client=None,
                   user_id: str = "", **_):
    client = client or get_client(cfg)
    local = [r[0] for r in conn.execute(
        "SELECT order_no FROM production_orders WHERE id LIKE ? ORDER BY order_no",
        (PROJECT_ORDER_PREFIX + "%",))] if include_local else []
    wanted = _codes(_codes(projects) + local)
    _check_keys(cfg, wanted, "项目号")
    errors: list[dict] = []
    mos = client.fetch_project_mos(wanted, errors=errors) if wanted else []
    mo_nos = [r["docno"] for r in mos]
    _check_keys(cfg, mo_nos, "生产订单号")
    picklists: dict[str, list] = {}
    if mo_nos:
        for raw in client.fetch_production_orders(mo_nos, errors=errors):
            if not isinstance(raw, dict):
                continue
            try:
                m = mappers.map_production_order(raw)
            except ValueError:
                continue
            picklists[m["order_no"]] = m["picklist"]
    by_prj: dict[str, list] = {}
    for r in mos:
        by_prj.setdefault(r["projectcode"], []).append(r)
    from app.main import _categorize_device  # 与聚合 BOM 导入同一套机台归类
    now = _now()
    creator = user_id or "u9-sync"
    st = dict(orders_inserted=0, orders_updated=0, devices_inserted=0, devices_existing=0,
              requirements_inserted=0, requirements_updated=0, materials_created=0, tasks_created=0,
              devices_renamed=0, boms_created=0, boms_updated=0, boms_unchanged=0)
    sample: list[dict] = []
    for code in wanted:
        rows = by_prj.get(code) or []
        if not rows:
            continue
        name = str(rows[0].get("projectname") or "").strip() or code
        if name == code:  # 项目名称常与编码相同，取项目总装 MO 的产品名（如 JSBY-26B-001淮安天合项目）
            name = next((str(r.get("itemname")) for r in rows if code in str(r.get("itemname") or "")), code)
        status = _project_status([r.get("docstate") for r in rows])
        cur = conn.execute("SELECT id, status, product_name FROM production_orders WHERE order_no=?",
                           (code,)).fetchone()
        if cur is None:
            order_id = f"{PROJECT_ORDER_PREFIX}{code}"
            st["orders_inserted"] += 1
            if not dry_run:
                conn.execute("INSERT INTO production_orders(id, order_no, product_name, status,"
                             " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                             (order_id, code, name, status, now, now))
        else:
            order_id = cur[0]
            new_status = status if mappers.STATUS_RANK.get(status, 0) >                 mappers.STATUS_RANK.get(cur[1], 0) else cur[1]
            if new_status != cur[1]:
                st["orders_updated"] += 1
                if not dry_run:
                    conn.execute("UPDATE production_orders SET status=?, updated_at=? WHERE id=?",
                                 (new_status, now, order_id))
        seq = conn.execute("SELECT COALESCE(MAX(sequence_no),0) FROM order_devices WHERE order_id=?",
                           (order_id,)).fetchone()[0]
        for r in rows:
            mo = r["docno"]
            pname = str(r.get("itemname") or "").strip() or str(r.get("itemcode") or "") or mo
            pick = picklists.get(mo, [])
            dno = device_no_for(code, r.get("machineno"), mo)
            od_id = f"u9od-{mo}"
            od = conn.execute("SELECT id, order_id, device_no FROM order_devices WHERE id=?",
                              (od_id,)).fetchone()
            taken = conn.execute("SELECT id, order_id FROM order_devices WHERE device_no=?", (dno,)).fetchone()
            if taken is not None and taken[0] != od_id:
                if taken[1] != order_id:
                    errors.append({"key": mo, "error": f"机台号 {dno} 已挂在其他订单（{taken[1]}），跳过"})
                    continue
                dno = f"{dno}({mo})"  # 同一项目里机台编号重复：附上 MO 单号区分
            action = "已有"
            workshop, capability = _categorize_device(pname)
            if od is not None and od[2] != dno:
                action = "改号"
                st["devices_renamed"] += 1
                if not dry_run:
                    _rename_device(conn, od_id, mo, code, od[2], dno, now)
            if od is None:
                action = "新增"
                st["devices_inserted"] += 1
                seq += 1
                if not dry_run:
                    dev = conn.execute("SELECT id FROM devices WHERE device_no=?", (dno,)).fetchone()
                    dev_id = dev[0] if dev else f"u9dev-{mo}"
                    if dev is None:
                        conn.execute("INSERT INTO devices(id, device_no, device_name, workshop,"
                                     " model_capability, status, created_by, created_at, updated_at)"
                                     " VALUES(?,?,?,?,?,?,?,?,?)",
                                     (dev_id, dno, pname, workshop, capability, "ACTIVE", creator, now, now))
                    conn.execute("INSERT INTO order_devices(id, order_id, device_type, device_no,"
                                 " sequence_no, created_at) VALUES(?,?,?,?,?,?)",
                                 (od_id, order_id, capability, dno, seq, now))
                    if not conn.execute("SELECT 1 FROM assembly_tasks WHERE order_no=? AND device_no=?",
                                        (code, dno)).fetchone():
                        conn.execute("INSERT INTO assembly_tasks(id, order_no, device_id, device_no, status,"
                                     " progress_stage, task_version, created_at, updated_at)"
                                     " VALUES(?,?,?,?,?,?,?,?,?)",
                                     (f"u9task-{mo}", code, dev_id, dno, "WAITING_MATERIAL", 0, 1, now, now))
                        st["tasks_created"] += 1
            else:
                st["devices_existing"] += 1
            if not dry_run and pick:
                a, b, c = _upsert_device_requirements(conn, order_id, od_id, pick, now)
                st["requirements_inserted"] += a
                st["requirements_updated"] += b
                st["materials_created"] += c
            if not dry_run:
                bres = _upsert_mo_bom(conn, order_id, mo, dno, pname, r.get("qty"), pick, creator, now)
                key = {"新建": "boms_created", "更新": "boms_updated", "未变": "boms_unchanged"}.get(bres)
                if key:
                    st[key] += 1
            if len(sample) < SAMPLE_SIZE:
                try:
                    lbl = mappers.U9_MO_STATUS_LABELS.get(int(r.get("docstate")), str(r.get("docstate")))
                except (TypeError, ValueError):
                    lbl = "（未查到）"
                sample.append({"动作": action, "订单（项目号）": code, "机台号": dno, "MO": mo,
                               "产品": pname, "数量": mappers.num(r.get("qty") or 0),
                               "U9状态": lbl, "BOM行": len(pick)})
    not_found = [p for p in wanted if p not in by_prj]
    return {"requested": len(wanted), "projects": len(by_prj), "mos": len(mos),
            "total": len(mos), "inserted": st["orders_inserted"] + st["devices_inserted"],
            "updated": st["orders_updated"], **st,
            "not_found": not_found[:200], "not_found_count": len(not_found),
            "errors": errors[:50], "sample": sample}


_SYNC_FNS = {
    "items": _sync_items,
    "categories": _sync_categories,
    "boms": _sync_boms,
    "inventory": _sync_inventory,
    "orders": _sync_orders,
    "projects": _sync_projects,
}


# ---------------------------------------------------------------- 按年份查找订单（只读，不写库）
def _lookup_orders_via_sql(client, year, project: str, limit: int) -> dict:
    rows = client.lookup_orders_sql(year=year, project=project, limit=limit)
    matched: list[str] = []
    product_codes: list[str] = []
    seen_pc: set[str] = set()
    sample: list[dict] = []
    project_codes: list[str] = []
    for r in rows:
        no = r["docno"]
        matched.append(no)
        pj = str(r.get("projectcode") or "").strip()
        if pj and pj not in project_codes:
            project_codes.append(pj)
        pc = str(r.get("itemcode") or "").strip()
        if pc and pc not in seen_pc:
            seen_pc.add(pc)
            product_codes.append(pc)
        if len(sample) < SAMPLE_SIZE:
            st = r.get("docstate")
            try:
                label = mappers.U9_MO_STATUS_LABELS.get(int(st), str(st))
            except (TypeError, ValueError):
                label = "（未查到）"
            sample.append({"单号": no, "项目号": r.get("projectcode") or "",
                           "产品料号": pc, "产品": r.get("itemname") or pc,
                           "数量": mappers.num(r.get("qty") or 0),
                           "开工日": str(r.get("startdate") or "")[:10],
                           "完工日": str(r.get("completedate") or "")[:10],
                           "U9状态": label})
    return {"year": year, "scanned": len(rows), "matched": len(matched),
            "doc_nos": matched, "product_codes": product_codes, "project_codes": project_codes,
            "no_project": sum(1 for r in rows if not str(r.get("projectcode") or "").strip()),
            "sample": sample, "errors": [], "project": project}


def lookup_orders_by_year(cfg: U9Config, year: int | None, limit: int = 2000,
                          client=None, project: str = "") -> dict:
    """按年份查生产订单号：先取 U9 最近 N 个单号，再逐单读 MODoc 按开工年过滤。

    U9 的订单同步接口只能按单号查询，没有"列出某年订单"的发现能力，
    本函数补上这一环：返回 {year, scanned, matched, doc_nos, product_codes,
    sample, errors}，调用方可直接把 doc_nos 喂给 sync/orders，
    product_codes 喂给 sync/boms。全程只读，不写任何业务表。
    project 可选：只查挂在该 U9 项目号下的订单（如 JSBY-26B-001，前缀匹配）；
    给了项目号时 year 可为空（None/""），表示不按年份过滤。
    """
    project = (project or "").strip()
    if year in (None, "") and project:
        year = None
    else:
        try:
            year = int(year)
        except (TypeError, ValueError):
            raise U9Error(f"年份无效：{year!r}")
        if not 2000 <= year <= 2100:
            raise U9Error(f"年份超出范围（2000-2100）：{year}")
    client = client or get_client(cfg)
    errors: list[dict] = []
    if hasattr(client, "lookup_orders_sql"):
        return _lookup_orders_via_sql(client, year, project, limit)
    doc_nos = client.fetch_recent_order_nos(limit, errors=errors, project=project)
    raw = client.fetch_production_orders(doc_nos, errors=errors) if doc_nos else []
    y = str(year) if year is not None else ""
    matched: list[str] = []
    product_codes: list[str] = []
    seen_pc: set[str] = set()
    sample: list[dict] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        try:
            m = mappers.map_production_order(r)
        except ValueError:
            continue
        sd, cd = (m["start_date"] or "")[:4], (m["complete_date"] or "")[:4]
        # 开工年/完工年命中；两个日期都缺时回落为单号含年份（如 MO2026010001）
        hit = (not y) or sd == y or cd == y or ((not sd and not cd) and y in m["order_no"])
        if not hit:
            continue
        matched.append(m["order_no"])
        pc = m["product_code"]
        if pc and pc not in seen_pc:
            seen_pc.add(pc)
            product_codes.append(pc)
        if len(sample) < SAMPLE_SIZE:
            sample.append({"单号": m["order_no"], "产品料号": pc,
                           "产品": m["product_name"], "数量": m["qty"],
                           "开工日": m["start_date"], "完工日": m["complete_date"],
                           "U9状态": m["u9_state"] or "（未查到）"})
    return {"year": year, "scanned": len(doc_nos), "matched": len(matched),
            "doc_nos": matched, "product_codes": product_codes,
            "sample": sample, "errors": errors[:50], "project": project}


# ---------------------------------------------------------------- 入口
def delete_u9_orders_by_doc_prefix(conn, doc_prefix: str) -> dict:
    """删除 U9 同步导入的指定单号前缀的订单（测试环境清理用，如 MO- 清掉全部 U9 导入订单）。

    只删 id 为 u9mo-<prefix> 或 u9prj-<prefix>（项目订单）开头的 production_orders，其物料需求经外键级联删除；
    手工创建的订单（id 非 u9mo- 开头）不受影响。返回 {deleted_orders, deleted_requirements}。
    """
    import re as _re
    prefix = (doc_prefix or "").strip()
    if not prefix or not _re.match(r"^[A-Za-z0-9_\-.]{1,60}$", prefix):
        raise ValueError(f"单号前缀不合规：{prefix!r}")
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM production_orders WHERE id LIKE ? OR id LIKE ?",
        (f"u9mo-{prefix}%", f"{PROJECT_ORDER_PREFIX}{prefix}%")).fetchall()]
    req = 0
    if ids:
        q = ",".join("?" * len(ids))
        # 项目订单带出的机台：装配任务、U9 建的机台档案一并清掉（被别处引用的机台档案保留）
        devs = conn.execute(f"SELECT od.device_no, po.order_no FROM order_devices od"
                            f" JOIN production_orders po ON po.id=od.order_id WHERE od.order_id IN ({q})",
                            ids).fetchall()
        for dno, ono in devs:
            conn.execute("SAVEPOINT u9task")
            try:
                conn.execute("DELETE FROM assembly_tasks WHERE order_no=? AND device_no=?", (ono, dno))
                conn.execute("RELEASE u9task")
            except sqlite3.IntegrityError:  # 已有扫码/进度记录引用的任务保留
                conn.execute("ROLLBACK TO u9task")
                conn.execute("RELEASE u9task")
        dev_nos = [d[0] for d in devs]
        # 物料需求经外键级联删除，先计数
        q = ",".join("?" * len(ids))
        req = conn.execute(
            f"SELECT COUNT(*) FROM order_material_requirements WHERE order_id IN ({q})",
            ids).fetchone()[0]
        mo_ids = [r[0] for r in conn.execute(
            f"SELECT id FROM production_order_models WHERE order_id IN ({q}) AND id LIKE 'u9pom-%'", ids)]
        conn.execute(f"DELETE FROM production_orders WHERE id IN ({q})", ids)
        # MO 备料表生成的机台 BOM（u9bomv-<MO>-<版本>）一并清掉，被别处引用的保留
        for pid in mo_ids:
            conn.execute("SAVEPOINT u9bom")
            try:
                conn.execute("DELETE FROM bom_items WHERE bom_version_id LIKE ?", (f"u9bomv-{pid[6:]}-%",))
                conn.execute("DELETE FROM bom_versions WHERE id LIKE ?", (f"u9bomv-{pid[6:]}-%",))
                conn.execute("RELEASE u9bom")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK TO u9bom")
                conn.execute("RELEASE u9bom")
        for dno in dev_nos:
            conn.execute("SAVEPOINT u9dev")
            try:
                conn.execute("DELETE FROM devices WHERE device_no=? AND id LIKE 'u9dev-%'", (dno,))
                conn.execute("RELEASE u9dev")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK TO u9dev")
                conn.execute("RELEASE u9dev")
    return {"deleted_orders": len(ids), "deleted_requirements": req}


def run_sync(entity: str, conn: sqlite3.Connection, cfg: U9Config,
             dry_run: bool = True, **kwargs) -> dict:
    """执行一次同步。返回 {run_id, log_id, entity, dry_run, status, stats} 并写日志表。"""
    if entity not in _SYNC_FNS:
        raise ValueError(f"未知同步实体: {entity}，可选 {ENTITIES}")
    if not _SYNC_LOCK.acquire(blocking=False):
        cur = _RUNNING or {}
        raise U9SyncBusy(f"已有 U9 同步在运行（{cur.get('label', cur.get('entity', '?'))}，"
                         f"开始于 {cur.get('started_at', '?')}），请等它结束后再试")
    try:
        _RUNNING.update(entity=entity, started_at=_now(), dry_run=dry_run)
        return _run_sync_locked(entity, conn, cfg, dry_run, **kwargs)
    finally:
        _RUNNING.clear()
        _SYNC_LOCK.release()


def _run_sync_locked(entity: str, conn: sqlite3.Connection, cfg: U9Config,
                     dry_run: bool, **kwargs) -> dict:
    run_id = uuid.uuid4().hex[:12]
    started = _now()
    if kwargs.get("client") is None:
        client = get_client(cfg)
        secs = getattr(cfg, "sync_max_seconds", 0) or 0
        if secs:
            client.deadline = time.monotonic() + secs
            client.deadline_seconds = secs
        kwargs["client"] = client
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
