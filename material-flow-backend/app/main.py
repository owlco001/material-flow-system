from __future__ import annotations

import hashlib
import hmac
import json
import csv
import io
import os
import random
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.xlsx_parser import (
    HeaderDetectService,
    HeaderMissingFieldsError,
    XlsxMaxRowsExceededError,
    XlsxParseError,
    XlsxSheetReader,
)

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

DATA_DIR = Path(os.environ.get("MATERIAL_FLOW_DATA", "/srv/material-flow/data"))
UPLOAD_DIR = Path(os.environ.get("MATERIAL_FLOW_UPLOADS", "/srv/material-flow/uploads"))
DB_PATH = DATA_DIR / "material_flow.db"
INITIAL_ADMIN_PASSWORD = os.environ.get("INITIAL_ADMIN_PASSWORD")
SERVICE_NAME = "material-flow"


def _env_flag(value: str | None) -> bool:
    """Parse an opt-in boolean; missing and unknown values stay disabled."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


ENABLE_ADMIN_ROLE_PREVIEW = _env_flag(os.environ.get("ENABLE_ADMIN_ROLE_PREVIEW"))
VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"
try:
    APP_VERSION = VERSION_FILE.read_text(encoding="ascii").strip()
except OSError:
    raise RuntimeError("VERSION file is required") from None
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", APP_VERSION):
    raise RuntimeError("VERSION file has an invalid format")

app = FastAPI(title="智慧工厂 API", version=APP_VERSION)
bearer = HTTPBearer(auto_error=False)

HEALTH_TABLES = frozenset({
    "users", "sessions", "consumed_refresh_tokens", "materials", "locations", "inventory",
    "transfer_requests", "audit_logs", "audit_events", "transfer_operations",
    "handover_operations", "material_work_items", "material_work_item_projections",
    "material_handovers", "exceptions", "location_bindings", "stocktakes", "employee_managers",
    "production_orders", "order_devices", "order_material_requirements", "login_attempts",
    "assembly_tasks", "assembly_task_stages", "assembly_stage_operations", "labor_records", "progress_events", "temporary_transfers", "assembly_operations", "bom_versions", "bom_items", "production_order_models",
    "admin_user_delete_operations", "admin_user_edit_operations", "admin_user_password_reset_operations",
    "assembly_model_versions",
 })

GLB_MAX_BYTES = 15 * 1024 * 1024
GLB_MAGIC = b"glTF"
MODEL_CODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# 密码哈希器：Argon2id，参数对齐 OWASP 2024 推荐（契约 A03）
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

# ==================== 契约常量 ====================
# 依据《物料流转系统-V1-API契约冻结补遗》，本文件覆盖旧文档中的冲突定义。

SCAN_TYPES = ("PRODUCTION_ORDER", "FLOW_NO", "MATERIAL_CODE", "LOCATION_CODE", "UNKNOWN")
# 禁止使用的历史枚举，收到即拒绝
FORBIDDEN_SCAN_TYPES = ("ORDER_NO", "LOGISTICS_NO", "ORDER", "LOGISTICS")

ACCEPTED_ORDER_DOC_TYPES = ("PRODUCTION_ORDER",)

TRANSFER_TYPES = ("INBOUND", "OUTBOUND", "TRANSFER", "STOCKTAKE")
ROLES = ("OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR", "ASSEMBLER")
ASSEMBLY_STATUSES = ("WAITING_MATERIAL", "MATERIAL_ACCEPTED", "IN_PROGRESS", "PAUSED_FOR_TEMPORARY_TRANSFER", "COMPLETED")
HANDOVER_STATES = ("PENDING", "CONFIRMED", "REJECTED", "CANCELLED")
TRANSFER_STATES = frozenset({
    "PENDING_APPROVAL", "APPROVED", "REJECTED", "EXECUTED",
})

# 工作台展示状态是从正式订单需求、流转申请和交接记录实时投影出来的，
# 不在 material_work_items 中维护第二份数量或状态事实。
WORKSPACE_STATUS_CODES = frozenset({
    "OUT_OF_STOCK", "ARRIVED", "IN_STOCK",
    "OUTBOUND_PENDING", "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED",
    "PENDING", "PICKED_UP", "AT_STATION", "REJECTED", "CANCELLED",
})
WORKSPACE_STATUS_LABELS = {
    "OUT_OF_STOCK": "缺货",
    "ARRIVED": "到货",
    "IN_STOCK": "在库",
    "OUTBOUND_PENDING": "待出库",
    "OUTBOUND_APPROVED": "已审批待出库",
    "OUTBOUND_CONFIRMED": "已出库",
    "PENDING": "待交接",
    "PICKED_UP": "已领取",
    "AT_STATION": "已到机台",
    "REJECTED": "已驳回",
    "CANCELLED": "已取消",
}
WORKSPACE_STATUS_COLORS = {
    "OUT_OF_STOCK": "status-red",
    "ARRIVED": "status-yellow",
    "IN_STOCK": "status-green",
    "OUTBOUND_PENDING": "status-yellow",
    "OUTBOUND_APPROVED": "status-blue",
    "OUTBOUND_CONFIRMED": "status-green",
    "PENDING": "status-yellow",
    "PICKED_UP": "status-green",
    "AT_STATION": "status-green",
    "REJECTED": "status-red",
    "CANCELLED": "status-neutral",
}
OUTBOUND_STATUS_TO_WORKSPACE = {
    "PENDING_APPROVAL": "OUTBOUND_PENDING",
    "APPROVED": "OUTBOUND_APPROVED",
    "EXECUTED": "OUTBOUND_CONFIRMED",
}

# 固定错误码
CODE_INVALID_REQUEST_ID = "INVALID_REQUEST_ID"
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_FORBIDDEN = "FORBIDDEN"
CODE_INVALID_SCAN_TYPE = "INVALID_SCAN_TYPE"
CODE_IDEMPOTENCY_KEY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
CODE_IDEMPOTENCY_PAYLOAD_MISMATCH = "IDEMPOTENCY_PAYLOAD_MISMATCH"
CODE_INVENTORY_VERSION_CONFLICT = "INVENTORY_VERSION_CONFLICT"
CODE_INSUFFICIENT_INVENTORY = "INSUFFICIENT_INVENTORY"
CODE_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
CODE_INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"
CODE_OLD_PASSWORD_MISMATCH = "OLD_PASSWORD_MISMATCH"
CODE_PASSWORD_UNCHANGED = "PASSWORD_UNCHANGED"
CODE_PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
CODE_ORDER_NOT_FOUND = "ORDER_NOT_FOUND"

# 令牌时效（契约 A03）
ACCESS_TOKEN_SECONDS = 3600           # 1 小时，缩短泄露暴露窗口
REFRESH_TOKEN_SECONDS = 30 * 86400    # 30 天，覆盖现场设备的长期离线场景

# 登录失败锁定策略（契约 A03）
LOGIN_MAX_FAILURES = 5          # 窗口内允许的最大失败次数
LOGIN_WINDOW_SECONDS = 15 * 60  # 计数窗口：15 分钟
LOGIN_LOCK_SECONDS = 15 * 60    # 锁定时长：15 分钟
CODE_TRANSFER_STATE_CONFLICT = "TRANSFER_STATE_CONFLICT"
CODE_APPROVAL_EXECUTOR_SAME_USER = "APPROVAL_EXECUTOR_SAME_USER"
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_RETRYABLE_UPSTREAM_ERROR = "RETRYABLE_UPSTREAM_ERROR"
CODE_INVALID_DOCUMENT_TYPE = "INVALID_DOCUMENT_TYPE"
CODE_ROLE_PREVIEW_ADMIN_ONLY = "ROLE_PREVIEW_ADMIN_ONLY"
CODE_ROLE_PREVIEW_DISABLED = "ROLE_PREVIEW_DISABLED"
CODE_INVALID_VIEW_ROLE = "INVALID_VIEW_ROLE"
CODE_ROLE_PREVIEW_READ_ONLY = "ROLE_PREVIEW_READ_ONLY"
CODE_INVALID_CLIENT_OPERATION_ID = "INVALID_CLIENT_OPERATION_ID"
CODE_SETUP_ALREADY_INITIALIZED = "SETUP_ALREADY_INITIALIZED"
CODE_USER_NOT_FOUND = "USER_NOT_FOUND"
CODE_USER_CANNOT_DELETE_SELF = "USER_CANNOT_DELETE_SELF"
CODE_LAST_ADMIN_CANNOT_DELETE = "LAST_ADMIN_CANNOT_DELETE"
CODE_USER_ALREADY_DELETED = "USER_ALREADY_DELETED"
CODE_USER_HAS_ACTIVE_BUSINESS = "USER_HAS_ACTIVE_BUSINESS"
CODE_USER_CANNOT_DISABLE_SELF = "USER_CANNOT_DISABLE_SELF"


class ApiError(HTTPException):
    """统一错误结构：{"error": {code, message, retryable, traceId, details}}"""

    def __init__(self, status_code: int, code: str, message: str,
                 retryable: bool = False, trace_id: str = "", details: dict | None = None) -> None:
        self.code = code
        self.retryable = retryable
        self.trace_id = trace_id
        self.details = details or {}
        super().__init__(status_code=status_code, detail=message)


def _safe_trace_id(candidate: str | None) -> str | None:
    """Only echo a syntactically valid request id; never reflect arbitrary input."""
    try:
        return str(uuid.UUID(candidate)) if candidate else None
    except (ValueError, AttributeError):
        return None


def _request_trace_id(request: Request) -> str:
    return _safe_trace_id(request.headers.get("X-Request-Id")) or str(uuid.uuid4())


def _safe_http_message(status_code: int, detail: Any) -> str:
    """Keep legacy ``detail`` responses useful without reflecting request data."""
    if isinstance(detail, str) and detail in {"账号或密码错误", "未登录", "会话已失效"}:
        return str(detail)
    return {
        400: "请求参数无效",
        401: "未登录或会话已失效",
        403: "无权执行此操作",
        404: "资源不存在",
        409: "资源状态冲突",
        413: "请求内容过大",
        422: "请求参数无效",
        429: "请求过于频繁",
    }.get(status_code, "服务器暂时不可用")


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {
            "code": exc.code,
            "message": exc.detail,
            "retryable": exc.retryable,
            "traceId": _safe_trace_id(exc.trace_id) or _request_trace_id(request),
            **({"details": exc.details} if exc.details else {}),
        }},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _exc: RequestValidationError):
    """Do not return Pydantic's input values; they may contain passwords or tokens."""
    message = "请求参数无效"
    trace_id = _request_trace_id(request)
    return JSONResponse(
        status_code=422,
        content={
            "detail": message,
            "error": {
                "code": CODE_VALIDATION_ERROR,
                "message": message,
                "retryable": False,
                "traceId": trace_id,
            },
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    message = _safe_http_message(exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": message,
            "error": {
                "code": {
                    401: CODE_UNAUTHORIZED,
                    403: CODE_FORBIDDEN,
                    422: CODE_VALIDATION_ERROR,
                }.get(exc.status_code, "HTTP_ERROR"),
                "message": message,
                "retryable": exc.status_code in {408, 429} or exc.status_code >= 500,
                "traceId": _request_trace_id(request),
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, _exc: Exception):
    """Turn unexpected failures into a stable response without stack/database details."""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "服务器内部错误",
                "retryable": True,
                "traceId": _request_trace_id(request),
            },
        },
    )


@app.middleware("http")
async def reject_preview_writes(request: Request, call_next):
    """A preview context may never be carried into a mutating endpoint."""
    is_preview_action = request.url.path.startswith("/api/v1/workspace/role-preview/")
    has_preview_context = (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and not is_preview_action
        and bool(request.headers.get("Authorization"))
        and (request.query_params.get("viewRole") is not None
             or request.headers.get("X-Workspace-View-Role") is not None)
    )
    if has_preview_context:
        credentials = request.headers.get("Authorization", "")
        token = credentials[7:].strip() if credentials.lower().startswith("bearer ") else ""
        authenticated_role = None
        if token:
            c = db()
            try:
                row = c.execute(
                    "SELECT u.role FROM sessions s JOIN users u ON u.id=s.user_id "
                    "WHERE s.token=? AND s.expires_at>? AND s.token_type='ACCESS' "
                    "AND u.active=1",
                    (token, int(time.time())),
                ).fetchone()
                authenticated_role = row["role"] if row else None
            finally:
                c.close()
        if authenticated_role is None:
            return await call_next(request)
        preview_role = request.query_params.get("viewRole") or request.headers.get(
            "X-Workspace-View-Role"
        )
        if preview_role not in ROLES:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "viewRole 不是有效的工作台角色",
                    "error": {
                        "code": CODE_INVALID_VIEW_ROLE,
                        "message": "viewRole 不是有效的工作台角色",
                        "retryable": False,
                        "traceId": _request_trace_id(request),
                    },
                },
            )
        if authenticated_role != "ADMIN":
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "仅 ADMIN 可使用角色预览",
                    "error": {
                        "code": CODE_ROLE_PREVIEW_ADMIN_ONLY,
                        "message": "仅 ADMIN 可使用角色预览",
                        "retryable": False,
                        "traceId": _request_trace_id(request),
                    },
                },
            )
        if not ENABLE_ADMIN_ROLE_PREVIEW:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": "管理员角色预览未启用",
                    "error": {
                        "code": CODE_ROLE_PREVIEW_DISABLED,
                        "message": "管理员角色预览未启用",
                        "retryable": False,
                        "traceId": _request_trace_id(request),
                    },
                },
            )
        return JSONResponse(
            status_code=403,
            content={
                "detail": "管理员角色预览仅支持只读工作台查询",
                "error": {
                    "code": CODE_ROLE_PREVIEW_READ_ONLY,
                    "message": "管理员角色预览仅支持只读工作台查询",
                    "retryable": False,
                    "traceId": _request_trace_id(request),
                },
            },
        )
    return await call_next(request)


def require_request_id(x_request_id: str | None) -> str:
    """契约 2 节：X-Request-Id 缺失或非 UUID 时返回 400 INVALID_REQUEST_ID。"""
    if not x_request_id:
        raise ApiError(400, CODE_INVALID_REQUEST_ID, "缺少 X-Request-Id 请求头")
    try:
        uuid.UUID(x_request_id)
    except (ValueError, AttributeError):
        raise ApiError(400, CODE_INVALID_REQUEST_ID, "X-Request-Id 必须是合法 UUID") from None
    return x_request_id


def require_client_operation_id(candidate: str | None, trace_id: str) -> str:
    """Preview requests use a UUID operation id for retry-safe audit writes."""
    if not candidate:
        raise ApiError(
            400, CODE_INVALID_CLIENT_OPERATION_ID,
            "缺少 X-Client-Operation-Id 请求头", trace_id=trace_id,
        )
    try:
        return str(uuid.UUID(candidate))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(
            400, CODE_INVALID_CLIENT_OPERATION_ID,
            "X-Client-Operation-Id 必须是合法 UUID", trace_id=trace_id,
        ) from None


def require_idempotency_key(idempotency_key: str | None, client_operation_id: Any, trace_id: str) -> str:
    """契约 2 节：Idempotency-Key 必须存在，且与 body.clientOperationId 一致。"""
    if not idempotency_key:
        raise ApiError(400, CODE_VALIDATION_ERROR, "缺少 Idempotency-Key 请求头", trace_id=trace_id)
    try:
        key_uuid = uuid.UUID(idempotency_key)
    except (ValueError, AttributeError, TypeError):
        raise ApiError(400, CODE_VALIDATION_ERROR, "Idempotency-Key 必须是合法 UUID", trace_id=trace_id) from None
    # client_operation_id 可能是 UUID 对象或字符串，统一转 str 比较
    if str(key_uuid).lower() != str(client_operation_id).lower():
        raise ApiError(
            400, CODE_IDEMPOTENCY_KEY_MISMATCH,
            "Idempotency-Key 与 clientOperationId 不一致", trace_id=trace_id,
        )
    return idempotency_key


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_storage_dirs() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise RuntimeError("后端存储目录配置不可用") from None


def _enable_wal(c: sqlite3.Connection) -> None:
    """Enable WAL once, tolerating another process finishing startup first."""
    deadline = time.monotonic() + 30.0
    while True:
        try:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() == "wal":
                return
            c.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def db() -> sqlite3.Connection:
    _ensure_storage_dirs()
    c = sqlite3.connect(DB_PATH, timeout=30.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    _enable_wal(c)
    return c


def hash_password(password: str) -> str:
    """生成 Argon2id 密码哈希（契约 A03）。

    参数对齐 OWASP 2024 推荐值：
      time_cost=3、memory_cost=64MiB、parallelism=4。
    编码串自带算法与参数，便于后续平滑调参。
    """
    return _ph.hash(password)


def check_password(password: str, encoded: str, c: sqlite3.Connection | None = None,
                   user_id: str | None = None) -> bool:
    """校验密码，并在命中历史 scrypt 哈希时透明升级为 Argon2id。

    历史库中存在 scrypt$salt$digest 格式；校验通过后立即改写为 Argon2id，
    无需强制全员改密，也无需停机迁移。
    """
    # --- 历史格式：透明升级路径 ---
    if encoded.startswith("scrypt$"):
        try:
            _, salt_hex, digest_hex = encoded.split("$", 2)
        except ValueError:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
        )
        if not hmac.compare_digest(actual.hex(), digest_hex):
            return False
        # 校验通过 → 就地升级为 Argon2id
        if c is not None and user_id is not None:
            try:
                c.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (hash_password(password), user_id),
                )
                c.commit()
            except sqlite3.Error:
                # 升级失败不影响本次登录，下次登录会重试
                pass
        return True

    # --- 当前格式：Argon2id ---
    try:
        _ph.verify(encoded, password)
    except VerifyMismatchError:
        return False
    except (InvalidHashError, VerificationError):
        # 哈希串损坏 / 格式非法，一律视为校验失败，不抛 500
        return False

    # 参数已过时则顺带重算（Argon2 官方推荐的 needs_rehash 机制）
    if _ph.check_needs_rehash(encoded) and c is not None and user_id is not None:
        try:
            c.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(password), user_id),
            )
            c.commit()
        except sqlite3.Error:
            pass
    return True


def audit(c: sqlite3.Connection, user_id: str | None, role: str | None, action: str, resource: str, resource_id: str | None, result: str = "SUCCESS", request_id: str = "") -> None:
    c.execute("INSERT INTO audit_logs(operator_id, role, action, resource_type, resource_id, request_id, occurred_at, result) VALUES(?,?,?,?,?,?,?,?)", (user_id, role, action, resource, resource_id, request_id, now(), result))


def init_db() -> None:
    """Initialize or migrate the local database as one serialized operation."""
    _ensure_storage_dirs()
    c = db()
    try:
        _init_db(c)
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _init_db(c: sqlite3.Connection) -> None:
    c.executescript("""
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS employee_managers(employee_id TEXT PRIMARY KEY REFERENCES users(id), manager_id TEXT NOT NULL REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL, token_type TEXT NOT NULL DEFAULT 'ACCESS', device_id TEXT);
    -- 已消费的刷新令牌墓碑表：用于检测令牌重放。
    -- 若直接删除旧令牌，"令牌不存在" 与 "令牌被重放" 无法区分，
    -- 泄露检测就永远不会触发。故消费后写入墓碑，保留至自然过期。
    CREATE TABLE IF NOT EXISTS consumed_refresh_tokens(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, consumed_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, specification TEXT, unit TEXT NOT NULL, batch_no TEXT, expiry_date TEXT, total_quantity INTEGER NOT NULL DEFAULT 0, available_quantity INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS locations(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0), UNIQUE(material_id, location_id));
    CREATE TABLE IF NOT EXISTS transfer_requests(id TEXT PRIMARY KEY, client_operation_id TEXT UNIQUE NOT NULL, type TEXT NOT NULL, document_no TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT, approved_at TEXT, executed_at TEXT, rejection_reason TEXT);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, operator_id TEXT, role TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT, request_id TEXT, occurred_at TEXT NOT NULL, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, actor_user_id TEXT NOT NULL, actor_role TEXT NOT NULL, request_id TEXT NOT NULL, client_operation_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, server_time TEXT NOT NULL, device_id TEXT, source_ip TEXT, result TEXT NOT NULL, view_role TEXT, action TEXT);
    CREATE TABLE IF NOT EXISTS transfer_operations(client_operation_id TEXT PRIMARY KEY, transfer_request_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS handover_operations(client_operation_id TEXT PRIMARY KEY, handover_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS stocktake_operations(client_operation_id TEXT PRIMARY KEY, stocktake_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS exception_operations(client_operation_id TEXT PRIMARY KEY, exception_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assembly_tasks(id TEXT PRIMARY KEY, order_no TEXT NOT NULL, device_id TEXT NOT NULL, device_no TEXT NOT NULL, assigned_assembler_id TEXT REFERENCES users(id), status TEXT NOT NULL CHECK(status IN ('WAITING_MATERIAL','MATERIAL_ACCEPTED','IN_PROGRESS','PAUSED_FOR_TEMPORARY_TRANSFER','COMPLETED')), progress_stage INTEGER NOT NULL DEFAULT 0 CHECK(progress_stage BETWEEN 0 AND 3), task_version INTEGER NOT NULL DEFAULT 1, material_accepted_at TEXT, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS labor_records(id TEXT PRIMARY KEY, task_id TEXT REFERENCES assembly_tasks(id), worker_user_id TEXT NOT NULL REFERENCES users(id), type TEXT NOT NULL CHECK(type IN ('ASSEMBLY','TEMPORARY_TRANSFER')), status TEXT NOT NULL CHECK(status IN ('ACTIVE','COMPLETED')), started_at TEXT NOT NULL, ended_at TEXT, duration_minutes INTEGER CHECK(duration_minutes IS NULL OR duration_minutes >= 0), remark TEXT, client_operation_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS progress_events(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES assembly_tasks(id), worker_user_id TEXT NOT NULL REFERENCES users(id), from_stage INTEGER NOT NULL, to_stage INTEGER NOT NULL CHECK(to_stage BETWEEN 1 AND 3), task_version INTEGER NOT NULL, server_time TEXT NOT NULL, client_operation_id TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS temporary_transfers(id TEXT PRIMARY KEY, worker_user_id TEXT NOT NULL REFERENCES users(id), source_task_id TEXT REFERENCES assembly_tasks(id), labor_record_id TEXT NOT NULL UNIQUE REFERENCES labor_records(id), status TEXT NOT NULL CHECK(status IN ('ACTIVE','COMPLETED')), remark TEXT NOT NULL CHECK(length(remark) BETWEEN 1 AND 500), started_at TEXT NOT NULL, ended_at TEXT, client_operation_id TEXT NOT NULL UNIQUE, device_id TEXT);
    CREATE TABLE IF NOT EXISTS assembly_operations(client_operation_id TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assembly_task_stages(task_id TEXT NOT NULL REFERENCES assembly_tasks(id) ON DELETE CASCADE, stage_no INTEGER NOT NULL CHECK(stage_no BETWEEN 1 AND 3), status TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK(status IN ('NOT_STARTED','IN_PROGRESS','COMPLETED','REWORK_REQUIRED')), version INTEGER NOT NULL DEFAULT 1, started_at TEXT, completed_at TEXT, rework_reason TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(task_id, stage_no));
    CREATE TABLE IF NOT EXISTS assembly_stage_operations(client_operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage_no INTEGER NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assembly_task_members(task_id TEXT NOT NULL REFERENCES assembly_tasks(id) ON DELETE CASCADE, assembler_id TEXT NOT NULL REFERENCES users(id), assignment_role TEXT NOT NULL CHECK(assignment_role IN ('LEAD','MEMBER')), assigned_by TEXT NOT NULL REFERENCES users(id), assigned_at TEXT NOT NULL, removed_at TEXT, PRIMARY KEY(task_id, assembler_id));
    CREATE TABLE IF NOT EXISTS assembly_assignment_operations(client_operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_labor_worker ON labor_records(worker_user_id) WHERE status='ACTIVE';
    CREATE INDEX IF NOT EXISTS idx_assembly_tasks_assembler ON assembly_tasks(assigned_assembler_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_assembly_task_members_active ON assembly_task_members(assembler_id, removed_at, task_id);
    CREATE INDEX IF NOT EXISTS idx_assembly_task_members_task ON assembly_task_members(task_id, removed_at, assignment_role);
    CREATE INDEX IF NOT EXISTS idx_labor_records_task ON labor_records(task_id, status);
    CREATE TABLE IF NOT EXISTS material_work_items(id TEXT PRIMARY KEY, requirement_id TEXT, material_id TEXT NOT NULL, device_id TEXT, assigned_user_id TEXT, quantity INTEGER NOT NULL CHECK(quantity > 0));
    -- 工作台状态投影只保存状态、责任和最后交接引用，不复制订单需求数量事实。
    CREATE TABLE IF NOT EXISTS material_work_item_projections(work_item_id TEXT PRIMARY KEY, status_code TEXT NOT NULL CHECK(status_code IN ('PENDING','PICKED_UP','AT_STATION','REJECTED','CANCELLED')), current_owner_user_id TEXT, last_handover_id TEXT, target_device_id TEXT, status_updated_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS material_handovers(id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL, transfer_request_id TEXT NOT NULL REFERENCES transfer_requests(id), quantity INTEGER NOT NULL CHECK(quantity > 0), from_location TEXT NOT NULL, device_id TEXT, receiver_user_id TEXT, remark TEXT, client_operation_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL CHECK(status IN ('PENDING','CONFIRMED','REJECTED','CANCELLED')), created_by TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_by TEXT, confirmed_at TEXT, decision_reason TEXT);
    CREATE TABLE IF NOT EXISTS exceptions(id TEXT PRIMARY KEY, material_id TEXT, type TEXT NOT NULL, book_quantity INTEGER NOT NULL DEFAULT 0, actual_quantity INTEGER NOT NULL DEFAULT 0, difference INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, description TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT, order_no TEXT, device_id TEXT);
    CREATE TABLE IF NOT EXISTS location_bindings(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0), evidence_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS stocktakes(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT, book_quantity INTEGER NOT NULL, actual_quantity INTEGER NOT NULL CHECK(actual_quantity >= 0), difference INTEGER NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_by TEXT, confirmed_at TEXT);
    CREATE TABLE IF NOT EXISTS production_orders(id TEXT PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, product_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'IN_PROGRESS', created_at TEXT NOT NULL, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS production_order_models(id TEXT PRIMARY KEY, order_id TEXT REFERENCES production_orders(id) ON DELETE CASCADE, model_code TEXT NOT NULL UNIQUE, model_name TEXT NOT NULL DEFAULT '', bom_version_id TEXT, planned_quantity INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, device_no TEXT UNIQUE NOT NULL, device_name TEXT NOT NULL, workshop TEXT NOT NULL, model_capability TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISABLED','MAINTENANCE')), created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS production_model_devices(id TEXT PRIMARY KEY, order_model_id TEXT NOT NULL REFERENCES production_order_models(id) ON DELETE CASCADE, device_id TEXT NOT NULL REFERENCES devices(id), status TEXT NOT NULL CHECK(status IN ('ASSIGNED','RELEASED','CLOSED')), assigned_by TEXT NOT NULL, assigned_at TEXT NOT NULL, UNIQUE(order_model_id, device_id));
    CREATE TABLE IF NOT EXISTS production_order_operations(client_operation_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS device_operations(client_operation_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS device_assignment_operations(client_operation_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bom_versions(id TEXT PRIMARY KEY, model_code TEXT NOT NULL, version_no INTEGER NOT NULL CHECK(version_no > 0), status TEXT NOT NULL CHECK(status IN ('DRAFT','PUBLISHED','ARCHIVED')), source_file_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL CHECK(row_count >= 0), created_by TEXT NOT NULL, created_at TEXT NOT NULL, published_by TEXT, published_at TEXT, UNIQUE(model_code, version_no));
    CREATE TABLE IF NOT EXISTS bom_items(id TEXT PRIMARY KEY, bom_version_id TEXT NOT NULL REFERENCES bom_versions(id) ON DELETE CASCADE, material_id TEXT NOT NULL REFERENCES materials(id), material_code TEXT NOT NULL, material_name TEXT NOT NULL, specification TEXT, unit TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity > 0), scrap_rate REAL NOT NULL DEFAULT 0 CHECK(scrap_rate >= 0 AND scrap_rate < 1), substitute_material_codes TEXT NOT NULL DEFAULT '[]', line_no INTEGER NOT NULL, UNIQUE(bom_version_id, material_code));
    CREATE INDEX IF NOT EXISTS idx_bom_versions_model_status ON bom_versions(model_code, status, version_no);
    -- 订单物料需求：订单 × 设备 × 物料主数据的关联。
    -- material_id 必须指向 materials.id（真实主数据），不可用物料编码或设备编号冒充。
    -- device_id 允许为空，兼容不按设备拆分的订单。
    CREATE TABLE IF NOT EXISTS order_material_requirements(id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE, device_id TEXT REFERENCES order_devices(id) ON DELETE CASCADE, material_id TEXT NOT NULL REFERENCES materials(id), required_quantity INTEGER NOT NULL CHECK(required_quantity > 0), arrived_quantity INTEGER NOT NULL DEFAULT 0 CHECK(arrived_quantity >= 0), in_stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK(in_stock_quantity >= 0), status_code TEXT NOT NULL CHECK(status_code IN ('OUT_OF_STOCK','ARRIVED','IN_STOCK')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, CHECK(arrived_quantity <= required_quantity), CHECK(in_stock_quantity <= arrived_quantity), UNIQUE(order_id, device_id, material_id));
    CREATE TABLE IF NOT EXISTS order_devices(id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE, device_type TEXT NOT NULL, device_no TEXT UNIQUE NOT NULL, sequence_no INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(order_id, device_type, sequence_no));
    CREATE TABLE IF NOT EXISTS login_attempts(username TEXT PRIMARY KEY, failed_count INTEGER NOT NULL DEFAULT 0, first_failed_at INTEGER NOT NULL, locked_until INTEGER);
    CREATE TABLE IF NOT EXISTS setup_state(id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('UNINITIALIZED','INITIALIZED')), admin_username TEXT NOT NULL, initialized_at TEXT, initialized_by TEXT, version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS setup_operations(client_operation_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_user_delete_operations(client_operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_user_edit_operations(client_operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_user_password_reset_operations(client_operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload_digest TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS assembly_model_versions(
        id TEXT PRIMARY KEY,
        model_code TEXT NOT NULL,
        model_name TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        format TEXT NOT NULL CHECK(format='GLB'),
        byte_size INTEGER NOT NULL CHECK(byte_size > 0 AND byte_size <= 15728640),
        sha256 TEXT NOT NULL,
        storage_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('PUBLISHED','ARCHIVED')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        idempotency_key TEXT UNIQUE,
        UNIQUE(model_code, version),
        UNIQUE(model_code, sha256)
    );
    CREATE INDEX IF NOT EXISTS idx_assembly_model_versions_published
        ON assembly_model_versions(model_code, status, version);
    CREATE TABLE IF NOT EXISTS inventory_import_batches(id TEXT PRIMARY KEY, file_sha256 TEXT NOT NULL, header_row INTEGER NOT NULL, total_count INTEGER NOT NULL, valid_count INTEGER NOT NULL, invalid_count INTEGER NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, expires_at TEXT NOT NULL, snapshot_id TEXT);
    CREATE TABLE IF NOT EXISTS inventory_import_rows(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES inventory_import_batches(id) ON DELETE CASCADE, line_no INTEGER NOT NULL, raw_row_json TEXT NOT NULL, parsed_json TEXT NOT NULL, material_code TEXT NOT NULL, location_code TEXT, available_quantity_decimal TEXT, on_hand_quantity_decimal TEXT, valid INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory_import_operations(client_operation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES inventory_import_batches(id), created_by TEXT NOT NULL REFERENCES users(id), payload_json TEXT NOT NULL, result_json TEXT NOT NULL, snapshot_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory_snapshots(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE REFERENCES inventory_import_batches(id), snapshot_name TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory_snapshot_rows(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES inventory_snapshots(id) ON DELETE CASCADE, line_no INTEGER NOT NULL, material_code TEXT NOT NULL, location_code TEXT, available_quantity_decimal TEXT NOT NULL, on_hand_quantity_decimal TEXT NOT NULL, parsed_json TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_material_handovers_work_item ON material_handovers(work_item_id, created_at, id);
    CREATE INDEX IF NOT EXISTS idx_audit_events_entity ON audit_events(entity_id, id);
    """)
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 and INITIAL_ADMIN_PASSWORD and INITIAL_ADMIN_PASSWORD.strip():
        # 环境注入的初始密码同样是临时凭据，首次登录必须完成改密。
        c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", ("u_admin", "owlco", "系统管理员", "ADMIN", hash_password(INITIAL_ADMIN_PASSWORD), 1, 1, now()))
    setup_state = c.execute("SELECT 1 FROM setup_state WHERE id='default'").fetchone()
    if not setup_state:
        initialized = c.execute("SELECT 1 FROM users WHERE username='owlco' AND role='ADMIN'").fetchone() is not None
        c.execute("INSERT INTO setup_state(id,state,admin_username,initialized_at,initialized_by,version) VALUES(?,?,?,?,?,1)",
                  ('default', 'INITIALIZED' if initialized else 'UNINITIALIZED', 'owlco', now() if initialized else None,
                   'environment' if initialized else None))
    if c.execute("SELECT 1 FROM materials").fetchone() is None:
        c.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?,?,?)", ("mat_001", "MTR-001", "工业轴承", "6205-2RS", "件", "B20260912", None, 986, 986, 1))
        c.execute("INSERT INTO locations VALUES(?,?,?)", ("loc_001", "A-01-03", "一号库位"))
        c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("inv_001", "mat_001", "loc_001", 986))

    _migrate_schema(c)
    seed_demo_order(c)
    c.commit()


