"""物料主档（U9 料品档案导出 ItemMaster.xlsx）导入。

来源表结构（2026-10-10 版导出，单 sheet「物料主档」）：
  第 1 行是标题「物料主档」，第 2 行是表头，共 70 列；料号全表唯一（约 3.7 万行）。

设计要点
- ``materials`` 继续只承载流转需要的核心字段（料号/品名/规格/单位/分类 + 数量）。
  主档的其余属性落在 1:1 的 ``material_master``（按料号关联），避免把 70 列塞进热表。
- 导入**绝不改写数量/批次/效期**：materials 的数量只由库存快照和流转维护。
- 两步式：预览（解析 + 校验 + 与库内比对：新增/变更/未变化/错误）→ 确认写入。
  预览只保存上传文件和统计/错误/差异样本，不把 3.7 万行 JSON 存进数据库；
  提交时按 sha256 校验同一文件后重新解析，保证写入的就是预览过的内容。
- 按行哈希增量：未变化的行不写库，同一份主档重复导入几乎是空操作。
- 文件中缺失的本地料号只统计、不删除（主档导出可能是筛选过的子集）。
- 参考成本/最新成本只对 ADMIN 返回。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from app.xlsx_parser import (
    HeaderDetectService,
    HeaderMissingFieldsError,
    XlsxMaxRowsExceededError,
    XlsxParseError,
    XlsxSheetReader,
)

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # nginx 模板 client_max_body_size 32m
MAX_ROWS = 200_000
PREVIEW_TTL_SECONDS = 2 * 3600
MAX_STORED_ERRORS = 1000
MAX_STORED_CHANGES = 200
IMPORT_ROLES = {"ADMIN", "WAREHOUSE_ADMIN", "PLANNER"}
COST_FIELDS = ("ref_cost", "latest_cost")


# --------------------------------------------------------------------------- 列定义
# (表头, 字段名, 类型)；类型：text / num / int / flag(√) / bool01 / serial_date / dot_datetime
@dataclass(frozen=True)
class Col:
    header: str
    field: str
    kind: str = "text"
    label: str = ""


COLUMNS: tuple[Col, ...] = (
    Col("主分类.分类编码", "main_category_code"),
    Col("主分类", "main_category_name"),
    Col("财务分类.分类编码", "finance_category_code"),
    Col("财务分类.分类名称", "finance_category_name"),
    Col("成本分类.分类编码", "cost_category_code"),
    Col("成本分类", "cost_category_name"),
    Col("料号", "code"),
    Col("参考料号1(U9图号)", "drawing_no", label="U9图号"),
    Col("品名", "name"),
    Col("规格", "specification"),
    Col("描述(品牌)", "brand", label="品牌"),
    Col("实体扩展字段.全局段6(加工件分类)", "machining_class", label="加工件分类"),
    Col("附件", "attachment_flag", "int", label="附件"),
    Col("T6料号", "t6_code"),
    Col("参考料号2(T6图号)", "t6_drawing_no", label="T6图号"),
    Col("库存单位.编码", "unit_code"),
    Col("库存单位.名称", "unit_name"),
    Col("重量单位.编码", "weight_unit_code"),
    Col("重量单位.名称", "weight_unit_name"),
    Col("库存单位重量", "unit_weight", "num"),
    Col("设备编号", "device_no"),
    Col("项目号", "project_no"),
    Col("形态属性", "item_form"),
    Col("存储地点", "storage_location"),
    Col("仓库业务员.编码", "warehouse_clerk_code"),
    Col("仓库业务员.名称", "warehouse_clerk_name"),
    Col("采购员.编码", "buyer_code"),
    Col("采购员.名称", "buyer_name"),
    Col("料品采购相关信息.最小叫货量", "min_order_qty", "num"),
    Col("采购预处理提前期(天)", "purchase_lead_days", "int"),
    Col("累计制造提前期(天)", "mfg_lead_days", "int"),
    Col("生产部门.编码", "production_dept_code"),
    Col("生产部门.名称", "production_dept_name"),
    Col("进货质检方案", "incoming_qc_plan"),
    Col("完工质检方案", "completion_qc_plan"),
    Col("收货程序", "receiving_procedure"),
    Col("安全库存量", "safety_stock", "num"),
    Col("库存上限", "stock_upper_limit", "num"),
    Col("生产分类.分类编码", "production_category_code"),
    Col("生产分类.分类名称", "production_category_name"),
    Col("库存分类", "stock_category"),
    Col("实体扩展字段.全局段4(打单分类)", "print_category", label="打单分类"),
    Col("段1(编号分类)", "segment1"),
    Col("段2(流水号)", "segment2"),
    Col("专用料", "is_special", "flag"),
    Col("料品生产相关信息.是否进行工程变更版本控制", "eco_version_control", "flag"),
    Col("可销售", "can_sell", "flag"),
    Col("可委外", "can_outsource", "flag"),
    Col("可生产", "can_produce", "flag"),
    Col("可库存交易", "can_stock_trade", "flag"),
    Col("可采购", "can_purchase", "flag"),
    Col("有效性.失效日期", "effective_until", "serial_date", label="失效日期"),
    Col("参考成本", "ref_cost", "num"),
    Col("最新成本", "latest_cost", "num"),
    Col("料品库存相关信息", "u9_item_id", label="U9料品ID"),
    Col("创建人", "u9_created_by"),
    Col("创建时间", "u9_created_at", "dot_datetime"),
    Col("修改人", "u9_modified_by"),
    Col("修改时间", "u9_modified_at", "dot_datetime"),
)
# 表中存在、但当前导出为空或与上列重复的列：原样收进 extra_json，表结构变化时不丢数据。
#   库存主单位(=库存单位.名称)、财务分类(=财务分类.分类名称)、采购分类、料品形态、控制组织、
#   是否版本数量控制、是否成分控制、是否等级控制、MRP分类、料品销售相关信息.可用量检查/可用量规则
REQUIRED_HEADERS = ("料号", "品名")
UNIT_FALLBACK_HEADER = "库存主单位"
_BY_HEADER = {c.header: c for c in COLUMNS}
FIELDS = tuple(c.field for c in COLUMNS)
DATA_FIELDS = tuple(f for f in FIELDS if f != "code")
FIELD_LABELS = {c.field: (c.label or c.header) for c in COLUMNS}
FIELD_KINDS = {c.field: c.kind for c in COLUMNS}

_SQL_TYPES = {"num": "REAL", "int": "INTEGER", "flag": "INTEGER"}


def ensure_tables(c: sqlite3.Connection) -> None:
    cols = ",\n  ".join(f"{f} {_SQL_TYPES.get(FIELD_KINDS[f], 'TEXT')}" for f in DATA_FIELDS)
    c.executescript(f"""
