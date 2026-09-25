"""Agent 导入管线：解析 → 列映射 → 校验 → 预览 → 确认写入。

只读现有业务表做参照校验，写入时走与现有 API 一致的语义
（materials/inventory upsert、production_orders 插入、audit_events 留痕），
提交前必须经过预览确认，并用幂等键防止重复提交。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from app.agent.config import AgentConfig
from app.agent.llm import LLMError, chat
from app.xlsx_parser import XlsxParseError, XlsxSheetReader

MAX_IMPORT_ROWS = 20000

# 导入目标定义：字段名 -> (是否必填, 别名列表)
TARGETS: dict[str, dict[str, tuple[bool, list[str]]]] = {
    "materials": {
        "code": (True, ["物料编码", "料号", "编码", "code"]),
        "name": (True, ["名称", "物料名称", "name"]),
        "specification": (False, ["规格", "型号", "specification"]),
        "unit": (True, ["单位", "unit"]),
        "batch_no": (False, ["批次", "批号", "batch_no"]),
        "expiry_date": (False, ["有效期", "到期日", "expiry_date"]),
        "total_quantity": (False, ["总数量", "库存", "total_quantity"]),
        "available_quantity": (False, ["可用数量", "available_quantity"]),
    },
    "inventory": {
        "material_code": (True, ["物料编码", "料号", "material_code"]),
        "location_code": (True, ["库位编码", "库位", "location_code"]),
        "quantity": (True, ["数量", "库存数量", "quantity"]),
    },
    "orders": {
        "order_no": (True, ["订单号", "单号", "order_no"]),
        "product_name": (False, ["产品名称", "产品", "product_name"]),
        "status": (False, ["状态", "status"]),
    },
}

_QUANTITY_FIELDS = {"total_quantity", "available_quantity", "quantity"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# 1. 解析
# ---------------------------------------------------------------------------

def _clean(value: object) -> str:
    return str(value if value is not None else "").strip()


def _is_blank_row(values: list[str]) -> bool:
    return not any(v.strip() for v in values)


def parse_upload(file_name: str, raw: bytes) -> dict:
    """解析上传文件，返回 {"headers", "rows", "total_rows"}。

    .xlsx 读取第一张表，取首个非空行作表头；.csv 按 utf-8-sig 解析。
    """
    if not raw:
        raise ValueError("文件为空")
    suffix = (file_name or "").rsplit(".", 1)
    ext = ("." + suffix[1].lower()) if len(suffix) == 2 else ""
    if ext == ".xlsx":
        return _parse_xlsx(raw)
    if ext == ".csv":
        return _parse_csv(raw)
    raise ValueError("仅支持 .xlsx/.csv")


def _parse_xlsx(raw: bytes) -> dict:
    try:
        sheet_rows = XlsxSheetReader.read_rows(raw, max_rows=MAX_IMPORT_ROWS + 1)
    except XlsxParseError as exc:
        raise ValueError("XLSX 文件无法解析") from exc
    header_values: list[str] | None = None
    data: list[dict[str, str]] = []
    for row in sheet_rows:
        values = [_clean(v) for v in row.values]
        if header_values is None:
            if _is_blank_row(values):
                continue
            header_values = values
            continue
        if _is_blank_row(values):
            continue
        data.append(_row_dict(header_values, values))
    if header_values is None:
        raise ValueError("未找到表头行")
    _check_row_limit(len(data))
    return {"headers": header_values, "rows": data, "total_rows": len(data)}


def _parse_csv(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV 文件编码错误，请使用 UTF-8 编码") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise ValueError("未找到表头行")
        headers = [_clean(h) for h in fieldnames if h is not None]
        if not any(headers):
            raise ValueError("未找到表头行")
        data: list[dict[str, str]] = []
        for record in reader:
            values = [_clean(record.get(h)) for h in fieldnames if h is not None]
            if _is_blank_row(values):
                continue
            data.append(_row_dict(headers, values))
    except csv.Error as exc:
        raise ValueError("CSV 文件无法解析") from exc
    _check_row_limit(len(data))
    return {"headers": headers, "rows": data, "total_rows": len(data)}


def _row_dict(headers: list[str], values: list[str]) -> dict[str, str]:
    row: dict[str, str] = {}
    for index, header in enumerate(headers):
        if not header or header in row:
            continue
        row[header] = values[index] if index < len(values) else ""
    return row


def _check_row_limit(count: int) -> None:
    if count > MAX_IMPORT_ROWS:
        raise ValueError(f"文件行数超过 {MAX_IMPORT_ROWS}")


# ---------------------------------------------------------------------------
# 2. 列映射
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return "".join(text.split()).lower()


def _fallback_mapping(target: str, headers: list[str]) -> dict[str, str | None]:
    fields = TARGETS[target]
    norm_headers = [(_normalize(h), h) for h in headers]
    mapping: dict[str, str | None] = {}
    for field, (_, aliases) in fields.items():
        candidates = {_normalize(field)} | {_normalize(a) for a in aliases}
        matched: str | None = None
        for normed, original in norm_headers:
            if normed and normed in candidates:
                matched = original
                break
        mapping[field] = matched
    return mapping


_MAPPING_SYSTEM_PROMPT = (
    "你是表格列映射助手。任务：把用户上传表格的表头映射到目标数据模型的字段。"
    "只返回 JSON，不要输出任何解释文字。JSON 格式为 {\"字段名\": \"表头原文\" 或 null}，"
    "字段名必须来自给定的目标字段列表，值为表头原文（必须一字不差），"
    "找不到对应表头时值为 null。"
)


def suggest_column_mapping(
    cfg: AgentConfig, target: str, headers: list[str]
) -> dict[str, str | None]:
    """用 LLM 建议表头到目标字段的映射；失败时回退到归一化精确匹配。"""
    if target not in TARGETS:
        raise ValueError("不支持的导入目标")
    fields = TARGETS[target]
    field_lines = "\n".join(
        f"- {name}: {', '.join(aliases)}" for name, (_, aliases) in fields.items()
    )
    header_lines = "\n".join(f"- {h}" for h in headers)
    user_prompt = (
        f"导入目标：{target}\n目标字段（字段名: 别名）：\n{field_lines}\n"
        f"表格表头：\n{header_lines}\n只返回 JSON。"
    )
    try:
        result = chat(
            cfg,
            [
                {"role": "system", "content": _MAPPING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return _parse_mapping_json(result.content, target, headers)
    except (LLMError, ValueError):
        return _fallback_mapping(target, headers)


def _parse_mapping_json(
    content: str, target: str, headers: list[str]
) -> dict[str, str | None]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("映射结果不是 JSON 对象")
    header_set = set(headers)
    mapping: dict[str, str | None] = {}
    for field in TARGETS[target]:
        value = data.get(field)
        mapping[field] = value if (value is None or value in header_set) else None
    return mapping


def apply_column_mapping(
    parsed: dict, column_map: dict[str, str | None]
) -> list[dict[str, str]]:
    """按列映射把解析行转换为以目标字段名为 key 的行。"""
    mapped: list[dict[str, str]] = []
    for row in parsed["rows"]:
        mapped.append(
            {
                field: _clean(row.get(header)) if header else ""
                for field, header in column_map.items()
            }
        )
    return mapped


# ---------------------------------------------------------------------------
# 3. 校验
# ---------------------------------------------------------------------------

def _parse_non_negative_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not number.is_integer() or number < 0:
        return None
    return int(number)


def validate_rows(
    target: str, mapped_rows: list[dict], conn: sqlite3.Connection
) -> tuple[list[dict], list[dict]]:
    """校验映射后的行，返回 (valid_rows, errors)。"""
    if target not in TARGETS:
        raise ValueError("不支持的导入目标")
    if target == "materials":
        return _validate_materials(mapped_rows)
    if target == "inventory":
        return _validate_inventory(mapped_rows, conn)
    return _validate_orders(mapped_rows, conn)


def _validate_materials(mapped_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    valid_rows, errors, seen_codes = [], [], set()
    for line_no, row in enumerate(mapped_rows, 1):
        code = _clean(row.get("code"))
        name = _clean(row.get("name"))
        unit = _clean(row.get("unit"))
        row_errors: list[dict] = []
        if not code:
            row_errors.append({"row": line_no, "field": "code", "message": "物料编码不能为空"})
        elif code in seen_codes:
            row_errors.append({"row": line_no, "field": "code", "message": "文件内物料编码重复"})
        if not name:
            row_errors.append({"row": line_no, "field": "name", "message": "物料名称不能为空"})
        if not unit:
            row_errors.append({"row": line_no, "field": "unit", "message": "单位不能为空"})
        quantities: dict[str, int] = {}
        for field, label in (("total_quantity", "总数量"), ("available_quantity", "可用数量")):
            raw_value = _clean(row.get(field))
            if not raw_value:
                quantities[field] = 0
                continue
            parsed = _parse_non_negative_int(raw_value)
            if parsed is None:
                row_errors.append(
                    {"row": line_no, "field": field, "message": f"{label}必须为非负整数"}
                )
            else:
                quantities[field] = parsed
        if row_errors:
            errors.extend(row_errors)
            continue
        seen_codes.add(code)
        valid_rows.append({
            "code": code,
            "name": name,
            "specification": _clean(row.get("specification")),
            "unit": unit,
            "batch_no": _clean(row.get("batch_no")),
            "expiry_date": _clean(row.get("expiry_date")),
            "total_quantity": quantities["total_quantity"],
            "available_quantity": quantities["available_quantity"],
        })
    return valid_rows, errors


def _validate_inventory(
    mapped_rows: list[dict], conn: sqlite3.Connection
) -> tuple[list[dict], list[dict]]:
    valid_rows, errors = [], []
    for line_no, row in enumerate(mapped_rows, 1):
        material_code = _clean(row.get("material_code"))
        location_code = _clean(row.get("location_code"))
        raw_quantity = _clean(row.get("quantity"))
        row_errors: list[dict] = []
        if not material_code:
            row_errors.append({"row": line_no, "field": "material_code", "message": "物料编码不能为空"})
        elif not conn.execute(
            "SELECT 1 FROM materials WHERE code=?", (material_code,)
        ).fetchone():
            row_errors.append({"row": line_no, "field": "material_code", "message": "物料不存在"})
        if not location_code:
            row_errors.append({"row": line_no, "field": "location_code", "message": "库位编码不能为空"})
        elif not conn.execute(
            "SELECT 1 FROM locations WHERE code=?", (location_code,)
        ).fetchone():
            row_errors.append({"row": line_no, "field": "location_code", "message": "库位不存在"})
        quantity = _parse_non_negative_int(raw_quantity)
        if raw_quantity and quantity is None:
            row_errors.append({"row": line_no, "field": "quantity", "message": "数量必须为非负整数"})
        elif not raw_quantity:
            row_errors.append({"row": line_no, "field": "quantity", "message": "数量不能为空"})
        if row_errors:
            errors.extend(row_errors)
            continue
        valid_rows.append({
            "material_code": material_code,
            "location_code": location_code,
            "quantity": quantity,
        })
    return valid_rows, errors


def _validate_orders(
    mapped_rows: list[dict], conn: sqlite3.Connection
) -> tuple[list[dict], list[dict]]:
    valid_rows, errors, seen = [], [], set()
    for line_no, row in enumerate(mapped_rows, 1):
        order_no = _clean(row.get("order_no"))
        row_errors: list[dict] = []
        if not order_no:
            row_errors.append({"row": line_no, "field": "order_no", "message": "订单号不能为空"})
        elif order_no in seen:
            row_errors.append({"row": line_no, "field": "order_no", "message": "文件内订单号重复"})
        elif conn.execute(
            "SELECT 1 FROM production_orders WHERE order_no=?", (order_no,)
        ).fetchone():
            row_errors.append({"row": line_no, "field": "order_no", "message": "订单号已存在"})
        if row_errors:
            errors.extend(row_errors)
            continue
        seen.add(order_no)
        valid_rows.append({
            "order_no": order_no,
            "product_name": _clean(row.get("product_name")),
            "status": _clean(row.get("status")),
        })
    return valid_rows, errors


# ---------------------------------------------------------------------------
# 4/5. 预览任务（创建/查询）与确认写入
# ---------------------------------------------------------------------------

def _ensure_operation_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_import_operations(
            client_operation_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def create_import_job(
    conn: sqlite3.Connection,
    user_id: str,
    target: str,
    file_name: str,
    raw: bytes,
    column_map: dict[str, str | None],
    valid_rows: list[dict],
    errors: list[dict],
) -> str:
    """创建导入任务（待确认），返回 job id。"""
    if target not in TARGETS:
        raise ValueError("不支持的导入目标")
    job_id = _new_id()
    conn.execute(
        "INSERT INTO agent_import_jobs(id, user_id, target, file_name, file_sha256,"
        " column_map_json, rows_json, errors_json, status, created_at, committed_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_id,
            user_id,
            target,
            file_name,
            hashlib.sha256(raw).hexdigest(),
            json.dumps(column_map, ensure_ascii=False),
            json.dumps(valid_rows, ensure_ascii=False),
            json.dumps(errors, ensure_ascii=False),
            "PENDING_CONFIRM",
            _utcnow(),
            None,
        ),
    )
    conn.commit()
    return job_id


def get_import_job(
    conn: sqlite3.Connection, user_id: str, job_id: str
) -> dict | None:
    """查询导入任务（校验归属），返回全字段，rows/errors/column_map 已解析。"""
    row = conn.execute(
        "SELECT id, user_id, target, file_name, file_sha256, column_map_json,"
        " rows_json, errors_json, status, created_at, committed_at"
        " FROM agent_import_jobs WHERE id=? AND user_id=?",
        (job_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "target": row[2],
        "file_name": row[3],
        "file_sha256": row[4],
        "column_map": json.loads(row[5]),
        "rows": json.loads(row[6]),
        "errors": json.loads(row[7]),
        "status": row[8],
        "created_at": row[9],
        "committed_at": row[10],
    }


def commit_import_job(
    conn: sqlite3.Connection, user_id: str, job_id: str, client_operation_id: str
) -> dict:
    """确认并写入导入任务。幂等：同一幂等键重复提交返回存档结果。"""
    if not client_operation_id:
        raise ValueError("幂等键不能为空")
    _ensure_operation_table(conn)
    job = get_import_job(conn, user_id, job_id)
    if job is None:
        raise ValueError("导入任务不存在")

    prior = conn.execute(
        "SELECT job_id, result_json FROM agent_import_operations"
        " WHERE client_operation_id=?",
        (client_operation_id,),
    ).fetchone()

    if job["status"] == "COMMITTED":
        if prior and prior[0] == job_id:
            result = json.loads(prior[1])
            result = {**result, "idempotent": True}
            return result
        raise ValueError("幂等键冲突")
    if job["status"] != "PENDING_CONFIRM":
        raise ValueError("导入任务状态异常，无法提交")
    if prior:
        raise ValueError("幂等键冲突")
    if job["errors"]:
        raise ValueError("存在校验错误，无法提交")

    target = job["target"]
    rows = job["rows"]
    ts = _utcnow()
    role_row = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    actor_role = role_row[0] if role_row else "UNKNOWN"
    request_id = _new_id()

    try:
        conn.execute("BEGIN IMMEDIATE")
        if target == "materials":
            _commit_materials(conn, rows, ts)
        elif target == "inventory":
            _commit_inventory(conn, rows, ts)
        elif target == "orders":
            _commit_orders(conn, rows, ts)
        else:
            raise ValueError("不支持的导入目标")

        after_summary = {
            "status": "COMMITTED",
            "target": target,
            "committed_rows": len(rows),
            "client_operation_id": client_operation_id,
        }
        conn.execute(
            "INSERT INTO audit_events(event_type, entity_type, entity_id, actor_user_id,"
            " actor_role, request_id, client_operation_id, before_json, after_json,"
            " server_time, device_id, source_ip, result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "AGENT_IMPORT",
                "AGENT_IMPORT_JOB",
                job_id,
                user_id,
                actor_role,
                request_id,
                client_operation_id,
                json.dumps(
                    {"status": "PENDING_CONFIRM", "target": target, "rows": len(rows)},
                    ensure_ascii=False,
                ),
                json.dumps(after_summary, ensure_ascii=False),
                ts,
                None,
                None,
                "SUCCESS",
            ),
        )
        conn.execute(
            "UPDATE agent_import_jobs SET status='COMMITTED', committed_at=?"
            " WHERE id=?",
            (ts, job_id),
        )
        result = {
            "job_id": job_id,
            "target": target,
            "committed_rows": len(rows),
            "idempotent": False,
        }
        conn.execute(
            "INSERT INTO agent_import_operations(client_operation_id, job_id, user_id,"
            " result_json, created_at) VALUES(?,?,?,?,?)",
            (
                client_operation_id,
                job_id,
                user_id,
                json.dumps(result, ensure_ascii=False),
                ts,
            ),
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def _commit_materials(conn: sqlite3.Connection, rows: list[dict], ts: str) -> None:
    for row in rows:
        existing = conn.execute(
            "SELECT id FROM materials WHERE code=?", (row["code"],)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE materials SET name=?, specification=?, unit=?, batch_no=?,"
                " expiry_date=?, total_quantity=?, available_quantity=?,"
                " version=version+1 WHERE id=?",
                (
                    row["name"],
                    row.get("specification") or None,
                    row["unit"],
                    row.get("batch_no") or None,
                    row.get("expiry_date") or None,
                    row["total_quantity"],
                    row["available_quantity"],
                    existing[0],
                ),
            )
        else:
            conn.execute(
                "INSERT INTO materials(id, code, name, specification, unit, batch_no,"
                " expiry_date, total_quantity, available_quantity, version)"
                " VALUES(?,?,?,?,?,?,?,?,?,1)",
                (
                    _new_id(),
                    row["code"],
                    row["name"],
                    row.get("specification") or None,
                    row["unit"],
                    row.get("batch_no") or None,
                    row.get("expiry_date") or None,
                    row["total_quantity"],
                    row["available_quantity"],
                ),
            )


def _commit_inventory(conn: sqlite3.Connection, rows: list[dict], ts: str) -> None:
    del ts
    for row in rows:
        material = conn.execute(
            "SELECT id FROM materials WHERE code=?", (row["material_code"],)
        ).fetchone()
        location = conn.execute(
            "SELECT id FROM locations WHERE code=?", (row["location_code"],)
        ).fetchone()
        if not material:
            raise ValueError(f"物料不存在：{row['material_code']}")
        if not location:
            raise ValueError(f"库位不存在：{row['location_code']}")
        existing = conn.execute(
            "SELECT id FROM inventory WHERE material_id=? AND location_id=?",
            (material[0], location[0]),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE inventory SET quantity=? WHERE id=?",
                (row["quantity"], existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO inventory(id, material_id, location_id, quantity)"
                " VALUES(?,?,?,?)",
                (_new_id(), material[0], location[0], row["quantity"]),
            )


def _commit_orders(conn: sqlite3.Connection, rows: list[dict], ts: str) -> None:
    for row in rows:
        if conn.execute(
            "SELECT 1 FROM production_orders WHERE order_no=?", (row["order_no"],)
        ).fetchone():
            raise ValueError(f"订单号已存在：{row['order_no']}")
        conn.execute(
            "INSERT INTO production_orders(id, order_no, product_name, status,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (
                _new_id(),
                row["order_no"],
                row.get("product_name") or "",
                row.get("status") or "IN_PROGRESS",
                ts,
                ts,
            ),
        )