# ==================== 演示订单种子 ====================

# 26B-013 的 BOM 用到三类物料。规格明确要求：
# 不能伪造 material_id —— 每条需求的外键都必须能 JOIN 到 materials 真实主数据。
# 故这里补齐三条脱敏测试物料（均为通用件，不含真实厂商信息）。
DEMO_MATERIALS = (
    ("mat_ctl_cabinet", "MTR-CTL-001", "控制柜", "GGD-800x600x2200", "台"),
    ("mat_pos_sensor", "MTR-SEN-002", "位置传感器", "PNP-NO-M12", "只"),
    ("mat_drive_unit", "MTR-DRV-003", "输送驱动组件", "3kW-380V", "套"),
)

DEMO_ORDER_NO = "26B-013"
DEMO_ORDER_ID = "ord_demo_26b013"
DEMO_PRODUCT = "自动化流水线设备"

# 设备构成：横向 10 + 十字 10 + 缓存 3 + 合片 2 = 25 台
DEMO_DEVICE_PLAN = (
    ("横向输送机", "HZ", 10),
    ("十字输送机", "SZ", 10),
    ("缓存", "CC", 3),
    ("合片机", "HP", 2),
)

# 固定 seed：规格要求"随机只允许使用固定 seed 的测试种子，确保重复执行可复现"。
_DEMO_SEED = 20260913

# 三种状态的量值规则（规格第 6 节验收标准）：
#   OUT_OF_STOCK → 缺货，到货数为 0
#   ARRIVED      → 已到货未入库，在库数为 0
#   IN_STOCK     → 已入库，在库数 = 需求数
_STATUS_PLAN = (
    # (status_code, required, arrived, in_stock)
    ("OUT_OF_STOCK", 20, 0, 0),
    ("ARRIVED", 20, 20, 0),
    ("IN_STOCK", 20, 20, 20),
)


def seed_demo_order(c: sqlite3.Connection) -> None:
    """幂等写入 26B-013 演示订单。

    幂等性：全部使用固定 id + INSERT OR IGNORE，重复执行不产生重复行。
    这比"先查再插"更稳 —— 并发启动时也不会插重。
    """
    ts = now()

    for mat_id, code, name, spec, unit in DEMO_MATERIALS:
        c.execute(
            "INSERT OR IGNORE INTO materials VALUES(?,?,?,?,?,?,?,?,?,?)",
            (mat_id, code, name, spec, unit, None, None, 0, 0, 1),
        )

    # production_orders 的 product_name / updated_at 是新增列，用 upsert 补齐
    c.execute(
        """INSERT INTO production_orders(id, order_no, product_name, status, created_at, updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(order_no) DO UPDATE SET
             product_name=excluded.product_name,
             updated_at=excluded.updated_at""",
        (DEMO_ORDER_ID, DEMO_ORDER_NO, DEMO_PRODUCT, "RELEASED", ts, ts),
    )
    order_id = c.execute(
        "SELECT id FROM production_orders WHERE order_no=?", (DEMO_ORDER_NO,)
    ).fetchone()["id"]

    # 固定 seed 的随机源，保证可复现
    rnd = random.Random(_DEMO_SEED)

    seq = 0
    for device_type, prefix, count in DEMO_DEVICE_PLAN:
        for i in range(1, count + 1):
            seq += 1
            device_id = f"dev_demo_{prefix}{i:02d}"
            device_no = f"{DEMO_ORDER_NO}-{prefix}{i:02d}"
            c.execute(
                "INSERT OR IGNORE INTO order_devices VALUES(?,?,?,?,?,?)",
                (device_id, order_id, device_type, device_no, seq, ts),
            )
            for mat_id, _code, _name, _spec, _unit in DEMO_MATERIALS:
                # 每台设备三类物料，状态在三种之间轮转 + 固定 seed 抖动，
                # 保证 75 条里三种状态都出现（验收要求）
                status, required, arrived, in_stock = _STATUS_PLAN[
                    (seq + len(mat_id)) % len(_STATUS_PLAN)
                ]
                req_id = f"omr_demo_{device_id}_{mat_id}"
                c.execute(
                    """INSERT OR IGNORE INTO order_material_requirements
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (req_id, order_id, device_id, mat_id, required, arrived, in_stock, status, ts, ts),
                )
    # rnd 保留以便将来加入状态抖动；当前轮转方案已满足"三态全覆盖"且完全确定
    _ = rnd.random()


def _migrate_schema(c: sqlite3.Connection) -> None:
    """幂等的增量迁移。

    历史库的 sessions 表只有 (token, user_id, expires_at) 三列，
    引入 refresh token 后需要补 token_type 与 device_id。
    SQLite 不支持 ADD COLUMN IF NOT EXISTS，故先查 PRAGMA 再补。

    同样地，production_orders 早期只有 model_name，未区分"产品名"，
    新模型要求 product_name + updated_at，此处按需补齐。

    早期订单需求表没有 device、入库数量、状态和时间字段，不能直接
    ADD COLUMN（旧表上的约束和唯一索引仍会保留）。迁移时重建为当前
    结构，并把历史需求保留为未入库的按订单级需求。
    """
    c.execute("UPDATE users SET role='MATERIAL' WHERE role='MATERIAL_CLERK'")

    temporary_transfer_columns = {r["name"] for r in c.execute("PRAGMA table_info(temporary_transfers)").fetchall()}
    if "device_id" not in temporary_transfer_columns:
        c.execute("ALTER TABLE temporary_transfers ADD COLUMN device_id TEXT")
    c.execute("""UPDATE temporary_transfers SET device_id=(SELECT device_id FROM assembly_tasks t
                 WHERE t.id=temporary_transfers.source_task_id) WHERE device_id IS NULL""")

    transfer_cols = {r["name"] for r in c.execute("PRAGMA table_info(transfer_requests)").fetchall()}
    if "rejection_reason" not in transfer_cols:
        c.execute("ALTER TABLE transfer_requests ADD COLUMN rejection_reason TEXT")

    exception_cols = {r["name"] for r in c.execute("PRAGMA table_info(exceptions)").fetchall()}
    if "order_no" not in exception_cols:
        c.execute("ALTER TABLE exceptions ADD COLUMN order_no TEXT")
    if "device_id" not in exception_cols:
        c.execute("ALTER TABLE exceptions ADD COLUMN device_id TEXT")
    existing = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
    if "token_type" not in existing:
        c.execute("ALTER TABLE sessions ADD COLUMN token_type TEXT NOT NULL DEFAULT 'ACCESS'")
    if "device_id" not in existing:
        c.execute("ALTER TABLE sessions ADD COLUMN device_id TEXT")

    audit_event_columns = {
        r["name"] for r in c.execute("PRAGMA table_info(audit_events)").fetchall()
    }
    if "view_role" not in audit_event_columns:
        c.execute("ALTER TABLE audit_events ADD COLUMN view_role TEXT")
    if "action" not in audit_event_columns:
        c.execute("ALTER TABLE audit_events ADD COLUMN action TEXT")

    po_cols = {r["name"] for r in c.execute("PRAGMA table_info(production_orders)").fetchall()}
    if "product_name" not in po_cols:
        c.execute("ALTER TABLE production_orders ADD COLUMN product_name TEXT NOT NULL DEFAULT ''")
        # 把历史 model_name 迁移过来，避免既有订单产品名为空
        if "model_name" in po_cols:
            c.execute(
                "UPDATE production_orders SET product_name = COALESCE(model_name,'') "
                "WHERE product_name = ''"
            )
    if "updated_at" not in po_cols:
        c.execute("ALTER TABLE production_orders ADD COLUMN updated_at TEXT")
        c.execute("UPDATE production_orders SET updated_at = created_at WHERE updated_at IS NULL")

    _migrate_legacy_order_requirements(c)
    model_cols = {r["name"] for r in c.execute("PRAGMA table_info(production_order_models)").fetchall()}
    if "planned_quantity" not in model_cols:
        c.execute("ALTER TABLE production_order_models ADD COLUMN planned_quantity INTEGER NOT NULL DEFAULT 0")
    if "version" not in model_cols:
        c.execute("ALTER TABLE production_order_models ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    model_version_cols = {r["name"] for r in c.execute("PRAGMA table_info(assembly_model_versions)").fetchall()}
    if model_version_cols and "idempotency_key" not in model_version_cols:
        c.execute("ALTER TABLE assembly_model_versions ADD COLUMN idempotency_key TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_assembly_model_versions_idempotency ON assembly_model_versions(idempotency_key)")


def _migrate_legacy_order_requirements(c: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in c.execute(
            "PRAGMA table_info(order_material_requirements)"
        ).fetchall()
    }
    required_columns = {
        "id", "order_id", "device_id", "material_id", "required_quantity",
        "arrived_quantity", "in_stock_quantity", "status_code", "created_at",
        "updated_at",
    }
    if required_columns <= columns:
        return

    legacy_columns = {
        "id", "order_id", "material_id", "required_quantity", "arrived_quantity",
    }
    if not legacy_columns <= columns:
        raise RuntimeError("历史订单需求表结构无法安全迁移")

    legacy_table = "order_material_requirements_legacy"
    if c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (legacy_table,),
    ).fetchone():
        raise RuntimeError("检测到未完成的订单需求迁移")

    c.execute(
        "ALTER TABLE order_material_requirements RENAME TO " + legacy_table
    )
    c.execute(
        """CREATE TABLE order_material_requirements(
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE,
            device_id TEXT REFERENCES order_devices(id) ON DELETE CASCADE,
            material_id TEXT NOT NULL REFERENCES materials(id),
            required_quantity INTEGER NOT NULL CHECK(required_quantity > 0),
            arrived_quantity INTEGER NOT NULL DEFAULT 0 CHECK(arrived_quantity >= 0),
            in_stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK(in_stock_quantity >= 0),
            status_code TEXT NOT NULL CHECK(status_code IN ('OUT_OF_STOCK','ARRIVED','IN_STOCK')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(arrived_quantity <= required_quantity),
            CHECK(in_stock_quantity <= arrived_quantity),
            UNIQUE(order_id, device_id, material_id)
        )"""
    )

    rows = c.execute(
        "SELECT id,order_id,material_id,required_quantity,arrived_quantity "
        f"FROM {legacy_table}"
    ).fetchall()
    for row in rows:
        required = row["required_quantity"]
        arrived = row["arrived_quantity"]
        if (
            isinstance(required, bool)
            or not isinstance(required, int)
            or required <= 0
            or isinstance(arrived, bool)
            or not isinstance(arrived, int)
            or arrived < 0
            or arrived > required
        ):
            raise RuntimeError("历史订单需求数据无法安全迁移")
        created_at = now()
        status = "ARRIVED" if arrived else "OUT_OF_STOCK"
        c.execute(
            """INSERT INTO order_material_requirements
               (id,order_id,device_id,material_id,required_quantity,
                arrived_quantity,in_stock_quantity,status_code,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                row["id"], row["order_id"], None, row["material_id"], required,
                arrived, 0, status, created_at, created_at,
            ),
        )
    c.execute("DROP TABLE " + legacy_table)


@app.on_event("startup")
def startup() -> None:
    init_db()


def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> sqlite3.Row:
    if not credentials:
        raise HTTPException(401, "未登录")
    c = db()
    row = c.execute(
        "SELECT u.*, s.device_id AS session_device_id FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token=? AND s.expires_at>? AND u.active=1 "
        # 必须限定 ACCESS：否则长效 refresh token 可当 access token 使用，
        # 短时效设计形同虚设，泄露后危害窗口被放大到 30 天。
        "AND s.token_type='ACCESS'",
        (credentials.credentials, int(time.time())),
    ).fetchone()
    c.close()
    if not row:
        raise HTTPException(401, "会话已失效")
    if row["role"] not in ROLES:
        raise HTTPException(403, "账号角色无效")
    # 首次登录的临时密码只能用于完成改密；服务端不能依赖客户端隐藏业务入口。
    # 登出不依赖此依赖项，健康检查也不需要认证，因此这里仅放行改密端点。
    if row["must_change_password"] and request.url.path != "/api/v1/auth/change-password":
        raise ApiError(
            403,
            CODE_PASSWORD_CHANGE_REQUIRED,
            "请先修改初始密码",
        )
    return row


def _workspace_context(
    user: sqlite3.Row,
    view_role: str | None,
    x_request_id: str | None,
    client_operation_id: str | None,
) -> tuple[str, bool, str, str | None]:
    """Resolve a read-only role projection without changing authenticated identity."""
    if view_role is None:
        return user["role"], False, x_request_id or "", None
    if view_role not in ROLES:
        raise ApiError(400, CODE_INVALID_VIEW_ROLE, "viewRole 不是有效的工作台角色")
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_ROLE_PREVIEW_ADMIN_ONLY, "仅 ADMIN 可使用角色预览")
    if not ENABLE_ADMIN_ROLE_PREVIEW:
        raise ApiError(404, CODE_ROLE_PREVIEW_DISABLED, "管理员角色预览未启用")
    trace_id = require_request_id(x_request_id)
    operation_id = require_client_operation_id(client_operation_id, trace_id)
    return view_role, True, trace_id, operation_id