CREATE TABLE IF NOT EXISTS material_master(
  code TEXT PRIMARY KEY,
  {cols},
  extra_json TEXT NOT NULL DEFAULT '{{}}',
  row_hash TEXT NOT NULL,
  source_batch_id TEXT,
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mm_drawing_no ON material_master(drawing_no);
CREATE INDEX IF NOT EXISTS idx_mm_t6_code ON material_master(t6_code);
CREATE INDEX IF NOT EXISTS idx_mm_device_no ON material_master(device_no);
CREATE INDEX IF NOT EXISTS idx_mm_project_no ON material_master(project_no);
CREATE INDEX IF NOT EXISTS idx_mm_main_category ON material_master(main_category_code);
CREATE INDEX IF NOT EXISTS idx_mm_item_form ON material_master(item_form);
CREATE INDEX IF NOT EXISTS idx_mm_storage ON material_master(storage_location);
CREATE TABLE IF NOT EXISTS material_master_import_batches(
  id TEXT PRIMARY KEY,
  file_name TEXT NOT NULL,
  file_sha256 TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  sheet_name TEXT,
  header_row INTEGER NOT NULL DEFAULT 0,
  total_count INTEGER NOT NULL DEFAULT 0,
  valid_count INTEGER NOT NULL DEFAULT 0,
  invalid_count INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0,
  changed_count INTEGER NOT NULL DEFAULT 0,
  unchanged_count INTEGER NOT NULL DEFAULT 0,
  missing_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{{}}',
  errors_json TEXT NOT NULL DEFAULT '[]',
  changes_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK(status IN ('PREVIEW','COMMITTED','EXPIRED','FAILED')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  committed_by TEXT,
  committed_at TEXT,
  result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_mm_batches_created ON material_master_import_batches(created_at);
CREATE TABLE IF NOT EXISTS material_master_import_operations(
  client_operation_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  created_by TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
""")
    # 后续版本新增列时幂等补齐
    existing = {r[1] for r in c.execute("PRAGMA table_info(material_master)").fetchall()}
    for f in DATA_FIELDS:
        if f not in existing:
            c.execute(f"ALTER TABLE material_master ADD COLUMN {f} {_SQL_TYPES.get(FIELD_KINDS[f], 'TEXT')}")


# --------------------------------------------------------------------------- 值解析
_EXCEL_EPOCH = date(1899, 12, 30)
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
_DOT_DT_RE = re.compile(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
FOREVER = "9999-12-31"


class CellError(ValueError):
    pass


def _parse_num(v: str) -> float:
    v = v.replace(",", "")
    if not _NUM_RE.match(v):
        raise CellError("必须是数字")
    return float(v)


def _parse_serial_date(v: str) -> str:
    if _NUM_RE.match(v):
        serial = float(v)
        if serial >= 2958000:  # Excel 9999 年附近 = 永久有效
            return FOREVER
        if serial <= 0:
            raise CellError("日期无效")
        return (_EXCEL_EPOCH + timedelta(days=int(serial))).isoformat()
    m = _DOT_DT_RE.match(v)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            pass
    raise CellError("日期无效")


def _parse_dot_datetime(v: str) -> str:
    m = _DOT_DT_RE.match(v)
    if m:
        try:
            dt = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0), int(m[6] or 0))
            return dt.isoformat(sep=" ")
        except ValueError:
            pass
    if _NUM_RE.match(v):  # 万一导出成 Excel 序列值
        serial = float(v)
        dt = datetime(1899, 12, 30) + timedelta(days=serial)
        return dt.replace(microsecond=0).isoformat(sep=" ")
    raise CellError("时间格式无法识别")


def _parse_cell(kind: str, raw: str) -> Any:
    v = raw.strip()
    if v == "":
        return None
    if kind == "text":
        return v
    if kind == "num":
        n = _parse_num(v)
        return int(n) if n.is_integer() and abs(n) < 2**53 else n
    if kind == "int":
        n = _parse_num(v)
        if not n.is_integer():
            raise CellError("必须是整数")
        return int(n)
    if kind == "flag":
        if v in ("√", "✓", "✔", "是", "Y", "y", "TRUE", "true", "1"):
            return 1
        if v in ("×", "否", "N", "n", "FALSE", "false", "0"):
            return 0
        raise CellError("应为 √ 或空")
    if kind == "serial_date":
        return _parse_serial_date(v)
    if kind == "dot_datetime":
        return _parse_dot_datetime(v)
    return v


def row_hash(record: dict[str, Any], extra: dict[str, str]) -> str:
    payload = json.dumps([[f, record.get(f)] for f in FIELDS] + [["_extra", extra]],
                         ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- 解析
@dataclass
class ParsedRow:
    line_no: int
    code: str
    record: dict[str, Any]
    extra: dict[str, str]
    errors: list[dict[str, Any]]
    hash: str = ""


@dataclass
class ParseMeta:
    sheet_name: str | None
    header_row: int
    recognized_headers: list[str]
    extra_headers: list[str]
    missing_optional_headers: list[str]


class TemplateError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _pick_sheet(path: Path) -> str | None:
    names = XlsxSheetReader.sheet_names(path)
    for preferred in ("物料主档", "ItemMaster", "料品"):
        for n in names:
            if preferred in n:
                return n
    return names[0] if names else None


def parse_file(path: Path) -> tuple[ParseMeta, Iterator[ParsedRow]]:
    """返回 (表头元数据, 行迭代器)。表头在前 20 行内自动定位（跳过标题行）。"""
    try:
        sheet = _pick_sheet(path)
        head_rows = []
        for r in XlsxSheetReader.iter_rows(path, sheet_name=sheet):
            head_rows.append(r)
            if len(head_rows) >= 20:
                break
        header = HeaderDetectService.detect_header(head_rows, REQUIRED_HEADERS)
    except HeaderMissingFieldsError as exc:
        raise TemplateError("MATERIAL_MASTER_TEMPLATE_INVALID", "未找到物料主档表头（需要「料号」「品名」列）",
                            {"missingFields": list(exc.missing_fields)}) from None
    except XlsxParseError:
        raise TemplateError("MATERIAL_MASTER_TEMPLATE_INVALID", "XLSX 文件无法解析") from None

    col_of: dict[str, int] = {}
    extra_cols: dict[str, int] = {}
    for idx, h in enumerate(header.headers):
        if not h:
            continue
        # HeaderDetectService 会去掉空白；COLUMNS 的表头本身不含空白
        if h in _BY_HEADER and _BY_HEADER[h].field not in col_of:
            col_of[_BY_HEADER[h].field] = idx
        elif h not in extra_cols:
            extra_cols[h] = idx
    unit_fallback_idx = extra_cols.get(UNIT_FALLBACK_HEADER)
    if "unit_name" not in col_of and unit_fallback_idx is None:
        raise TemplateError("MATERIAL_MASTER_TEMPLATE_INVALID", "缺少库存单位列（「库存单位.名称」或「库存主单位」）",
                            {"missingFields": ["库存单位.名称"]})
    meta = ParseMeta(
        sheet_name=sheet,
        header_row=header.header_row_index,
        recognized_headers=[c.header for c in COLUMNS if c.field in col_of],
        extra_headers=list(extra_cols),
        missing_optional_headers=[c.header for c in COLUMNS if c.field not in col_of],
    )

    def rows() -> Iterator[ParsedRow]:
        try:
            for r in XlsxSheetReader.iter_rows(path, sheet_name=sheet, max_rows=MAX_ROWS + 50):
                if r.index <= header.header_row_index:
                    continue
                vals = r.values
                if not any(v.strip() for v in vals):
                    continue
                cell = lambda i: vals[i] if i is not None and i < len(vals) else ""  # noqa: E731
                record: dict[str, Any] = {}
                errors: list[dict[str, Any]] = []
                for col in COLUMNS:
                    idx = col_of.get(col.field)
                    if idx is None:
                        record[col.field] = None
                        continue
                    raw = cell(idx)
                    try:
                        record[col.field] = _parse_cell(col.kind, raw)
                    except CellError as e:
                        record[col.field] = None
                        errors.append({"lineNo": r.index, "field": col.header, "code": "INVALID_VALUE",
                                       "message": f"{col.header}{e}", "value": raw[:60]})
                if not record.get("unit_name") and unit_fallback_idx is not None:
                    record["unit_name"] = cell(unit_fallback_idx).strip() or None
                extra = {h: cell(i).strip() for h, i in extra_cols.items() if cell(i).strip()}
                code = record.get("code") or ""
                if not code:
                    errors.append({"lineNo": r.index, "field": "料号", "code": "REQUIRED", "message": "料号不能为空"})
                elif len(code) > 64:
                    errors.append({"lineNo": r.index, "field": "料号", "code": "TOO_LONG", "message": "料号超过 64 字符"})
                if not record.get("name"):
                    errors.append({"lineNo": r.index, "field": "品名", "code": "REQUIRED", "message": "品名不能为空"})
                if not record.get("unit_name"):
                    errors.append({"lineNo": r.index, "field": "库存单位.名称", "code": "REQUIRED", "message": "库存单位不能为空"})
                pr = ParsedRow(r.index, code, record, extra, errors)
                if not errors:
                    pr.hash = row_hash(record, extra)
                yield pr
        except XlsxMaxRowsExceededError:
            raise TemplateError("MATERIAL_MASTER_FILE_TOO_LARGE", f"行数超过 {MAX_ROWS}") from None
        except XlsxParseError:
            raise TemplateError("MATERIAL_MASTER_TEMPLATE_INVALID", "XLSX 文件无法解析") from None

    return meta, rows()


# --------------------------------------------------------------------------- 比对
def _existing_hashes(c: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in c.execute("SELECT code, row_hash FROM material_master")}


def _diff(c: sqlite3.Connection, code: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    old = c.execute("SELECT * FROM material_master WHERE code=?", (code,)).fetchone()
    if old is None:
        return []
    out = []
    for f in DATA_FIELDS:
        if f in COST_FIELDS:
            continue
        before = old[f]
        after = record.get(f)
        if isinstance(before, float) and isinstance(after, (int, float)) and float(after) == before:
            continue
        if before != after:
            out.append({"field": FIELD_LABELS[f], "before": before, "after": after})
    return out


@dataclass
class Analysis:
    meta: ParseMeta
    total: int = 0
    valid: int = 0
    invalid: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    missing: int = 0
    errors: list[dict[str, Any]] | None = None
    changes: list[dict[str, Any]] | None = None
    field_change_counts: dict[str, int] | None = None
    form_counts: dict[str, int] | None = None
    new_materials: int = 0


def analyze(c: sqlite3.Connection, path: Path, *,
            on_row: Callable[[ParsedRow, str], None] | None = None) -> Analysis:
    """解析并与库内比对。on_row(row, action) 在提交阶段用于写库。"""
    meta, rows = parse_file(path)
    existing = _existing_hashes(c)
    material_codes = {r[0] for r in c.execute("SELECT code FROM materials")}
    a = Analysis(meta=meta, errors=[], changes=[], field_change_counts={}, form_counts={})
    seen: dict[str, int] = {}
    for row in rows:
        a.total += 1
        if row.code and row.code in seen:
            row.errors.append({"lineNo": row.line_no, "field": "料号", "code": "DUPLICATE",
                               "message": f"料号与第 {seen[row.code]} 行重复"})
        elif row.code:
            seen[row.code] = row.line_no
        if row.errors:
            a.invalid += 1
            if len(a.errors) < MAX_STORED_ERRORS:
                a.errors.extend(row.errors[: MAX_STORED_ERRORS - len(a.errors)])
            continue
        a.valid += 1
        form = row.record.get("item_form") or "未填"
        a.form_counts[form] = a.form_counts.get(form, 0) + 1
        old_hash = existing.get(row.code)
        if old_hash is None:
            action = "NEW"
            a.new += 1
        elif old_hash == row.hash:
            action = "UNCHANGED"
            a.unchanged += 1
        else:
            action = "CHANGED"
            a.changed += 1
            diff = _diff(c, row.code, row.record)
            for d in diff:
                a.field_change_counts[d["field"]] = a.field_change_counts.get(d["field"], 0) + 1
            if len(a.changes) < MAX_STORED_CHANGES:
                a.changes.append({"lineNo": row.line_no, "code": row.code, "name": row.record.get("name"),
                                  "fields": diff[:12]})
        if row.code not in material_codes:
            a.new_materials += 1
        if on_row is not None:
            on_row(row, action)
    a.missing = sum(1 for code in existing if code not in seen)
    if a.total == 0:
        raise TemplateError("MATERIAL_MASTER_EMPTY", "表头之后没有数据行")
    return a


# --------------------------------------------------------------------------- 存储
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def import_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "imports" / "material_master"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batch_file(data_dir: Path, batch_id: str) -> Path:
    if not re.fullmatch(r"mmb_[0-9a-f]{32}", batch_id):
        raise ValueError("bad batch id")
    return import_dir(data_dir) / f"{batch_id}.xlsx"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cleanup_expired(c: sqlite3.Connection, data_dir: Path) -> None:
    now = _now()
    for r in c.execute("SELECT id FROM material_master_import_batches WHERE status='PREVIEW' AND expires_at<=?",
                       (now,)).fetchall():
        try:
            _batch_file(data_dir, r[0]).unlink(missing_ok=True)
        except ValueError:
            pass
        c.execute("UPDATE material_master_import_batches SET status='EXPIRED' WHERE id=?", (r[0],))


def create_preview(c: sqlite3.Connection, data_dir: Path, *, tmp_path: Path, file_name: str,
                   user_id: str) -> dict[str, Any]:
    """tmp_path 是已落盘的上传文件；成功后移动到批次目录。调用方负责事务提交。"""
    a = analyze(c, tmp_path)
    batch_id = "mmb_" + uuid.uuid4().hex
    digest = sha256_file(tmp_path)
    size = tmp_path.stat().st_size
    dest = _batch_file(data_dir, batch_id)
    shutil.move(str(tmp_path), dest)
    created = _now()
    expires = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + PREVIEW_TTL_SECONDS,
                                     timezone.utc).isoformat()
    summary = {
        "sheetName": a.meta.sheet_name,
        "recognizedColumns": len(a.meta.recognized_headers),
        "extraHeaders": a.meta.extra_headers,
        "missingOptionalHeaders": a.meta.missing_optional_headers,
        "fieldChangeCounts": dict(sorted(a.field_change_counts.items(), key=lambda kv: -kv[1])),
        "itemFormCounts": dict(sorted(a.form_counts.items(), key=lambda kv: -kv[1])),
        "newMaterials": a.new_materials,
    }
    c.execute(
        "INSERT INTO material_master_import_batches(id,file_name,file_sha256,file_size,sheet_name,header_row,"
        "total_count,valid_count,invalid_count,new_count,changed_count,unchanged_count,missing_count,"
        "summary_json,errors_json,changes_json,status,created_by,created_at,expires_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PREVIEW',?,?,?)",
        (batch_id, file_name[:200], digest, size, a.meta.sheet_name, a.meta.header_row, a.total, a.valid,
         a.invalid, a.new, a.changed, a.unchanged, a.missing, json.dumps(summary, ensure_ascii=False),
         json.dumps(a.errors, ensure_ascii=False), json.dumps(a.changes, ensure_ascii=False, default=str),
         user_id, created, expires),
    )
    return batch_view(c.execute("SELECT * FROM material_master_import_batches WHERE id=?", (batch_id,)).fetchone())


def batch_view(r: sqlite3.Row, *, with_details: bool = True) -> dict[str, Any]:
    out = {
        "previewId": r["id"], "batchId": r["id"], "fileName": r["file_name"], "fileSha256": r["file_sha256"],
        "fileSize": r["file_size"], "sheetName": r["sheet_name"], "headerRow": r["header_row"],
        "status": r["status"], "createdBy": r["created_by"], "createdAt": r["created_at"],
        "expiresAt": r["expires_at"], "committedAt": r["committed_at"],
        "preview": {
            "totalRows": r["total_count"], "validRows": r["valid_count"], "invalidRows": r["invalid_count"],
            "newRows": r["new_count"], "changedRows": r["changed_count"], "unchangedRows": r["unchanged_count"],
            "missingInFile": r["missing_count"],
            "canCommit": r["status"] == "PREVIEW" and r["valid_count"] > 0,
        },
        "summary": json.loads(r["summary_json"] or "{}"),
    }
    if r["result_json"]:
        out["result"] = json.loads(r["result_json"])
    if with_details:
        out["errors"] = json.loads(r["errors_json"] or "[]")
        out["changes"] = json.loads(r["changes_json"] or "[]")
    return out


_UPSERT_SQL = (
    f"INSERT INTO material_master(code,{','.join(DATA_FIELDS)},extra_json,row_hash,source_batch_id,imported_at,updated_at)"
    f" VALUES(?,{','.join('?' * len(DATA_FIELDS))},?,?,?,?,?)"
    f" ON CONFLICT(code) DO UPDATE SET {','.join(f'{f}=excluded.{f}' for f in DATA_FIELDS)},"
    " extra_json=excluded.extra_json, row_hash=excluded.row_hash, source_batch_id=excluded.source_batch_id,"
    " updated_at=excluded.updated_at"
)


def commit_batch(c: sqlite3.Connection, data_dir: Path, *, batch_id: str, user_id: str,
                 skip_invalid: bool) -> dict[str, Any]:
    """在调用方已开启的事务里写库；返回结果摘要。"""
    b = c.execute("SELECT * FROM material_master_import_batches WHERE id=?", (batch_id,)).fetchone()
    if b is None or b["status"] != "PREVIEW":
        raise TemplateError("MATERIAL_MASTER_PREVIEW_EXPIRED", "预览不存在、已过期或已提交")
    if datetime.fromisoformat(b["expires_at"]) <= datetime.now(timezone.utc):
        raise TemplateError("MATERIAL_MASTER_PREVIEW_EXPIRED", "预览已过期，请重新上传")
    if b["valid_count"] <= 0:
        raise TemplateError("MATERIAL_MASTER_VALIDATION_FAILED", "没有可导入的有效行")
    if b["invalid_count"] > 0 and not skip_invalid:
        raise TemplateError("MATERIAL_MASTER_VALIDATION_FAILED",
                            f"有 {b['invalid_count']} 行错误；修正后重新上传，或勾选「跳过错误行」")
    path = _batch_file(data_dir, batch_id)
    if not path.is_file() or sha256_file(path) != b["file_sha256"]:
        raise TemplateError("MATERIAL_MASTER_PREVIEW_EXPIRED", "预览文件已丢失，请重新上传")

    ts = _now()
    upserts: list[tuple] = []
    material_updates: list[tuple] = []
    material_inserts: list[tuple] = []
    existing_materials = {r[0]: (r[1], r[2], r[3], r[4]) for r in
                          c.execute("SELECT code, name, specification, unit, category FROM materials")}

    def on_row(row: ParsedRow, action: str) -> None:
        rec = row.record
        name, spec, unit = rec["name"], rec.get("specification"), rec["unit_name"]
        category = rec.get("main_category_name")
        if action != "UNCHANGED":
            upserts.append((row.code, *[rec.get(f) for f in DATA_FIELDS],
                            json.dumps(row.extra, ensure_ascii=False), row.hash, batch_id, ts, ts))
        cur = existing_materials.get(row.code)
        if cur is None:
            material_inserts.append((uuid.uuid4().hex, row.code, name, spec, unit, category))
        elif (cur[0], cur[1], cur[2]) != (name, spec, unit) or (not cur[3] and category):
            # 分类只在本地为空时回填，不覆盖仓库在后台手工调整过的分类
            material_updates.append((name, spec, unit, category, row.code))

    a = analyze(c, path, on_row=on_row)
    if a.valid != b["valid_count"] or a.invalid != b["invalid_count"]:
        raise TemplateError("MATERIAL_MASTER_PREVIEW_STALE", "数据已变化，请重新预览")
    c.executemany(_UPSERT_SQL, upserts)
    c.executemany(
        "INSERT INTO materials(id, code, name, specification, unit, category) VALUES(?,?,?,?,?,?)",
        material_inserts,
    )
    c.executemany(
        "UPDATE materials SET name=?, specification=?, unit=?,"
        " category=CASE WHEN category IS NULL OR category='' THEN ? ELSE category END,"
        " version=version+1 WHERE code=?",
        material_updates,
    )
    result = {
        "batchId": batch_id, "status": "COMMITTED", "committedAt": ts,
        "masterInserted": a.new, "masterUpdated": a.changed, "masterUnchanged": a.unchanged,
        "materialsCreated": len(material_inserts), "materialsUpdated": len(material_updates),
        "skippedInvalid": a.invalid, "missingInFile": a.missing,
    }
    c.execute("UPDATE material_master_import_batches SET status='COMMITTED', committed_by=?, committed_at=?,"
              " result_json=? WHERE id=?", (user_id, ts, json.dumps(result, ensure_ascii=False), batch_id))
    return result


def finish_commit_files(data_dir: Path, batch_id: str) -> None:
    """事务提交成功后删除暂存文件（失败不影响结果）。"""
    try:
        _batch_file(data_dir, batch_id).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


# --------------------------------------------------------------------------- 查询
def master_view(r: sqlite3.Row, *, include_cost: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in FIELDS:
        if f in COST_FIELDS and not include_cost:
            continue
        out[_camel(f)] = r[f]
    for f in ("is_special", "eco_version_control", "can_sell", "can_outsource", "can_produce",
              "can_stock_trade", "can_purchase"):
        if _camel(f) in out:
            out[_camel(f)] = bool(out[_camel(f)])
    eff = r["effective_until"]
    out["effective"] = eff is None or eff >= date.today().isoformat()
    out["extra"] = json.loads(r["extra_json"] or "{}")
    out["updatedAt"] = r["updated_at"]
    return out


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


SEARCH_FIELDS = ("code", "name", "specification", "drawing_no", "t6_code", "t6_drawing_no", "device_no",
                 "project_no")


def search(c: sqlite3.Connection, *, q: str = "", item_form: str = "", storage_location: str = "",
           main_category_code: str = "", device_no: str = "", project_no: str = "",
           limit: int = 50, offset: int = 0) -> tuple[int, list[sqlite3.Row]]:
    where, args = [], []
    if q:
        like = f"%{q}%"
        where.append("(" + " OR ".join(f"{f} LIKE ?" for f in SEARCH_FIELDS) + ")")
        args += [like] * len(SEARCH_FIELDS)
    for f, v in (("item_form", item_form), ("storage_location", storage_location),
                 ("main_category_code", main_category_code), ("device_no", device_no), ("project_no", project_no)):
        if v:
            where.append(f"{f}=?")
            args.append(v)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = c.execute(f"SELECT COUNT(*) FROM material_master{w}", args).fetchone()[0]
    rows = c.execute(f"SELECT * FROM material_master{w} ORDER BY code LIMIT ? OFFSET ?",
                     [*args, limit, offset]).fetchall()
    return total, rows


def facets(c: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    out = {}
    for f in ("item_form", "storage_location", "finance_category_name"):
        out[_camel(f)] = [{"value": r[0], "count": r[1]} for r in c.execute(
            f"SELECT {f}, COUNT(*) FROM material_master WHERE {f} IS NOT NULL GROUP BY {f} ORDER BY 2 DESC LIMIT 50")]
    return out
