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

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

DATA_DIR = Path(os.environ.get("MATERIAL_FLOW_DATA", "/srv/material-flow/data"))
UPLOAD_DIR = Path(os.environ.get("MATERIAL_FLOW_UPLOADS", "/srv/material-flow/uploads"))
DB_PATH = DATA_DIR / "material_flow.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="物料流转系统 API", version="0.1.0")
bearer = HTTPBearer(auto_error=False)
password_hasher = PasswordHasher()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        return password_hasher.hash(password)
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, encoded: str) -> bool:
    if encoded.startswith("$argon2id$"):
        try:
            return password_hasher.verify(encoded, password)
        except VerifyMismatchError:
            return False
    _, salt_hex, digest_hex = encoded.split("$", 2)
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(actual.hex(), digest_hex)


def audit(c: sqlite3.Connection, user_id: str | None, role: str | None, action: str, resource: str, resource_id: str | None, result: str = "SUCCESS", request_id: str = "") -> None:
    c.execute("INSERT INTO audit_logs(operator_id, role, action, resource_type, resource_id, request_id, occurred_at, result) VALUES(?,?,?,?,?,?,?,?)", (user_id, role, action, resource, resource_id, request_id, now(), result))


def init_db() -> None:
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, must_change_password INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS setup_initializations(idempotency_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, specification TEXT, unit TEXT NOT NULL, batch_no TEXT, expiry_date TEXT, total_quantity INTEGER NOT NULL DEFAULT 0, available_quantity INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS locations(id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS inventory(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0), UNIQUE(material_id, location_id));
    CREATE TABLE IF NOT EXISTS transfer_requests(id TEXT PRIMARY KEY, client_operation_id TEXT UNIQUE NOT NULL, type TEXT NOT NULL, document_no TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT, approved_at TEXT, executed_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, operator_id TEXT, role TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT, request_id TEXT, occurred_at TEXT NOT NULL, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS exceptions(id TEXT PRIMARY KEY, material_id TEXT, type TEXT NOT NULL, book_quantity INTEGER NOT NULL DEFAULT 0, actual_quantity INTEGER NOT NULL DEFAULT 0, difference INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, description TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT);
    CREATE TABLE IF NOT EXISTS location_bindings(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity >= 0), evidence_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS stocktakes(id TEXT PRIMARY KEY, material_id TEXT NOT NULL, location_id TEXT, book_quantity INTEGER NOT NULL, actual_quantity INTEGER NOT NULL CHECK(actual_quantity >= 0), difference INTEGER NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_by TEXT, confirmed_at TEXT);
    CREATE TABLE IF NOT EXISTS production_orders(id TEXT PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, product_name TEXT NOT NULL, planned_quantity INTEGER NOT NULL CHECK(planned_quantity >= 0), planned_delivery_date TEXT, status TEXT NOT NULL DEFAULT 'RELEASED', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS production_order_models(id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES production_orders(id), model_code TEXT NOT NULL, model_name TEXT NOT NULL, planned_quantity INTEGER NOT NULL CHECK(planned_quantity >= 0), status TEXT NOT NULL DEFAULT 'INTERNAL_PROCESSING', UNIQUE(order_id, model_code));
    CREATE TABLE IF NOT EXISTS material_requirements(id TEXT PRIMARY KEY, order_model_id TEXT NOT NULL REFERENCES production_order_models(id), material_id TEXT NOT NULL, required_quantity INTEGER NOT NULL CHECK(required_quantity > 0), arrived_quantity INTEGER NOT NULL DEFAULT 0, in_stock_quantity INTEGER NOT NULL DEFAULT 0, issued_quantity INTEGER NOT NULL DEFAULT 0);
    """)
    if c.execute("SELECT 1 FROM users WHERE username='owlco'").fetchone() is None:
        password = os.environ.get("INITIAL_ADMIN_PASSWORD")
        if password:
            user_hash = hash_password(password)
            c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", ("u_admin", "owlco", "系统管理员", "ADMIN", user_hash, 1, 1, now()))
    if c.execute("SELECT 1 FROM materials").fetchone() is None:
        c.execute("INSERT INTO materials VALUES(?,?,?,?,?,?,?,?,?,?)", ("mat_001", "MTR-001", "工业轴承", "6205-2RS", "件", "B20260912", None, 986, 986, 1))
        c.execute("INSERT INTO locations VALUES(?,?,?)", ("loc_001", "A-01-03", "一号库位"))
        c.execute("INSERT INTO inventory VALUES(?,?,?,?)", ("inv_001", "mat_001", "loc_001", 986))
    production_order_seed(c)
    c.commit(); c.close()


def production_order_seed(c: sqlite3.Connection) -> None:
    """仅当 production_orders 为空时注入确定性演示数据（固定 id，幂等）。"""
    if c.execute("SELECT 1 FROM production_orders").fetchone() is not None:
        return
    ts = now()
    c.execute("INSERT INTO production_orders VALUES(?,?,?,?,?,?,?,?)", ("ord_001", "SO20260919", "工业轴承总成", 200, "2026-10-05", "RELEASED", ts, ts))
    c.execute("INSERT INTO production_order_models VALUES(?,?,?,?,?,?)", ("pom_001", "ord_001", "BDX-6205", "6205-2RS 轴承", 200, "INTERNAL_PROCESSING"))
    c.execute("INSERT INTO material_requirements VALUES(?,?,?,?,?,?,?)", ("mr_001", "pom_001", "mat_001", 200, 986, 986, 0))


@app.on_event("startup")
def startup() -> None:
    init_db()


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> sqlite3.Row:
    if not credentials:
        raise HTTPException(401, "未登录")
    c = db(); row = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires_at>? AND u.active=1", (credentials.credentials, int(time.time()))).fetchone(); c.close()
    if not row: raise HTTPException(401, "会话已失效")
    if row["must_change_password"] and request.url.path != "/api/v1/auth/change-password":
        raise HTTPException(403, {"code": "PASSWORD_CHANGE_REQUIRED", "message": "首次登录必须修改密码", "traceId": request.headers.get("x-request-id", ""), "retryable": False, "details": {}})
    return row


class Login(BaseModel):
    username: str
    password: str
    deviceId: str = Field(min_length=1, max_length=128)
    clientVersion: str = "0.1.0"


class SetupAdmin(BaseModel):
    password: str = Field(min_length=12, max_length=256)
    confirmPassword: str = Field(min_length=12, max_length=256)
    idempotencyKey: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}$", min_length=8, max_length=128)


class ChangePassword(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=12, max_length=256)
    confirmPassword: str = Field(min_length=12, max_length=256)


def setup_error(code: str, message: str, status: int, request_id: str = "") -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, "traceId": request_id, "retryable": False, "details": {}})


class Scan(BaseModel):
    rawValue: str = Field(min_length=1, max_length=128)
    clientOperationId: str = Field(min_length=1, max_length=128)


class Transfer(BaseModel):
    clientOperationId: str = Field(min_length=1, max_length=128)
    type: str
    documentNo: str | None = None
    items: list[dict[str, Any]]
    remark: str | None = None
    evidenceIds: list[str] = []


class Decision(BaseModel):
    decision: str
    comment: str = ""


@app.get("/healthz")
def health() -> dict[str, str]: return {"status": "ok", "service": "material-flow", "serverTime": now()}


@app.get("/api/v1/setup/status")
def setup_status() -> dict[str, Any]:
    c = db(); initialized = c.execute("SELECT 1 FROM users WHERE username='owlco' AND active=1").fetchone() is not None; c.close()
    return {"initialized": initialized, "username": "owlco"}


@app.post("/api/v1/setup/initialize-admin")
def initialize_admin(body: SetupAdmin, response: Response, x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    request_id = x_request_id or ""
    if body.password != body.confirmPassword:
        raise setup_error("PASSWORD_CONFIRMATION_MISMATCH", "两次密码不一致", 400, request_id)
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute("SELECT user_id,password_hash FROM setup_initializations WHERE idempotency_key=?", (body.idempotencyKey,)).fetchone()
        if old:
            if not check_password(body.password, old["password_hash"]):
                audit(c, None, None, "INITIALIZE_ADMIN", "SETUP", body.idempotencyKey, "FAILED", request_id); c.commit()
                raise setup_error("IDEMPOTENCY_PAYLOAD_MISMATCH", "幂等键对应的请求内容不同", 409, request_id)
            audit(c, old["user_id"], "ADMIN", "INITIALIZE_ADMIN", "SETUP", body.idempotencyKey, "SUCCESS", request_id); c.commit()
            response.status_code = 200
            return {"initialized": True, "username": "owlco", "mustChangePassword": True, "idempotent": True}
        if c.execute("SELECT 1 FROM users WHERE username='owlco'").fetchone():
            audit(c, None, None, "INITIALIZE_ADMIN", "SETUP", body.idempotencyKey, "FAILED", request_id); c.commit()
            raise setup_error("ALREADY_INITIALIZED", "管理员已初始化", 409, request_id)
        user_id = "u_admin"; password_hash = hash_password(body.password)
        response.status_code = 201
        c.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (user_id, "owlco", "系统管理员", "ADMIN", password_hash, 1, 1, now()))
        c.execute("INSERT INTO setup_initializations VALUES(?,?,?,?)", (body.idempotencyKey, user_id, password_hash, now()))
        audit(c, user_id, "ADMIN", "INITIALIZE_ADMIN", "SETUP", body.idempotencyKey, "SUCCESS", request_id); c.commit()
        return {"initialized": True, "username": "owlco", "mustChangePassword": True}
    except HTTPException:
        c.rollback(); raise
    finally:
        c.close()


@app.post("/api/v1/auth/login")
def login(body: Login, x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    c = db(); user = c.execute("SELECT * FROM users WHERE username=? AND active=1", (body.username,)).fetchone()
    if not user or not check_password(body.password, user["password_hash"]):
        audit(c, user["id"] if user else None, user["role"] if user else None, "LOGIN", "USER", user["id"] if user else None, "FAILED", x_request_id or ""); c.commit(); c.close(); raise HTTPException(401, "账号或密码错误")
    token = secrets.token_urlsafe(32); expires = int(time.time()) + 86400
    c.execute("INSERT INTO sessions VALUES(?,?,?)", (token, user["id"], expires)); audit(c, user["id"], user["role"], "LOGIN", "USER", user["id"], "SUCCESS", x_request_id or ""); c.commit(); c.close()
    return {"accessToken": token, "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat(), "mustChangePassword": bool(user["must_change_password"]), "user": {"id": user["id"], "username": user["username"], "displayName": user["display_name"], "role": user["role"]}}


@app.post("/api/v1/auth/change-password")
def change_password(body: ChangePassword, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None)) -> dict[str, bool]:
    if body.newPassword != body.confirmPassword:
        raise setup_error("PASSWORD_CONFIRMATION_MISMATCH", "两次密码不一致", 400, x_request_id or "")
    if not check_password(body.currentPassword, user["password_hash"]):
        raise setup_error("CURRENT_PASSWORD_INVALID", "当前密码错误", 401, x_request_id or "")
    c = db(); c.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?", (hash_password(body.newPassword), user["id"])); audit(c, user["id"], user["role"], "CHANGE_PASSWORD", "USER", user["id"], "SUCCESS", x_request_id or ""); c.commit(); c.close()
    return {"success": True, "mustChangePassword": False}


@app.post("/api/v1/scan/resolve")
def resolve_scan(body: Scan, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    """扫码类型识别。

    依据《厂内流转业务模型更正》：
      - 支持生产订单号 / 料号 / 库位码 / 流转单号；
      - 「物流号（LOGISTICS_NO）」概念已作废，不再识别 WL / LOG 前缀；
      - 首版采用前缀+正则配置，规则不硬编码到客户端，由服务端最终判定。
    """
    raw = body.rawValue.strip().upper()
    typ = "UNKNOWN"
    resource_id = raw
    if re.match(r"^MTR-[A-Z0-9-]+$", raw):
        typ = "MATERIAL_CODE"
    elif re.match(r"^(SO|PO)\d+$", raw):
        typ = "PRODUCTION_ORDER"
    elif re.match(r"^[A-Z]-\d{2}-\d{2}$", raw):
        typ = "LOCATION_CODE"
    elif re.match(r"^FL\d+$", raw):
        typ = "FLOW_RECORD"
    return {"type": typ, "normalizedValue": raw, "resourceId": resource_id}


@app.get("/api/v1/materials/{code}/inventory")
def inventory(code: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); m = c.execute("SELECT * FROM materials WHERE code=?", (code,)).fetchone()
    if not m: c.close(); raise HTTPException(404, "物料不存在")
    rows = c.execute("SELECT l.code locationCode, i.quantity FROM inventory i JOIN locations l ON l.id=i.location_id WHERE i.material_id=?", (m["id"],)).fetchall(); c.close()
    return {"material": {"id": m["id"], "code": m["code"], "name": m["name"], "specification": m["specification"], "unit": m["unit"], "batchNo": m["batch_no"], "expiryDate": m["expiry_date"]}, "inventory": {"totalQuantity": m["total_quantity"], "availableQuantity": m["available_quantity"], "reservedQuantity": 0, "locations": [dict(r) for r in rows]}, "version": m["version"]}


@app.post("/api/v1/orders/material-status")
def material_status(body: dict[str, str], user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db(); rows = c.execute("SELECT * FROM materials ORDER BY code").fetchall(); c.close()
    return {"documentNo": body.get("documentNo", ""), "documentType": body.get("documentType", "ORDER_NO"), "items": [{"materialId": r["id"], "materialCode": r["code"], "name": r["name"], "requiredQuantity": 0, "arrivedQuantity": 0, "inStockQuantity": r["available_quantity"], "statusCode": "IN_STOCK" if r["available_quantity"] > 0 else "OUT_OF_STOCK", "label": "在库" if r["available_quantity"] > 0 else "缺货", "colorToken": "status-green" if r["available_quantity"] > 0 else "status-red"} for r in rows], "serverTime": now()}


@app.post("/api/v1/transfer-requests")
def create_transfer(body: Transfer, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    if body.type not in {"INBOUND", "OUTBOUND"}: raise HTTPException(400, "V1 仅支持入库或出库申请")
    c = db(); old = c.execute("SELECT id,status FROM transfer_requests WHERE client_operation_id=?", (body.clientOperationId,)).fetchone()
    if old: c.close(); return {"requestId": old["id"], "status": old["status"], "idempotent": True}
    rid = "tr_" + uuid.uuid4().hex; c.execute("INSERT INTO transfer_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid, body.clientOperationId, body.type, body.documentNo, "PENDING_APPROVAL", body.model_dump_json(), user["id"], now(), None, None, None)); audit(c, user["id"], user["role"], "CREATE", "TRANSFER_REQUEST", rid, "SUCCESS", x_request_id or ""); c.commit(); c.close(); return {"requestId": rid, "status": "PENDING_APPROVAL", "serverTime": now()}


@app.post("/api/v1/transfer-requests/{rid}/approve")
def approve(rid: str, body: Decision, user: sqlite3.Row = Depends(current_user)) -> dict[str, str]:
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}: raise HTTPException(403, "无审批权限")
    if body.decision not in {"APPROVE", "REJECT"}: raise HTTPException(400, "decision 无效")
    status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"; c = db(); cur = c.execute("UPDATE transfer_requests SET status=?,approved_by=?,approved_at=? WHERE id=? AND status='PENDING_APPROVAL'", (status, user["id"], now(), rid));
    if cur.rowcount != 1: c.close(); raise HTTPException(409, "申请状态不允许审批")
    audit(c, user["id"], user["role"], "APPROVE", "TRANSFER_REQUEST", rid); c.commit(); c.close(); return {"requestId": rid, "status": status}


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
def execute_transfer(rid: str, user: sqlite3.Row = Depends(current_user), x_request_id: str | None = Header(default=None)) -> dict[str, str]:
    if user["role"] not in {"ADMIN", "WAREHOUSE_ADMIN"}: raise HTTPException(403, "无执行权限")
    c = db(); row = c.execute("SELECT * FROM transfer_requests WHERE id=?", (rid,)).fetchone()
    if not row: c.close(); raise HTTPException(404, "申请不存在")
    if row["status"] != "APPROVED": c.close(); raise HTTPException(409, "申请尚未审批通过")
    payload = json.loads(row["payload_json"]); c.execute("BEGIN IMMEDIATE")
    for item in payload.get("items", []):
        material = c.execute("SELECT id,available_quantity,version FROM materials WHERE id=?", (item.get("materialId"),)).fetchone()
        quantity = item.get("quantity")
        if not material or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0: c.rollback(); c.close(); raise HTTPException(400, "物料或数量无效")
        if row["type"] == "OUTBOUND" and material["available_quantity"] < quantity: c.rollback(); c.close(); raise HTTPException(409, "库存不足")
        delta = quantity if row["type"] == "INBOUND" else -quantity
        c.execute("UPDATE materials SET total_quantity=total_quantity+?, available_quantity=available_quantity+?, version=version+1 WHERE id=? AND available_quantity+? >= 0", (delta, delta, material["id"], delta))
        if c.total_changes < 1: c.rollback(); c.close(); raise HTTPException(409, "库存版本或库存数量冲突")
    c.execute("UPDATE transfer_requests SET status='EXECUTED',executed_at=? WHERE id=? AND status='APPROVED'", (now(), rid)); audit(c, user["id"], user["role"], "EXECUTE", "TRANSFER_REQUEST", rid, "SUCCESS", x_request_id or ""); c.commit(); c.close(); return {"requestId": rid, "status": "EXECUTED"}


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


def _model_requirements(order_model_id: str, c: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = c.execute("""
    SELECT mr.id, mr.material_id, mr.required_quantity, mr.arrived_quantity, mr.in_stock_quantity, mr.issued_quantity,
           m.code material_code, m.name material_name, m.specification, m.unit, m.batch_no
    FROM material_requirements mr LEFT JOIN materials m ON m.id=mr.material_id
    WHERE mr.order_model_id=? ORDER BY mr.id
    """, (order_model_id,)).fetchall()
    result = []
    for r in rows:
        available = max(0, r["in_stock_quantity"] - r["issued_quantity"])
        shortage = max(0, r["required_quantity"] - available)
        if shortage > 0:
            status_code, label, color = "SHORTAGE", "缺货", "status-red"
        elif available < r["required_quantity"]:
            status_code, label, color = "IN_PROCESS", "部分可用", "status-yellow"
        else:
            status_code, label, color = "AVAILABLE", "齐套", "status-green"
        result.append({
            "materialId": r["material_id"], "materialCode": r["material_code"], "materialName": r["material_name"],
            "specification": r["specification"], "unit": r["unit"], "batchNo": r["batch_no"],
            "requiredQuantity": r["required_quantity"], "arrivedQuantity": r["arrived_quantity"],
            "inStockQuantity": r["in_stock_quantity"], "issuedQuantity": r["issued_quantity"],
            "availableQuantity": available, "shortageQuantity": shortage,
            "statusCode": status_code, "label": label, "colorToken": color,
        })
    return result


@app.get("/api/v1/production-orders")
def list_production_orders(page: int = 1, pageSize: int = 20, keyword: str | None = None, status: str | None = None, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    page = max(1, page); pageSize = min(100, max(1, pageSize))
    c = db()
    where: list[str] = []; args: list[Any] = []
    if keyword:
        like = f"%{keyword}%"
        where.append("(o.order_no LIKE ? OR o.product_name LIKE ?)"); args.extend([like, like])
    if status:
        where.append("o.status = ?"); args.append(status)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = c.execute(f"SELECT COUNT(*) FROM production_orders o{where_sql}", args).fetchone()[0]
    rows = c.execute(
        f"SELECT o.id, o.order_no, o.product_name, o.planned_quantity, o.planned_delivery_date, o.status, o.created_at, o.updated_at FROM production_orders o{where_sql} ORDER BY o.order_no LIMIT ? OFFSET ?",
        args + [pageSize, (page - 1) * pageSize]).fetchall()
    items = []
    for o in rows:
        model_count = c.execute("SELECT COUNT(*) FROM production_order_models WHERE order_id=?", (o["id"],)).fetchone()[0]
        req_rows = c.execute("""
            SELECT mr.required_quantity, mr.in_stock_quantity, mr.issued_quantity, m.id material_id
            FROM material_requirements mr JOIN production_order_models m ON m.id=mr.order_model_id
            WHERE m.order_id=?
        """, (o["id"],)).fetchall()
        available_map: dict[str, int] = {}
        total_required = 0
        for rr in req_rows:
            total_required += rr["required_quantity"]
        # each material contributes its available quantity toward total available; shortage when available < required
        shortage_count = 0
        for rr in req_rows:
            avail = max(0, rr["in_stock_quantity"] - rr["issued_quantity"])
            if avail < rr["required_quantity"]:
                shortage_count += 1
            available_map[rr["material_id"]] = avail
        available = sum(available_map.values())
        completion = min(100, int(available * 100 / total_required)) if total_required else 100
        last_flow_at = c.execute("SELECT MAX(created_at) FROM transfer_requests WHERE document_no=?", (o["order_no"],)).fetchone()[0]
        items.append({
            "orderNo": o["order_no"], "productName": o["product_name"], "plannedQuantity": o["planned_quantity"],
            "plannedDeliveryDate": o["planned_delivery_date"], "status": o["status"], "modelCount": model_count,
            "materialCompletionRate": completion, "shortageCount": shortage_count, "lastFlowAt": last_flow_at,
            "createdAt": o["created_at"], "updatedAt": o["updated_at"],
        })
    c.close()
    return {"items": items, "page": page, "pageSize": pageSize, "total": total, "serverTime": now()}


@app.get("/api/v1/production-orders/{orderNo}")
def get_production_order_detail(orderNo: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db()
    order = c.execute("SELECT * FROM production_orders WHERE order_no=?", (orderNo,)).fetchone()
    if not order:
        c.close(); raise HTTPException(404, "生产订单不存在")
    model_rows = c.execute("SELECT * FROM production_order_models WHERE order_id=? ORDER BY model_code", (order["id"],)).fetchall()
    models = []
    for m in model_rows:
        reqs = c.execute("SELECT * FROM material_requirements WHERE order_model_id=?", (m["id"],)).fetchall()
        shortage = sum(1 for r in reqs if max(0, r["in_stock_quantity"] - r["issued_quantity"]) < r["required_quantity"])
        required_count = len(reqs)
        available_total = sum(max(0, r["in_stock_quantity"] - r["issued_quantity"]) for r in reqs)
        required_total = sum(r["required_quantity"] for r in reqs)
        models.append({
            "modelCode": m["model_code"], "modelName": m["model_name"], "plannedQuantity": m["planned_quantity"],
            "status": m["status"], "requiredMaterialCount": required_count, "shortageMaterialCount": shortage,
            "completionRate": min(100, int(available_total * 100 / required_total)) if required_total else 100,
        })
    c.close()
    return {"order": {
        "orderNo": order["order_no"], "productName": order["product_name"], "plannedQuantity": order["planned_quantity"],
        "plannedDeliveryDate": order["planned_delivery_date"], "status": order["status"],
        "createdAt": order["created_at"], "updatedAt": order["updated_at"],
    }, "models": models, "serverTime": now()}


@app.get("/api/v1/production-orders/{orderNo}/models/{modelCode}")
def get_model_detail(orderNo: str, modelCode: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = db()
    order = c.execute("SELECT id FROM production_orders WHERE order_no=?", (orderNo,)).fetchone()
    if not order:
        c.close(); raise HTTPException(404, "生产订单不存在")
    model = c.execute("SELECT * FROM production_order_models WHERE order_id=? AND model_code=?", (order["id"], modelCode)).fetchone()
    if not model:
        c.close(); raise HTTPException(404, "机型不存在")
    reqs = _model_requirements(model["id"], c)
    shortage = sum(1 for r in reqs if r["shortageQuantity"] > 0)
    available_total = sum(r["availableQuantity"] for r in reqs)
    required_total = sum(r["requiredQuantity"] for r in reqs)
    model_summary = {
        "modelCode": model["model_code"], "modelName": model["model_name"], "plannedQuantity": model["planned_quantity"],
        "status": model["status"], "requiredMaterialCount": len(reqs), "shortageMaterialCount": shortage,
        "completionRate": min(100, int(available_total * 100 / required_total)) if required_total else 100,
    }
    c.close()
    return {"model": model_summary, "requirements": reqs, "serverTime": now()}


@app.get("/api/v1/production-orders/{orderNo}/models/{modelCode}/flow-records")
def list_model_flow_records(orderNo: str, modelCode: str, page: int = 1, pageSize: int = 50, flowType: str | None = None, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    page = max(1, page); pageSize = min(100, max(1, pageSize))
    c = db()
    order = c.execute("SELECT id FROM production_orders WHERE order_no=?", (orderNo,)).fetchone()
    if not order:
        c.close(); raise HTTPException(404, "生产订单不存在")
    model = c.execute("SELECT id FROM production_order_models WHERE order_id=? AND model_code=?", (order["id"], modelCode)).fetchone()
    if not model:
        c.close(); raise HTTPException(404, "机型不存在")
    where = " WHERE t.document_no=?"
    args: list[Any] = [orderNo]
    if flowType:
        where += " AND t.type=?"
        args.append(flowType)
    total = c.execute(f"SELECT COUNT(*) FROM transfer_requests t{where}", args).fetchone()[0]
    rows = c.execute(
        f"SELECT t.id, t.document_no, t.type, t.status, t.created_by, t.created_at, t.approved_by, t.approved_at, t.executed_at, t.payload_json FROM transfer_requests t{where} ORDER BY t.created_at DESC LIMIT ? OFFSET ?",
        args + [pageSize, (page - 1) * pageSize]).fetchall()
    material_ids: list[str] = []
    for r in rows:
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        for item in payload.get("items", []):
            mid = item.get("materialId")
            if mid:
                material_ids.append(mid)
    code_map: dict[str, str] = {}
    if material_ids:
        placeholders = ",".join("?" for _ in material_ids)
        mrows = c.execute(f"SELECT id, code FROM materials WHERE id IN ({placeholders})", material_ids).fetchall()
        code_map = {r["id"]: r["code"] for r in mrows}
    items = []
    for r in rows:
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        items_list = payload.get("items", [])
        quantity_total = 0
        for it in items_list:
            q = it.get("quantity", 0)
            if isinstance(q, int) and not isinstance(q, bool):
                quantity_total += q
        mcs = [code_map.get(it.get("materialId"), "") for it in items_list]
        mcs = [x for x in mcs if x]
        deduped: list[str] = []
        for x in mcs:
            if x not in deduped:
                deduped.append(x)
        items.append({
            "flowNo": r["id"], "documentNo": r["document_no"], "type": r["type"], "quantityTotal": quantity_total,
            "status": r["status"], "createdBy": r["created_by"], "createdAt": r["created_at"],
            "approvedBy": r["approved_by"], "approvedAt": r["approved_at"], "executedAt": r["executed_at"],
            "materialCodes": deduped,
        })
    c.close()
    return {"items": items, "page": page, "pageSize": pageSize, "total": total, "serverTime": now()}


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