def _append_role_preview_audit(
    c: sqlite3.Connection,
    *,
    actor: sqlite3.Row,
    view_role: str,
    action: str,
    request_id: str,
    client_operation_id: str,
) -> None:
    """Append one minimal, retry-idempotent ADMIN_ROLE_PREVIEW event."""
    if action not in {"ENTER", "QUERY", "EXIT"}:
        raise ValueError("invalid role preview audit action")
    prior = c.execute(
        "SELECT view_role,action FROM audit_events "
        "WHERE event_type='ADMIN_ROLE_PREVIEW' AND client_operation_id=?",
        (client_operation_id,),
    ).fetchone()
    if prior:
        if prior["view_role"] != view_role or prior["action"] != action:
            raise ApiError(
                409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                "相同 X-Client-Operation-Id 的预览请求不一致", trace_id=request_id,
            )
        return
    server_time = now()
    metadata = json.dumps(
        {"viewRole": view_role, "action": action}, ensure_ascii=False, sort_keys=True,
    )
    c.execute(
        """INSERT INTO audit_events
           (event_type,entity_type,entity_id,actor_user_id,actor_role,request_id,
            client_operation_id,before_json,after_json,server_time,device_id,source_ip,
            result,view_role,action)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "ADMIN_ROLE_PREVIEW", "WORKSPACE", "ROLE_PREVIEW", actor["id"], "ADMIN",
            request_id, client_operation_id, "{}", metadata, server_time, None, None,
            "SUCCESS", view_role, action,
        ),
    )


# ==================== 令牌签发与轮转 ====================

def issue_session(c: sqlite3.Connection, user_id: str, device_id: str) -> tuple[str, str, int]:
    """签发一对 access token / refresh token。

    设计（契约 A03）：
      - access token 短时效（1 小时），泄露后暴露窗口小；
      - refresh token 长时效（30 天），仅在刷新端点可用；
      - 两者都落库，登出 / 改密可一次性全清。

    返回 (access_token, refresh_token, access_expires_at)
    """
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    now_ts = int(time.time())
    access_exp = now_ts + ACCESS_TOKEN_SECONDS
    refresh_exp = now_ts + REFRESH_TOKEN_SECONDS

    c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
              (access, user_id, access_exp, "ACCESS", device_id))
    c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
              (refresh, user_id, refresh_exp, "REFRESH", device_id))
    return access, refresh, access_exp


def revoke_all_sessions(c: sqlite3.Connection, user_id: str) -> int:
    """吊销该用户全部会话（改密 / 登出 / 检测到令牌重用）。"""
    cur = c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return cur.rowcount


def _revoke_refresh_family(c: sqlite3.Connection, token: str) -> None:
    """刷新令牌被重用时，连同该用户全部令牌一并吊销。

    这是 OAuth2 的 refresh token rotation 标准做法：
    合法客户端每次刷新都会换到新令牌，旧的立即作废。
    若旧令牌再次出现，说明它被窃取并被重放 —— 此时无法区分
    攻击者与合法用户，只能把整族令牌作废，强制重新登录。

    为避免误伤：这里按 user_id 吊销全部会话，而不是按 device_id。
    多设备场景下，若只吊销单设备，攻击者仍可在其他设备上活动。
    """
    row = c.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
    if row:
        uid = row["user_id"]
    else:
        # 令牌已被消费（不在 sessions 中），从墓碑表取 user_id
        row = c.execute(
            "SELECT user_id FROM consumed_refresh_tokens WHERE token=?", (token,)
        ).fetchone()
        uid = row["user_id"] if row else None
    if uid:
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))


class Login(BaseModel):
    username: str | None = None
    employeeNo: str | None = None
    password: str
    deviceId: str = Field(min_length=1, max_length=128)
    clientVersion: str = "0.1.0"

    def login_identifier(self) -> str:
        value = (self.username or self.employeeNo or "").strip()
        if not value or (self.username and self.employeeNo and self.username.strip() != self.employeeNo.strip()):
            raise ValueError("username 或 employeeNo 必须提供且一致")
        return value


class ChangePassword(BaseModel):
    oldPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=8, max_length=256)


class Scan(BaseModel):
    """契约补遗 3.1：rawValue 长度 1-128，clientOperationId 为 UUID。"""
    rawValue: str = Field(min_length=1, max_length=128)
    clientOperationId: uuid.UUID


class TransferItem(BaseModel):
    """契约补遗 4.1：嵌套类型，避免裸 dict 导致字段漂移。"""
    materialId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1)
    batchNo: str | None = Field(default=None, max_length=64)
    sourceLocationCode: str | None = Field(default=None, max_length=64)
    targetLocationCode: str | None = Field(default=None, max_length=64)
    expectedInventoryVersion: int = Field(ge=1)


class Transfer(BaseModel):
    clientOperationId: uuid.UUID
    type: str
    documentNo: str | None = Field(default=None, max_length=64)
    items: list[TransferItem] = Field(min_length=1, max_length=100)
    remark: str | None = Field(default=None, max_length=500)
    # 契约：可变默认值必须用 default_factory
    evidenceIds: list[str] = Field(default_factory=list, max_length=20)


class HandoverCreate(BaseModel):
    workItemId: str = Field(min_length=1, max_length=128)
    transferRequestId: str = Field(min_length=1, max_length=128)
    quantity: int = Field(ge=1)
    fromLocation: str = Field(min_length=1, max_length=128)
    deviceId: str | None = Field(default=None, max_length=128)
    receiverUserId: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=500)
    clientOperationId: uuid.UUID


class HandoverDecision(BaseModel):
    clientOperationId: uuid.UUID
    requestId: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


class Decision(BaseModel):
    decision: str
    comment: str = ""
    clientOperationId: uuid.UUID | None = None


class LocationBindingCreate(BaseModel):
    """库位绑定写入契约；保留既有 camelCase 字段名。"""
    materialCode: str = Field(min_length=1, max_length=64)
    locationCode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(default=0, ge=0)
    evidenceIds: list[str] = Field(default_factory=list, max_length=20)
    clientOperationId: uuid.UUID | None = None


class StocktakeCreate(BaseModel):
    """盘点创建契约。bookQuantity 缺省时沿用服务端账面数量。"""
    materialCode: str = Field(min_length=1, max_length=64)
    locationCode: str | None = Field(default=None, max_length=64)
    actualQuantity: int = Field(ge=0)
    bookQuantity: int | None = Field(default=None, ge=0)
    clientOperationId: uuid.UUID | None = None


class ExceptionCreate(BaseModel):
    """异常创建契约，关联资源必须完整且为真实主数据。"""
    orderNo: str | None = Field(default=None, max_length=64)
    deviceId: str | None = Field(default=None, max_length=128)
    materialId: str | None = Field(default=None, max_length=64)
    type: str = Field(default="OTHER", min_length=1, max_length=32)
    bookQuantity: int = Field(default=0, ge=0)
    actualQuantity: int = Field(default=0, ge=0)
    description: str | None = Field(default=None, max_length=500)
    evidenceIds: list[str] = Field(default_factory=list, max_length=20)
    clientOperationId: uuid.UUID | None = None


class EmployeeCreate(BaseModel):
    employeeNo: str = Field(min_length=1, max_length=64)
    displayName: str = Field(min_length=1, max_length=128)
    role: str
    password: str = Field(min_length=8, max_length=256)
    managerId: str | None = Field(default=None, max_length=128)


class DeleteUserRequest(BaseModel):
    clientOperationId: uuid.UUID


class EditUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientOperationId: uuid.UUID
    displayName: str | None = Field(default=None, min_length=1, max_length=128)
    role: str | None = None
    active: bool | None = None


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    newPassword: str = Field(min_length=8, max_length=256)
    clientOperationId: uuid.UUID


class InitializeAdminRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)
    confirmPassword: str = Field(min_length=8, max_length=256)
    clientOperationId: uuid.UUID


def _setup_payload_digest(body: InitializeAdminRequest) -> str:
    # Store only a non-reversible comparison digest; never persist the password.
    return hashlib.sha256(
        json.dumps({"password": body.password, "confirmPassword": body.confirmPassword},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


@app.get("/api/v1/setup/status")
def setup_status() -> dict[str, Any]:
    c = db()
    try:
        state = c.execute("SELECT * FROM setup_state WHERE id='default'").fetchone()
        if state is None:
            initialized = c.execute("SELECT 1 FROM users WHERE username='owlco' AND role='ADMIN'").fetchone() is not None
            return {"initialized": initialized, "adminUsername": "owlco", "mustChangePassword": True, "serverTime": now()}
        admin = c.execute("SELECT must_change_password FROM users WHERE username=?", (state["admin_username"],)).fetchone()
        return {
            "initialized": state["state"] == "INITIALIZED",
            "adminUsername": state["admin_username"],
            "mustChangePassword": bool(admin["must_change_password"]) if admin else True,
            "serverTime": now(),
        }
    finally:
        c.close()


@app.post("/api/v1/setup/initialize-admin")
def initialize_admin(
    body: InitializeAdminRequest,
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    if body.password != body.confirmPassword:
        raise ApiError(400, CODE_VALIDATION_ERROR, "两次密码不一致", trace_id=trace_id)
    try:
        key = str(uuid.UUID(idempotency_key or ""))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(400, CODE_VALIDATION_ERROR, "Idempotency-Key 必须是合法 UUID", trace_id=trace_id) from None
    if key != str(body.clientOperationId):
        raise ApiError(400, CODE_IDEMPOTENCY_KEY_MISMATCH, "Idempotency-Key 与 clientOperationId 不一致", trace_id=trace_id)

    digest = _setup_payload_digest(body)
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute("SELECT * FROM setup_operations WHERE client_operation_id=?", (key,)).fetchone()
        if previous:
            if previous["payload_digest"] != digest:
                c.rollback()
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求参数不一致", trace_id=trace_id)
            result = json.loads(previous["result_json"])
            c.commit()
            return result
        state = c.execute("SELECT * FROM setup_state WHERE id='default'").fetchone()
        if state is None:
            c.execute("INSERT INTO setup_state(id,state,admin_username,version) VALUES('default','UNINITIALIZED','owlco',1)")
            state = c.execute("SELECT * FROM setup_state WHERE id='default'").fetchone()
        if state and state["state"] == "INITIALIZED":
            c.rollback()
            raise ApiError(409, CODE_SETUP_ALREADY_INITIALIZED, "管理员已初始化，不可覆盖", trace_id=trace_id)
        user = c.execute("SELECT id FROM users WHERE username='owlco'").fetchone()
        if user:
            c.execute("UPDATE users SET password_hash=?, role='ADMIN', must_change_password=1, active=1 WHERE id=?",
                      (hash_password(body.password), user["id"]))
            uid = user["id"]
        else:
            uid = "u_admin"
            c.execute("INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (uid, "owlco", "系统管理员", "ADMIN", hash_password(body.password), 1, 1, now()))
        ts = now()
        c.execute("UPDATE setup_state SET state='INITIALIZED', initialized_at=?, initialized_by=?, version=version+1 WHERE id='default'",
                  (ts, "setup",))
        result = {"initialized": True, "username": "owlco", "mustChangePassword": True, "serverTime": ts, "traceId": trace_id}
        c.execute("INSERT INTO setup_operations(client_operation_id,payload_digest,result_json,created_at) VALUES(?,?,?,?)",
                  (key, digest, json.dumps(result, ensure_ascii=False, sort_keys=True), ts))
        audit(c, None, None, "INITIALIZE_ADMIN", "SETUP", "default", "SUCCESS", trace_id)
        c.commit()
        return result
    except ApiError:
        raise
    finally:
        c.close()


@app.post("/api/v1/admin/users")
def add_employee(body: EmployeeCreate, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可添加员工")
    if body.role not in ROLES or body.role == "ADMIN":
        raise ApiError(400, CODE_VALIDATION_ERROR, "角色无效")
    c = db()
    try:
        if body.managerId and not c.execute("SELECT 1 FROM users WHERE id=? AND active=1", (body.managerId,)).fetchone():
            raise ApiError(400, CODE_VALIDATION_ERROR, "直属领导不存在")
        existing = c.execute("SELECT * FROM users WHERE username=?", (body.employeeNo,)).fetchone()
        if existing:
            manager = c.execute("SELECT manager_id FROM employee_managers WHERE employee_id=?", (existing["id"],)).fetchone()
            if existing["role"] != body.role or existing["display_name"] != body.displayName or (manager and manager["manager_id"] or None) != body.managerId:
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "工号已存在但员工信息不一致")
            return {"id": existing["id"], "employeeNo": existing["username"], "displayName": existing["display_name"], "role": existing["role"], "mustChangePassword": bool(existing["must_change_password"])}
        uid = "u_" + uuid.uuid4().hex
        c.execute("INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)", (uid, body.employeeNo, body.displayName, body.role, hash_password(body.password), 1, 1, now()))
        if body.managerId:
            c.execute("INSERT INTO employee_managers(employee_id,manager_id) VALUES(?,?)", (uid, body.managerId))
        audit(c, user["id"], user["role"], "CREATE", "USER", uid, "SUCCESS", x_request_id or "")
        c.commit()
        return {"id": uid, "employeeNo": body.employeeNo, "displayName": body.displayName, "role": body.role, "mustChangePassword": True}
    finally:
        c.close()


def _user_has_active_business(c: sqlite3.Connection, user_id: str) -> bool:
    return bool(c.execute("""SELECT (
        EXISTS(SELECT 1 FROM assembly_tasks WHERE assigned_assembler_id=? AND status <> 'COMPLETED')
        OR EXISTS(SELECT 1 FROM labor_records WHERE worker_user_id=? AND status='ACTIVE')
        OR EXISTS(SELECT 1 FROM transfer_requests WHERE created_by=? AND status='PENDING_APPROVAL')
        OR EXISTS(SELECT 1 FROM stocktakes WHERE created_by=? AND status='PENDING_CONFIRM')
        OR EXISTS(SELECT 1 FROM exceptions WHERE created_by=? AND status='PENDING')
        OR EXISTS(SELECT 1 FROM material_handovers WHERE status='PENDING' AND (created_by=? OR receiver_user_id=?))
    )""", (user_id, user_id, user_id, user_id, user_id, user_id, user_id)).fetchone()[0])


def _redacted_user(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "displayName": row["display_name"], "role": row["role"], "active": bool(row["active"])}


@app.patch("/api/v1/admin/users/{user_id}")
def edit_employee(user_id: str, body: EditUserRequest, user: sqlite3.Row = Depends(current_user),
                  x_request_id: str | None = Header(default=None),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可编辑用户", trace_id=trace_id)
    changes = body.model_dump(exclude_none=True)
    changes.pop("clientOperationId", None)
    if not changes:
        raise ApiError(400, CODE_VALIDATION_ERROR, "至少提供一个可编辑字段", trace_id=trace_id)
    if "role" in changes and changes["role"] not in ("OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "WORKSHOP_SUPERVISOR", "ASSEMBLER"):
        raise ApiError(400, CODE_VALIDATION_ERROR, "角色无效", trace_id=trace_id)
    operation_id = str(body.clientOperationId)
    payload = json.dumps({"userId": user_id, **changes}, ensure_ascii=False, sort_keys=True)
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM admin_user_edit_operations WHERE client_operation_id=?", (operation_id,)).fetchone()
        if prior:
            if prior["user_id"] != user_id or _payload_digest(prior["payload_json"]) != _payload_digest(payload):
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"]); result.update(traceId=trace_id, idempotent=True)
            c.rollback(); return result
        target = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise ApiError(404, CODE_USER_NOT_FOUND, "用户不存在", trace_id=trace_id)
        if target["id"] == user["id"] and changes.get("active") is False:
            raise ApiError(409, CODE_USER_CANNOT_DISABLE_SELF, "不能停用当前登录用户", trace_id=trace_id)
        new_role = changes.get("role", target["role"]); new_active = changes.get("active", bool(target["active"]))
        if target["role"] == "ADMIN" and (new_role != "ADMIN" or not new_active) and c.execute("SELECT COUNT(*) FROM users WHERE role='ADMIN' AND active=1").fetchone()[0] <= 1:
            raise ApiError(409, CODE_LAST_ADMIN_CANNOT_DELETE, "不能停用或降级最后一个启用的 ADMIN", trace_id=trace_id)
        if not new_active and target["active"] and _user_has_active_business(c, user_id):
            raise ApiError(409, CODE_USER_HAS_ACTIVE_BUSINESS, "用户存在未完成业务责任", trace_id=trace_id)
        before = _redacted_user(target)
        sets, values = [], []
        for field, column in (("displayName", "display_name"), ("role", "role"), ("active", "active")):
            if field in changes: sets.append(f"{column}=?"); values.append(changes[field])
        values.append(user_id); c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", values)
        updated = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(); after = _redacted_user(updated)
        if "active" in changes and not changes["active"]: revoke_all_sessions(c, user_id)
        audit(c, user["id"], user["role"], "UPDATE", "USER", user_id, "SUCCESS", trace_id)
        ts = now(); result = {"userId": user_id, **after, "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,request_id,client_operation_id,before_json,after_json,server_time,device_id,source_ip,result) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("ADMIN_USER_EDIT", "USER", user_id, user["id"], user["role"], trace_id, operation_id, json.dumps(before), json.dumps(after), ts, None, None, "SUCCESS"))
        c.execute("INSERT INTO admin_user_edit_operations VALUES(?,?,?,?,?)", (operation_id, user_id, payload, json.dumps(result), ts)); c.commit(); return result
    except ApiError:
        c.rollback(); raise
    finally: c.close()


@app.post("/api/v1/admin/users/{user_id}/password-reset")
def reset_employee_password(user_id: str, body: PasswordResetRequest, user: sqlite3.Row = Depends(current_user),
                            x_request_id: str | None = Header(default=None),
                            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id); require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    if user["role"] != "ADMIN": raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可重置密码", trace_id=trace_id)
    operation_id = str(body.clientOperationId); digest = hashlib.sha256(body.newPassword.encode()).hexdigest()
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE"); prior = c.execute("SELECT * FROM admin_user_password_reset_operations WHERE client_operation_id=?", (operation_id,)).fetchone()
        if prior:
            if prior["user_id"] != user_id or prior["payload_digest"] != digest: raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"]); result.update(traceId=trace_id, idempotent=True); c.rollback(); return result
        target = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target: raise ApiError(404, CODE_USER_NOT_FOUND, "用户不存在", trace_id=trace_id)
        before = _redacted_user(target); c.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?", (hash_password(body.newPassword), user_id)); revoke_all_sessions(c, user_id)
        after = {**before, "mustChangePassword": True}; ts = now(); result = {"userId": user_id, "mustChangePassword": True, "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,request_id,client_operation_id,before_json,after_json,server_time,device_id,source_ip,result) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("ADMIN_PASSWORD_RESET", "USER", user_id, user["id"], user["role"], trace_id, operation_id, json.dumps(before), json.dumps(after), ts, None, None, "SUCCESS"))
        c.execute("INSERT INTO admin_user_password_reset_operations VALUES(?,?,?,?,?)", (operation_id, user_id, digest, json.dumps(result), ts)); c.commit(); return result
    except ApiError: c.rollback(); raise
    except Exception:
        c.rollback()
        raise
    finally: c.close()


@app.delete("/api/v1/admin/users/{user_id}")
def delete_employee(
    user_id: str,
    body: DeleteUserRequest,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Disable a user while preserving history and all foreign-key references."""
    trace_id = require_request_id(x_request_id)
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可删除用户", trace_id=trace_id)

    operation_id = str(body.clientOperationId)
    payload = json.dumps(
        {"userId": user_id, "clientOperationId": operation_id},
        ensure_ascii=False,
        sort_keys=True,
    )
    c = db()
    try:
        # The idempotency read, business gates, state change, session revocation and
        # audit write share one lock so concurrent retries cannot split the result.
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute(
            "SELECT * FROM admin_user_delete_operations WHERE client_operation_id=?",
            (operation_id,),
        ).fetchone()
        if prior:
            if (
                prior["user_id"] != user_id
                or _payload_digest(prior["payload_json"]) != _payload_digest(payload)
            ):
                raise ApiError(
                    409,
                    CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                    "相同幂等键的请求体不一致",
                    trace_id=trace_id,
                )
            result = json.loads(prior["result_json"])
            result.update(idempotent=True, traceId=trace_id, serverTime=now())
            c.rollback()
            return result

        target = c.execute(
            "SELECT id,role,active FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not target:
            raise ApiError(404, CODE_USER_NOT_FOUND, "用户不存在", trace_id=trace_id)
        if target["id"] == user["id"]:
            raise ApiError(
                409,
                CODE_USER_CANNOT_DELETE_SELF,
                "不能删除当前登录用户",
                trace_id=trace_id,
            )
        if not target["active"]:
            raise ApiError(
                409,
                CODE_USER_ALREADY_DELETED,
                "用户已被停用",
                trace_id=trace_id,
            )
        if target["role"] == "ADMIN" and c.execute(
            "SELECT COUNT(*) FROM users WHERE role='ADMIN' AND active=1"
        ).fetchone()[0] <= 1:
            raise ApiError(
                409,
                CODE_LAST_ADMIN_CANNOT_DELETE,
                "不能删除最后一个启用的 ADMIN",
                trace_id=trace_id,
            )

        has_active_business = c.execute(
            """SELECT (
                EXISTS(
                    SELECT 1 FROM assembly_tasks
                     WHERE assigned_assembler_id=? AND status <> 'COMPLETED'
                )
                OR EXISTS(
                    SELECT 1 FROM labor_records
                     WHERE worker_user_id=? AND status='ACTIVE'
                )
                OR EXISTS(
                    SELECT 1 FROM transfer_requests
                     WHERE created_by=? AND status='PENDING_APPROVAL'
                )
                OR EXISTS(
                    SELECT 1 FROM stocktakes
                     WHERE created_by=? AND status='PENDING_CONFIRM'
                )
                OR EXISTS(
                    SELECT 1 FROM exceptions
                     WHERE created_by=? AND status='PENDING'
                )
                OR EXISTS(
                    SELECT 1 FROM material_handovers
                     WHERE status='PENDING' AND (created_by=? OR receiver_user_id=?)
                )
            )""",
            (user_id, user_id, user_id, user_id, user_id, user_id, user_id),
        ).fetchone()[0]
        if has_active_business:
            raise ApiError(
                409,
                CODE_USER_HAS_ACTIVE_BUSINESS,
                "用户存在未完成的装配任务、待审批单据或交接",
                trace_id=trace_id,
            )

        changed_at = now()
        c.execute("UPDATE users SET active=0 WHERE id=? AND active=1", (user_id,))
        revoke_all_sessions(c, user_id)
        audit(c, user["id"], user["role"], "DELETE", "USER", user_id, "SUCCESS", trace_id)
        result = {
            "userId": user_id,
            "status": "DELETED",
            "serverTime": changed_at,
            "traceId": trace_id,
            "idempotent": False,
        }
        c.execute(
            """INSERT INTO admin_user_delete_operations
               (client_operation_id,user_id,payload_json,result_json,created_at)
               VALUES(?,?,?,?,?)""",
            (operation_id, user_id, payload, json.dumps(result, ensure_ascii=False), changed_at),
        )
        c.commit()
        return result
    except ApiError:
        c.rollback()
        raise
    except Exception:
        c.rollback()
        raise ApiError(
            500,
            CODE_RETRYABLE_UPSTREAM_ERROR,
            "用户删除失败",
            retryable=True,
            trace_id=trace_id,
        ) from None
    finally:
        c.close()


class TransferApproval(BaseModel):
    decision: str
    comment: str = Field(default="", max_length=500)
    clientOperationId: uuid.UUID


class TransferAction(BaseModel):
    clientOperationId: uuid.UUID


def _assembly_model_payload(row: sqlite3.Row, *, include_server_time: bool = False) -> dict[str, Any]:
    payload = {
        "modelId": row["id"], "modelCode": row["model_code"], "modelName": row["model_name"],
        "version": row["version"], "format": row["format"], "byteSize": row["byte_size"],
        "sha256": row["sha256"],
        "downloadPath": f"/api/v1/assembly-models/{row['model_code']}/versions/{row['version']}/content",
    }
    if include_server_time:
        payload["serverTime"] = now()
    return payload


@app.post("/api/v1/assembly-models", status_code=201)
async def upload_assembly_model(
    modelCode: str = Form(...), modelName: str = Form(...), file: UploadFile = File(...),
    sha256: str | None = Form(default=None),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    trace_id = _safe_trace_id(x_request_id) or str(uuid.uuid4())
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 或 WAREHOUSE_ADMIN 可上传模型", trace_id=trace_id)
    if not MODEL_CODE_RE.fullmatch(modelCode):
        raise ApiError(422, CODE_VALIDATION_ERROR, "modelCode 格式无效", trace_id=trace_id)
    modelName = modelName.strip()
    if not 1 <= len(modelName) <= 128:
        raise ApiError(422, CODE_VALIDATION_ERROR, "modelName 格式无效", trace_id=trace_id)
    if not idempotency_key:
        raise ApiError(400, CODE_VALIDATION_ERROR, "缺少 Idempotency-Key 请求头", trace_id=trace_id)
    try:
        idempotency_key = str(uuid.UUID(idempotency_key))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(400, CODE_VALIDATION_ERROR, "Idempotency-Key 必须是合法 UUID", trace_id=trace_id) from None
    if not file.filename or Path(file.filename).suffix.lower() != ".glb":
        raise ApiError(415, "MODEL_FILE_TYPE_UNSUPPORTED", "模型文件必须是 .glb", trace_id=trace_id)
    if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ApiError(422, CODE_VALIDATION_ERROR, "sha256 格式无效", trace_id=trace_id)

    _ensure_storage_dirs()
    temp_dir = UPLOAD_DIR / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{uuid.uuid4().hex}.upload"
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with temp_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                byte_size += len(chunk)
                if byte_size > GLB_MAX_BYTES:
                    raise ApiError(413, "MODEL_FILE_TOO_LARGE", "模型文件超过 15 MiB", trace_id=trace_id)
                digest.update(chunk)
                output.write(chunk)
        calculated_sha = digest.hexdigest()
        with temp_path.open("rb") as source:
            if source.read(4) != GLB_MAGIC:
                raise ApiError(422, "MODEL_FILE_INVALID_GLTF", "文件不是有效 GLB", trace_id=trace_id)
        if sha256 and sha256.lower() != calculated_sha:
            raise ApiError(422, "MODEL_SHA256_MISMATCH", "文件 SHA-256 校验失败", trace_id=trace_id)

        c = db()
        moved_path: Path | None = None
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute("SELECT * FROM assembly_model_versions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior:
                if prior["model_code"] != modelCode or prior["model_name"] != modelName or prior["sha256"] != calculated_sha or prior["byte_size"] != byte_size:
                    raise ApiError(409, "MODEL_UPLOAD_PAYLOAD_MISMATCH", "相同幂等键的上传内容不一致", trace_id=trace_id)
                c.rollback()
                payload = _assembly_model_payload(prior)
                payload.update(status=prior["status"], idempotent=True, traceId=trace_id)
                return JSONResponse(status_code=200, content=payload)
            prior = c.execute("SELECT * FROM assembly_model_versions WHERE model_code=? AND sha256=?", (modelCode, calculated_sha)).fetchone()
            if prior:
                c.rollback()
                payload = _assembly_model_payload(prior)
                payload.update(status=prior["status"], idempotent=True, traceId=trace_id)
                return JSONResponse(status_code=200, content=payload)

            version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM assembly_model_versions WHERE model_code=?", (modelCode,)).fetchone()["next_version"]
            model_id = "asmmdl_" + uuid.uuid4().hex
            storage_key = f"assembly-models/{model_id}/v{version}.glb"
            target = UPLOAD_DIR / storage_key
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, target)
            moved_path = target
            created_at = now()
            c.execute("UPDATE assembly_model_versions SET status='ARCHIVED' WHERE model_code=? AND status='PUBLISHED'", (modelCode,))
            c.execute("""INSERT INTO assembly_model_versions
                (id,model_code,model_name,version,format,byte_size,sha256,storage_key,status,created_by,created_at,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (model_id, modelCode, modelName, version, "GLB", byte_size, calculated_sha, storage_key, "PUBLISHED", user["id"], created_at, idempotency_key))
            c.commit()
            row = c.execute("SELECT * FROM assembly_model_versions WHERE id=?", (model_id,)).fetchone()
            payload = _assembly_model_payload(row)
            payload.update(status="PUBLISHED", idempotent=False, traceId=trace_id)
            return JSONResponse(status_code=201, content=payload)
        except Exception:
            c.rollback()
            if moved_path and moved_path.exists():
                moved_path.unlink()
            raise
        finally:
            c.close()
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/v1/assembly-models/{model_code}/published")
def published_assembly_model(model_code: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    if not MODEL_CODE_RE.fullmatch(model_code):
        raise HTTPException(404, "资源不存在")
    c = db()
    row = c.execute("SELECT * FROM assembly_model_versions WHERE model_code=? AND status='PUBLISHED' ORDER BY version DESC LIMIT 1", (model_code,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "资源不存在")
    return _assembly_model_payload(row, include_server_time=True)


@app.get("/api/v1/assembly-models/{model_code}/versions/{version}/content")
def download_assembly_model(model_code: str, version: int, user: sqlite3.Row = Depends(current_user)) -> FileResponse:
    if not MODEL_CODE_RE.fullmatch(model_code) or version < 1:
        raise HTTPException(404, "资源不存在")
    c = db()
    row = c.execute("SELECT * FROM assembly_model_versions WHERE model_code=? AND version=? AND status='PUBLISHED'", (model_code, version)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "资源不存在")
    root = UPLOAD_DIR.resolve()
    path = (root / row["storage_key"]).resolve()
    if root not in path.parents or path.suffix.lower() != ".glb" or not path.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(path, media_type="model/gltf-binary", headers={"ETag": f'"{row["sha256"]}"', "Cache-Control": "private, max-age=86400"})


# ==================== BOM CSV/XLSX import (A1/2A) ====================
BOM_COLUMNS = ("modelCode", "materialCode", "materialName", "specification", "unit", "quantity", "scrapRate", "substituteMaterialCodes")
BOM_XLSX_REQUIRED_COLUMNS = ("料品编码", "料品名称", "规格", "单位名称", "实际用量", "是否生效")
INVENTORY_XLSX_REQUIRED_COLUMNS = (
    "存储地点名称",
    "料号",
    "品名",
    "库存单位名称",
    "库位编码",
    "库位名称",
    "库存可用量(库存单位)",
    "现存量(库存单位)",
)
BOM_PREVIEWS: dict[str, dict[str, Any]] = {}
BOM_IMPORT_ROLES = {"ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR"}


class BomCommitRequest(BaseModel):
    previewId: uuid.UUID
    clientOperationId: uuid.UUID
    publish: bool = False


def _bom_decimal(value: str, *, positive: bool = False, rate: bool = False) -> float | None:
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None
    if not d.is_finite() or (positive and d <= 0) or (rate and (d < 0 or d >= 1)):
        return None
    if -d.as_tuple().exponent > 6:
        return None
    return float(d)


def _bom_authorized(user: sqlite3.Row) -> None:
    if user["role"] not in BOM_IMPORT_ROLES:
        raise ApiError(403, CODE_FORBIDDEN, "无权执行 BOM 导入")


def _is_xlsx_upload(file: UploadFile) -> bool:
    filename = (file.filename or "").lower()
    content_type = (file.content_type or "").lower()
    return filename.endswith(".xlsx") or content_type in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/xlsx",
    }


def _inventory_decimal(value: str) -> str | None:
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal((value or "").strip())
    except (InvalidOperation, AttributeError):
        return None
    if not d.is_finite():
        return None
    return str(d)


def _cell_by_header(row, header, field: str) -> str:
    index = header.column_map[field]
    return row.values[index].strip() if index < len(row.values) else ""


def _inventory_preview_from_xlsx(raw: bytes, *, trace_id: str) -> dict[str, Any]:
    try:
        rows = XlsxSheetReader.read_rows(raw, max_rows=20001)
        header = HeaderDetectService.detect_header(rows, INVENTORY_XLSX_REQUIRED_COLUMNS)
    except HeaderMissingFieldsError as exc:
        raise ApiError(
            422,
            "INVENTORY_TEMPLATE_INVALID",
            "XLSX 库存快照模板缺少必要表头",
            trace_id=trace_id,
            details={"missingFields": list(exc.missing_fields)},
        ) from None
    except XlsxMaxRowsExceededError:
        raise ApiError(422, "INVENTORY_FILE_TOO_LARGE", "XLSX 行数超过 20000", trace_id=trace_id) from None
    except XlsxParseError:
        raise ApiError(422, "INVENTORY_TEMPLATE_INVALID", "XLSX 文件无法解析", trace_id=trace_id) from None

    preview_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total_rows = 0
    for row in rows:
        if row.index <= header.header_row_index:
            continue
        values = {field: _cell_by_header(row, header, field) for field in INVENTORY_XLSX_REQUIRED_COLUMNS}
        if not any(values.values()):
            continue
        total_rows += 1
        item_errors = []
        if not values["料号"]:
            item_errors.append({"lineNo": row.index, "field": "materialCode", "code": "REQUIRED", "message": "料号不能为空"})
        available_quantity = _inventory_decimal(values["库存可用量(库存单位)"])
        on_hand_quantity = _inventory_decimal(values["现存量(库存单位)"])
        if available_quantity is None:
            item_errors.append({"lineNo": row.index, "field": "availableQuantity", "code": "INVALID_QUANTITY", "message": "数量必须为合法 Decimal"})
        if on_hand_quantity is None:
            item_errors.append({"lineNo": row.index, "field": "onHandQuantity", "code": "INVALID_QUANTITY", "message": "数量必须为合法 Decimal"})
        if item_errors:
            errors.extend(item_errors)
            continue
        preview_rows.append({
            "lineNo": row.index,
            "storageLocationName": values["存储地点名称"],
            "materialCode": values["料号"],
            "materialName": values["品名"],
            "unit": values["库存单位名称"],
            "locationCode": values["库位编码"],
            "locationName": values["库位名称"],
            "availableQuantity": available_quantity,
            "onHandQuantity": on_hand_quantity,
        })
    return {
        "preview": {
            "totalRows": total_rows,
            "validRows": len(preview_rows),
            "invalidRows": total_rows - len(preview_rows),
            "canCommit": False,
        },
        "rows": preview_rows,
        "errors": errors,
        "headerRow": header.header_row_index,
    }


INVENTORY_IMPORT_ROLES = {"ADMIN", "WAREHOUSE_ADMIN"}


class InventoryCommitRequest(BaseModel):
    previewId: uuid.UUID
    clientOperationId: uuid.UUID
    snapshotName: str = Field(min_length=1, max_length=128)


def _persist_inventory_preview(c: sqlite3.Connection, *, user_id: str, digest: str, parsed: dict[str, Any]) -> tuple[str, str]:
    batch_id = str(uuid.uuid4()); created_at = now()
    expires_at = datetime.fromtimestamp(time.time() + 1800, timezone.utc).isoformat()
    summary = parsed["preview"]
    c.execute("INSERT INTO inventory_import_batches(id,file_sha256,header_row,total_count,valid_count,invalid_count,status,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (batch_id, digest, parsed["headerRow"], summary["totalRows"], summary["validRows"], summary["invalidRows"], "PREVIEW", user_id, created_at, expires_at))
    for item in parsed["rows"]:
        encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
        c.execute("INSERT INTO inventory_import_rows VALUES(?,?,?,?,?,?,?,?,?,1)", (str(uuid.uuid4()), batch_id, item["lineNo"], encoded, encoded, item["materialCode"], item["locationCode"], item["availableQuantity"], item["onHandQuantity"]))
    for line_no in {e["lineNo"] for e in parsed["errors"]}:
        c.execute("INSERT INTO inventory_import_rows VALUES(?,?,?,?,?,?,?,?,?,0)", (str(uuid.uuid4()), batch_id, line_no, "{}", "{}", "", None, None, None))
    return batch_id, expires_at


@app.post("/api/v1/inventory/import/preview")
async def preview_inventory_import(
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _safe_trace_id(x_request_id) or str(uuid.uuid4())
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise ApiError(413, "INVENTORY_FILE_TOO_LARGE", "文件超过 10MB", trace_id=trace_id)
    if not _is_xlsx_upload(file):
        raise ApiError(422, "INVENTORY_TEMPLATE_INVALID", "库存快照预览仅支持 XLSX 文件", trace_id=trace_id)
    parsed = _inventory_preview_from_xlsx(raw, trace_id=trace_id)
    digest = hashlib.sha256(raw).hexdigest()
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        preview_id, expires_at = _persist_inventory_preview(c, user_id=user["id"], digest=digest, parsed=parsed)
        c.commit()
    finally:
        c.close()
    return {
        "previewId": preview_id,
        "batch": {
            "type": "INVENTORY_SNAPSHOT_XLSX",
            "fileSha256": digest,
            "headerRow": parsed["headerRow"],
        },
        "preview": parsed["preview"],
        "status": "PREVIEW",
        "expiresAt": expires_at,
        "rows": parsed["rows"],
        "errors": parsed["errors"],
        "traceId": trace_id,
        "serverTime": now(),
    }


@app.post("/api/v1/inventory/import/commit")
def commit_inventory_import(body: InventoryCommitRequest, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = _safe_trace_id(x_request_id) or str(uuid.uuid4())
    if user["role"] not in INVENTORY_IMPORT_ROLES:
        raise ApiError(403, CODE_FORBIDDEN, "无权提交库存快照", trace_id=trace_id)
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    operation_id = str(body.clientOperationId); payload = json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM inventory_import_operations WHERE client_operation_id=?", (operation_id,)).fetchone()
        if prior:
            if prior["created_by"] != user["id"] or prior["payload_json"] != payload:
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"]); result.update(idempotent=True, traceId=trace_id); c.rollback(); return result
        batch = c.execute("SELECT * FROM inventory_import_batches WHERE id=? AND created_by=?", (str(body.previewId), user["id"])).fetchone()
        if not batch or batch["status"] != "PREVIEW" or datetime.fromisoformat(batch["expires_at"]) <= datetime.now(timezone.utc):
            raise ApiError(409, "INVENTORY_PREVIEW_EXPIRED", "预览不存在、已过期或已提交", trace_id=trace_id)
        if batch["invalid_count"] != 0 or batch["valid_count"] <= 0:
            raise ApiError(422, "INVENTORY_VALIDATION_FAILED", "预览包含错误或没有有效行", trace_id=trace_id)
        snapshot_id, ts = str(uuid.uuid4()), now()
        c.execute("INSERT INTO inventory_snapshots VALUES(?,?,?,?,?,?)", (snapshot_id, batch["id"], body.snapshotName, "COMMITTED", user["id"], ts))
        for row in c.execute("SELECT * FROM inventory_import_rows WHERE batch_id=? AND valid=1", (batch["id"],)).fetchall():
            c.execute("INSERT INTO inventory_snapshot_rows VALUES(?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), snapshot_id, row["line_no"], row["material_code"], row["location_code"], row["available_quantity_decimal"], row["on_hand_quantity_decimal"], row["parsed_json"]))
        result = {"previewId": batch["id"], "batchId": batch["id"], "snapshotId": snapshot_id, "snapshotName": body.snapshotName, "status": "COMMITTED", "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("UPDATE inventory_import_batches SET status='COMMITTED', snapshot_id=? WHERE id=?", (snapshot_id, batch["id"]))
        c.execute("INSERT INTO inventory_import_operations VALUES(?,?,?,?,?,?,?)", (operation_id, batch["id"], user["id"], payload, json.dumps(result, ensure_ascii=False, sort_keys=True), snapshot_id, ts)); c.commit(); return result
    except ApiError:
        c.rollback(); raise
    finally:
        c.close()


def _validate_bom_preview_rows(
    raw_rows: list[dict[str, str]],
    *,
    model_code: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    c = db()
    try:
        material_cols = {r["name"] for r in c.execute("PRAGMA table_info(materials)").fetchall()}
        code_col = "material_code" if "material_code" in material_cols else "code"
        valid_items, errors, seen = [], [], set()
        for row in raw_rows:
            line_no = int(row["lineNo"])
            item_errors = []
            values = {k: (row.get(k) or "").strip() for k in BOM_COLUMNS}
            for field in ("modelCode", "materialCode", "materialName", "unit"):
                if not values[field]:
                    item_errors.append({"lineNo": line_no, "field": field, "code": "REQUIRED", "message": "字段不能为空"})
            if values["modelCode"] != model_code:
                item_errors.append({"lineNo": line_no, "field": "modelCode", "code": "MODEL_MISMATCH", "message": "机型不匹配"})
            key = (values["modelCode"], values["materialCode"])
            if key in seen:
                item_errors.append({"lineNo": line_no, "field": "materialCode", "code": "DUPLICATE", "message": "物料重复"})
            seen.add(key)
            quantity = _bom_decimal(values["quantity"], positive=True)
            scrap = _bom_decimal(values["scrapRate"] or "0", rate=True)
            if quantity is None:
                item_errors.append({"lineNo": line_no, "field": "quantity", "code": "INVALID_QUANTITY", "message": "数量必须为正数且最多6位小数"})
            if scrap is None:
                item_errors.append({"lineNo": line_no, "field": "scrapRate", "code": "INVALID_SCRAP_RATE", "message": "损耗率必须在[0,1)且最多6位小数"})
            material = c.execute(f"SELECT * FROM materials WHERE {code_col}=?", (values["materialCode"],)).fetchone()
            if not material:
                item_errors.append({"lineNo": line_no, "field": "materialCode", "code": "UNKNOWN_MATERIAL", "message": "物料不存在"})
            if item_errors:
                errors.extend(item_errors)
                continue
            valid_items.append({
                "lineNo": line_no,
                "materialId": material["id"],
                "materialCode": values["materialCode"],
                "materialName": values["materialName"],
                "specification": values["specification"],
                "unit": values["unit"],
                "quantity": quantity,
                "scrapRate": scrap,
                "substituteMaterialCodes": values["substituteMaterialCodes"],
            })
        return valid_items, errors
    finally:
        c.close()


def _csv_bom_rows(raw: bytes, *, trace_id: str) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error):
        raise ApiError(422, CODE_VALIDATION_ERROR, "CSV 必须为 UTF-8 格式", trace_id=trace_id) from None
    if not rows or tuple(rows[0].keys()) != BOM_COLUMNS or len(rows) > 20000:
        raise ApiError(
            422,
            "BOM_TEMPLATE_INVALID" if rows and tuple(rows[0].keys()) != BOM_COLUMNS else "BOM_FILE_TOO_LARGE",
            "CSV 模板或行数无效",
            trace_id=trace_id,
        )
    return [{**row, "lineNo": str(line_no)} for line_no, row in enumerate(rows, 2)]


def _xlsx_bom_rows(raw: bytes, *, model_code: str, trace_id: str) -> list[dict[str, str]]:
    try:
        rows = XlsxSheetReader.read_rows(raw, max_rows=20001)
        header = HeaderDetectService.detect_header(rows, BOM_XLSX_REQUIRED_COLUMNS)
    except HeaderMissingFieldsError as exc:
        raise ApiError(
            422,
            "BOM_TEMPLATE_INVALID",
            "XLSX BOM 模板缺少必要表头",
            trace_id=trace_id,
            details={"missingFields": list(exc.missing_fields)},
        ) from None
    except XlsxMaxRowsExceededError:
        raise ApiError(422, "BOM_FILE_TOO_LARGE", "XLSX 行数超过 20000", trace_id=trace_id) from None
    except XlsxParseError:
        raise ApiError(422, "BOM_TEMPLATE_INVALID", "XLSX 文件无法解析", trace_id=trace_id) from None

    preview_rows: list[dict[str, str]] = []
    for row in rows:
        if row.index <= header.header_row_index:
            continue
        values = {
            field: (
                row.values[header.column_map[field]].strip()
                if header.column_map[field] < len(row.values)
                else ""
            )
            for field in BOM_XLSX_REQUIRED_COLUMNS
        }
        if not any(values.values()):
            continue
        if values["是否生效"] != "是":
            continue
        preview_rows.append({
            "lineNo": str(row.index),
            "modelCode": model_code,
            "materialCode": values["料品编码"],
            "materialName": values["料品名称"],
            "specification": values["规格"],
            "unit": values["单位名称"],
            "quantity": values["实际用量"],
            "scrapRate": "0",
            "substituteMaterialCodes": "",
        })
    if len(preview_rows) > 20000:
        raise ApiError(422, "BOM_FILE_TOO_LARGE", "XLSX 行数超过 20000", trace_id=trace_id)
    return preview_rows


@app.post("/api/v1/boms/import/preview")
async def preview_bom_import(
    file: UploadFile = File(...), modelCode: str = Form(...),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    _bom_authorized(user)
    trace_id = _safe_trace_id(x_request_id) or str(uuid.uuid4())
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise ApiError(413, "BOM_FILE_TOO_LARGE", "文件超过 10MB", trace_id=trace_id)
    rows = (
        _xlsx_bom_rows(raw, model_code=modelCode, trace_id=trace_id)
        if _is_xlsx_upload(file)
        else _csv_bom_rows(raw, trace_id=trace_id)
    )
    valid_items, errors = _validate_bom_preview_rows(rows, model_code=modelCode)
    pid = str(uuid.uuid4()); digest = hashlib.sha256(raw).hexdigest()
    BOM_PREVIEWS[pid] = {"userId": user["id"], "created": time.time(), "modelCode": modelCode, "sha": digest, "rows": valid_items, "errors": errors}
    return {"previewId": pid, "fileSha256": digest, "modelCode": modelCode, "totalRows": len(rows), "validRows": len(valid_items), "invalidRows": len(rows) - len(valid_items), "canCommit": not errors and bool(valid_items), "errors": errors, "warnings": [], "items": valid_items, "traceId": trace_id, "serverTime": now()}


@app.post("/api/v1/boms/import/commit")
def commit_bom_import(body: BomCommitRequest, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    _bom_authorized(user); trace_id = _safe_trace_id(x_request_id) or str(uuid.uuid4())
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    operation_id = str(body.clientOperationId); payload = json.dumps(body.model_dump(mode="json"), sort_keys=True)
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM audit_events WHERE event_type='BOM_IMPORT' AND client_operation_id=?", (operation_id,)).fetchone()
        if prior:
            if prior["before_json"] != payload: raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["after_json"]); result.update(idempotent=True, traceId=trace_id); c.rollback(); return result
        preview = BOM_PREVIEWS.get(str(body.previewId))
        if not preview or preview["userId"] != user["id"] or time.time() - preview["created"] > 1800:
            raise ApiError(409, "BOM_PREVIEW_EXPIRED", "预览不存在、已过期或不属于当前用户", trace_id=trace_id)
        if preview["errors"] or not preview["rows"]: raise ApiError(422, "BOM_VALIDATION_FAILED", "预览包含错误", trace_id=trace_id)
        if not c.execute("SELECT 1 FROM production_order_models WHERE model_code=?", (preview["modelCode"],)).fetchone():
            raise ApiError(422, "MODEL_NOT_FOUND", "机型不存在", trace_id=trace_id)
        version = c.execute("SELECT COALESCE(MAX(version_no),0)+1 n FROM bom_versions WHERE model_code=?", (preview["modelCode"],)).fetchone()["n"]
        vid, ts = str(uuid.uuid4()), now(); status = "PUBLISHED" if body.publish else "DRAFT"
        c.execute("INSERT INTO bom_versions VALUES(?,?,?,?,?,?,?,?,?,?)", (vid, preview["modelCode"], version, status, preview["sha"], len(preview["rows"]), user["id"], ts, user["id"] if body.publish else None, ts if body.publish else None))
        for item in preview["rows"]:
            c.execute("INSERT INTO bom_items VALUES(?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), vid, item["materialId"], item["materialCode"], item["materialName"], item["specification"], item["unit"], item["quantity"], item["scrapRate"], json.dumps([x for x in item["substituteMaterialCodes"].split(';') if x]), item["lineNo"]))
        if body.publish: c.execute("UPDATE bom_versions SET status='ARCHIVED' WHERE model_code=? AND status='PUBLISHED' AND id<>?", (preview["modelCode"], vid))
        result = {"bomVersionId": vid, "modelCode": preview["modelCode"], "versionNo": version, "status": status, "itemCount": len(preview["rows"]), "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,request_id,client_operation_id,before_json,after_json,server_time,device_id,source_ip,result) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("BOM_IMPORT", "BOM_VERSION", vid, user["id"], user["role"], trace_id, operation_id, payload, json.dumps(result, ensure_ascii=False, sort_keys=True), ts, None, None, "SUCCESS"))
        c.commit(); return result
    except ApiError: c.rollback(); raise
    except Exception:
        c.rollback()
        raise
    finally: c.close()


@app.get("/api/v1/boms/versions")
def list_bom_versions(modelCode: str | None = None, status: str | None = None, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=100), user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    _bom_authorized(user); c = db(); where, args = [], []
    if modelCode: where.append("model_code=?"); args.append(modelCode)
    if status: where.append("status=?"); args.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""; total = c.execute("SELECT COUNT(*) n FROM bom_versions" + clause, args).fetchone()["n"]
    rows = c.execute("SELECT id,model_code,version_no,status,source_file_sha256,row_count,created_by,created_at,published_by,published_at FROM bom_versions" + clause + " ORDER BY created_at DESC LIMIT ? OFFSET ?", args + [pageSize, (page - 1) * pageSize]).fetchall(); c.close()
    return {"items": [dict(r) for r in rows], "page": page, "pageSize": pageSize, "total": total, "serverTime": now()}



# ==================== Production orders and devices (A2) ====================
PRODUCTION_WRITE_ROLES = {"ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR"}

class ProductionOrderModelCreate(BaseModel):
    modelCode: str = Field(min_length=1, max_length=64)
    modelName: str = Field(min_length=1, max_length=128)
    plannedQuantity: int = Field(gt=0)
    bomVersionId: str = Field(min_length=1, max_length=128)

class ProductionOrderCreate(BaseModel):
    clientOperationId: uuid.UUID
    orderNo: str = Field(min_length=1, max_length=64)
    productName: str = Field(min_length=1, max_length=128)
    plannedQuantity: int = Field(gt=0)
    plannedDeliveryDate: str | None = None
    models: list[ProductionOrderModelCreate] = Field(min_length=1, max_length=100)

class DeviceCreate(BaseModel):
    clientOperationId: uuid.UUID
    deviceNo: str = Field(min_length=1, max_length=64)
    deviceName: str = Field(min_length=1, max_length=128)
    workshop: str = Field(min_length=1, max_length=128)
    modelCapability: str | None = Field(default=None, max_length=128)

class AssignDeviceRequest(BaseModel):
    clientOperationId: uuid.UUID
    deviceId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(gt=0)


def _production_write_allowed(user: sqlite3.Row) -> None:
    if user["role"] not in PRODUCTION_WRITE_ROLES:
        raise ApiError(403, CODE_FORBIDDEN, "无订单、机台或绑定权限")


def _operation_replay(c, table: str, operation_id: str, payload: str, trace_id: str):
    prior = c.execute(f"SELECT payload_json,result_json FROM {table} WHERE client_operation_id=?", (operation_id,)).fetchone()
    if not prior:
        return None
    if _payload_digest(prior["payload_json"]) != _payload_digest(payload):
        raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
    result = json.loads(prior["result_json"])
    result.update(idempotent=True, traceId=trace_id)
    return result


@app.post("/api/v1/production-orders")
def create_production_order(body: ProductionOrderCreate, user: sqlite3.Row = Depends(current_user),
                           x_request_id: str | None = Header(default=None),
                           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id); require_idempotency_key(idempotency_key, body.clientOperationId, trace_id); _production_write_allowed(user)
    if len({m.modelCode for m in body.models}) != len(body.models) or sum(m.plannedQuantity for m in body.models) > body.plannedQuantity:
        raise ApiError(422, CODE_VALIDATION_ERROR, "订单机型数量无效", trace_id=trace_id)
    payload = body.model_dump_json(); op = str(body.clientOperationId); c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        replay = _operation_replay(c, "production_order_operations", op, payload, trace_id)
        if replay: c.rollback(); return replay
        if c.execute("SELECT 1 FROM production_orders WHERE order_no=?", (body.orderNo,)).fetchone():
            raise ApiError(409, "ORDER_NO_CONFLICT", "订单号已存在", trace_id=trace_id)
        for model in body.models:
            bom = c.execute("SELECT id FROM bom_versions WHERE id=? AND model_code=? AND status='PUBLISHED'", (model.bomVersionId, model.modelCode)).fetchone()
            if not bom: raise ApiError(422, "BOM_NOT_PUBLISHED", "订单只能引用已发布 BOM", trace_id=trace_id)
        ts = now(); oid = "ord_" + uuid.uuid4().hex
        c.execute("INSERT INTO production_orders(id,order_no,product_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)", (oid, body.orderNo, body.productName, "IN_PROGRESS", ts, ts))
        for model in body.models:
            c.execute("INSERT INTO production_order_models(id,order_id,model_code,model_name,bom_version_id,planned_quantity,created_at) VALUES(?,?,?,?,?,?,?)", ("pom_" + uuid.uuid4().hex, oid, model.modelCode, model.modelName, model.bomVersionId, model.plannedQuantity, ts))
        result = {"orderId": oid, "orderNo": body.orderNo, "productName": body.productName, "plannedQuantity": body.plannedQuantity, "status": "IN_PROGRESS", "models": [m.model_dump() for m in body.models], "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("INSERT INTO production_order_operations VALUES(?,?,?,?)", (op, payload, json.dumps(result, ensure_ascii=False), ts)); audit(c, user["id"], user["role"], "CREATE", "PRODUCTION_ORDER", oid, "SUCCESS", trace_id); c.commit(); return result
    except ApiError: c.rollback(); raise
    except Exception:
        c.rollback()
        raise
    finally: c.close()


@app.post("/api/v1/devices")
def create_device(body: DeviceCreate, user: sqlite3.Row = Depends(current_user),
                 x_request_id: str | None = Header(default=None),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id); require_idempotency_key(idempotency_key, body.clientOperationId, trace_id); _production_write_allowed(user)
    payload = body.model_dump_json(); op = str(body.clientOperationId); c = db()
    try:
        c.execute("BEGIN IMMEDIATE"); replay = _operation_replay(c, "device_operations", op, payload, trace_id)
        if replay: c.rollback(); return replay
        if c.execute("SELECT 1 FROM devices WHERE device_no=?", (body.deviceNo,)).fetchone(): raise ApiError(409, "DEVICE_NO_CONFLICT", "机台编号已存在", trace_id=trace_id)
        ts = now(); did = "dev_" + uuid.uuid4().hex
        c.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?)", (did, body.deviceNo, body.deviceName, body.workshop, body.modelCapability, "ACTIVE", user["id"], ts, ts))
        result = {"deviceId": did, "deviceNo": body.deviceNo, "deviceName": body.deviceName, "workshop": body.workshop, "modelCapability": body.modelCapability, "status": "ACTIVE", "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("INSERT INTO device_operations VALUES(?,?,?,?)", (op, payload, json.dumps(result, ensure_ascii=False), ts)); audit(c, user["id"], user["role"], "CREATE", "DEVICE", did, "SUCCESS", trace_id); c.commit(); return result
    except ApiError: c.rollback(); raise
    except Exception:
        c.rollback()
        raise
    finally: c.close()


@app.post("/api/v1/production-orders/{order_no}/models/{model_code}/assign-device")
def assign_device(order_no: str, model_code: str, body: AssignDeviceRequest, user: sqlite3.Row = Depends(current_user),
                  x_request_id: str | None = Header(default=None),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id); require_idempotency_key(idempotency_key, body.clientOperationId, trace_id); _production_write_allowed(user)
    payload = json.dumps({"orderNo": order_no, "modelCode": model_code, **body.model_dump(mode="json")}, sort_keys=True); op = str(body.clientOperationId); c = db()
    try:
        c.execute("BEGIN IMMEDIATE"); replay = _operation_replay(c, "device_assignment_operations", op, payload, trace_id)
        if replay: c.rollback(); return replay
        model = c.execute("SELECT m.*,o.status order_status FROM production_order_models m JOIN production_orders o ON o.id=m.order_id WHERE o.order_no=? AND m.model_code=?", (order_no, model_code)).fetchone()
        if not model: raise ApiError(404, "ORDER_MODEL_NOT_FOUND", "订单机型不存在", trace_id=trace_id)
        if model["order_status"] not in ("IN_PROGRESS", "RELEASED"): raise ApiError(409, "ORDER_STATE_CONFLICT", "订单状态不允许绑定", trace_id=trace_id)
        if model["version"] != body.expectedVersion: raise ApiError(409, "ORDER_MODEL_VERSION_CONFLICT", "订单机型版本冲突", trace_id=trace_id)
        device = c.execute("SELECT * FROM devices WHERE id=?", (body.deviceId,)).fetchone()
        if not device: raise ApiError(404, "DEVICE_NOT_FOUND", "机台不存在", trace_id=trace_id)
        if device["status"] != "ACTIVE": raise ApiError(409, "DEVICE_NOT_ACTIVE", "机台未启用", trace_id=trace_id)
        if device["model_capability"] and device["model_capability"] != model_code: raise ApiError(409, "DEVICE_CAPABILITY_MISMATCH", "机台能力不匹配", trace_id=trace_id)
        ts = now(); assignment_id = "pmd_" + uuid.uuid4().hex; task_id = "task_" + uuid.uuid4().hex
        c.execute("INSERT INTO production_model_devices(id,order_model_id,device_id,status,assigned_by,assigned_at) VALUES(?,?,?,?,?,?)", (assignment_id, model["id"], device["id"], "ASSIGNED", user["id"], ts))
        c.execute("INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,task_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (task_id, order_no, device["id"], device["device_no"], "WAITING_MATERIAL", 0, 1, ts, ts))
        c.execute("UPDATE production_order_models SET version=version+1 WHERE id=? AND version=?", (model["id"], body.expectedVersion))
        result = {"taskId": task_id, "orderNo": order_no, "modelCode": model_code, "deviceId": device["id"], "deviceNo": device["device_no"], "status": "WAITING_MATERIAL", "expectedVersion": body.expectedVersion + 1, "serverTime": ts, "traceId": trace_id, "idempotent": False}
        c.execute("INSERT INTO device_assignment_operations VALUES(?,?,?,?)", (op, payload, json.dumps(result, ensure_ascii=False), ts)); audit(c, user["id"], user["role"], "ASSIGN", "ASSEMBLY_TASK", task_id, "SUCCESS", trace_id); c.commit(); return result
    except ApiError: c.rollback(); raise
    except Exception:
        c.rollback()
        raise
    finally: c.close()

@app.get("/healthz")
def health() -> dict[str, str]:
    c: sqlite3.Connection | None = None
    try:
        c = db()
        tables = {
            row["name"] for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not HEALTH_TABLES <= tables:
            raise sqlite3.DatabaseError("schema is incomplete")
        if c.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("database check failed")
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "database": "ok",
            "version": APP_VERSION,
            "serverTime": now(),
        }
    except (OSError, RuntimeError, sqlite3.Error):
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "service": SERVICE_NAME, "version": APP_VERSION},
        )
    finally:
        if c is not None:
            c.close()


@app.post("/api/v1/auth/login")
def login(body: Login, x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    """登录。

    契约 A03 —— 失败锁定：同一账号 15 分钟内累计 5 次失败即锁定 15 分钟。
    锁定期间即使密码正确也拒绝，避免离线暴力破解。

    Argon2id 迁移：历史 scrypt 哈希在校验通过后就地升级（见 check_password）。
    """
    c = db()
    username = body.login_identifier()
    attempt = c.execute("SELECT * FROM login_attempts WHERE username=?", (username,)).fetchone()

    # 1) 锁定窗口检查（先于密码校验，避免锁定期间仍消耗哈希算力）
    if attempt and attempt["locked_until"] and int(time.time()) < attempt["locked_until"]:
        remain = attempt["locked_until"] - int(time.time())
        audit(c, None, None, "LOGIN", "USER", username, "LOCKED", x_request_id or "")
        c.commit(); c.close()
        raise ApiError(
            429, CODE_ACCOUNT_LOCKED,
            f"账号已锁定，请在 {max(1, remain // 60)} 分钟后重试",
            retryable=True,
        )

    user = c.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
    ok = bool(user) and check_password(body.password, user["password_hash"], c, user["id"])

    if not ok:
        # 2) 累计失败次数与首败时间（超出窗口则重新计数）
        window_start = int(time.time()) - LOGIN_WINDOW_SECONDS
        if attempt and attempt["first_failed_at"] >= window_start:
            count = attempt["failed_count"] + 1
            first_at = attempt["first_failed_at"]
        else:
            count, first_at = 1, int(time.time())

        locked_until = (
            int(time.time()) + LOGIN_LOCK_SECONDS
            if count >= LOGIN_MAX_FAILURES else None
        )
        c.execute(
            "INSERT INTO login_attempts(username, failed_count, first_failed_at, locked_until) "
            "VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET "
            "failed_count=excluded.failed_count, first_failed_at=excluded.first_failed_at, "
            "locked_until=excluded.locked_until",
            (username, count, first_at, locked_until),
        )
        audit(c, user["id"] if user else None, user["role"] if user else None,
              "LOGIN", "USER", user["id"] if user else None, "FAILED", x_request_id or "")
        c.commit(); c.close()

        if locked_until:
            raise ApiError(
                429, CODE_ACCOUNT_LOCKED,
                f"连续失败 {count} 次，账号已锁定 {LOGIN_LOCK_SECONDS // 60} 分钟",
                retryable=True,
            )
        # 不回显账号是否存在，避免用户名枚举
        raise HTTPException(401, "账号或密码错误")

    # 3) 登录成功 → 清空失败计数，签发新令牌对
    #
    # 注意：这里不再吊销该用户的既有会话。
    # 现场作业允许同一账号在多台设备（PDA / 手机）同时在线，
    # 登录即踢下线会造成收料高峰期的相互顶号。
    # 需要强制下线改用 POST /auth/logout-all。
    c.execute("DELETE FROM login_attempts WHERE username=?", (username,))
    access, refresh, access_exp = issue_session(c, user["id"], body.deviceId)
    audit(c, user["id"], user["role"], "LOGIN", "USER", user["id"], "SUCCESS", x_request_id or "")
    c.commit(); c.close()
    return {
        "accessToken": access,
        "refreshToken": refresh,
        "expiresAt": datetime.fromtimestamp(access_exp, timezone.utc).isoformat(),
        "refreshExpiresAt": datetime.fromtimestamp(
            int(time.time()) + REFRESH_TOKEN_SECONDS, timezone.utc
        ).isoformat(),
        "mustChangePassword": bool(user["must_change_password"]),
        "user": {
            "id": user["id"], "username": user["username"],
            "displayName": user["display_name"], "role": user["role"],
            "mustChangePassword": bool(user["must_change_password"]),
        },
    }


class RefreshRequest(BaseModel):
    refreshToken: str = Field(min_length=16, max_length=256)
    deviceId: str = Field(default="unknown", max_length=128)


@app.post("/api/v1/auth/refresh")
def refresh(body: RefreshRequest, x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    """用刷新令牌换取新的令牌对（refresh token rotation）。

    安全要点：
      - 只有 token_type='REFRESH' 的令牌能走此端点，
        access token 拿来刷新会被拒（避免长短期令牌混用）；
      - 每次刷新都签发新令牌并作废旧的（一次性使用）；
      - 旧令牌被重复使用 = 疑似泄露，整族令牌一并吊销。
    """
    c = db()
    now_ts = int(time.time())

    # 1) 先查墓碑：该令牌是否已被消费过？
    #    命中即说明有人在重放旧令牌 → 视为泄露，整族吊销。
    gravestone = c.execute(
        "SELECT * FROM consumed_refresh_tokens WHERE token=?", (body.refreshToken,)
    ).fetchone()
    if gravestone is not None:
        _revoke_refresh_family(c, body.refreshToken)
        audit(c, gravestone["user_id"], None, "REFRESH_REUSE_DETECTED", "SESSION",
              None, "FAILED", x_request_id or "")
        c.commit(); c.close()
        raise ApiError(
            401, CODE_INVALID_REFRESH_TOKEN,
            "检测到刷新令牌重复使用，出于安全已注销全部会话，请重新登录",
            retryable=False,
        )

    # 2) 查活跃令牌
    row = c.execute("SELECT * FROM sessions WHERE token=?", (body.refreshToken,)).fetchone()

    if row is None or row["token_type"] != "REFRESH" or row["expires_at"] <= now_ts:
        c.close()
        raise ApiError(401, CODE_INVALID_REFRESH_TOKEN, "刷新令牌无效或已过期")

    user = c.execute(
        "SELECT * FROM users WHERE id=? AND active=1", (row["user_id"],)
    ).fetchone()
    if not user:
        c.close()
        raise ApiError(401, CODE_INVALID_REFRESH_TOKEN, "账号不可用")
    if user["must_change_password"]:
        c.close()
        raise ApiError(403, CODE_PASSWORD_CHANGE_REQUIRED, "请先修改初始密码")

    uid, device = row["user_id"], (row["device_id"] or body.deviceId)

    # 3) 一次性消费：写墓碑 + 删活跃令牌 + 签发新对
    c.execute(
        "INSERT OR REPLACE INTO consumed_refresh_tokens VALUES(?,?,?)",
        (body.refreshToken, uid, now_ts),
    )
    c.execute("DELETE FROM sessions WHERE token=?", (body.refreshToken,))

    # 顺带清理已过期的墓碑，避免无限增长
    c.execute(
        "DELETE FROM consumed_refresh_tokens WHERE consumed_at < ?",
        (now_ts - REFRESH_TOKEN_SECONDS,),
    )

    access, new_refresh, access_exp = issue_session(c, uid, device)
    audit(c, uid, user["role"], "REFRESH", "SESSION", None, "SUCCESS", x_request_id or "")
    c.commit(); c.close()
    return {
        "accessToken": access,
        "refreshToken": new_refresh,
        "expiresAt": datetime.fromtimestamp(access_exp, timezone.utc).isoformat(),
        "refreshExpiresAt": datetime.fromtimestamp(
            now_ts + REFRESH_TOKEN_SECONDS, timezone.utc
        ).isoformat(),
        "mustChangePassword": bool(user["must_change_password"]),
    }


@app.post("/api/v1/auth/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_request_id: str | None = Header(default=None),
) -> dict[str, str]:
    """登出当前设备：同时吊销该设备的 access 与 refresh 令牌。"""
    if not credentials:
        raise HTTPException(401, "未登录")
    c = db()
    row = c.execute("SELECT * FROM sessions WHERE token=?", (credentials.credentials,)).fetchone()
    if not row:
        c.close()
        return {"result": "OK"}

    device = row["device_id"]
    if device:
        c.execute(
            "DELETE FROM sessions WHERE user_id=? AND device_id=?",
            (row["user_id"], device),
        )
    else:
        c.execute("DELETE FROM sessions WHERE token=?", (credentials.credentials,))
    audit(c, row["user_id"], None, "LOGOUT", "SESSION", None, "SUCCESS", x_request_id or "")
    c.commit(); c.close()
    return {"result": "OK"}


@app.post("/api/v1/auth/logout-all")
def logout_all(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """吊销当前用户全部设备的会话（疑似账号泄露时的应急手段）。"""
    c = db()
    n = revoke_all_sessions(c, user["id"])
    audit(c, user["id"], user["role"], "LOGOUT_ALL", "SESSION", None, "SUCCESS", x_request_id or "")
    c.commit(); c.close()
    return {"result": "OK", "revokedCount": n}


@app.post("/api/v1/scan/resolve")
def resolve_scan(body: Scan, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    """扫码类型识别。

    依据《厂内流转业务模型更正》：
      - 支持生产订单号 / 料号 / 库位码 / 流转单号；
      - 「物流号（LOGISTICS_NO）」概念已作废，不再识别 WL / LOG 前缀；
      - 首版采用前缀+正则配置，规则不硬编码到客户端，由服务端最终判定。
    """
    raw = body.rawValue.strip()
    upper = raw.upper()
    typ = "UNKNOWN"
    resource_id: str | None = None
    c = db()
    try:
        order = c.execute("SELECT id FROM production_orders WHERE order_no=?", (upper,)).fetchone()
        material = c.execute("SELECT id FROM materials WHERE code=?", (upper,)).fetchone()
        location = c.execute("SELECT id FROM locations WHERE code=?", (upper,)).fetchone()
        device = c.execute("SELECT id FROM order_devices WHERE device_no=?", (upper,)).fetchone()
    finally:
        c.close()
    if order:
        typ, resource_id = "PRODUCTION_ORDER", order["id"]
    # 设备码没有冻结的扫码枚举；即使命中数据库，也必须保持 UNKNOWN，
    # 不能把内部设备类型泄露成运行时契约。
    elif material:
        typ, resource_id = "MATERIAL_CODE", material["id"]
    elif location:
        typ, resource_id = "LOCATION_CODE", location["id"]
    elif re.match(r"^MTR-[A-Z0-9-]+$", upper):
        typ = "MATERIAL_CODE"
    elif re.match(r"^(SO|PO)\d+$", upper):
        # 早期订单号格式：SO202609120001
        raise ApiError(404, CODE_ORDER_NOT_FOUND, "订单不存在")
    elif re.match(r"^\d{2}[A-Z]-\d{3}$", upper):
        # 现行订单号格式：26B-013（年份+线别-序号）。
        raise ApiError(404, CODE_ORDER_NOT_FOUND, "订单不存在")
    elif re.match(r"^[A-Z]-\d{2}-\d{2}$", upper):
        typ = "LOCATION_CODE"
    elif re.match(r"^FL\d+$", upper):
        typ = "FLOW_NO"
    if typ != "UNKNOWN" and resource_id is None:
        resource_id = upper
    return {"type": typ, "normalizedValue": upper, "resourceId": resource_id}


@app.post("/api/v1/auth/change-password")
def change_password(
    body: ChangePassword,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """修改本人密码。

    安全约束：
      - 必须校验旧密码，防止会话被劫持后直接改密锁死账号；
      - 新密码不得与旧密码相同；
      - 改密后吊销该用户**全部**会话（含其他设备），
        因为旧密码可能已泄露，其他设备上的会话不再可信；
      - 单据里 mustChangePassword 会一并清零。
    """
    c = db()
    row = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row or not check_password(body.oldPassword, row["password_hash"]):
        audit(c, user["id"], user["role"], "CHANGE_PASSWORD", "USER",
              user["id"], "FAILED", x_request_id or "")
        c.commit(); c.close()
        raise ApiError(401, CODE_OLD_PASSWORD_MISMATCH, "原密码不正确", retryable=False)

    if body.oldPassword == body.newPassword:
        c.close()
        raise ApiError(400, CODE_PASSWORD_UNCHANGED, "新密码不能与原密码相同", retryable=False)

    c.execute(
        "UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
        (hash_password(body.newPassword), user["id"]),
    )
    revoked = revoke_all_sessions(c, user["id"])
    audit(c, user["id"], user["role"], "CHANGE_PASSWORD", "USER",
          user["id"], "SUCCESS", x_request_id or "")
    c.commit(); c.close()
    return {
        "result": "OK",
        "revokedSessions": revoked,
        "message": "密码已更新，请重新登录",
    }


@app.get("/api/v1/materials/{code}/inventory")
def inventory(code: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); m = c.execute("SELECT * FROM materials WHERE code=?", (code,)).fetchone()
    if not m: c.close(); raise HTTPException(404, "物料不存在")
    rows = c.execute("SELECT l.code locationCode, i.quantity FROM inventory i JOIN locations l ON l.id=i.location_id WHERE i.material_id=?", (m["id"],)).fetchall(); c.close()
    return {"material": {"id": m["id"], "code": m["code"], "name": m["name"], "specification": m["specification"], "unit": m["unit"], "batchNo": m["batch_no"], "expiryDate": m["expiry_date"]}, "inventory": {"totalQuantity": m["total_quantity"], "availableQuantity": m["available_quantity"], "reservedQuantity": 0, "locations": [dict(r) for r in rows]}, "version": m["version"]}


def _material_summary(rows: list[sqlite3.Row] | list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate exactly the material requirement rows returned by an endpoint."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        material_id = row["material_id"] if "material_id" in row.keys() else row["materialId"]
        item = grouped.setdefault(material_id, {
            "materialId": material_id,
            "materialCode": row["material_code"] if "material_code" in row.keys() else row["materialCode"],
            "materialName": row["material_name"] if "material_name" in row.keys() else row["materialName"],
            "unit": row["unit"],
            "requiredQuantity": 0,
            "arrivedQuantity": 0,
            "inStockQuantity": 0,
        })
        item["requiredQuantity"] += row["required_quantity"] if "required_quantity" in row.keys() else row["requiredQuantity"]
        item["arrivedQuantity"] += row["arrived_quantity"] if "arrived_quantity" in row.keys() else row["arrivedQuantity"]
        item["inStockQuantity"] += row["in_stock_quantity"] if "in_stock_quantity" in row.keys() else row["inStockQuantity"]

    items: list[dict[str, Any]] = []
    for item in grouped.values():
        required = item["requiredQuantity"]
        arrived = item["arrivedQuantity"]
        in_stock = item["inStockQuantity"]
        if in_stock >= required:
            status_code = "IN_STOCK"
        elif arrived >= required:
            status_code = "ARRIVED"
        else:
            status_code = "OUT_OF_STOCK"
        item.update({
            "shortageQuantity": max(required - in_stock, 0),
            "statusCode": status_code,
            "statusLabel": WORKSPACE_STATUS_LABELS[status_code],
        })
        items.append(item)
    return {
        "items": items,
        "totalMaterialTypes": len(items),
        "totalRequiredQuantity": sum(item["requiredQuantity"] for item in items),
        "totalArrivedQuantity": sum(item["arrivedQuantity"] for item in items),
        "totalInStockQuantity": sum(item["inStockQuantity"] for item in items),
        "totalShortageQuantity": sum(item["shortageQuantity"] for item in items),
    }


@app.post("/api/v1/orders/material-status")
def material_status(
    body: dict[str, str],
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """生产订单物料状态。

    对齐《订单物料状态-后端模型对齐规格》：
      - 按 production_orders.order_no 精确查订单；
      - 经 order_id 查 order_material_requirements，再 JOIN materials / order_devices；
      - requiredQuantity / arrivedQuantity / inStockQuantity / statusCode
        **一律取自需求表**，不得用库存主表数量替代 —— 否则同一物料出现在
        多个订单时会串数据；
      - 订单不存在返回 404 ORDER_NOT_FOUND，不再回退成全量物料。

    documentType 仅接受冻结契约中的 PRODUCTION_ORDER；ORDER_NO 已废弃。
    """
    trace_id = x_request_id or ""
    document_no = (body.get("documentNo") or "").strip()
    document_type = (body.get("documentType") or "").strip().upper()

    # 仅放行冻结的生产订单枚举；ORDER_NO 等历史值必须显式拒绝。
    if document_type not in ACCEPTED_ORDER_DOC_TYPES:
        if document_type in FORBIDDEN_SCAN_TYPES:
            raise ApiError(
                400, CODE_INVALID_SCAN_TYPE,
                "该 documentType 已作废，请使用 PRODUCTION_ORDER",
                trace_id=trace_id,
            )
        raise ApiError(
            400, CODE_VALIDATION_ERROR,
            "documentType 必须为 PRODUCTION_ORDER", trace_id=trace_id,
        )
    if not document_no:
        raise ApiError(400, CODE_VALIDATION_ERROR, "documentNo 不能为空", trace_id=trace_id)

    c = db()
    order = c.execute(
        "SELECT id, order_no, product_name, status FROM production_orders WHERE order_no=?",
        (document_no,),
    ).fetchone()
    if not order:
        c.close()
        # 规格第 5 节：订单不存在返回 404，不再回退成全量物料
        raise ApiError(
            404, CODE_ORDER_NOT_FOUND,
            "订单不存在", trace_id=trace_id,
        )

    rows = c.execute(
        """
        SELECT r.id            AS requirement_id,
               r.device_id     AS device_id,
               d.device_type   AS device_type,
               d.device_no     AS device_no,
               d.sequence_no   AS sequence_no,
               r.material_id   AS material_id,
               m.code          AS material_code,
               m.name          AS material_name,
               m.specification AS specification,
               m.unit          AS unit,
               r.required_quantity  AS required_quantity,
               r.arrived_quantity   AS arrived_quantity,
               r.in_stock_quantity  AS in_stock_quantity,
               r.status_code        AS status_code
        FROM order_material_requirements r
        JOIN materials m ON m.id = r.material_id
        LEFT JOIN order_devices d ON d.id = r.device_id
        WHERE r.order_id = ?
        ORDER BY COALESCE(d.sequence_no, 0), m.code
        """,
        (order["id"],),
    ).fetchall()
    c.close()

    items = [
        {
            "requirementId": r["requirement_id"],
            "deviceId": r["device_id"],
            "deviceType": r["device_type"],
            "deviceNo": r["device_no"],
            "materialId": r["material_id"],
            "materialCode": r["material_code"],
            "materialName": r["material_name"],
            "specification": r["specification"],
            "unit": r["unit"],
            "requiredQuantity": r["required_quantity"],
            "arrivedQuantity": r["arrived_quantity"],
            "inStockQuantity": r["in_stock_quantity"],
            "statusCode": r["status_code"],
        }
        for r in rows
    ]

    return {
        "documentNo": order["order_no"],
        "documentType": document_type,
        "orderId": order["id"],
        "productName": order["product_name"],
        "orderStatus": order["status"],
        "items": items,
        "materialSummary": _material_summary(rows),
        "serverTime": now(),
        "traceId": trace_id,
    }


@app.get("/api/v1/orders/{order_no}/detail")
def order_detail(
    order_no: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user: sqlite3.Row = Depends(current_user),
) -> dict[str, Any]:
    """Return one order's server-fact aggregate with the existing visibility scope."""
    c = db()
    order = c.execute(
        "SELECT id, order_no, product_name, status FROM production_orders WHERE order_no=?",
        (order_no,),
    ).fetchone()
    if not order:
        c.close()
        raise ApiError(404, CODE_ORDER_NOT_FOUND, "订单不存在")

    # Material rows deliberately reuse the established server-side scope.  This
    # prevents this aggregate from becoming a broad order-material disclosure.
    scoped = _workspace_requirements(c, user, order_no)
    if user["role"] not in {"ADMIN", "WORKSHOP_SUPERVISOR", "ASSEMBLER"} and not scoped:
        c.close()
        raise ApiError(403, CODE_FORBIDDEN, "无权查看该订单")
    if user["role"] == "ASSEMBLER" and not c.execute(
        "SELECT 1 FROM assembly_tasks WHERE order_no=? AND assigned_assembler_id=?",
        (order_no, user["id"]),
    ).fetchone():
        c.close()
        raise ApiError(403, CODE_FORBIDDEN, "无权查看该订单")

    if user["role"] in {"ADMIN", "WORKSHOP_SUPERVISOR"}:
        task_where, task_args = "order_no=?", [order_no]
    elif user["role"] == "ASSEMBLER":
        task_where, task_args = "order_no=? AND assigned_assembler_id=?", [order_no, user["id"]]
    else:
        task_where, task_args = "1=0", []
    task_total = c.execute(
        f"SELECT count(*) AS n FROM assembly_tasks WHERE {task_where}", task_args
    ).fetchone()["n"]
    task_rows = c.execute(
        f"""SELECT id,device_id,device_no,status,progress_stage,task_version,
                    assigned_assembler_id
               FROM assembly_tasks WHERE {task_where}
              ORDER BY created_at,id LIMIT ? OFFSET ?""",
        task_args + [pageSize, (page - 1) * pageSize],
    ).fetchall()

    visible_task_ids = [r["id"] for r in task_rows] if user["role"] == "ASSEMBLER" else [
        r["id"] for r in c.execute("SELECT id FROM assembly_tasks WHERE order_no=?", (order_no,)).fetchall()
    ]
    labor_args: list[Any] = [order_no]
    labor_scope = "t.order_no=?"
    if user["role"] == "ASSEMBLER":
        labor_scope += " AND l.worker_user_id=?"
        labor_args.append(user["id"])
    elif user["role"] not in {"ADMIN", "WORKSHOP_SUPERVISOR"}:
        labor_scope = "1=0"
        labor_args = []
    labor = c.execute(
        f"""SELECT l.type, COALESCE(sum(l.duration_minutes),0) AS minutes
              FROM labor_records l LEFT JOIN assembly_tasks t ON t.id=l.task_id
             WHERE ({labor_scope}) AND l.status='COMPLETED'
             GROUP BY l.type""",
        labor_args,
    ).fetchall()
    labor_totals = {r["type"]: int(r["minutes"] or 0) for r in labor}

    # Only event types already written by the handover/transfer/assembly paths
    # are projected; raw before/after payloads and network metadata stay private.
    entity_ids = set(visible_task_ids)
    entity_ids.update(r["id"] for r in c.execute(
        """SELECT h.id FROM material_handovers h
             JOIN material_work_items w ON w.id=h.work_item_id
             JOIN order_material_requirements r ON r.id=w.requirement_id
             JOIN production_orders o ON o.id=r.order_id WHERE o.order_no=?""", (order_no,)
    ).fetchall())
    entity_ids.update(r["id"] for r in c.execute(
        "SELECT id FROM transfer_requests WHERE document_no=?", (order_no,)
    ).fetchall())
    allowed_events = {
        "HANDOVER_CREATED", "HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP",
        "MATERIAL_AT_STATION", "HANDOVER_REJECTED", "HANDOVER_CANCELLED",
        "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED", "MATERIAL_ACCEPTED_FOR_ASSEMBLY",
        "ASSEMBLY_STARTED", "ASSEMBLY_PROGRESS_UPDATED", "ASSEMBLY_COMPLETED",
    }
    events: list[dict[str, Any]] = []
    if entity_ids:
        marks = ",".join("?" for _ in entity_ids)
        rows = c.execute(
            f"SELECT event_type,entity_id,actor_user_id,server_time,after_json FROM audit_events WHERE entity_id IN ({marks}) ORDER BY server_time,id",
            list(entity_ids),
        ).fetchall()
        for row in rows:
            if row["event_type"] not in allowed_events:
                continue
            try:
                after = json.loads(row["after_json"])
            except (TypeError, ValueError):
                after = {}
            events.append({
                "type": row["event_type"], "entityId": row["entity_id"],
                "status": after.get("status") or after.get("workspaceStatus") or after.get("handoverStatus"),
                "serverTime": row["server_time"], "actorId": row["actor_user_id"],
            })
    c.close()
    materials = [{
        "requirementId": r["requirement_id"], "deviceId": r["device_id"],
        "deviceType": r["device_type"], "deviceNo": r["device_no"],
        "materialId": r["material_id"], "materialCode": r["material_code"],
        "materialName": r["material_name"], "specification": r["specification"],
        "unit": r["unit"], "requiredQuantity": r["required_quantity"],
        "arrivedQuantity": r["arrived_quantity"], "inStockQuantity": r["in_stock_quantity"],
        "statusCode": r["requirement_status_code"],
    } for r in scoped]
    assembly_minutes = labor_totals.get("ASSEMBLY", 0)
    transfer_minutes = labor_totals.get("TEMPORARY_TRANSFER", 0)
    return {
        "orderId": order["id"], "orderNo": order["order_no"],
        "productName": order["product_name"], "orderStatus": order["status"],
        "materials": materials,
        "materialSummary": _material_summary(scoped),
        "assemblyTasks": [{
            "taskId": r["id"], "deviceId": r["device_id"], "deviceNo": r["device_no"],
            "status": r["status"], "progressStage": r["progress_stage"],
            "taskVersion": r["task_version"], "assignedAssemblerId": r["assigned_assembler_id"],
        } for r in task_rows],
        "laborSummary": {
            "assemblyLaborMinutes": assembly_minutes,
            "temporaryTransferLaborMinutes": transfer_minutes,
            "totalLaborMinutes": assembly_minutes + transfer_minutes,
        },
        "timeline": events,
        "page": page, "pageSize": pageSize, "total": task_total,
    }


def _workspace_status_meta(status_code: str) -> tuple[str, str]:
    """返回服务端状态展示元数据，并对未知状态保持安全降级。"""
    return (
        WORKSPACE_STATUS_LABELS.get(status_code, "未知状态"),
        WORKSPACE_STATUS_COLORS.get(status_code, "status-neutral"),
    )


def _workspace_status_domain(status_code: str) -> str:
    """使用工作台相同的状态域映射，避免 transfer 响应另造一套状态语义。"""
    if status_code in {"OUT_OF_STOCK", "ARRIVED", "IN_STOCK"}:
        return "INVENTORY"
    if status_code.startswith("OUTBOUND_"):
        return "OUTBOUND"
    if status_code in {"PENDING", "REJECTED", "CANCELLED"}:
        return "HANDOVER"
    if status_code in {"PICKED_UP", "AT_STATION"}:
        return "WORKSPACE"
    return "UNKNOWN"


def _workspace_requirements(
    c: sqlite3.Connection, user: sqlite3.Row, order_no: str | None,
    view_role: str | None = None,
) -> list[sqlite3.Row]:
    """读取工作台的正式订单需求行。

    material_work_items 只作为责任人投影使用。工作台的数量始终从
    order_material_requirements 与 material_handovers 实时派生，避免再维护
    一套 issued/picked 数量事实。
    """
    effective_role = view_role or user["role"]
    if effective_role not in ROLES:
        raise ApiError(403, CODE_FORBIDDEN, "账号角色无效")

    query = """
        SELECT r.id AS requirement_id,
               COALESCE(
                   (SELECT w.id
                      FROM material_work_items w
                     WHERE w.requirement_id = r.id OR w.id = r.id
                     ORDER BY (w.requirement_id IS NULL), w.id
                     LIMIT 1),
                   r.id
               ) AS work_item_id,
               (SELECT w.assigned_user_id
                  FROM material_work_items w
                 WHERE w.requirement_id = r.id OR w.id = r.id
                 ORDER BY (w.requirement_id IS NULL), w.id
                 LIMIT 1) AS assigned_user_id,
               (SELECT u.display_name
                  FROM users u
                 WHERE u.id = (
                     SELECT w.assigned_user_id
                       FROM material_work_items w
                      WHERE w.requirement_id = r.id OR w.id = r.id
                      ORDER BY (w.requirement_id IS NULL), w.id
                      LIMIT 1
                 )) AS assigned_user_name,
               o.order_no AS order_no,
               o.product_name AS product_name,
               o.status AS order_status,
               r.device_id AS device_id,
               d.device_type AS device_type,
               d.device_no AS device_no,
               d.sequence_no AS sequence_no,
               r.material_id AS material_id,
               m.code AS material_code,
               m.name AS material_name,
               m.specification AS specification,
               m.unit AS unit,
               r.required_quantity AS required_quantity,
               r.arrived_quantity AS arrived_quantity,
               r.in_stock_quantity AS in_stock_quantity,
               r.status_code AS requirement_status_code,
               r.updated_at AS requirement_updated_at,
               r.created_at AS requirement_created_at
          FROM order_material_requirements r
          JOIN production_orders o ON o.id = r.order_id
          JOIN materials m ON m.id = r.material_id
          LEFT JOIN order_devices d ON d.id = r.device_id
         WHERE 1=1
    """
    args: list[Any] = []
    if order_no is not None:
        query += " AND o.order_no = ?"
        args.append(order_no)

    # 操作员只能看到服务端登记的本人责任范围，不能靠客户端隐藏按钮实现。
    # 责任范围同时兼容历史数据：既支持 material_work_items.assignment，
    # 也支持本人作为交接创建人/接收人的正式交接记录。
    if effective_role == "OPERATOR":
        operator_id = user["id"] if view_role is None else None
        operator_predicate = "w.assigned_user_id = ?" if operator_id else (
            "EXISTS (SELECT 1 FROM users scoped_operator "
            "WHERE scoped_operator.id = w.assigned_user_id "
            "AND scoped_operator.role = 'OPERATOR' AND scoped_operator.active = 1)"
        )
        handover_predicate = (
            "(h.receiver_user_id = ? OR h.created_by = ? OR h.confirmed_by = ?)"
            if operator_id else
            "EXISTS (SELECT 1 FROM users scoped_operator_handover "
            "WHERE scoped_operator_handover.id IN "
            "(h.receiver_user_id, h.created_by, h.confirmed_by) "
            "AND scoped_operator_handover.role = 'OPERATOR' "
            "AND scoped_operator_handover.active = 1)"
        )
        query += """
            AND (
                EXISTS (
                    SELECT 1
                      FROM material_work_items w
                     WHERE (w.requirement_id = r.id OR w.id = r.id)
                       AND {operator_predicate}
                )
                OR EXISTS (
                    SELECT 1
                      FROM material_handovers h
                     WHERE (
                         h.work_item_id = r.id
                         OR EXISTS (
                             SELECT 1
                               FROM material_work_items w2
                              WHERE w2.id = h.work_item_id
                                AND w2.requirement_id = r.id
                         )
                     )
                       AND {handover_predicate}
                )
            )
        """.format(operator_predicate=operator_predicate, handover_predicate=handover_predicate)
        if operator_id:
            args.extend([operator_id] * 4)
    elif effective_role == "MATERIAL":
        material_id = user["id"] if view_role is None else None
        material_assignment = "w.assigned_user_id = ?" if material_id else (
            "EXISTS (SELECT 1 FROM users scoped_material "
            "WHERE scoped_material.id = w.assigned_user_id "
            "AND scoped_material.role = 'MATERIAL' AND scoped_material.active = 1)"
        )
        material_creator = "t.created_by = ?" if material_id else (
            "EXISTS (SELECT 1 FROM users scoped_material_transfer "
            "WHERE scoped_material_transfer.id = t.created_by "
            "AND scoped_material_transfer.role = 'MATERIAL' "
            "AND scoped_material_transfer.active = 1)"
        )
        material_handover = (
            "(h.created_by = ? OR h.confirmed_by = ?)" if material_id else
            "EXISTS (SELECT 1 FROM users scoped_material_handover "
            "WHERE scoped_material_handover.id IN (h.created_by, h.confirmed_by) "
            "AND scoped_material_handover.role = 'MATERIAL' "
            "AND scoped_material_handover.active = 1)"
        )
        # 物料员只看服务端能证明与本人作业相关的出库/交接行：
        # 既兼容责任人投影，也兼容正式出库单/交接记录的创建人。
        # 不把整个订单需求表暴露给普通物料员。
        query += """
            AND (
                EXISTS (
                    SELECT 1
                      FROM material_work_items w
                     WHERE (w.requirement_id = r.id OR w.id = r.id)
                       AND {material_assignment}
                )
                OR EXISTS (
                    SELECT 1
                      FROM transfer_requests t
                     WHERE t.type = 'OUTBOUND'
                       AND t.document_no = o.order_no
                       AND {material_creator}
                       AND EXISTS (
                           SELECT 1
                             FROM json_each(t.payload_json, '$.items') payload_item
                            WHERE json_extract(payload_item.value, '$.materialId') = r.material_id
                       )
                )
                OR EXISTS (
                    SELECT 1
                      FROM material_handovers h
                     WHERE (
                         h.work_item_id = r.id
                         OR EXISTS (
                             SELECT 1
                               FROM material_work_items w2
                              WHERE w2.id = h.work_item_id
                                AND w2.requirement_id = r.id
                         )
                     )
                       AND {material_handover}
                )
            )
        """.format(
            material_assignment=material_assignment,
            material_creator=material_creator,
            material_handover=material_handover,
        )
        if material_id:
            args.extend([material_id] * 4)
    elif effective_role == "ASSEMBLER":
        # Assemblers may see material facts only for devices on their assigned
        # tasks; this keeps the aggregate consistent with task ownership.
        assembler_id = user["id"] if view_role is None else None
        if assembler_id:
            query += """
                AND EXISTS (
                    SELECT 1 FROM assembly_tasks at
                     WHERE at.order_no = o.order_no
                       AND at.device_id = r.device_id
                       AND at.assigned_assembler_id = ?
                )
            """
            args.append(assembler_id)
        else:
            query += """
                AND EXISTS (
                    SELECT 1 FROM assembly_tasks at
                     JOIN users au ON au.id=at.assigned_assembler_id
                     WHERE at.order_no=o.order_no AND at.device_id=r.device_id
                       AND au.role='ASSEMBLER' AND au.active=1
                )
            """

    query += " ORDER BY o.order_no, COALESCE(d.sequence_no, 0), m.code, r.id"
    return c.execute(query, args).fetchall()


def _transfer_visibility(c: sqlite3.Connection, transfer: sqlite3.Row,
                         user: sqlite3.Row) -> bool:
    """判断 transfer 是否属于当前用户的正式业务范围。"""
    # 列表和详情必须复用同一 SQL 谓词；否则客户端会看到状态但打不开
    # 对应申请，或通过详情绕过列表的责任范围。
    scope, scope_args = _transfer_visibility_sql(user, "t")
    return c.execute(
        f"SELECT 1 FROM transfer_requests t WHERE t.id=? AND {scope} LIMIT 1",
        [transfer["id"], *scope_args],
    ).fetchone() is not None


def _transfer_visibility_sql(user: sqlite3.Row, alias: str = "t") -> tuple[str, list[Any]]:
    """返回与 _transfer_visibility 相同语义的 SQL 谓词和参数。"""
    if user["role"] in {"ADMIN", "WAREHOUSE_ADMIN"}:
        return "1=1", []
    scope = f"""(
        {alias}.created_by = ?
        OR EXISTS (
            SELECT 1 FROM material_handovers h
             WHERE h.transfer_request_id = {alias}.id
               AND (
                   h.created_by = ? OR h.receiver_user_id = ? OR h.confirmed_by = ?
                   OR EXISTS (
                       SELECT 1 FROM material_work_items hw
                        WHERE (hw.id = h.work_item_id OR hw.requirement_id = h.work_item_id)
                          AND hw.assigned_user_id = ?
                   )
               )
        )
        OR EXISTS (
            SELECT 1
              FROM material_work_items w
              JOIN order_material_requirements r
                ON r.id = w.requirement_id OR r.id = w.id
              JOIN production_orders o ON o.id = r.order_id
             WHERE w.assigned_user_id = ?
               AND {alias}.document_no = o.order_no
               AND EXISTS (
                   SELECT 1
                     FROM json_each({alias}.payload_json, '$.items') item
                    WHERE json_extract(item.value, '$.materialId') = r.material_id
               )
        )
    )"""
    return scope, [user["id"]] * 6


def _transfer_workspace_status(c: sqlite3.Connection, transfer: sqlite3.Row) -> tuple[str, sqlite3.Row | None]:
    """投影 transfer 的最新交接状态，口径与工作台/时间线一致。"""
    handover = c.execute(
        """SELECT h.*, r.device_id AS requirement_device_id,
                         w.device_id AS work_device_id
             FROM material_handovers h
             LEFT JOIN material_work_items w ON w.id = h.work_item_id
             LEFT JOIN order_material_requirements r
               ON r.id = COALESCE(w.requirement_id, h.work_item_id)
            WHERE h.transfer_request_id=?
            ORDER BY h.created_at DESC, h.id DESC
            LIMIT 1""",
        (transfer["id"],),
    ).fetchone()
    if not handover:
        return (
            OUTBOUND_STATUS_TO_WORKSPACE.get(transfer["status"], transfer["status"]),
            None,
        )

    workspace_status = handover["status"]
    if handover["status"] == "CONFIRMED":
        target_device = handover["requirement_device_id"] or handover["work_device_id"]
        workspace_status = (
            "AT_STATION"
            if handover["device_id"] and target_device
            and handover["device_id"] == target_device
            else "PICKED_UP"
        )
    projection = c.execute(
        """SELECT status_code
             FROM material_work_item_projections
            WHERE work_item_id=? AND last_handover_id=?""",
        (handover["work_item_id"], handover["id"]),
    ).fetchone()
    if projection and projection["status_code"] in WORKSPACE_STATUS_CODES:
        workspace_status = projection["status_code"]
    return workspace_status, handover


def _public_transfer(c: sqlite3.Connection, row: sqlite3.Row,
                     include_payload: bool = False) -> dict[str, Any]:
    """构造 transfer 的公开读模型，统一列表、详情与工作台状态字段。"""
    workspace_status, handover = _transfer_workspace_status(c, row)
    status_label, color_token = _workspace_status_meta(workspace_status)
    item = {
        "id": row["id"],
        "client_operation_id": row["client_operation_id"],
        "type": row["type"],
        "document_no": row["document_no"],
        "status": row["status"],
        "statusCode": row["status"],
        "transferStatus": row["status"],
        "workspaceStatus": workspace_status,
        "statusLabel": status_label,
        "colorToken": color_token,
        "statusDomain": _workspace_status_domain(workspace_status),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "rejection_reason": row["rejection_reason"] if "rejection_reason" in row.keys() else None,
        "executed_at": row["executed_at"],
        "handoverStatus": handover["status"] if handover else None,
        "lastHandoverId": handover["id"] if handover else None,
        "lastHandoverAt": handover["created_at"] if handover else None,
    }
    if include_payload:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {"items": []}
        item["payload"] = payload
        item["items"] = payload.get("items", [])
        item["remark"] = payload.get("remark")
        item["evidenceIds"] = payload.get("evidenceIds", [])
        item["payload_json"] = row["payload_json"]
    return item


def _workspace_handovers(
    c: sqlite3.Connection, work_item_ids: set[str],
) -> dict[str, list[sqlite3.Row]]:
    if not work_item_ids:
        return {}
    placeholders = ",".join("?" for _ in work_item_ids)
    rows = c.execute(
        f"""
        SELECT h.*,
               t.status AS transfer_status,
               s.display_name AS sender_name,
               receiver.display_name AS receiver_name,
               confirmer.display_name AS confirmed_by_name
          FROM material_handovers h
          LEFT JOIN transfer_requests t ON t.id = h.transfer_request_id
          LEFT JOIN users s ON s.id = h.created_by
          LEFT JOIN users receiver ON receiver.id = h.receiver_user_id
          LEFT JOIN users confirmer ON confirmer.id = h.confirmed_by
         WHERE h.work_item_id IN ({placeholders})
         ORDER BY h.created_at, h.id
        """,
        list(work_item_ids),
    ).fetchall()
    result: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        result.setdefault(row["work_item_id"], []).append(row)
    return result


def _workspace_projections(
    c: sqlite3.Connection, work_item_ids: set[str],
) -> dict[str, sqlite3.Row]:
    """读取工作台状态投影；历史数据没有投影时由交接事实兼容重建。"""
    if not work_item_ids:
        return {}
    placeholders = ",".join("?" for _ in work_item_ids)
    rows = c.execute(
        f"""SELECT p.*, u.display_name AS projection_owner_name
                FROM material_work_item_projections p
                LEFT JOIN users u ON u.id = p.current_owner_user_id
               WHERE p.work_item_id IN ({placeholders})""",
        list(work_item_ids),
    ).fetchall()
    return {row["work_item_id"]: row for row in rows}


def _workspace_transfer_records(
    c: sqlite3.Connection, order_nos: set[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """从正式 OUTBOUND 流转单 payload 投影出库状态，不复制数量到工作项表。"""
    if not order_nos:
        return {}
    placeholders = ",".join("?" for _ in order_nos)
    rows = c.execute(
        f"""
        SELECT t.id, t.status, t.document_no, t.payload_json, t.created_at,
               t.approved_at, t.executed_at, t.created_by,
               u.display_name AS created_by_name
          FROM transfer_requests t
          JOIN users u ON u.id = t.created_by
         WHERE t.type = 'OUTBOUND' AND t.document_no IN ({placeholders})
         ORDER BY t.created_at, t.id
        """,
        list(order_nos),
    ).fetchall()
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        for item in payload.get("items", []):
            material_id = item.get("materialId")
            if not material_id:
                continue
            key = (row["document_no"], material_id)
            result.setdefault(key, []).append({
                "id": row["id"],
                "status": row["status"],
                "quantity": item.get("quantity", 0),
                "createdAt": row["created_at"],
                "approvedAt": row["approved_at"],
                "executedAt": row["executed_at"],
                "createdBy": row["created_by"],
                "createdByName": row["created_by_name"],
            })
    return result


def _workspace_last_handover(
    row: sqlite3.Row, handovers: dict[str, list[sqlite3.Row]],
) -> list[sqlite3.Row]:
    """一个需求可能兼容两个工作项 ID，合并后按交接服务端时间排序。"""
    candidates = {row["requirement_id"], row["work_item_id"]}
    values: list[sqlite3.Row] = []
    for work_item_id in candidates:
        values.extend(handovers.get(work_item_id, []))
    # 同一交接不会同时使用两个 ID；去重仍可防止脏历史数据放大数量。
    unique: dict[str, sqlite3.Row] = {item["id"]: item for item in values}
    return sorted(unique.values(), key=lambda item: (item["created_at"], item["id"]))


def _workspace_item(
    row: sqlite3.Row,
    handovers: dict[str, list[sqlite3.Row]],
    projections: dict[str, sqlite3.Row],
    transfers: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    item_handovers = _workspace_last_handover(row, handovers)
    item_transfers = list(transfers.get((row["order_no"], row["material_id"]), []))

    # 交接记录本身携带正式 transferRequestId。即使历史流转单没有 documentNo，
    # 也能把它的服务端状态纳入该需求行的展示状态。
    known_transfer_ids = {record["id"] for record in item_transfers}
    for handover in item_handovers:
        transfer_id = handover["transfer_request_id"]
        if transfer_id and transfer_id not in known_transfer_ids:
            item_transfers.append({
                "id": transfer_id,
                "status": handover["transfer_status"],
                "quantity": 0,
                "createdAt": handover["created_at"],
                "approvedAt": None,
                "executedAt": None,
                "createdBy": handover["created_by"],
                "createdByName": handover["sender_name"],
            })
            known_transfer_ids.add(transfer_id)
    item_transfers.sort(key=lambda record: (record["createdAt"] or "", record["id"] or ""))
    latest_transfer = item_transfers[-1] if item_transfers else None
    latest_handover = item_handovers[-1] if item_handovers else None

    projection = projections.get(row["work_item_id"]) or projections.get(row["requirement_id"])
    derived_status_code = None
    if latest_handover and latest_handover["status"] == "PENDING":
        derived_status_code = "PENDING"
    elif latest_handover and latest_handover["status"] == "CONFIRMED":
        # AT_STATION 只能由确认事件 + 订单登记机台共同证明。
        derived_status_code = (
            "AT_STATION"
            if latest_handover["device_id"]
            and row["device_id"]
            and latest_handover["device_id"] == row["device_id"]
            else "PICKED_UP"
        )
    elif latest_handover and latest_handover["status"] in {"REJECTED", "CANCELLED"}:
        # 驳回/取消是交接状态域的终态，工作台必须能读到同一个终态，
        # 不能回退成出库或订单需求状态而与时间线脱节。
        derived_status_code = latest_handover["status"]
    else:
        derived_status_code = (
            OUTBOUND_STATUS_TO_WORKSPACE.get(latest_transfer["status"])
            if latest_transfer else None
        ) or row["requirement_status_code"]

    # 投影必须与最后一条交接事实绑定后才能作为工作台状态读取；这样既能
    # 用正式投影提供稳定可读状态，也不会让历史脏投影覆盖时间线事实。
    status_code = derived_status_code
    if (
        projection
        and latest_handover
        and projection["last_handover_id"] == latest_handover["id"]
        and projection["status_code"] in WORKSPACE_STATUS_CODES
    ):
        status_code = projection["status_code"]

    status_label, color_token = _workspace_status_meta(status_code)
    # 流转单 item 只有 order + material 维度，无法安全分摊到同一订单的多台机台。
    # 因此工作项数量只从带 work_item_id 的正式交接记录派生，避免把一笔出库
    # 复制到每台设备，也避免读取 work_items.quantity 形成第二套数量事实。
    issued_quantity = sum(
        handover["quantity"]
        for handover in item_handovers
        if handover["status"] not in {"REJECTED", "CANCELLED"}
    )
    picked_quantity = sum(
        handover["quantity"]
        for handover in item_handovers
        if handover["status"] == "CONFIRMED"
    )

    owner_id = row["assigned_user_id"]
    owner_name = row["assigned_user_name"]
    projection_matches = bool(
        projection
        and latest_handover
        and projection["last_handover_id"] == latest_handover["id"]
    )
    if projection_matches and projection["current_owner_user_id"]:
        owner_id = projection["current_owner_user_id"]
        owner_name = projection["projection_owner_name"]
    elif latest_handover and latest_handover["status"] in {"PENDING", "CONFIRMED"}:
        owner_id = (
            latest_handover["receiver_user_id"]
            or latest_handover["confirmed_by"]
            or latest_handover["created_by"]
        )
        owner_name = (
            latest_handover["receiver_name"]
            or latest_handover["confirmed_by_name"]
            or latest_handover["sender_name"]
        )
    elif latest_transfer and latest_transfer["createdBy"]:
        owner_id = latest_transfer["createdBy"]
        owner_name = latest_transfer["createdByName"]

    updated_values = [row["requirement_updated_at"], row["requirement_created_at"]]
    updated_values.extend(
        value
        for handover in item_handovers
        for value in (handover["created_at"], handover["confirmed_at"])
    )
    updated_values.extend(record["createdAt"] for record in item_transfers)
    if projection:
        updated_values.append(projection["status_updated_at"])
    updated_at = max((value for value in updated_values if value), default=now())

    last_handover = None
    if latest_handover:
        last_handover = {
            "id": latest_handover["id"],
            "status": latest_handover["status"],
            "quantity": latest_handover["quantity"],
            "fromLocation": latest_handover["from_location"],
            "deviceId": latest_handover["device_id"],
            "transferRequestId": latest_handover["transfer_request_id"],
            "senderUserId": latest_handover["created_by"],
            "senderName": latest_handover["sender_name"],
            "receiverUserId": latest_handover["receiver_user_id"],
            "receiverName": latest_handover["receiver_name"],
            "initiatedAt": latest_handover["created_at"],
            "confirmedBy": latest_handover["confirmed_by"],
            "confirmedAt": latest_handover["confirmed_at"],
            "remark": latest_handover["remark"],
        }

    return {
        "id": row["work_item_id"],
        "requirementId": row["requirement_id"],
        "orderNo": row["order_no"],
        "productName": row["product_name"],
        "orderStatus": row["order_status"],
        "deviceId": row["device_id"],
        "deviceType": row["device_type"],
        "deviceNo": row["device_no"],
        "materialId": row["material_id"],
        "materialCode": row["material_code"],
        "materialName": row["material_name"],
        "specification": row["specification"],
        "unit": row["unit"],
        "requiredQuantity": row["required_quantity"],
        "arrivedQuantity": row["arrived_quantity"],
        "inStockQuantity": row["in_stock_quantity"],
        "issuedQuantity": issued_quantity,
        "pickedQuantity": picked_quantity,
        "statusCode": status_code,
        "statusLabel": status_label,
        "label": status_label,
        "colorToken": color_token,
        "statusDomain": (
            "INVENTORY" if status_code in {"OUT_OF_STOCK", "ARRIVED", "IN_STOCK"}
            else "OUTBOUND" if status_code.startswith("OUTBOUND_")
            else "HANDOVER" if status_code in {"PENDING", "REJECTED", "CANCELLED"}
            else "WORKSPACE" if status_code in {"PICKED_UP", "AT_STATION"}
            else "UNKNOWN"
        ),
        "updatedAt": updated_at,
        "assignedUserId": row["assigned_user_id"],
        "assignedUserName": row["assigned_user_name"],
        "currentOwnerUserId": owner_id,
        "currentOwnerName": owner_name,
        "responsibilitySummary": {
            "assignedUserId": row["assigned_user_id"],
            "assignedUserName": row["assigned_user_name"],
            "currentOwnerUserId": owner_id,
            "currentOwnerName": owner_name,
        },
        "lastHandoverId": latest_handover["id"] if latest_handover else None,
        "lastHandoverStatus": latest_handover["status"] if latest_handover else None,
        "lastHandover": last_handover,
        "handoverSummary": {
            "lastHandoverId": latest_handover["id"] if latest_handover else None,
            "lastStatus": latest_handover["status"] if latest_handover else None,
            "lastInitiatedAt": latest_handover["created_at"] if latest_handover else None,
            "lastConfirmedAt": latest_handover["confirmed_at"] if latest_handover else None,
            "count": len(item_handovers),
        },
        "transferRequestId": latest_transfer["id"] if latest_transfer else None,
        "transferStatus": latest_transfer["status"] if latest_transfer else None,
    }


def _upsert_workspace_projection(
    c: sqlite3.Connection, work_item_id: str, status_code: str,
    current_owner_user_id: str | None, handover_id: str,
    target_device_id: str | None, transition_time: str,
) -> None:
    """在交接状态事务内刷新工作台可读状态投影。

    该投影只保存工作台状态和责任引用，订单需求数量仍然只从
    ``order_material_requirements`` 读取，交接数量仍然只从
    ``material_handovers`` 聚合，避免形成第二套数量事实。
    """
    c.execute(
        """INSERT INTO material_work_item_projections
               (work_item_id,status_code,current_owner_user_id,last_handover_id,
                target_device_id,status_updated_at,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(work_item_id) DO UPDATE SET
                status_code=excluded.status_code,
                current_owner_user_id=excluded.current_owner_user_id,
                last_handover_id=excluded.last_handover_id,
                target_device_id=excluded.target_device_id,
                status_updated_at=excluded.status_updated_at,
                updated_at=excluded.updated_at""",
        (work_item_id, status_code, current_owner_user_id, handover_id,
         target_device_id, transition_time, transition_time),
    )


def _workspace_items(c: sqlite3.Connection, user: sqlite3.Row,
                     order_no: str | None = None,
                     view_role: str | None = None) -> list[dict[str, Any]]:
    rows = _workspace_requirements(c, user, order_no, view_role)
    work_item_ids = {
        work_item_id
        for row in rows
        for work_item_id in (row["requirement_id"], row["work_item_id"])
        if work_item_id
    }
    handovers = _workspace_handovers(c, work_item_ids)
    projections = _workspace_projections(c, work_item_ids)
    transfers = _workspace_transfer_records(c, {row["order_no"] for row in rows})
    return [_workspace_item(row, handovers, projections, transfers) for row in rows]


def _workspace_summary_flow_counts(
    c: sqlite3.Connection, user: sqlite3.Row, view_role: str | None = None,
) -> tuple[int, int, int]:
    """读取正式流转/交接事实的汇总计数。

    流转申请的 documentNo 可以为空，所以这三项不能仅通过订单需求
    工作项反推；权限条件仍在 SQL 中按当前用户角色执行。
    """
    effective_role = view_role or user["role"]
    preview_scope = view_role is not None
    flow_args: list[Any] = []
    flow_scope = "1=1"
    if effective_role not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        if preview_scope:
            flow_scope = (
                "EXISTS (SELECT 1 FROM users scoped_flow_user "
                "WHERE scoped_flow_user.id = transfer_requests.created_by "
                "AND scoped_flow_user.role = ? AND scoped_flow_user.active = 1)"
            )
            flow_args.append(effective_role)
        else:
            flow_scope = "created_by = ?"
            flow_args.append(user["id"])

    pending_approval = c.execute(
        f"""
        SELECT COUNT(*) AS count
          FROM transfer_requests
         WHERE {flow_scope} AND status = 'PENDING_APPROVAL'
        """,
        flow_args,
    ).fetchone()["count"]
    pending_outbound_args = list(flow_args)
    pending_outbound = c.execute(
        f"""
        SELECT COUNT(*) AS count
          FROM transfer_requests
         WHERE {flow_scope}
           AND type = 'OUTBOUND'
           AND status IN ('PENDING_APPROVAL', 'APPROVED')
        """,
        pending_outbound_args,
    ).fetchone()["count"]

    handover_args: list[Any] = []
    if effective_role in {"ADMIN", "WAREHOUSE_ADMIN"}:
        handover_scope = "1=1"
    elif effective_role == "OPERATOR":
        if preview_scope:
            handover_scope = """
                EXISTS (
                    SELECT 1 FROM users scoped_operator_handover
                     WHERE scoped_operator_handover.id IN
                           (receiver_user_id, created_by, confirmed_by)
                       AND scoped_operator_handover.role = 'OPERATOR'
                       AND scoped_operator_handover.active = 1
                )
            """
        else:
            handover_scope = """
                (
                    receiver_user_id = ?
                    OR created_by = ?
                    OR confirmed_by = ?
                    OR EXISTS (
                        SELECT 1
                          FROM material_work_items w
                         WHERE (w.id = material_handovers.work_item_id
                                OR w.requirement_id = material_handovers.work_item_id)
                           AND w.assigned_user_id = ?
                    )
                )
            """
            handover_args.extend([user["id"]] * 4)
    elif preview_scope:
        handover_scope = """
            EXISTS (
                SELECT 1 FROM users scoped_material_handover
                 WHERE scoped_material_handover.id IN (created_by, confirmed_by)
                   AND scoped_material_handover.role = 'MATERIAL'
                   AND scoped_material_handover.active = 1
            )
        """
    else:
        handover_scope = "(created_by = ? OR confirmed_by = ?)"
        handover_args.extend([user["id"], user["id"]])

    pending_handover = c.execute(
        f"""
        SELECT COUNT(*) AS count
          FROM material_handovers
         WHERE {handover_scope} AND status = 'PENDING'
        """,
        handover_args,
    ).fetchone()["count"]
    return pending_approval, pending_outbound, pending_handover


def _workspace_status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """使用与工作台列表相同的最终投影计算状态摘要，避免摘要与列表分叉。"""
    statuses = (
        "OUT_OF_STOCK", "ARRIVED", "IN_STOCK",
        "OUTBOUND_PENDING", "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED",
        "PENDING", "PICKED_UP", "AT_STATION", "REJECTED", "CANCELLED",
    )
    return {status: sum(item["statusCode"] == status for item in items) for status in statuses}


def _workspace_handover_status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """统计列表中最后一条交接状态；计数口径与时间线最后状态一致。"""
    return {
        status: sum(item["lastHandoverStatus"] == status for item in items)
        for status in HANDOVER_STATES
    }


def _workspace_filter_status(items: list[dict[str, Any]], status: str | None,
                             trace_id: str = "") -> list[dict[str, Any]]:
    if not status:
        return items
    normalized = status.strip().upper()
    if normalized not in WORKSPACE_STATUS_CODES:
        raise ApiError(400, CODE_VALIDATION_ERROR, "status 不是有效的工作台状态", trace_id=trace_id)
    return [
        item for item in items
        if item["statusCode"] == normalized
        or item["lastHandoverStatus"] == normalized
    ]


@app.get("/api/v1/workspace/summary")
def workspace_summary(
    viewRole: str | None = Query(default=None, max_length=64),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    x_client_operation_id: str | None = Header(default=None, alias="X-Client-Operation-Id"),
) -> dict[str, Any]:
    """返回当前服务端角色可见范围内的工作台汇总。"""
    effective_role, preview, trace_id, operation_id = _workspace_context(
        user, viewRole, x_request_id, x_client_operation_id,
    )
    c = db()
    try:
        items = _workspace_items(c, user, view_role=viewRole)
        pending_approval_count, pending_outbound_count, pending_handover_count = (
            _workspace_summary_flow_counts(c, user, viewRole)
        )
        if preview:
            _append_role_preview_audit(
                c, actor=user, view_role=effective_role, action="QUERY",
                request_id=trace_id, client_operation_id=operation_id,
            )
            c.commit()
    finally:
        c.close()
    generated_at = now()
    status_counts = _workspace_status_counts(items)
    handover_status_counts = _workspace_handover_status_counts(items)
    return {
        "role": effective_role,
        "preview": preview,
        "authenticatedRole": user["role"],
        "pendingApprovalCount": pending_approval_count,
        "pendingOutboundCount": pending_outbound_count,
        "pendingHandoverCount": pending_handover_count,
        "atStationCount": status_counts["AT_STATION"],
        "pickedUpCount": status_counts["PICKED_UP"],
        "outOfStockCount": status_counts["OUT_OF_STOCK"],
        "pendingCount": status_counts["PENDING"],
        "confirmedCount": handover_status_counts["CONFIRMED"],
        "rejectedCount": status_counts["REJECTED"],
        "cancelledCount": status_counts["CANCELLED"],
        "rejectedHandoverCount": handover_status_counts["REJECTED"],
        "cancelledHandoverCount": handover_status_counts["CANCELLED"],
        "statusCounts": status_counts,
        "handoverStatusCounts": handover_status_counts,
        "generatedAt": generated_at,
        "serverTime": generated_at,
        "traceId": trace_id,
    }


@app.get("/api/v1/workspace/material-items")
def workspace_material_items(
    viewRole: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=64),
    orderNo: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=50),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    x_client_operation_id: str | None = Header(default=None, alias="X-Client-Operation-Id"),
) -> dict[str, Any]:
    """按服务端角色范围返回订单需求工作项，并提供稳定分页元数据。"""
    effective_role, preview, trace_id, operation_id = _workspace_context(
        user, viewRole, x_request_id, x_client_operation_id,
    )
    normalized_order_no = orderNo.strip() if orderNo is not None else None
    c = db()
    try:
        all_items = _workspace_items(c, user, normalized_order_no, viewRole)
        if preview:
            _append_role_preview_audit(
                c, actor=user, view_role=effective_role, action="QUERY",
                request_id=trace_id, client_operation_id=operation_id,
            )
            c.commit()
    finally:
        c.close()
    visible_items = _workspace_filter_status(all_items, status, trace_id)
    total = len(visible_items)
    start = (page - 1) * pageSize
    items = visible_items[start:start + pageSize]
    return {
        "items": items,
        "role": effective_role,
        "preview": preview,
        "authenticatedRole": user["role"],
        "page": page,
        "pageSize": pageSize,
        "total": total,
        "totalPages": (total + pageSize - 1) // pageSize if total else 0,
        "serverTime": now(),
        "traceId": trace_id,
    }


@app.post("/api/v1/workspace/role-preview/enter")
def enter_role_preview(
    viewRole: str = Query(..., max_length=64),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    x_client_operation_id: str | None = Header(default=None, alias="X-Client-Operation-Id"),
) -> dict[str, Any]:
    """Record a preview switch; it never changes the authenticated session."""
    effective_role, preview, trace_id, operation_id = _workspace_context(
        user, viewRole, x_request_id, x_client_operation_id,
    )
    c = db()
    try:
        _append_role_preview_audit(
            c, actor=user, view_role=effective_role, action="ENTER",
            request_id=trace_id, client_operation_id=operation_id,
        )
        c.commit()
    finally:
        c.close()
    return {
        "role": effective_role,
        "preview": preview,
        "authenticatedRole": user["role"],
        "traceId": trace_id,
    }


@app.post("/api/v1/workspace/role-preview/exit")
def exit_role_preview(
    viewRole: str = Query(..., max_length=64),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    x_client_operation_id: str | None = Header(default=None, alias="X-Client-Operation-Id"),
) -> dict[str, Any]:
    """Record exit from a preview and return the real ADMIN context."""
    _, _, trace_id, operation_id = _workspace_context(
        user, viewRole, x_request_id, x_client_operation_id,
    )
    c = db()
    try:
        _append_role_preview_audit(
            c, actor=user, view_role=viewRole, action="EXIT",
            request_id=trace_id, client_operation_id=operation_id,
        )
        c.commit()
    finally:
        c.close()
    return {
        "role": user["role"],
        "preview": False,
        "authenticatedRole": user["role"],
        "traceId": trace_id,
    }


@app.post("/api/v1/transfer-requests")
def create_transfer(
    body: Transfer,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)

    if body.type not in TRANSFER_TYPES:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       f"type 必须为 {'/'.join(TRANSFER_TYPES)}", trace_id=trace_id)
    if not body.items:
        raise ApiError(400, CODE_VALIDATION_ERROR, "items 不能为空", trace_id=trace_id)
    if body.type in {"INBOUND", "OUTBOUND"} and user["role"] not in {
        "OPERATOR", "MATERIAL", "ADMIN",
    }:
        raise ApiError(403, CODE_FORBIDDEN, "当前角色不能提交入库或出库申请", trace_id=trace_id)

    c = db()
    op_id_str = str(body.clientOperationId)
    try:
        # The idempotency read and insert share the same write lock so a retry
        # cannot create two transfer requests under concurrent delivery.
        c.execute("BEGIN IMMEDIATE")
        old = c.execute(
            "SELECT id,status,payload_json FROM transfer_requests WHERE client_operation_id=?",
            (op_id_str,),
        ).fetchone()
        if old:
            payload_digest = _payload_digest(body.model_dump_json())
            stored_digest = _payload_digest(old["payload_json"])
            if payload_digest != stored_digest:
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                               "相同幂等键的请求体不一致", trace_id=trace_id)
            result = {"requestId": old["id"], "status": old["status"],
                      "idempotent": True, "serverTime": now(), "traceId": trace_id}
            c.rollback()
            c.close()
            return result

        # 数量与库存版本前置校验（整数校验见 _validate_item）
        for item in body.items:
            _validate_item(item, trace_id)

        rid = "tr_" + uuid.uuid4().hex
        c.execute(
            "INSERT INTO transfer_requests (id,client_operation_id,type,document_no,status,payload_json,created_by,created_at,approved_by,approved_at,executed_at,rejection_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, op_id_str, body.type, body.documentNo, "PENDING_APPROVAL",
             body.model_dump_json(), user["id"], now(), None, None, None, None),
        )
        audit(c, user["id"], user["role"], "CREATE", "TRANSFER_REQUEST", rid, "SUCCESS", trace_id)
        c.commit()
    except ApiError:
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "流转申请创建失败",
                       retryable=True, trace_id=trace_id) from None
    c.close()
    status = "PENDING_APPROVAL"
    return {"requestId": rid, "status": status, "serverTime": now(), "traceId": trace_id}


def _payload_digest(payload_json: str) -> str:
    """幂等 payload 摘要：忽略键顺序，仅比较业务内容。"""
    try:
        normalized = json.dumps(json.loads(payload_json), sort_keys=True, ensure_ascii=False)
    except (ValueError, TypeError):
        normalized = payload_json
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_item(item: Any, trace_id: str) -> None:
    """契约 6 节：数量必须是 JSON 整数，拒绝 bool / 小数 / 负数 / 科学计数法。"""
    qty = getattr(item, "quantity", None)
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise ApiError(400, CODE_VALIDATION_ERROR, "quantity 必须是整数", trace_id=trace_id)
    if qty < 1:
        raise ApiError(400, CODE_VALIDATION_ERROR, "quantity 必须为正整数", trace_id=trace_id)
    ver = getattr(item, "expectedInventoryVersion", None)
    if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       "expectedInventoryVersion 必须是正整数", trace_id=trace_id)


