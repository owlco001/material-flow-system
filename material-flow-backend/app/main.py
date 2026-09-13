from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel, Field

DATA_DIR = Path(os.environ.get("MATERIAL_FLOW_DATA", "/srv/material-flow/data"))
UPLOAD_DIR = Path(os.environ.get("MATERIAL_FLOW_UPLOADS", "/srv/material-flow/uploads"))
DB_PATH = DATA_DIR / "material_flow.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="物料流转系统 API", version="0.1.0")
bearer = HTTPBearer(auto_error=False)

# 密码哈希器：Argon2id，参数对齐 OWASP 2024 推荐（契约 A03）
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

# ==================== 契约常量 ====================
# 依据《物料流转系统-V1-API契约冻结补遗》，本文件覆盖旧文档中的冲突定义。

SCAN_TYPES = ("PRODUCTION_ORDER", "FLOW_NO", "MATERIAL_CODE", "LOCATION_CODE", "UNKNOWN")
# 禁止使用的历史枚举，收到即拒绝
FORBIDDEN_SCAN_TYPES = ("ORDER_NO", "LOGISTICS_NO", "ORDER", "LOGISTICS")

TRANSFER_TYPES = ("INBOUND", "OUTBOUND", "TRANSFER", "STOCKTAKE")

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


class ApiError(HTTPException):
    """统一错误结构：{"error": {code, message, retryable, traceId, details}}"""

    def __init__(self, status_code: int, code: str, message: str,
                 retryable: bool = False, trace_id: str = "", details: dict | None = None) -> None:
        self.code = code
        self.retryable = retryable
        self.trace_id = trace_id
        self.details = details or {}
        super().__init__(status_code=status_code, detail=message)


@app.exception_handler(ApiError)
async def api_error_handler(_request, exc: ApiError):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {
            "code": exc.code,
            "message": exc.detail,
            "retryable": exc.retryable,
            "traceId": exc.trace_id,
            **({"details": exc.details} if exc.details else {}),
        }},
    )