def _execute_transfer_items(
    rid: str,
    user: sqlite3.Row,
    trace_id: str,
    operation_id: str | None = None,
    operation_payload: str | None = None,
    request: Request | None = None,
) -> dict[str, Any]:
    """在单个事务内执行库存变更。

    契约 4.3 / 6 节：
      - 逐 item 校验 expectedInventoryVersion，任一冲突整单回滚
      - 同时更新 materials 汇总与 inventory 库位明细
      - 库存不足、库位不存在整单回滚
      - 已执行过的单据再次执行返回原结果，不重复扣减
    """
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        if operation_id is not None and operation_payload is not None:
            prior = c.execute(
                "SELECT * FROM transfer_operations WHERE client_operation_id=?",
                (operation_id,),
            ).fetchone()
            if prior:
                if (
                    prior["transfer_request_id"] != rid
                    or prior["action"] != "EXECUTE"
                    or _payload_digest(prior["payload_json"])
                    != _payload_digest(operation_payload)
                ):
                    raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                                   "相同幂等键的请求体不一致", trace_id=trace_id)
                result = json.loads(prior["result_json"])
                c.rollback()
                c.close()
                result["idempotent"] = True
                result["traceId"] = trace_id
                return result

        row = c.execute("SELECT * FROM transfer_requests WHERE id=?", (rid,)).fetchone()
        if not row:
            raise ApiError(404, CODE_VALIDATION_ERROR, "申请不存在", trace_id=trace_id)
        if row["status"] == "EXECUTED":
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "申请已执行，不能重复执行", trace_id=trace_id)
        if row["status"] != "APPROVED":
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, "申请尚未审批通过", trace_id=trace_id)

        # 审批人与执行人不能是同一用户（TRANSFER 无需审批，跳过）
        if row["approved_by"] and row["approved_by"] == user["id"]:
            raise ApiError(409, CODE_APPROVAL_EXECUTOR_SAME_USER,
                           "审批人与执行人不能是同一用户", trace_id=trace_id)

        payload = json.loads(row["payload_json"])
        for item in payload.get("items", []):
            material_id = item.get("materialId")
            quantity = item.get("quantity")
            expected_version = item.get("expectedInventoryVersion")
            source_code = item.get("sourceLocationCode")
            target_code = item.get("targetLocationCode")

            material = c.execute(
                "SELECT id,available_quantity,version FROM materials WHERE id=?", (material_id,)
            ).fetchone()
            if not material:
                raise ApiError(400, CODE_VALIDATION_ERROR, "物料不存在", trace_id=trace_id)

            # 乐观锁：版本不匹配整单回滚
            if expected_version is not None and material["version"] != expected_version:
                raise ApiError(
                    409, CODE_INVENTORY_VERSION_CONFLICT,
                    "库存已变化，请刷新后重试", trace_id=trace_id,
                    details={"materialId": material_id,
                             "serverVersion": material["version"],
                             "clientVersion": expected_version},
                )

            transfer_type = row["type"]
            delta = quantity if transfer_type == "INBOUND" else -quantity

            if transfer_type in {"OUTBOUND", "TRANSFER"}:
                if material["available_quantity"] < quantity:
                    raise ApiError(409, CODE_INSUFFICIENT_INVENTORY,
                                   "库存不足", trace_id=trace_id,
                                   details={"materialId": material_id,
                                            "available": material["available_quantity"],
                                            "requested": quantity})

            updated = c.execute(
                "UPDATE materials SET total_quantity=total_quantity+?, "
                "available_quantity=available_quantity+?, version=version+1 "
                "WHERE id=? AND version=? AND available_quantity+? >= 0",
                (delta, delta, material["id"], material["version"], delta),
            )
            if updated.rowcount != 1:
                raise ApiError(
                    409, CODE_INVENTORY_VERSION_CONFLICT,
                    "库存已变化，请刷新后重试", trace_id=trace_id,
                    details={"materialId": material_id},
                )

            # 同步库位明细，避免汇总与明细漂移
            if source_code and transfer_type in {"OUTBOUND", "TRANSFER"}:
                _adjust_location_qty(c, material["id"], source_code, -quantity, trace_id)
            if target_code and transfer_type in {"INBOUND", "TRANSFER"}:
                _adjust_location_qty(c, material["id"], target_code, quantity, trace_id)

        execution_time = now()
        c.execute(
            "UPDATE transfer_requests SET status='EXECUTED',executed_at=? WHERE id=?",
            (execution_time, rid),
        )
        audit(c, user["id"], user["role"], "EXECUTE", "TRANSFER_REQUEST", rid, "SUCCESS", trace_id)
        if row["type"] == "OUTBOUND" and request is not None and operation_id is not None:
            _audit_event(
                c, "OUTBOUND_CONFIRMED", rid, user, trace_id, operation_id,
                {"status": "APPROVED"},
                {"status": "EXECUTED", "executedAt": execution_time},
                request, entity_type="TRANSFER_REQUEST",
            )
        result = {
            "requestId": rid,
            "status": "EXECUTED",
            "serverTime": execution_time,
            "traceId": trace_id,
        }
        if operation_id is not None and operation_payload is not None:
            c.execute(
                "INSERT INTO transfer_operations VALUES(?,?,?,?,?,?)",
                (operation_id, rid, "EXECUTE", operation_payload,
                 json.dumps(result, ensure_ascii=False), execution_time),
            )
        c.commit()
    except ApiError:
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "执行失败", retryable=True,
                       trace_id=trace_id) from None
    c.close()
    return result


def _adjust_location_qty(c: sqlite3.Connection, material_id: str, location_code: str,
                         delta: int, trace_id: str) -> None:
    """按库位码调整 inventory 明细；库位不存在或数量不足则整单回滚。"""
    loc = c.execute("SELECT id FROM locations WHERE code=?", (location_code,)).fetchone()
    if not loc:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       "库位不存在", trace_id=trace_id)
    existing = c.execute(
        "SELECT id,quantity FROM inventory WHERE material_id=? AND location_id=?",
        (material_id, loc["id"]),
    ).fetchone()
    if existing is None:
        if delta < 0:
            raise ApiError(409, CODE_INSUFFICIENT_INVENTORY,
                           "库位无可用库存", trace_id=trace_id)
        c.execute("INSERT INTO inventory VALUES(?,?,?,?)",
                  ("inv_" + uuid.uuid4().hex, material_id, loc["id"], delta))
        return
    new_qty = existing["quantity"] + delta
    if new_qty < 0:
        raise ApiError(409, CODE_INSUFFICIENT_INVENTORY,
                           "库位库存不足", trace_id=trace_id)
    c.execute("UPDATE inventory SET quantity=? WHERE id=?", (new_qty, existing["id"]))


@app.post("/api/v1/transfer-requests/{rid}/approve")
def approve(
    rid: str,
    body: TransferApproval,
    request: Request,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    # ADMIN may approve any request; other roles require the applicant's direct manager relation.
    if body.decision not in {"APPROVE", "REJECT"}:
        raise ApiError(400, CODE_VALIDATION_ERROR, "decision 无效", trace_id=trace_id)
    # 契约 4.2：拒绝必须填写原因，长度 1-500
    if body.decision == "REJECT":
        comment = (body.comment or "").strip()
        if not 1 <= len(comment) <= 500:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "拒绝时必须填写原因（1-500 字）", trace_id=trace_id)

    status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
    operation_id = str(body.clientOperationId)
    operation_payload = body.model_dump_json()
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute(
            "SELECT * FROM transfer_operations WHERE client_operation_id=?",
            (operation_id,),
        ).fetchone()
        if prior:
            if (
                prior["transfer_request_id"] != rid
                or prior["action"] != "APPROVE"
                or _payload_digest(prior["payload_json"])
                != _payload_digest(operation_payload)
            ):
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                               "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"])
            c.rollback()
            c.close()
            result["idempotent"] = True
            result["traceId"] = trace_id
            return result

        transfer = c.execute(
            "SELECT id,type,status,created_by,approved_by,approved_at FROM transfer_requests WHERE id=?",
            (rid,),
        ).fetchone()
        if not transfer:
            raise ApiError(404, CODE_VALIDATION_ERROR, "申请不存在", trace_id=trace_id)
        if user["role"] != "ADMIN":
            if transfer["created_by"] == user["id"]:
                raise ApiError(403, CODE_FORBIDDEN, "申请人不能审批本人申请", trace_id=trace_id)
            if not c.execute("SELECT 1 FROM employee_managers LIMIT 1").fetchone() and user["role"] == "WAREHOUSE_ADMIN":
                # Backward-compatible databases predating manager assignments.
                pass
            elif not c.execute(
                "SELECT 1 FROM employee_managers WHERE employee_id=? AND manager_id=?",
                (transfer["created_by"], user["id"]),
            ).fetchone():
                raise ApiError(403, CODE_FORBIDDEN, "仅申请人的直属领导可审批", trace_id=trace_id)
        cur = c.execute(
            "UPDATE transfer_requests SET status=?,approved_by=?,approved_at=?,rejection_reason=? "
            "WHERE id=? AND status='PENDING_APPROVAL'",
            (status, user["id"], now(), (body.comment.strip() if status == "REJECTED" else None), rid),
        )
        if cur.rowcount != 1:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "申请状态不允许审批", trace_id=trace_id)
        decision_time = c.execute(
            "SELECT approved_at FROM transfer_requests WHERE id=?", (rid,)
        ).fetchone()["approved_at"]
        audit(c, user["id"], user["role"], "APPROVE", "TRANSFER_REQUEST", rid, "SUCCESS", trace_id)
        if transfer["type"] == "OUTBOUND" and status == "APPROVED":
            _audit_event(
                c, "OUTBOUND_APPROVED", rid, user, trace_id, operation_id,
                {"status": "PENDING_APPROVAL"},
                {"status": "APPROVED", "approvedAt": decision_time},
                request, entity_type="TRANSFER_REQUEST",
            )
        elif status == "REJECTED":
            _audit_event(
                c, "TRANSFER_REJECTED", rid, user, trace_id, operation_id,
                {"status": "PENDING_APPROVAL"},
                {"status": "REJECTED", "reason": body.comment.strip()},
                request, entity_type="TRANSFER_REQUEST",
            )
        result = {
            "requestId": rid,
            "status": status,
            "serverTime": decision_time,
            "traceId": trace_id,
        }
        c.execute(
            "INSERT INTO transfer_operations VALUES(?,?,?,?,?,?)",
            (operation_id, rid, "APPROVE", operation_payload,
             json.dumps(result, ensure_ascii=False), decision_time),
        )
        c.commit()
    except ApiError:
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "申请审批失败",
                       retryable=True, trace_id=trace_id) from None
    c.close()
    return result