def require_request_id(x_request_id: str | None) -> str:
    """契约 2 节：X-Request-Id 缺失或非 UUID 时返回 400 INVALID_REQUEST_ID。"""
    if not x_request_id:
        raise ApiError(400, CODE_INVALID_REQUEST_ID, "缺少 X-Request-Id 请求头")
    try:
        uuid.UUID(x_request_id)
    except (ValueError, AttributeError):
        raise ApiError(400, CODE_INVALID_REQUEST_ID, "X-Request-Id 必须是合法 UUID") from None
    return x_request_id


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


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
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
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL, token_type TEXT NOT NULL DEFAULT 'ACCESS', device_id TEXT);
    -- 已消费的刷新令牌墓碑表：用于检测令牌重放。
    -- 若直接删除旧令牌，"令牌不存在" 与 "令牌被重放" 无法区分，
    -- 泄露检测就永远不会触发。故消费后写入墓碑，保留至自然过期。
    CREATE TABLE IF NOT EXISTS consumed_refresh_tokens(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, consumed_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, specification TEXT, unit TEXT NOT NULL, batch_no TEXT, expiry_date TEXT, total_quantity INTEGER NOT NULL DEFAULT 0, available_quantity INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS locations(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0), UNIQUE(material_id, location_id));
    CREATE TABLE IF NOT EXISTS transfer_requests(id TEXT PRIMARY KEY, client_operation_id TEXT UNIQUE NOT NULL, type TEXT NOT NULL, document_no TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT, approved_at TEXT, executed_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, operator_id TEXT, role TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT, request_id TEXT, occurred_at TEXT NOT NULL, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS exceptions(id TEXT PRIMARY KEY, material_id TEXT, type TEXT NOT NULL, book_quantity INTEGER NOT NULL DEFAULT 0, actual_quantity INTEGER NOT NULL DEFAULT 0, difference INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, description TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT);
    CREATE TABLE IF NOT EXISTS location_bindings(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0), evidence_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS stocktakes(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT, book_quantity INTEGER NOT NULL, actual_quantity INTEGER NOT NULL CHECK(actual_quantity >= 0), difference INTEGER NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_by TEXT, confirmed_at TEXT);
    CREATE TABLE IF NOT EXISTS production_orders(id TEXT PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, model_name TEXT, status TEXT NOT NULL DEFAULT 'IN_PROGRESS', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS order_material_requirements(id TEXT PRIMARY KEY, order_id TEXT NOT NULL, material_id TEXT NOT NULL, required_quantity INTEGER NOT NULL CHECK(required_quantity >= 0), arrived_quantity INTEGER NOT NULL DEFAULT 0 CHECK(arrived_quantity >= 0), UNIQUE(order_id, material_id));
    CREATE TABLE IF NOT EXISTS login_attempts(username TEXT PRIMARY KEY, failed_count INTEGER NOT NULL DEFAULT 0, first_failed_at INTEGER NOT NULL, locked_until INTEGER);
    """)
    if c.execute("SELECT 1 FROM users WHERE username='owlco'").fetchone() is None:
        password = os.environ.get("INITIAL_ADMIN_PASSWORD")
        if not password:
            raise RuntimeError("INITIAL_ADMIN_PASSWORD is required on first startup")
        c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", ("u_admin", "owlco", "系统管理员", "ADMIN", hash_password(password), 1, 1, now()))
    if c.execute("SELECT 1 FROM materials").fetchone() is None:
        c.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?,?,?)", ("mat_001", "MTR-001", "工业轴承", "6205-2RS", "件", "B20260912", None, 986, 986, 1))
        c.execute("INSERT INTO locations VALUES(?,?,?)", ("loc_001", "A-01-03", "一号库位"))
        c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("inv_001", "mat_001", "loc_001", 986))
    if c.execute("SELECT 1 FROM production_orders").fetchone() is None:
        c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?)", ("ord_001", "SO202609120001", "MS-300", "IN_PROGRESS", now()))
        c.execute("INSERT INTO order_material_requirements VALUES(?,?,?,?,?)", ("omr_001", "ord_001", "mat_001", 200, 200))

    _migrate_schema(c)
    c.commit(); c.close()


def _migrate_schema(c: sqlite3.Connection) -> None:
    """幂等的增量迁移。

    历史库的 sessions 表只有 (token, user_id, expires_at) 三列，
    引入 refresh token 后需要补 token_type 与 device_id。
    SQLite 不支持 ADD COLUMN IF NOT EXISTS，故先查 PRAGMA 再补。
    """
    existing = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
    if "token_type" not in existing:
        c.execute("ALTER TABLE sessions ADD COLUMN token_type TEXT NOT NULL DEFAULT 'ACCESS'")
    if "device_id" not in existing:
        c.execute("ALTER TABLE sessions ADD COLUMN device_id TEXT")


@app.on_event("startup")
def startup() -> None:
    init_db()


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> sqlite3.Row:
    if not credentials:
        raise HTTPException(401, "未登录")
    c = db()
    row = c.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token=? AND s.expires_at>? AND u.active=1 "
        # 必须限定 ACCESS：否则长效 refresh token 可当 access token 使用，
        # 短时效设计形同虚设，泄露后危害窗口被放大到 30 天。
        "AND s.token_type='ACCESS'",
        (credentials.credentials, int(time.time())),
    ).fetchone()
    c.close()
    if not row:
        raise HTTPException(401, "会话已失效")
    return row


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
    username: str
    password: str
    deviceId: str = Field(min_length=1, max_length=128)
    clientVersion: str = "0.1.0"


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


class Decision(BaseModel):
    decision: str
    comment: str = ""


@app.get("/healthz")
def health() -> dict[str, str]: return {"status": "ok", "service": "material-flow", "serverTime": now()}


@app.post("/api/v1/auth/login")
def login(body: Login, x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    """登录。

    契约 A03 —— 失败锁定：同一账号 15 分钟内累计 5 次失败即锁定 15 分钟。
    锁定期间即使密码正确也拒绝，避免离线暴力破解。

    Argon2id 迁移：历史 scrypt 哈希在校验通过后就地升级（见 check_password）。
    """
    c = db()
    username = body.username
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
    if re.match(r"^MTR-[A-Z0-9-]+$", upper):
        typ = "MATERIAL_CODE"
    elif re.match(r"^(SO|PO)\d+$", upper):
        typ = "PRODUCTION_ORDER"
    elif re.match(r"^[A-Z]-\d{2}-\d{2}$", upper):
        typ = "LOCATION_CODE"
    elif re.match(r"^FL\d+$", upper):
        typ = "FLOW_NO"
    if typ != "UNKNOWN":
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


@app.post("/api/v1/orders/material-status")
def material_status(
    body: dict[str, str],
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """生产订单物料状态。

    契约补遗 3.2：documentType 只允许 PRODUCTION_ORDER，收到 ORDER_NO 返回 400。
    物料清单按单据号关联的生产订单过滤，不再返回全量物料。
    """
    trace_id = x_request_id or ""
    document_no = (body.get("documentNo") or "").strip()
    document_type = (body.get("documentType") or "").strip().upper()

    if document_type in FORBIDDEN_SCAN_TYPES:
        raise ApiError(
            400, CODE_INVALID_SCAN_TYPE,
            f"documentType={document_type} 已作废，请使用 PRODUCTION_ORDER",
            trace_id=trace_id,
        )
    if document_type != "PRODUCTION_ORDER":
        raise ApiError(
            400, CODE_VALIDATION_ERROR,
            "documentType 必须为 PRODUCTION_ORDER", trace_id=trace_id,
        )
    if not document_no:
        raise ApiError(400, CODE_VALIDATION_ERROR, "documentNo 不能为空", trace_id=trace_id)

    c = db()
    order = c.execute(
        "SELECT id FROM production_orders WHERE order_no=?", (document_no,)
    ).fetchone()
    if not order:
        c.close()
        # 订单不存在时返回空清单，而非全量物料，避免前端误显示
        return {
            "documentNo": document_no,
            "documentType": "PRODUCTION_ORDER",
            "items": [],
            "serverTime": now(),
            "traceId": trace_id,
        }

    rows = c.execute(
        """
        SELECT m.*, r.required_quantity, r.arrived_quantity
        FROM order_material_requirements r
        JOIN materials m ON m.id = r.material_id
        WHERE r.order_id = ?
        ORDER BY m.code
        """,
        (order["id"],),
    ).fetchall()
    c.close()

    items = []
    for r in rows:
        available = r["available_quantity"]
        required = r["required_quantity"]
        arrived = r["arrived_quantity"]
        if available <= 0:
            status_code, label, color_token = "OUT_OF_STOCK", "缺货", "status-red"
        elif available < required:
            status_code, label, color_token = "ARRIVED", "部分到货", "status-yellow"
        else:
            status_code, label, color_token = "IN_STOCK", "在库", "status-green"
        items.append({
            "materialId": r["id"],
            "materialCode": r["code"],
            "name": r["name"],
            "requiredQuantity": required,
            "arrivedQuantity": arrived,
            "inStockQuantity": available,
            "statusCode": status_code,
            "label": label,
            "colorToken": color_token,
        })

    return {
        "documentNo": document_no,
        "documentType": "PRODUCTION_ORDER",
        "items": items,
        "serverTime": now(),
        "traceId": trace_id,
    }


@app.post("/api/v1/transfer-requests")
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

    c = db()
    # 幂等：相同 clientOperationId 返回首次业务结果
    op_id_str = str(body.clientOperationId)
    old = c.execute(
        "SELECT id,status,payload_json FROM transfer_requests WHERE client_operation_id=?",
        (op_id_str,),
    ).fetchone()
    if old:
        payload_digest = _payload_digest(body.model_dump_json())
        stored_digest = _payload_digest(old["payload_json"])
        c.close()
        if payload_digest != stored_digest:
            # 同幂等键但 body 不同
            raise ApiError(409, CODE_IDEMPOTENCY_PAYLOAD_MISMATCH,
                           "相同幂等键的请求体不一致", trace_id=trace_id)
        return {"requestId": old["id"], "status": old["status"],
                "idempotent": True, "serverTime": now(), "traceId": trace_id}

    # 数量与库存版本前置校验（整数校验见 _validate_item）
    for item in body.items:
        _validate_item(item, trace_id)

    rid = "tr_" + uuid.uuid4().hex
    c.execute(
        "INSERT INTO transfer_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (rid, op_id_str, body.type, body.documentNo, "PENDING_APPROVAL",
         body.model_dump_json(), user["id"], now(), None, None, None),
    )
    audit(c, user["id"], user["role"], "CREATE", "TRANSFER_REQUEST", rid, "SUCCESS", trace_id)
    c.commit()
    c.close()
    status = "PENDING_APPROVAL"
    if body.type == "TRANSFER":
        # 契约 4.3：TRANSFER 由创建接口完成事务执行，不进入审批
        status = _execute_transfer_items(rid, user, trace_id)
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


def _execute_transfer_items(rid: str, user: sqlite3.Row, trace_id: str) -> str:
    """在单个事务内执行库存变更。

    契约 4.3 / 6 节：
      - 逐 item 校验 expectedInventoryVersion，任一冲突整单回滚
      - 同时更新 materials 汇总与 inventory 库位明细
      - 库存不足、库位不存在整单回滚
      - 已执行过的单据再次执行返回原结果，不重复扣减
    """
    c = db()
    row = c.execute("SELECT * FROM transfer_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        c.close()
        raise ApiError(404, CODE_VALIDATION_ERROR, "申请不存在", trace_id=trace_id)

    if row["status"] == "EXECUTED":
        c.close()
        return "EXECUTED"  # 幂等：不重复变更库存

    if row["status"] != "APPROVED" and row["type"] != "TRANSFER":
        c.close()
        raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, "申请尚未审批通过", trace_id=trace_id)

    # 审批人与执行人不能是同一用户（TRANSFER 无需审批，跳过）
    if row["approved_by"] and row["approved_by"] == user["id"]:
        c.close()
        raise ApiError(409, CODE_APPROVAL_EXECUTOR_SAME_USER,
                       "审批人与执行人不能是同一用户", trace_id=trace_id)

    payload = json.loads(row["payload_json"])
    try:
        c.execute("BEGIN IMMEDIATE")
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
                raise ApiError(400, CODE_VALIDATION_ERROR, f"物料不存在: {material_id}", trace_id=trace_id)

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

        c.execute(
            "UPDATE transfer_requests SET status='EXECUTED',executed_at=? WHERE id=?",
            (now(), rid),
        )
        audit(c, user["id"], user["role"], "EXECUTE", "TRANSFER_REQUEST", rid, "SUCCESS", trace_id)
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
    return "EXECUTED"


def _adjust_location_qty(c: sqlite3.Connection, material_id: str, location_code: str,
                         delta: int, trace_id: str) -> None:
    """按库位码调整 inventory 明细；库位不存在或数量不足则整单回滚。"""
    loc = c.execute("SELECT id FROM locations WHERE code=?", (location_code,)).fetchone()
    if not loc:
        raise ApiError(400, CODE_VALIDATION_ERROR,
                       f"库位不存在: {location_code}", trace_id=trace_id)
    existing = c.execute(
        "SELECT id,quantity FROM inventory WHERE material_id=? AND location_id=?",
        (material_id, loc["id"]),
    ).fetchone()
    if existing is None:
        if delta < 0:
            raise ApiError(409, CODE_INSUFFICIENT_INVENTORY,
                           f"库位 {location_code} 无可用库存", trace_id=trace_id)
        c.execute("INSERT INTO inventory VALUES(?,?,?,?)",
                  ("inv_" + uuid.uuid4().hex, material_id, loc["id"], delta))
        return
    new_qty = existing["quantity"] + delta
    if new_qty < 0:
        raise ApiError(409, CODE_INSUFFICIENT_INVENTORY,
                       f"库位 {location_code} 库存不足", trace_id=trace_id)
    c.execute("UPDATE inventory SET quantity=? WHERE id=?", (new_qty, existing["id"]))


@app.post("/api/v1/transfer-requests/{rid}/approve")
def approve(
    rid: str,
    body: Decision,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无审批权限", trace_id=trace_id)
    if body.decision not in {"APPROVE", "REJECT"}:
        raise ApiError(400, CODE_VALIDATION_ERROR, "decision 无效", trace_id=trace_id)
    # 契约 4.2：拒绝必须填写原因，长度 1-500
    if body.decision == "REJECT":
        comment = (body.comment or "").strip()
        if not 1 <= len(comment) <= 500:
            raise ApiError(400, CODE_VALIDATION_ERROR,
                           "拒绝时必须填写原因（1-500 字）", trace_id=trace_id)

    status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
    c = db()
    cur = c.execute(
        "UPDATE transfer_requests SET status=?,approved_by=?,approved_at=? "
        "WHERE id=? AND status='PENDING_APPROVAL'",
        (status, user["id"], now(), rid),
    )
    if cur.rowcount != 1:
        c.close()
        raise ApiError(409, CODE_TRANSFER_STATE_CONFLICT, "申请状态不允许审批", trace_id=trace_id)
    audit(c, user["id"], user["role"], "APPROVE", "TRANSFER_REQUEST", rid, "SUCCESS", trace_id)
    c.commit()
    c.close()
    return {"requestId": rid, "status": status, "serverTime": now(), "traceId": trace_id}


@app.get("/api/v1/transfer-requests")
def list_transfers(status: str | None = None, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); query = "SELECT id,client_operation_id,type,document_no,status,created_by,created_at,approved_by,approved_at,executed_at FROM transfer_requests"; args: list[Any] = []
    if status:
        query += " WHERE status=?"; args.append(status)
    rows = c.execute(query + " ORDER BY created_at DESC LIMIT 100", args).fetchall(); c.close()
    return {"items": [dict(r) for r in rows], "serverTime": now()}


@app.get("/api/v1/transfer-requests/{rid}")
def get_transfer(rid: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); row = c.execute("SELECT * FROM transfer_requests WHERE id=?", (rid,)).fetchone(); c.close()
    if not row: raise HTTPException(404, "申请不存在")
    return dict(row)


@app.post("/api/v1/transfer-requests/{rid}/execute")
def execute_transfer(
    rid: str,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = require_request_id(x_request_id)
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}:
        raise ApiError(403, CODE_FORBIDDEN, "无执行权限", trace_id=trace_id)
    status = _execute_transfer_items(rid, user, trace_id)
    return {"requestId": rid, "status": status, "serverTime": now(), "traceId": trace_id}


@app.get("/api/v1/users")
def list_users(user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "ADMIN": raise HTTPException(403, "无用户管理权限")
    c = db(); rows = c.execute("SELECT id,username,display_name,role,active,must_change_password,created_at FROM users ORDER BY created_at").fetchall(); c.close(); return {"items": [dict(r) for r in rows]}


@app.get("/api/v1/audit-logs")
def audit_logs(page: int = 1, pageSize: int = 50, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "ADMIN": raise HTTPException(403, "无审计权限")
    page = max(1, page); pageSize = min(100, max(1, pageSize)); c = db(); rows = c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?", (pageSize, (page - 1) * pageSize)).fetchall(); c.close(); return {"items": [dict(r) for r in rows], "page": page, "pageSize": pageSize}


@app.post("/api/v1/location-bindings")
def bind_location(body: dict[str, Any], user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    quantity = body.get("quantity", 0)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0: raise HTTPException(400, "数量必须是非负整数")
    c = db(); material = c.execute("SELECT id FROM materials WHERE code=?", (body.get("materialCode"),)).fetchone(); location = c.execute("SELECT id FROM locations WHERE code=?", (body.get("locationCode"),)).fetchone()
    if not material or not location: c.close(); raise HTTPException(404, "物料或库位不存在")
    bid = "lb_" + uuid.uuid4().hex; evidence = json.dumps(body.get("evidenceIds", []), ensure_ascii=False); c.execute("INSERT INTO location_bindings VALUES(?,?,?,?,?,?,?,?)", (bid, material["id"], location["id"], quantity, evidence, user["id"], now())); audit(c, user["id"], user["role"], "BIND", "LOCATION", bid); c.commit(); c.close(); return {"bindingId": bid, "status": "BOUND", "serverTime": now()}


@app.post("/api/v1/stocktakes")
def create_stocktake(body: dict[str, Any], user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    actual = body.get("actualQuantity")
    if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0: raise HTTPException(400, "实盘数量必须是非负整数")
    c = db(); material = c.execute("SELECT id,total_quantity FROM materials WHERE code=?", (body.get("materialCode"),)).fetchone()
    if not material: c.close(); raise HTTPException(404, "物料不存在")
    book = body.get("bookQuantity", material["total_quantity"]); sid = "st_" + uuid.uuid4().hex; c.execute("INSERT INTO stocktakes VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sid, material["id"], body.get("locationCode"), book, actual, actual - book, "PENDING_CONFIRM", user["id"], now(), None, None)); audit(c, user["id"], user["role"], "CREATE", "STOCKTAKE", sid); c.commit(); c.close(); return {"stocktakeId": sid, "status": "PENDING_CONFIRM", "difference": actual - book, "serverTime": now()}


@app.get("/api/v1/stocktakes")
def list_stocktakes(user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); rows = c.execute("SELECT s.*,m.code material_code FROM stocktakes s JOIN materials m ON m.id=s.material_id ORDER BY s.created_at DESC LIMIT 100").fetchall(); c.close(); return {"items": [dict(r) for r in rows]}


@app.post("/api/v1/stocktakes/{sid}/confirm")
def confirm_stocktake(sid: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, str]:
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}: raise HTTPException(403, "无盘点确认权限")
    c = db(); row = c.execute("SELECT * FROM stocktakes WHERE id=? AND status='PENDING_CONFIRM'", (sid,)).fetchone()
    if not row: c.close(); raise HTTPException(404, "待确认盘点不存在")
    c.execute("UPDATE stocktakes SET status='CONFIRMED',confirmed_by=?,confirmed_at=? WHERE id=?", (user["id"], now(), sid)); audit(c, user["id"], user["role"], "CONFIRM", "STOCKTAKE", sid); c.commit(); c.close(); return {"stocktakeId": sid, "status": "CONFIRMED"}


@app.post("/api/v1/exceptions")
def create_exception(body: dict[str, Any], user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    actual = body.get("actualQuantity", 0); book = body.get("bookQuantity", 0)
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (actual, book)): raise HTTPException(400, "数量必须是非负整数")
    eid = "ex_" + uuid.uuid4().hex; c = db(); c.execute("INSERT INTO exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (eid, body.get("materialId"), body.get("type", "OTHER"), book, actual, actual - book, "PENDING", body.get("description"), json.dumps(body.get("evidenceIds", [])), user["id"], now(), None, None)); audit(c, user["id"], user["role"], "CREATE", "EXCEPTION", eid); c.commit(); c.close(); return {"exceptionId": eid, "status": "PENDING", "difference": actual - book, "serverTime": now()}


@app.get("/api/v1/exceptions")
def list_exceptions(user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); rows = c.execute("SELECT * FROM exceptions ORDER BY created_at DESC LIMIT 100").fetchall(); c.close(); return {"items": [dict(r) for r in rows]}


@app.post("/api/v1/exceptions/{eid}/review")
def review_exception(eid: str, body: Decision, user: sqlite3.Row = Depends(current_user)) -> dict[str, str]:
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}: raise HTTPException(403, "无异常审批权限")
    if body.decision not in {"APPROVE", "REJECT"}: raise HTTPException(400, "decision 无效")
    status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"; c = db(); cur = c.execute("UPDATE exceptions SET status=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='PENDING'", (status, user["id"], now(), eid))
    if cur.rowcount != 1: c.close(); raise HTTPException(409, "异常状态不允许审批")
    audit(c, user["id"], user["role"], "REVIEW", "EXCEPTION", eid); c.commit(); c.close(); return {"exceptionId": eid, "status": status}


@app.post("/api/v1/files")
def upload_file(purpose: str, file: UploadFile = File(...), user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    if purpose not in {"LOCATION", "EXCEPTION", "TRANSFER"}: raise HTTPException(400, "purpose 无效")
    suffix = Path(file.filename or "upload.bin").suffix.lower();
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}: raise HTTPException(400, "仅支持图片")
    fid = "file_" + uuid.uuid4().hex; target = UPLOAD_DIR / (fid + suffix); data = file.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024: raise HTTPException(413, "图片超过 5MB")
    target.write_bytes(data); return {"fileId": fid, "purpose": purpose, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