@app.get("/api/v1/transfer-requests")
def list_transfers(
    status: str | None = Query(default=None, max_length=32),
    user: sqlite3.Row = Depends(current_user),
) -> dict[str, Any]:
    c = db()
    if status is not None:
        status = status.strip().upper()
        if status not in TRANSFER_STATES:
            c.close()
            raise HTTPException(400, "status 不是有效的流转申请状态")
    query = """SELECT t.id,t.client_operation_id,t.type,t.document_no,t.status,
                      t.payload_json,t.created_by,t.created_at,t.approved_by,t.approved_at,t.executed_at
                 FROM transfer_requests t"""
    scope, args = _transfer_visibility_sql(user, "t")
    query += " WHERE " + scope
    if status:
        query += " AND t.status=?"
        args.append(status)
    rows = c.execute(query + " ORDER BY t.created_at DESC, t.id DESC LIMIT 100", args).fetchall()
    items = [_public_transfer(c, row) for row in rows]
    c.close()
    return {"items": items, "serverTime": now()}


def _audit_event(c: sqlite3.Connection, event_type: str, entity_id: str,
                 actor: sqlite3.Row, request_id: str, operation_id: str,
                 before: dict[str, Any], after: dict[str, Any], request: Request,
                 result: str = "SUCCESS", entity_type: str = "MATERIAL_HANDOVER") -> None:
    c.execute(
        """INSERT INTO audit_events
           (event_type,entity_type,entity_id,actor_user_id,actor_role,request_id,
            client_operation_id,before_json,after_json,server_time,device_id,source_ip,result)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event_type, entity_type, entity_id, actor["id"], actor["role"],
         request_id, operation_id, json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False), now(), actor["session_device_id"],
         request.client.host if request.client else None, result),
    )


def _public_audit_event(row: sqlite3.Row) -> dict[str, Any]:
    """审计/时间线白名单投影；source_ip 永远不进入客户端响应。"""
    keys = set(row.keys())

    def value(name: str) -> Any:
        return row[name] if name in keys else None

    return {
        "id": value("id"),
        "event_type": value("event_type"),
        "entity_type": value("entity_type"),
        "entity_id": value("entity_id"),
        "actor_user_id": value("actor_user_id"),
        "actor_role": value("actor_role"),
        "request_id": value("request_id"),
        "client_operation_id": value("client_operation_id"),
        "before_json": value("before_json"),
        "after_json": value("after_json"),
        "server_time": value("server_time"),
        "device_id": value("device_id"),
        "view_role": value("view_role"),
        "preview_action": value("action"),
        "result": value("result"),
    }


def _handover_headers(x_request_id: str | None, idempotency_key: str | None,
                      operation_id: uuid.UUID) -> str:
    trace_id = require_request_id(x_request_id)
    require_idempotency_key(idempotency_key, operation_id, trace_id)
    return trace_id


def _handover_visible(c: sqlite3.Connection, row: sqlite3.Row,
                      user: sqlite3.Row) -> bool:
    if user["role"] in {"ADMIN", "WAREHOUSE_ADMIN"}:
        return True
    if user["id"] in {row["created_by"], row["receiver_user_id"]}:
        return True
    # Work-item assignments are the non-privileged read scope.
    return c.execute(
        """SELECT 1
             FROM material_work_items w
            WHERE (w.id=? OR w.requirement_id=?)
              AND w.assigned_user_id=?""",
        (row["work_item_id"], row["work_item_id"], user["id"]),
    ).fetchone() is not None


def _validate_handover_relation(c: sqlite3.Connection, body: HandoverCreate,
                                transfer: sqlite3.Row, user: sqlite3.Row,
                                trace_id: str) -> dict[str, Any]:
    work = c.execute("SELECT * FROM material_work_items WHERE id=?",
                     (body.workItemId,)).fetchone()
    req = c.execute("SELECT * FROM order_material_requirements WHERE id=?",
                    (body.workItemId,)).fetchone()
    if not work and not req:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       "workItemId 不存在或不属于出库单", trace_id=trace_id)
    if work:
        if not work["requirement_id"]:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "工作项未关联正式订单需求", trace_id=trace_id)
        if work["requirement_id"]:
            req = c.execute(
                "SELECT * FROM order_material_requirements WHERE id=?",
                (work["requirement_id"],),
            ).fetchone()
            if not req:
                raise ApiError(400, CODE_VALIDATION_ERROR,
                               "工作项未关联有效订单需求", trace_id=trace_id)
            if req["material_id"] != work["material_id"]:
                raise ApiError(400, CODE_VALIDATION_ERROR,
                               "工作项物料与订单需求不一致", trace_id=trace_id)
            if work["device_id"] and req["device_id"] != work["device_id"]:
                raise ApiError(400, CODE_VALIDATION_ERROR,
                               "工作项目标机台与订单需求不一致", trace_id=trace_id)
        if work["assigned_user_id"] and body.receiverUserId != work["assigned_user_id"]:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "receiverUserId 与工作项归属不一致", trace_id=trace_id)
        if body.deviceId and work["device_id"] and body.deviceId != work["device_id"]:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "deviceId 与工作项目标机台不一致", trace_id=trace_id)
        material_id = work["material_id"]
        target_device_id = req["device_id"] if req else work["device_id"]
        max_quantity = req["required_quantity"] if req else work["quantity"]
    else:
        if body.deviceId and req["device_id"] and body.deviceId != req["device_id"]:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "deviceId 与工作项目标机台不一致", trace_id=trace_id)
        material_id = req["material_id"]
        target_device_id = req["device_id"]
        max_quantity = req["required_quantity"]
    assigned_work = work
    if assigned_work is None and req:
        assigned_work = c.execute(
            """SELECT * FROM material_work_items
                WHERE requirement_id=?
                ORDER BY id
                LIMIT 1""",
            (req["id"],),
        ).fetchone()
    if assigned_work and assigned_work["material_id"] != material_id:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       "工作项物料与订单需求不一致", trace_id=trace_id)
    if (
        assigned_work
        and assigned_work["assigned_user_id"]
        and body.receiverUserId
        and body.receiverUserId != assigned_work["assigned_user_id"]
    ):
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       "receiverUserId 与工作项归属不一致", trace_id=trace_id)
    if not target_device_id or body.deviceId != target_device_id:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       "deviceId 与订单目标机台不一致", trace_id=trace_id)
    if req:
        order = c.execute(
            "SELECT order_no FROM production_orders WHERE id=?", (req["order_id"],)
        ).fetchone()
        if not order:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "工作项未关联有效生产订单", trace_id=trace_id)
        if transfer["document_no"] != order["order_no"]:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "流转单与工作项所属订单不一致", trace_id=trace_id)
    payload = json.loads(transfer["payload_json"])
    transfer_quantity = sum(
        item.get("quantity", 0)
        for item in payload.get("items", [])
        if item.get("materialId") == material_id
    )
    if transfer_quantity <= 0:
        raise ApiError(400, CODE_VALIDATION_ERROR, "工作项不属于出库单", trace_id=trace_id)
    if body.quantity > transfer_quantity or body.quantity > max_quantity:
        raise ApiError(400, CODE_VALIDATION_ERROR, "交接数量超过已审批数量", trace_id=trace_id)
    related_work_item_ids = {body.workItemId}
    if work:
        related_work_item_ids.add(work["id"])
        if work["requirement_id"]:
            related_work_item_ids.add(work["requirement_id"])
    placeholders = ",".join("?" for _ in related_work_item_ids)
    existing_quantity = c.execute(
        f"""SELECT COALESCE(SUM(quantity), 0) AS quantity
               FROM material_handovers
              WHERE work_item_id IN ({placeholders})
                AND status IN ('PENDING','CONFIRMED')""",
        list(related_work_item_ids),
    ).fetchone()["quantity"]
    if existing_quantity + body.quantity > max_quantity:
        raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                       "交接数量与已有交接重叠", trace_id=trace_id)
    transfer_existing_quantity = c.execute(
        """SELECT COALESCE(SUM(h.quantity), 0) AS quantity
               FROM material_handovers h
               LEFT JOIN material_work_items w ON w.id = h.work_item_id
               LEFT JOIN order_material_requirements r
                 ON r.id = COALESCE(w.requirement_id, h.work_item_id)
              WHERE h.transfer_request_id=?
                AND h.status IN ('PENDING','CONFIRMED')
                AND COALESCE(w.material_id, r.material_id)=?""",
        (transfer["id"], material_id),
    ).fetchone()["quantity"]
    if transfer_existing_quantity + body.quantity > transfer_quantity:
        raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                       "交接数量超过流转单已审批数量", trace_id=trace_id)
    if body.receiverUserId:
        receiver = c.execute("SELECT id,role,active FROM users WHERE id=?",
                             (body.receiverUserId,)).fetchone()
        if not receiver or not receiver["active"] or receiver["role"] != "OPERATOR":
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "receiverUserId 必须是有效操作员", trace_id=trace_id)
        if user["role"] == "OPERATOR" and user["id"] != body.receiverUserId:
            raise ApiError(403, CODE_FORBIDDEN, "只能为本人接收交接", trace_id=trace_id)
    return {
        "requirement_id": req["id"] if req else None,
        "material_id": material_id,
        "target_device_id": target_device_id,
        "max_quantity": max_quantity,
        "transfer_quantity": transfer_quantity,
    }


@app.post("/api/v1/handovers")
def create_handover(
    body: HandoverCreate, request: Request,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = _handover_headers(x_request_id, idempotency_key, body.clientOperationId)
    if user["role"] != "MATERIAL":
        raise ApiError(403, CODE_FORBIDDEN, "仅物料员可发起交接", trace_id=trace_id)
    c = db()
    operation_id = str(body.clientOperationId)
    operation_payload = body.model_dump_json()
    try:
        # 先锁住整个交接写事务，幂等检查、重叠数量校验、状态投影和审计
        # 必须观察同一个数据库快照，避免并发重试写出两条交接。
        c.execute("BEGIN IMMEDIATE")
        old = c.execute(
            "SELECT * FROM material_handovers WHERE client_operation_id=?", (operation_id,)
        ).fetchone()
        if old:
            stored_payload = json.dumps({
                "workItemId": old["work_item_id"], "transferRequestId": old["transfer_request_id"],
                "quantity": old["quantity"], "fromLocation": old["from_location"],
                "deviceId": old["device_id"], "receiverUserId": old["receiver_user_id"],
                "remark": old["remark"], "clientOperationId": operation_id,
            }, ensure_ascii=False)
            if _payload_digest(operation_payload) != _payload_digest(stored_payload):
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                               "相同幂等键的请求体不一致", trace_id=trace_id)
            c.rollback()
            c.close()
            return {"handoverId": old["id"], "status": old["status"],
                    "idempotent": True, "traceId": trace_id}

        transfer = c.execute(
            """SELECT id,status,type,document_no,payload_json,approved_by,approved_at,
                              created_by
                 FROM transfer_requests WHERE id=?""",
            (body.transferRequestId,),
        ).fetchone()
        if not transfer or transfer["type"] != "OUTBOUND":
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "transferRequestId 必须关联 OUTBOUND 流转单", trace_id=trace_id)
        if transfer["status"] not in {"APPROVED", "EXECUTED"}:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "流转单尚未审批通过", trace_id=trace_id)
        if not transfer["approved_by"] or not transfer["approved_at"]:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "流转单缺少有效审批记录", trace_id=trace_id)
        approver = c.execute(
            "SELECT id,role,active FROM users WHERE id=?", (transfer["approved_by"],)
        ).fetchone()
        if (
            not approver
            or not approver["active"]
            or approver["role"] not in {"WAREHOUSE_ADMIN", "ADMIN"}
        ):
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "流转单审批人角色无效", trace_id=trace_id)
        relation = _validate_handover_relation(c, body, transfer, user, trace_id)
        hid = "ho_" + uuid.uuid4().hex
        created_at = now()
        c.execute("""INSERT INTO material_handovers
            (id,work_item_id,transfer_request_id,quantity,from_location,device_id,receiver_user_id,remark,client_operation_id,status,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, body.workItemId, body.transferRequestId, body.quantity, body.fromLocation,
             body.deviceId, body.receiverUserId, body.remark, operation_id, "PENDING",
             user["id"], created_at))
        _upsert_workspace_projection(
            c, body.workItemId, "PENDING", body.receiverUserId or user["id"], hid,
            relation["target_device_id"], created_at,
        )
        _audit_event(c, "HANDOVER_CREATED", hid, user, trace_id, operation_id, {},
                     {"status": "PENDING", "transferRequestId": body.transferRequestId,
                      "quantity": body.quantity, "workspaceStatus": "PENDING"}, request)
        c.commit()
    except ApiError:
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "交接创建失败",
                       retryable=True, trace_id=trace_id) from None
    c.close()
    return {"handoverId": hid, "status": "PENDING",
            "transferRequestId": body.transferRequestId, "traceId": trace_id}


def _decide_handover(hid: str, body: HandoverDecision, request: Request, user: sqlite3.Row,
                     x_request_id: str | None, idempotency_key: str | None, action: str) -> dict[str, Any]:
    trace_id = _handover_headers(x_request_id, idempotency_key, body.clientOperationId)
    if action == "CONFIRMED" and user["role"] not in {"OPERATOR", "WAREHOUSE_ADMIN", "ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无确认权限", trace_id=trace_id)
    if action == "REJECTED" and user["role"] not in {"WAREHOUSE_ADMIN", "ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "仅仓库管理员或管理员可驳回交接", trace_id=trace_id)
    if action == "CANCELLED" and user["role"] not in {"WAREHOUSE_ADMIN", "ADMIN", "MATERIAL"}:
        raise ApiError(403, CODE_FORBIDDEN, "无取消权限", trace_id=trace_id)
    if action in {"REJECTED", "CANCELLED"} and not (body.reason or "").strip():
        raise ApiError(400, CODE_VALIDATION_ERROR, "驳回或取消必须填写原因", trace_id=trace_id)
    operation_id = str(body.clientOperationId)
    operation_payload = body.model_dump_json()
    c = db()
    try:
        # BEGIN IMMEDIATE 把幂等闸门、状态转换、工作台投影和审计事件锁在
        # 同一事务中；两个并发请求不会都读到 PENDING 后各自写事件。
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM material_handovers WHERE id=?", (hid,)).fetchone()
        if not row:
            raise ApiError(404, CODE_VALIDATION_ERROR, "交接不存在", trace_id=trace_id)
        if not _handover_visible(c, row, user):
            raise ApiError(403, CODE_FORBIDDEN, "无权处理该交接", trace_id=trace_id)
        if action == "CANCELLED" and user["role"] == "MATERIAL" and row["created_by"] != user["id"]:
            raise ApiError(403, CODE_FORBIDDEN, "物料员只能取消本人发起的交接", trace_id=trace_id)
        if (
            action == "CONFIRMED"
            and row["receiver_user_id"]
            and row["receiver_user_id"] != user["id"]
            and user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}
        ):
            raise ApiError(403, CODE_FORBIDDEN, "只能由指定接收人确认", trace_id=trace_id)
        if action == "CONFIRMED" and user["role"] == "OPERATOR":
            assignment = c.execute(
                """SELECT assigned_user_id FROM material_work_items
                    WHERE id=? OR requirement_id=?
                    ORDER BY (requirement_id IS NULL), id
                    LIMIT 1""",
                (row["work_item_id"], row["work_item_id"]),
            ).fetchone()
            if (
                not row["receiver_user_id"]
                and assignment
                and assignment["assigned_user_id"]
                and assignment["assigned_user_id"] != user["id"]
            ):
                raise ApiError(403, CODE_FORBIDDEN, "只能由工作项归属操作员确认", trace_id=trace_id)

        prior = c.execute(
            "SELECT * FROM handover_operations WHERE client_operation_id=?",
            (operation_id,),
        ).fetchone()
        if prior:
            if (
                prior["handover_id"] != hid
                or prior["action"] != action
                or _payload_digest(prior["payload_json"]) != _payload_digest(operation_payload)
            ):
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                               "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"])
            c.rollback()
            c.close()
            result["idempotent"] = True
            result["traceId"] = trace_id
            return result

        transfer = c.execute(
            """SELECT id,status,type,document_no,payload_json,approved_by,approved_at
                 FROM transfer_requests WHERE id=?""",
            (row["transfer_request_id"],),
        ).fetchone()
        if not transfer or transfer["type"] != "OUTBOUND":
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接未关联有效出库单", trace_id=trace_id)
        if transfer["status"] not in {"APPROVED", "EXECUTED"}:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "关联流转单尚未审批通过", trace_id=trace_id)
        if not transfer["approved_by"] or not transfer["approved_at"]:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "关联流转单缺少有效审批记录", trace_id=trace_id)
        approver = c.execute(
            "SELECT role,active FROM users WHERE id=?", (transfer["approved_by"],)
        ).fetchone()
        if (
            not approver
            or not approver["active"]
            or approver["role"] not in {"WAREHOUSE_ADMIN", "ADMIN"}
        ):
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "关联流转单审批人角色无效", trace_id=trace_id)
        relation = c.execute(
            """SELECT w.id AS work_item_id, w.requirement_id, w.material_id AS work_material_id,
                              w.device_id AS work_device_id,
                              r.id AS requirement_id_resolved, r.order_id,
                              r.material_id, r.device_id AS requirement_device_id,
                              r.required_quantity
                         FROM material_handovers h
                         LEFT JOIN material_work_items w ON w.id = h.work_item_id
                         LEFT JOIN order_material_requirements r
                           ON r.id = COALESCE(w.requirement_id, h.work_item_id)
                        WHERE h.id=?""",
            (hid,),
        ).fetchone()
        if not relation or not relation["requirement_id_resolved"]:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接未关联有效订单需求", trace_id=trace_id)
        if (
            relation["work_material_id"]
            and relation["work_material_id"] != relation["material_id"]
        ):
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接工作项物料与订单需求不一致", trace_id=trace_id)
        if (
            relation["work_device_id"]
            and relation["work_device_id"] != relation["requirement_device_id"]
        ):
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接工作项目标机台与订单需求不一致", trace_id=trace_id)
        if (
            relation["requirement_device_id"]
            and row["device_id"] != relation["requirement_device_id"]
        ):
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接目标机台与订单需求不一致", trace_id=trace_id)
        order = c.execute(
            "SELECT order_no FROM production_orders WHERE id=?", (relation["order_id"],)
        ).fetchone()
        if not order or transfer["document_no"] != order["order_no"]:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接所属订单与流转单不一致", trace_id=trace_id)
        transfer_payload = json.loads(transfer["payload_json"])
        transfer_quantity = sum(
            item.get("quantity", 0)
            for item in transfer_payload.get("items", [])
            if item.get("materialId") == relation["material_id"]
        )
        if transfer_quantity < row["quantity"] or row["quantity"] > relation["required_quantity"]:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接数量超过已审批数量", trace_id=trace_id)

        if row["status"] != "PENDING":
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接状态不允许重复处理", trace_id=trace_id)

        transition_time = now()
        updated = c.execute(
            """UPDATE material_handovers
                  SET status=?, confirmed_by=?, confirmed_at=?, decision_reason=?
                WHERE id=? AND status='PENDING'""",
            (action, user["id"], transition_time, body.reason, hid),
        )
        if updated.rowcount != 1:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT,
                           "交接状态不允许重复处理", trace_id=trace_id)

        event_types = []
        if action == "CONFIRMED":
            event_types.extend(["HANDOVER_CONFIRMED", "MATERIAL_PICKED_UP"])
            target_device_id = relation["requirement_device_id"]
            at_station = bool(
                row["device_id"]
                and target_device_id
                and row["device_id"] == target_device_id
            )
            if at_station:
                event_types.append("MATERIAL_AT_STATION")
            projection_status = "AT_STATION" if at_station else "PICKED_UP"
            _upsert_workspace_projection(
                c, row["work_item_id"], projection_status,
                row["receiver_user_id"] or user["id"], hid,
                target_device_id, transition_time,
            )
        else:
            event_types.append({
                "REJECTED": "HANDOVER_REJECTED",
                "CANCELLED": "HANDOVER_CANCELLED",
            }[action])
            _upsert_workspace_projection(
                c, row["work_item_id"], action,
                row["receiver_user_id"] or row["created_by"], hid,
                relation["requirement_device_id"], transition_time,
            )

        for event_type in event_types:
            event_status = {
                "HANDOVER_CONFIRMED": "CONFIRMED",
                "MATERIAL_PICKED_UP": "PICKED_UP",
                "MATERIAL_AT_STATION": "AT_STATION",
                "HANDOVER_REJECTED": "REJECTED",
                "HANDOVER_CANCELLED": "CANCELLED",
            }[event_type]
            if event_type == "HANDOVER_CONFIRMED":
                event_before = {"handoverStatus": "PENDING", "workspaceStatus": "PENDING"}
            elif event_type == "MATERIAL_PICKED_UP":
                event_before = {"handoverStatus": "CONFIRMED", "workspaceStatus": "PENDING"}
            elif event_type == "MATERIAL_AT_STATION":
                event_before = {"handoverStatus": "CONFIRMED", "workspaceStatus": "PICKED_UP"}
            else:
                event_before = {"handoverStatus": "PENDING", "workspaceStatus": "PENDING"}
            after = {
                "status": event_status,
                "eventType": event_type,
                "handoverId": hid,
                "workItemId": row["work_item_id"],
            }
            if event_type in {"HANDOVER_CONFIRMED", "HANDOVER_REJECTED", "HANDOVER_CANCELLED"}:
                after["handoverStatus"] = event_status
            else:
                after["workspaceStatus"] = event_status
            if event_type == "MATERIAL_AT_STATION":
                after["targetDeviceId"] = target_device_id
            if body.reason:
                after["reason"] = body.reason
            _audit_event(
                c, event_type, hid, user, trace_id, operation_id,
                event_before, after, request,
            )

        result = {
            "handoverId": hid,
            "status": action,
            "eventTypes": event_types,
            "traceId": trace_id,
        }
        c.execute(
            "INSERT INTO handover_operations VALUES(?,?,?,?,?,?)",
            (operation_id, hid, action, operation_payload,
             json.dumps(result, ensure_ascii=False), transition_time),
        )
        c.commit()
    except ApiError:
        c.rollback()
        c.close()
        raise
    except Exception:
        c.rollback()
        c.close()
        raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "交接处理失败",
                       retryable=True, trace_id=trace_id) from None
    c.close()
    return result


@app.post("/api/v1/handovers/{hid}/confirm")
def confirm_handover(hid: str, body: HandoverDecision, request: Request, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return _decide_handover(hid, body, request, user, x_request_id, idempotency_key, "CONFIRMED")


@app.post("/api/v1/handovers/{hid}/reject")
def reject_handover(hid: str, body: HandoverDecision, request: Request, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return _decide_handover(hid, body, request, user, x_request_id, idempotency_key, "REJECTED")


@app.post("/api/v1/handovers/{hid}/cancel")
def cancel_handover(hid: str, body: HandoverDecision, request: Request, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    return _decide_handover(hid, body, request, user, x_request_id, idempotency_key, "CANCELLED")


@app.get("/api/v1/handovers/{hid}/timeline")
def handover_timeline(hid: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db()
    direct_handover = c.execute(
        "SELECT * FROM material_handovers WHERE id=?", (hid,)
    ).fetchone()
    if direct_handover:
        handovers = [direct_handover] if _handover_visible(c, direct_handover, user) else []
    else:
        handovers = c.execute(
            """SELECT h.*
                   FROM material_handovers h
                   LEFT JOIN material_work_items w ON w.id = h.work_item_id
                  WHERE h.work_item_id=? OR w.requirement_id=?
                  ORDER BY h.created_at, h.id""",
            (hid, hid),
        ).fetchall()
        handovers = [row for row in handovers if _handover_visible(c, row, user)]
    if not handovers:
        c.close()
        raise HTTPException(404, "交接不存在")
    handover_ids = [row["id"] for row in handovers]
    transfer_ids = {
        row["transfer_request_id"] for row in handovers if row["transfer_request_id"]
    }
    timeline_entity_ids = handover_ids + sorted(transfer_ids)
    placeholders = ",".join("?" for _ in timeline_entity_ids)
    rows = c.execute(
        f"SELECT * FROM audit_events WHERE entity_id IN ({placeholders}) ORDER BY server_time, id",
        timeline_entity_ids,
    ).fetchall()
    handover = handovers[-1]
    projection = c.execute(
        "SELECT * FROM material_work_item_projections WHERE work_item_id=?",
        (handover["work_item_id"],),
    ).fetchone()
    c.close()
    event_dicts = [_public_audit_event(r) for r in rows]
    latest_event_types = {
        event["event_type"] for event in event_dicts
        if event["entity_id"] == handover["id"]
    }
    workspace_status = projection["status_code"] if projection and projection["last_handover_id"] == handover["id"] else (
        "AT_STATION" if "MATERIAL_AT_STATION" in latest_event_types
        else "PICKED_UP" if "MATERIAL_PICKED_UP" in latest_event_types
        else handover["status"]
    )
    return {
        "handoverId": handover["id"],
        "workItemId": handover["work_item_id"],
        "status": handover["status"],
        "workspaceStatus": workspace_status,
        "items": event_dicts,
        "serverTime": now(),
    }


@app.get("/api/v1/transfer-requests/{rid}")
def get_transfer(rid: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db()
    row = c.execute("SELECT * FROM transfer_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        c.close()
        raise HTTPException(404, "申请不存在")
    if not _transfer_visibility(c, row, user):
        c.close()
        raise HTTPException(403, "无权查看该申请")
    result = _public_transfer(c, row, include_payload=True)
    c.close()
    return result


@app.post("/api/v1/transfer-requests/{rid}/execute")
def execute_transfer(
    rid: str,
    body: TransferAction,
    request: Request,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无执行权限", trace_id=trace_id)
    return _execute_transfer_items(
        rid,
        user,
        trace_id,
        operation_id=str(body.clientOperationId),
        operation_payload=body.model_dump_json(),
        request=request,
    )


@app.get("/api/v1/users")
def list_users(user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "ADMIN": raise HTTPException(403, "无用户管理权限")
    c = db(); rows = c.execute("SELECT id,username,display_name,role,active,must_change_password,created_at FROM users ORDER BY created_at").fetchall(); c.close(); return {"items": [dict(r) for r in rows]}


@app.get("/api/v1/audit-logs")
def audit_logs(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=100),
    from_: str | None = Query(default=None, alias="from", max_length=64),
    to_: str | None = Query(default=None, alias="to", max_length=64),
    eventType: str | None = Query(default=None, max_length=128),
    entityType: str | None = Query(default=None, max_length=128),
    operatorId: str | None = Query(default=None, max_length=128),
    action: str | None = Query(default=None, max_length=128),
    resourceType: str | None = Query(default=None, max_length=128),
    resourceId: str | None = Query(default=None, max_length=128),
    entityId: str | None = Query(default=None, max_length=128),
    user: sqlite3.Row = Depends(current_user),
) -> dict[str, Any]:
    """管理员全量审计查询；返回 audit_logs 与 audit_events 的脱敏统一视图。"""
    if user["role"] != "ADMIN":
        raise HTTPException(403, "无审计权限")

    def clean(value: str | None) -> str | None:
        value = value.strip() if value is not None else None
        return value or None

    def parse_time(value: str | None, label: str) -> str | None:
        value = clean(value)
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, f"{label} 必须是合法 ISO-8601 时间") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    start_time = parse_time(from_, "from")
    end_time = parse_time(to_, "to")
    if start_time and end_time and start_time > end_time:
        raise HTTPException(400, "from 不能晚于 to")

    event_filter = clean(eventType) or clean(action)
    entity_filter = clean(entityType) or clean(resourceType)
    operator_filter = clean(operatorId)
    resource_filter = clean(entityId) or clean(resourceId)
    feed = """
        SELECT 'AUDIT_LOG' AS record_type,
               id, action AS event_type, resource_type AS entity_type,
               resource_id AS entity_id, operator_id AS actor_user_id,
               role AS actor_role, request_id,
               NULL AS client_operation_id, NULL AS before_json,
               NULL AS after_json, occurred_at AS server_time,
               NULL AS device_id, NULL AS view_role, NULL AS preview_action, result
          FROM audit_logs
        UNION ALL
        SELECT 'AUDIT_EVENT' AS record_type,
               id, event_type, entity_type, entity_id, actor_user_id,
               actor_role, request_id, client_operation_id, before_json,
               after_json, server_time, device_id, view_role, action AS preview_action, result
          FROM audit_events
    """
    where = ["1=1"]
    args: list[Any] = []
    if event_filter:
        where.append("audit_feed.event_type=?")
        args.append(event_filter)
    if entity_filter:
        where.append("audit_feed.entity_type=?")
        args.append(entity_filter)
    if operator_filter:
        where.append("audit_feed.actor_user_id=?")
        args.append(operator_filter)
    if resource_filter:
        where.append("audit_feed.entity_id=?")
        args.append(resource_filter)
    if start_time:
        where.append("audit_feed.server_time>=?")
        args.append(start_time)
    if end_time:
        where.append("audit_feed.server_time<=?")
        args.append(end_time)
    where_sql = " AND ".join(where)
    c = db()
    try:
        total = c.execute(
            f"SELECT COUNT(*) AS count FROM ({feed}) audit_feed WHERE {where_sql}",
            args,
        ).fetchone()["count"]
        rows = c.execute(
            f"""SELECT audit_feed.*
                   FROM ({feed}) audit_feed
                  WHERE {where_sql}
                  ORDER BY audit_feed.server_time DESC,
                           audit_feed.record_type DESC,
                           audit_feed.id DESC
                  LIMIT ? OFFSET ?""",
            [*args, pageSize, (page - 1) * pageSize],
        ).fetchall()
    finally:
        c.close()

    items = []
    for row in rows:
        item = _public_audit_event(row)
        # 保留旧 audit_logs 客户端使用的字段别名；仍然不暴露 source_ip。
        item.update({
            "record_type": row["record_type"],
            "operator_id": row["actor_user_id"],
            "role": row["actor_role"],
            "action": row["event_type"],
            "resource_type": row["entity_type"],
            "resource_id": row["entity_id"],
            "occurred_at": row["server_time"],
            "view_role": row["view_role"],
            "preview_action": row["preview_action"],
        })
        if row["preview_action"]:
            item["action"] = row["preview_action"]
        items.append(item)
    return {
        "items": items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
        "totalPages": (total + pageSize - 1) // pageSize if total else 0,
        "serverTime": now(),
    }


def _write_trace_and_operation(x_request_id: str | None, idempotency_key: str | None, client_operation_id: uuid.UUID | None) -> tuple[str, str]:
    """统一校验写接口的可选幂等上下文，兼容旧客户端缺省请求头。"""
    trace_id = require_request_id(x_request_id) if x_request_id is not None else str(uuid.uuid4())
    operation = str(client_operation_id or uuid.uuid4())
    if idempotency_key is not None:
        require_idempotency_key(idempotency_key, operation, trace_id)
    return trace_id, operation


@app.post("/api/v1/location-bindings")
def bind_location(
    body: LocationBindingCreate,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if user["role"] not in {"MATERIAL", "WAREHOUSE_ADMIN", "ADMIN"}:
        raise HTTPException(403, "无库位绑定权限")
    trace_id, operation = _write_trace_and_operation(x_request_id, idempotency_key, body.clientOperationId)
    bid = "lb_" + uuid.UUID(operation).hex
    c = db()
    existing = c.execute("SELECT * FROM location_bindings WHERE id=?", (bid,)).fetchone()
    if existing:
        c.close()
        return {"bindingId": bid, "status": "BOUND", "idempotent": True, "serverTime": now()}
    material = c.execute("SELECT id FROM materials WHERE code=?", (body.materialCode,)).fetchone()
    location = c.execute("SELECT id FROM locations WHERE code=?", (body.locationCode,)).fetchone()
    if not material or not location:
        c.close(); raise HTTPException(404, "物料或库位不存在")
    evidence = json.dumps(body.evidenceIds, ensure_ascii=False)
    c.execute("INSERT INTO location_bindings VALUES(?,?,?,?,?,?,?)", (bid, material["id"], location["id"], body.quantity, evidence, user["id"], now()))
    audit(c, user["id"], user["role"], "BIND", "LOCATION", bid, request_id=trace_id)
    c.commit(); c.close()
    return {"bindingId": bid, "status": "BOUND", "serverTime": now()}


@app.post("/api/v1/stocktakes")
def create_stocktake(
    body: StocktakeCreate,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if user["role"] not in {"MATERIAL", "WAREHOUSE_ADMIN", "ADMIN"}:
        raise HTTPException(403, "无盘点创建权限")
    trace_id, operation = _write_trace_and_operation(x_request_id, idempotency_key, body.clientOperationId)
    sid = "st_" + uuid.UUID(operation).hex
    c = db()
    existing = c.execute("SELECT * FROM stocktakes WHERE id=?", (sid,)).fetchone()
    if existing:
        c.close(); return {"stocktakeId": sid, "status": existing["status"], "difference": existing["difference"], "idempotent": True, "serverTime": now()}
    material = c.execute("SELECT id,total_quantity FROM materials WHERE code=?", (body.materialCode,)).fetchone()
    if not material:
        c.close(); raise HTTPException(404, "物料不存在")
    book = material["total_quantity"] if body.bookQuantity is None else body.bookQuantity
    c.execute("INSERT INTO stocktakes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sid, material["id"], body.locationCode, book, body.actualQuantity, body.actualQuantity - book, "PENDING_CONFIRM", user["id"], now(), None, None))
    audit(c, user["id"], user["role"], "CREATE", "STOCKTAKE", sid, request_id=trace_id)
    c.commit(); c.close()
    return {"stocktakeId": sid, "status": "PENDING_CONFIRM", "difference": body.actualQuantity - book, "serverTime": now()}


@app.get("/api/v1/stocktakes")
def list_stocktakes(user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); rows = c.execute("SELECT s.*,m.code material_code FROM stocktakes s JOIN materials m ON m.id=s.material_id ORDER BY s.created_at DESC LIMIT 100").fetchall(); c.close(); return {"items": [dict(r) for r in rows]}


@app.post("/api/v1/stocktakes/{sid}/confirm")
def confirm_stocktake(
    sid: str,
    body: Decision = Decision(decision="CONFIRM"),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id) if x_request_id is not None else str(uuid.uuid4())
    operation = str(body.clientOperationId or uuid.uuid4())
    if idempotency_key is not None:
        require_idempotency_key(idempotency_key, body.clientOperationId or uuid.UUID(operation), trace_id)
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无盘点确认权限", trace_id=trace_id)
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM stocktake_operations WHERE client_operation_id=?", (operation,)).fetchone()
        payload = body.model_dump_json()
        if prior:
            if prior["stocktake_id"] != sid or _payload_digest(prior["payload_json"]) != _payload_digest(payload):
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"]); result.update(idempotent=True, traceId=trace_id)
            c.rollback(); c.close(); return result
        row = c.execute("SELECT * FROM stocktakes WHERE id=? AND status='PENDING_CONFIRM'", (sid,)).fetchone()
        if not row:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, "待确认盘点不存在或已处理", trace_id=trace_id)
        confirmed_at = now()
        c.execute("UPDATE stocktakes SET status='CONFIRMED',confirmed_by=?,confirmed_at=? WHERE id=?", (user["id"], confirmed_at, sid))
        audit(c, user["id"], user["role"], "CONFIRM", "STOCKTAKE", sid, request_id=trace_id)
        result = {"stocktakeId": sid, "status": "CONFIRMED", "traceId": trace_id}
        c.execute("INSERT INTO stocktake_operations VALUES(?,?,?,?,?,?)", (operation, sid, "CONFIRM", payload, json.dumps(result, ensure_ascii=False), confirmed_at))
        c.commit(); c.close(); return result
    except ApiError:
        c.rollback(); c.close(); raise
    except Exception:
        c.rollback(); c.close(); raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "盘点确认失败", retryable=True, trace_id=trace_id) from None


@app.post("/api/v1/exceptions")
def create_exception(
    body: ExceptionCreate,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if user["role"] not in {"OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "ADMIN", "WORKSHOP_SUPERVISOR"}:
        raise HTTPException(403, "无异常创建权限")
    trace_id, operation = _write_trace_and_operation(x_request_id, idempotency_key, body.clientOperationId)
    eid = "ex_" + uuid.UUID(operation).hex; c = db()
    existing = c.execute("SELECT * FROM exceptions WHERE id=?", (eid,)).fetchone()
    if existing:
        c.close(); return {"exceptionId": eid, "status": existing["status"], "difference": existing["difference"], "idempotent": True, "serverTime": now()}
    if not body.orderNo or not body.deviceId or not body.materialId:
        c.close(); raise HTTPException(400, "异常必须关联 orderNo、deviceId、materialId")
    if not c.execute("SELECT 1 FROM production_orders WHERE order_no=?", (body.orderNo,)).fetchone() or not c.execute("SELECT 1 FROM order_devices WHERE id=?", (body.deviceId,)).fetchone() or not c.execute("SELECT 1 FROM materials WHERE id=?", (body.materialId,)).fetchone():
        c.close(); raise HTTPException(400, "异常关联资源不存在")
    difference = body.actualQuantity - body.bookQuantity
    c.execute("INSERT INTO exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (eid, body.materialId, body.type, body.bookQuantity, body.actualQuantity, difference, "PENDING", body.description, json.dumps(body.evidenceIds), user["id"], now(), None, None, body.orderNo, body.deviceId))
    audit(c, user["id"], user["role"], "CREATE", "EXCEPTION", eid, request_id=trace_id); c.commit(); c.close()
    return {"exceptionId": eid, "status": "PENDING", "difference": difference, "serverTime": now()}


@app.get("/api/v1/exceptions")
def list_exceptions(user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); rows = c.execute("SELECT * FROM exceptions ORDER BY created_at DESC LIMIT 100").fetchall(); c.close(); return {"items": [dict(r) for r in rows]}


@app.post("/api/v1/exceptions/{eid}/review")
def review_exception(
    eid: str, body: Decision, request: Request,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id) if x_request_id is not None else str(uuid.uuid4())
    operation = str(body.clientOperationId or uuid.uuid4())
    if idempotency_key is not None:
        require_idempotency_key(idempotency_key, body.clientOperationId or uuid.UUID(operation), trace_id)
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无异常审批权限", trace_id=trace_id)
    if body.decision not in {"APPROVE", "REJECT"}:
        raise ApiError(400, CODE_VALIDATION_ERROR, "decision 无效", trace_id=trace_id)
    status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        payload = body.model_dump_json()
        prior = c.execute("SELECT * FROM exception_operations WHERE client_operation_id=?", (operation,)).fetchone()
        if prior:
            if prior["exception_id"] != eid or _payload_digest(prior["payload_json"]) != _payload_digest(payload):
                raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致", trace_id=trace_id)
            result = json.loads(prior["result_json"]); result.update(idempotent=True, traceId=trace_id)
            c.rollback(); c.close(); return result
        reviewed_at = now()
        cur = c.execute("UPDATE exceptions SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='PENDING'", (status, user["id"], reviewed_at, eid))
        if cur.rowcount != 1:
            raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, "异常状态不允许审批", trace_id=trace_id)
        audit(c, user["id"], user["role"], "REVIEW", "EXCEPTION", eid, request_id=trace_id)
        result = {"exceptionId": eid, "status": status, "traceId": trace_id}
        c.execute("INSERT INTO exception_operations VALUES(?,?,?,?,?,?)", (operation, eid, "REVIEW", payload, json.dumps(result, ensure_ascii=False), reviewed_at))
        c.commit(); c.close(); return result
    except ApiError:
        c.rollback(); c.close(); raise
    except Exception:
        c.rollback(); c.close(); raise ApiError(500, CODE_RETRYABLE_UPSTREAM_ERROR, "异常审批失败", retryable=True, trace_id=trace_id) from None


# Workshop assembly vertical slice routes
try:
    from .assembly_routes import register as _register_assembly_routes
except ImportError:
    from assembly_routes import register as _register_assembly_routes
_register_assembly_routes(app, db, now, current_user, _audit_event, ApiError)


@app.post("/api/v1/files")
def upload_file(purpose: str, file: UploadFile = File(...), user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    if purpose not in {"LOCATION", "EXCEPTION", "TRANSFER"}: raise HTTPException(400, "purpose 无效")
    suffix = Path(file.filename or "upload.bin").suffix.lower();
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}: raise HTTPException(400, "仅支持图片")
    fid = "file_" + uuid.uuid4().hex; target = UPLOAD_DIR / (fid + suffix); data = file.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024: raise HTTPException(413, "图片超过 5MB")
    target.write_bytes(data); return {"fileId": fid, "purpose": purpose, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
