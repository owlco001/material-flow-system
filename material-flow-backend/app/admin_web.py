"""Web 管理界面（SSR）。契约：docs/admin-web-contract.md。

路由挂载于 /admin/*；鉴权基于独立 web_sessions 表 + mf_session Cookie，
不与 APP 的 Bearer/refresh 会话语义混用。所有 POST 表单必须携带 CSRF token。
"""
from __future__ import annotations

import csv
import hmac
import io
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.main import (
    APP_VERSION,
    LOGIN_LOCK_SECONDS,
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    ApiError,
    BomCommitRequest,
    Decision,
    DeleteUserRequest,
    EditUserRequest,
    EmployeeCreate,
    PasswordResetRequest,
    ROLES,
    TransferAction,
    TransferApproval,
    add_employee,
    approve as api_approve,
    audit_logs as api_audit_logs,
    commit_bom_import as api_bom_commit,
    audit,
    check_password,
    confirm_stocktake as api_confirm_stocktake,
    db,
    delete_employee,
    edit_employee,
    hard_delete_employee,
    HardDeleteUserRequest,
    execute_transfer as api_execute_transfer,
    get_transfer,
    handover_timeline,
    inventory as api_inventory,
    list_transfers,
    _list_transfers,
    list_exceptions as api_list_exceptions,
    list_stocktakes as api_list_stocktakes,
    list_bom_versions as api_bom_versions,
    now,
    order_detail as api_order_detail,
    order_task_detail as api_order_task_detail,
    preview_bom_import as api_bom_preview,
    reset_employee_password,
    review_exception as api_review_exception,
    upload_assembly_model as api_upload_model,
    bind_device_model_3d as api_bind_device_model_3d,
    DeviceModel3dBind,
    create_order as api_create_order,
    OrderCreateRequest,
    OrderModelCreate,
    OrderStatusRequest,
    set_order_status as api_set_order_status,
    SessionRevokeRequest,
    revoke_user_sessions as api_revoke_sessions,
    TaskCreateRequest,
    create_assembly_task as api_create_task,
    app as backend_app,
    workspace_material_items as api_workspace_items,
    workspace_summary as api_workspace_summary,
    WORKSPACE_STATUS_LABELS,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

SESSION_COOKIE = "mf_session"
ANON_CSRF_COOKIE = "anon_csrf"
SESSION_SECONDS = 8 * 3600  # 固定 8 小时，不滑动续期（契约 §5.2）
# S1 准入仅 ADMIN；S3 起放宽 WAREHOUSE_ADMIN（契约 §1）
WEB_ROLES = ("ADMIN",)


def _see_other(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


def _csrf_ok(form_value: str | None, expected: str | None) -> bool:
    return bool(form_value) and bool(expected) and hmac.compare_digest(form_value, expected)


def _session_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    c = db()
    try:
        row = c.execute(
            "SELECT ws.csrf_token AS csrf_token, ws.expires_at AS expires_at,"
            " 'web' AS session_device_id, u.*"
            " FROM web_sessions ws JOIN users u ON u.id = ws.user_id"
            " WHERE ws.id=? AND u.active=1",
            (token,),
        ).fetchone()
    finally:
        c.close()
    if row is None or row["expires_at"] < int(time.time()):
        return None
    return row


def _authenticate(username: str, password: str) -> sqlite3.Row | None:
    """与 /api/v1/auth/login 同语义：共享 login_attempts 锁定事实源，成功清零计数。

    失败一律返回 None，不区分「用户不存在 / 密码错误 / 账号停用 / 锁定中」。
    """
    c = db()
    try:
        attempt = c.execute(
            "SELECT * FROM login_attempts WHERE username=?", (username,)
        ).fetchone()
        if attempt and attempt["locked_until"] and int(time.time()) < attempt["locked_until"]:
            return None
        user = c.execute(
            "SELECT * FROM users WHERE username=? AND active=1", (username,)
        ).fetchone()
        ok = bool(user) and check_password(password, user["password_hash"], c, user["id"])
        if not ok:
            window_start = int(time.time()) - LOGIN_WINDOW_SECONDS
            if attempt and attempt["first_failed_at"] >= window_start:
                count = attempt["failed_count"] + 1
                first_at = attempt["first_failed_at"]
            else:
                count, first_at = 1, int(time.time())
            locked_until = (
                int(time.time()) + LOGIN_LOCK_SECONDS if count >= LOGIN_MAX_FAILURES else None
            )
            c.execute(
                "INSERT INTO login_attempts(username, failed_count, first_failed_at, locked_until)"
                " VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET"
                " failed_count=excluded.failed_count,"
                " first_failed_at=excluded.first_failed_at,"
                " locked_until=excluded.locked_until",
                (username, count, first_at, locked_until),
            )
            c.commit()
            return None
        c.execute("DELETE FROM login_attempts WHERE username=?", (username,))
        audit(c, user["id"], user["role"], "LOGIN", "USER", user["id"], "SUCCESS", "web-admin")
        c.commit()
        return user
    finally:
        c.close()


@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if _session_user(request) is not None:
        return _see_other("/admin/exceptions")
    anon_csrf = request.cookies.get(ANON_CSRF_COOKIE) or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request,
        "login.html",
        {"error": request.query_params.get("error") == "1", "csrf_token": anon_csrf},
    )
    if request.cookies.get(ANON_CSRF_COOKIE) != anon_csrf:
        response.set_cookie(
            ANON_CSRF_COOKIE, anon_csrf, httponly=True, samesite="lax", path="/admin"
        )
    return response


@router.post("/admin/login")
async def admin_login(request: Request):
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), request.cookies.get(ANON_CSRF_COOKIE)):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    user = _authenticate(username, password) if username and password else None
    if user is None:
        return _see_other("/admin/login?error=1")
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    c = db()
    try:
        c.execute(
            "INSERT INTO web_sessions(id, user_id, csrf_token, created_at, expires_at,"
            " last_seen_at) VALUES(?,?,?,?,?,?)",
            (
                session_token,
                user["id"],
                csrf_token,
                now(),
                int(time.time()) + SESSION_SECONDS,
                int(time.time()),
            ),
        )
        c.commit()
    finally:
        c.close()
    response = _see_other("/admin/")
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(ANON_CSRF_COOKIE, "", max_age=0, httponly=True, samesite="lax", path="/admin")
    return response


@router.post("/admin/logout")
async def admin_logout(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    c = db()
    try:
        c.execute("DELETE FROM web_sessions WHERE id=?", (request.cookies.get(SESSION_COOKIE, ""),))
        c.commit()
    finally:
        c.close()
    response = _see_other("/admin/login")
    response.set_cookie(SESSION_COOKIE, "", max_age=0, httponly=True, samesite="lax", path="/")
    return response


@router.get("/admin/", response_class=HTMLResponse)
def admin_home(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    return _see_other("/admin/exceptions")
    if user["role"] not in WEB_ROLES:
        return HTMLResponse("403 禁止访问：管理界面仅对管理员开放", status_code=403)
    c = db()
    try:
        users_total, users_active = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(active),0) FROM users"
        ).fetchone()
        pending_transfers = c.execute(
            "SELECT COUNT(*) FROM transfer_requests WHERE status='PENDING_APPROVAL'"
        ).fetchone()[0]
        pending_handovers = c.execute(
            "SELECT COUNT(*) FROM material_handovers WHERE status='PENDING'"
        ).fetchone()[0]
        c.execute(
            "UPDATE web_sessions SET last_seen_at=? WHERE id=?",
            (int(time.time()), request.cookies.get(SESSION_COOKIE, "")),
        )
        c.commit()
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "app_version": APP_VERSION,
            "users_total": users_total,
            "users_active": users_active,
            "pending_transfers": pending_transfers,
            "pending_handovers": pending_handovers,
            "csrf_token": user["csrf_token"],
        },
    )


# ==================== S2：用户管理（契约 §6-S2）====================

ROLE_LABELS = {
    "OPERATOR": "操作员",
    "MATERIAL": "物料员",
    "WAREHOUSE_ADMIN": "仓库管理员",
    "ADMIN": "管理员",
    "PLANNER": "计划员",
    "WORKSHOP_SUPERVISOR": "车间主管",
    "ASSEMBLER": "装配工",
}
# 新建可选角色：ROLES 去掉 ADMIN（add_employee 同语义）。
CREATE_ROLES = tuple(role for role in ROLES if role != "ADMIN")
# 编辑可改角色：与 edit_employee 的白名单逐字一致（PLANNER 仅可创建不可改派）。
EDIT_ROLES = ("OPERATOR", "MATERIAL", "WAREHOUSE_ADMIN", "WORKSHOP_SUPERVISOR", "ASSEMBLER")

NOTICE_TEXTS = {
    "created": "用户已创建（首次登录需修改密码）",
    "updated": "用户已更新",
    "reset": "密码已重置（目标用户会话已吊销，首次登录需改密）",
    "disabled": "用户已停用",
    "deleted": "用户已彻底删除",
    "approved": "申请已审批通过",
    "rejected": "申请已驳回",
    "executed": "出库执行完成，库存已变更",
    "stocktake_confirmed": "盘点已确认",
    "exception_reviewed": "异常已审核",
    "assigned": "任务成员已分配",
    "unassigned": "任务成员已移除",
    "bom_imported": "BOM 已导入",
    "model_uploaded": "模型已上传并发布",
    "upload_bad_type": "上传失败：模型文件必须是 .glb",
    "upload_bad_glb": "上传失败：文件不是有效 GLB",
    "upload_too_large": "上传失败：模型文件超过 15 MiB",
    "upload_bad_code": "上传失败：modelCode 格式无效",
    "upload_bad_name": "上传失败：modelName 格式无效",
    "upload_forbidden": "上传失败：仅管理员或仓库管理员可上传模型",
    "upload_failed": "上传失败：未通过服务端校验",
    "order_created": "生产订单已创建",
    "order_status_changed": "订单状态已推进",
    "sessions_revoked": "该用户会话已全部强制下线",
    "task_created": "机台任务已创建",
    "model_bound": "机台 3D 模型绑定已保存",
    "bind_failed": "绑定失败：请检查机台与模型",
}


def _admin_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return None, HTMLResponse("403 禁止访问：管理界面仅对管理员开放", status_code=403)
    return user, None


def _api_error_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        # ApiError 的用户可见文案存在 HTTPException.detail（super().__init__(detail=message)）。
        return str(getattr(exc, "detail", None) or "操作被拒绝")
    if isinstance(exc, ValidationError):
        return "输入校验未通过（如密码至少 8 位）"
    return "操作失败"


def _render_users(request: Request, user: sqlite3.Row, error: str | None = None):
    q = (request.query_params.get("q") or "").strip()
    c = db()
    try:
        sql = (
            "SELECT u.id, u.username, u.display_name, u.role, u.active,"
            " u.must_change_password, u.created_at, m.display_name AS manager_name"
            " FROM users u LEFT JOIN employee_managers em ON em.employee_id=u.id"
            " LEFT JOIN users m ON m.id=em.manager_id"
        )
        args: list = []
        if q:
            sql += " WHERE u.username LIKE ? OR u.display_name LIKE ?"
            args = [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY u.created_at"
        rows = c.execute(sql, args).fetchall()
    finally:
        c.close()
    notice_code = request.query_params.get("notice", "")
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "rows": rows,
            "role_labels": ROLE_LABELS,
            "notice_text": NOTICE_TEXTS.get(notice_code),
            "error": error,
            "reset_ops": {row["id"]: str(uuid.uuid4()) for row in rows},
            "delete_ops": {row["id"]: str(uuid.uuid4()) for row in rows},
            "hard_delete_ops": {row["id"]: str(uuid.uuid4()) for row in rows},
        },
    )


def _render_user_form(
    request: Request,
    user: sqlite3.Row,
    mode: str,
    values: dict[str, Any],
    user_id: str | None = None,
    error: str | None = None,
):
    safe_values = {k: v for k, v in values.items() if k != "password"}
    c = db()
    try:
        managers = c.execute(
            "SELECT id, username, display_name FROM users WHERE active=1 ORDER BY username"
        ).fetchall()
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "user_form.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "mode": mode,
            "title": "新建用户" if mode == "create" else "编辑用户",
            "action": "/admin/users/new" if mode == "create" else f"/admin/users/{user_id}/edit",
            "values": safe_values,
            "managers": managers,
            "role_labels": ROLE_LABELS,
            "assignable_roles": CREATE_ROLES if mode == "create" else EDIT_ROLES,
            "client_operation_id": str(uuid.uuid4()) if mode == "edit" else None,
            "error": error,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_list(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    return _render_users(request, user)


@router.get("/admin/users/new", response_class=HTMLResponse)
def admin_user_new_form(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    return _render_user_form(request, user, mode="create", values={"role": CREATE_ROLES[0]})


@router.post("/admin/users/new")
async def admin_user_create(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    values = {
        "employeeNo": str(form.get("employeeNo", "")).strip(),
        "displayName": str(form.get("displayName", "")).strip(),
        "role": str(form.get("role", "")),
        "password": str(form.get("password", "")),
        "managerId": str(form.get("managerId", "")) or None,
    }
    try:
        add_employee(
            EmployeeCreate(**values),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError) as exc:
        return _render_user_form(
            request, user, mode="create", values=values, error=_api_error_message(exc)
        )
    return _see_other("/admin/users?notice=created")


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def admin_user_edit_form(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    c = db()
    try:
        target = c.execute(
            "SELECT id, username, display_name, role, active FROM users WHERE id=?", (user_id,)
        ).fetchone()
    finally:
        c.close()
    if target is None:
        return HTMLResponse("404 用户不存在", status_code=404)
    return _render_user_form(
        request,
        user,
        mode="edit",
        user_id=user_id,
        values={
            "employeeNo": target["username"],
            "displayName": target["display_name"],
            "role": target["role"] if target["role"] in EDIT_ROLES else EDIT_ROLES[0],
            "active": bool(target["active"]),
        },
    )


@router.post("/admin/users/{user_id}/edit")
async def admin_user_edit(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", ""))
    values = {
        "employeeNo": str(form.get("employeeNo", "")),
        "displayName": str(form.get("displayName", "")).strip(),
        "role": str(form.get("role", "")),
        "active": form.get("active") == "on",
    }
    try:
        edit_employee(
            user_id,
            EditUserRequest(
                clientOperationId=operation_id,
                employeeNo=values["employeeNo"] or None,
                displayName=values["displayName"],
                role=values["role"],
                active=values["active"],
            ),
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_user_form(
            request,
            user,
            mode="edit",
            user_id=user_id,
            values=values,
            error=_api_error_message(exc),
        )
    return _see_other("/admin/users?notice=updated")


@router.post("/admin/users/{user_id}/password-reset")
async def admin_user_password_reset(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", ""))
    try:
        reset_employee_password(
            user_id,
            PasswordResetRequest(
                newPassword=str(form.get("newPassword", "")), clientOperationId=operation_id
            ),
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_users(request, user, error=_api_error_message(exc))
    # API 语义只吊销 APP sessions；web_sessions 是本界面私有表，需在 web 层补删。
    c = db()
    try:
        c.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
        c.commit()
    finally:
        c.close()
    return _see_other("/admin/users?notice=reset")


@router.post("/admin/users/{user_id}/delete")
async def admin_user_delete(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", ""))
    try:
        delete_employee(
            user_id,
            DeleteUserRequest(clientOperationId=operation_id),
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_users(request, user, error=_api_error_message(exc))
    c = db()
    try:
        c.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))
        c.commit()
    finally:
        c.close()
    return _see_other("/admin/users?notice=disabled")


@router.post("/admin/users/{user_id}/hard-delete")
async def admin_user_hard_delete(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        hard_delete_employee(
            user_id,
            HardDeleteUserRequest(clientOperationId=operation_id),
            request,
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_users(request, user, error=_api_error_message(exc))
    return _see_other("/admin/users?notice=deleted")


# ==================== S3：流转审批与交接留痕（契约 §6.3）====================

MANAGER_ROLES = ("ADMIN", "WAREHOUSE_ADMIN")

TRANSFER_STATUS_LABELS = {
    "PENDING_APPROVAL": "待审批",
    "APPROVED": "已审批",
    "REJECTED": "已驳回",
    "EXECUTED": "已执行",
}

TRANSFER_TYPE_LABELS = {
    "INBOUND": "入库",
    "OUTBOUND": "出库",
    "TRANSFER": "调拨",
    "STOCKTAKE": "盘点",
}

TRANSFER_EXECUTE_VERBS = {
    "INBOUND": "执行入库",
    "OUTBOUND": "执行出库",
    "TRANSFER": "执行调拨",
    "STOCKTAKE": "执行盘点",
}

HANDOVER_STATUS_LABELS = {
    "PENDING": "待确认",
    "CONFIRMED": "已确认",
    "REJECTED": "已驳回",
    "CANCELLED": "已取消",
}

_BEIJING = timezone(timedelta(hours=8))


def _local_time(value: Any) -> Any:
    """UTC 时间字符串转北京时间显示，如 2026-09-25T03:16:09Z -> 2026-09-25 11:16:09。"""
    if not value:
        return value
    try:
        s = str(value).strip()
        dt = datetime.fromisoformat(s[:-1] + "+00:00") if s.endswith("Z") else datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return value


def _user_display_name(c: sqlite3.Connection, user_id: Any) -> str:
    if not user_id:
        return "—"
    row = c.execute("SELECT display_name FROM users WHERE id=?", (user_id,)).fetchone()
    return row["display_name"] if row and row["display_name"] else str(user_id)


def _manager_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in MANAGER_ROLES:
        return None, HTMLResponse(
            "403 禁止访问：审批与留痕页面仅对管理员/仓库管理员开放", status_code=403
        )
    return user, None


def _api_http_error_response(exc: HTTPException):
    return HTMLResponse(f"{exc.status_code}：{exc.detail}", status_code=exc.status_code)


@router.get("/admin/flows", response_class=HTMLResponse)
def admin_flows_list(request: Request):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    status = request.query_params.get("status") or None
    try:
        data = _list_transfers(status=status, page=1, page_size=100, user=user)
    except HTTPException as exc:
        return _api_http_error_response(exc)
    return templates.TemplateResponse(
        request,
        "flows.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
            "status_filter": status or "",
            "status_labels": TRANSFER_STATUS_LABELS,
            "type_labels": TRANSFER_TYPE_LABELS,
            "notice_text": NOTICE_TEXTS.get(request.query_params.get("notice", "")),
        },
    )


def _render_flow_detail(request: Request, user: sqlite3.Row, rid: str, error: str | None = None):
    try:
        item = get_transfer(rid, user=user)
    except HTTPException as exc:
        return _api_http_error_response(exc)
    c = db()
    try:
        handover_rows = c.execute(
            "SELECT id, status, quantity, receiver_user_id, created_at"
            " FROM material_handovers WHERE transfer_request_id=? ORDER BY created_at",
            (rid,),
        ).fetchall()
        created_by_name = _user_display_name(c, item.get("created_by"))
        approved_by_name = _user_display_name(c, item.get("approved_by"))
        payload_items: list[dict[str, Any]] = []
        for p in (item.get("payload") or {}).get("items", []) or []:
            mat_id = p.get("materialId")
            mrow = c.execute(
                "SELECT code, name, specification, unit FROM materials WHERE id=?", (mat_id,)
            ).fetchone() if mat_id else None
            payload_items.append({
                "code": p.get("materialCode") or (mrow["code"] if mrow else None) or "—",
                "name": (mrow["name"] if mrow else None) or "—",
                "spec": (mrow["specification"] if mrow else None) or "",
                "quantity": p.get("quantity"),
                "unit": p.get("unit") or (mrow["unit"] if mrow else "") or "",
            })
        handover_views = [{
            "id": h["id"],
            "status": h["status"],
            "status_label": HANDOVER_STATUS_LABELS.get(h["status"], h["status"]),
            "quantity": h["quantity"],
            "receiver_name": _user_display_name(c, h["receiver_user_id"]),
            "created_at": _local_time(h["created_at"]),
        } for h in handover_rows]
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "flow_detail.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "item": item,
            "created_by_name": created_by_name,
            "approved_by_name": approved_by_name,
            "created_at_local": _local_time(item.get("created_at")),
            "approved_at_local": _local_time(item.get("approved_at")),
            "payload_items": payload_items,
            "type_label": TRANSFER_TYPE_LABELS.get(item.get("type"), item.get("type")),
            "execute_verb": TRANSFER_EXECUTE_VERBS.get(item.get("type"), "执行"),
            "handovers": handover_views,
            "error": error,
            "notice_text": NOTICE_TEXTS.get(request.query_params.get("notice", "")),
            "can_approve": item.get("status") == "PENDING_APPROVAL",
            "can_execute": item.get("status") == "APPROVED",
            "decision_op": str(uuid.uuid4()),
            "execute_op": str(uuid.uuid4()),
            "status_labels": TRANSFER_STATUS_LABELS,
        },
    )


@router.get("/admin/flows/{rid}", response_class=HTMLResponse)
def admin_flow_detail(request: Request, rid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    return _render_flow_detail(request, user, rid)


@router.post("/admin/flows/{rid}/approve")
async def admin_flow_approve(request: Request, rid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", ""))
    decision = str(form.get("decision", ""))
    try:
        api_approve(
            rid,
            TransferApproval(
                decision=decision,
                comment=str(form.get("comment", "")),
                clientOperationId=operation_id,
            ),
            request,
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_flow_detail(request, user, rid, error=_api_error_message(exc))
    notice = "rejected" if decision == "REJECT" else "approved"
    return _see_other(f"/admin/flows/{rid}?notice={notice}")


@router.post("/admin/flows/{rid}/execute")
async def admin_flow_execute(request: Request, rid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", ""))
    try:
        api_execute_transfer(
            rid,
            TransferAction(clientOperationId=operation_id),
            request,
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_flow_detail(request, user, rid, error=_api_error_message(exc))
    return _see_other(f"/admin/flows/{rid}?notice=executed")


@router.get("/admin/handovers", response_class=HTMLResponse)
def admin_handovers_search(request: Request):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    query = str(request.query_params.get("query", "")).strip()
    result = None
    error = None
    if query:
        try:
            result = handover_timeline(query, user=user)
        except HTTPException as exc:
            error = f"{exc.status_code}：{exc.detail}"
    # 最近交接列表
    page = _page_param(request)
    page_size = 20
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) FROM material_handovers").fetchone()[0]
        rows = c.execute(
            "SELECT h.id, h.work_item_id, h.status, h.quantity, h.from_location,"
            " h.created_at, u.display_name AS creator_name"
            " FROM material_handovers h"
            " LEFT JOIN users u ON u.id = h.created_by"
            " ORDER BY h.created_at DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "handovers.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "query": query,
            "result": result,
            "error": error,
            "rows": rows,
            "page": page,
            "total_pages": (total + page_size - 1) // page_size if total else 1,
            "total": total,
            "ho_status_labels": {"PENDING": "待确认", "CONFIRMED": "已确认",
                                 "REJECTED": "已驳回", "CANCELLED": "已取消"},
        },
    )


@router.get("/admin/handovers/{hid}", response_class=HTMLResponse)
def admin_handover_timeline(request: Request, hid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    try:
        result = handover_timeline(hid, user=user)
    except HTTPException as exc:
        return _api_http_error_response(exc)
    return templates.TemplateResponse(
        request,
        "handovers.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "query": hid,
            "result": result,
            "error": None,
            "rows": [],
            "page": 1,
            "total_pages": 1,
            "total": 0,
            "ho_status_labels": {"PENDING": "待确认", "CONFIRMED": "已确认",
                                 "REJECTED": "已驳回", "CANCELLED": "已取消"},
        },
    )


# ==================== S4：工时汇总与车间概览（契约 §6.4）====================

# S21 起含 PLANNER：计划员需要订单/统计/任务视图（§6.16 契约注记）。
REPORT_ROLES = ("ADMIN", "WORKSHOP_SUPERVISOR", "PLANNER")


def _report_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in REPORT_ROLES:
        return None, HTMLResponse(
            "403 禁止访问：统计页面仅对管理员/车间主管开放", status_code=403
        )
    return user, None


def _api_endpoint(path: str, method: str = "GET"):
    """从 FastAPI 路由表解析既有 API 处理函数并直接调用（统计口径零漂移）。

    workshop 统计端点注册在 assembly_routes.register() 闭包内，无法按模块名导入；
    路由表解析保持「同一函数、同一 SQL」语义。
    """
    for route in backend_app.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"API endpoint not found: {method} {path}")


def _page_param(request: Request) -> int:
    try:
        return max(1, int(request.query_params.get("page", "1") or 1))
    except ValueError:
        return 1


@router.get("/admin/reports", response_class=HTMLResponse)
def admin_reports_overview(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    page = _page_param(request)
    try:
        summary = _api_endpoint("/api/v1/workshop/summary")(user=user)
        machines = _api_endpoint("/api/v1/workshop/machine-progress")(
            user=user, page=page, pageSize=20, deviceId=None
        )
    except HTTPException as exc:
        return _api_http_error_response(exc)
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "summary": summary,
            "machines": machines["items"],
            "page": page,
        },
    )


@router.get("/admin/reports/labor", response_class=HTMLResponse)
def admin_reports_labor(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    page = _page_param(request)
    filters = {
        "deviceId": request.query_params.get("deviceId") or None,
        "orderNo": request.query_params.get("orderNo") or None,
        "assemblerId": request.query_params.get("assemblerId") or None,
    }
    try:
        data = _api_endpoint("/api/v1/workshop/labor-summary")(
            page=page,
            pageSize=20,
            deviceId=filters["deviceId"],
            orderNo=filters["orderNo"],
            assemblerId=filters["assemblerId"],
            user=user,
        )
    except HTTPException as exc:
        return _api_http_error_response(exc)
    return templates.TemplateResponse(
        request,
        "reports_labor.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
            "page": data["page"],
            "total_pages": data["totalPages"],
            "total_rows": data["total"],
            "total_minutes": data["totalLaborMinutes"],
            "filters": filters,
        },
    )


# ==================== S5：装配模型管理（契约 §6.5）====================

MODEL_STATUS_LABELS = {
    "PUBLISHED": "已发布",
    "ARCHIVED": "已归档",
    "DRAFT": "草稿",
}


@router.get("/admin/models", response_class=HTMLResponse)
def admin_models_list(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    code = str(request.query_params.get("code", "")).strip()
    q = str(request.query_params.get("q", "")).strip()
    page = _page_param(request)
    page_size = 20
    c = db()
    try:
        where = "1=1"
        args: list = []
        if code:
            where += " AND model_code=?"
            args.append(code)
        if q:
            where += " AND (model_code LIKE ? OR model_name LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        total = c.execute(
            f"SELECT COUNT(*) FROM assembly_model_versions WHERE {where}", args
        ).fetchone()[0]
        rows = c.execute(
            "SELECT id, model_code, model_name, version, format, byte_size, sha256,"
            " status, created_by, created_at FROM assembly_model_versions"
            f" WHERE {where} ORDER BY model_code, version DESC LIMIT ? OFFSET ?",
            (*args, page_size, (page - 1) * page_size),
        ).fetchall()
    finally:
        c.close()

    published = None
    published_error = None
    if code:
        try:
            published = _api_endpoint("/api/v1/assembly-models/{model_code}/published")(
                model_code=code, user=user
            )
        except HTTPException as exc:
            published_error = f"{exc.status_code}：{exc.detail}"
    # 机台 3D 模型绑定：机台列表 + 已发布模型码
    dq = str(request.query_params.get("dq", "")).strip()
    dpage = max(1, int(request.query_params.get("dpage", "1") or 1))
    dpage_size = 20
    c = db()
    try:
        device_cols = {r["name"] for r in c.execute("PRAGMA table_info(devices)").fetchall()}
        has_3d_col = "model_3d_code" in device_cols
        col = "model_3d_code" if has_3d_col else "NULL AS model_3d_code"
        dwhere = "1=1"
        dargs: list = []
        if dq:
            dwhere += " AND (device_no LIKE ? OR device_name LIKE ?)"
            dargs += [f"%{dq}%", f"%{dq}%"]
        dtotal = c.execute(f"SELECT COUNT(*) FROM devices WHERE {dwhere}", dargs).fetchone()[0]
        device_rows = c.execute(
            f"SELECT id, device_no, device_name, {col} FROM devices"
            f" WHERE {dwhere} ORDER BY device_no LIMIT ? OFFSET ?",
            (*dargs, dpage_size, (dpage - 1) * dpage_size),
        ).fetchall()
        published_codes = [
            r["model_code"] for r in c.execute(
                "SELECT DISTINCT model_code FROM assembly_model_versions"
                " WHERE status='PUBLISHED' ORDER BY model_code"
            ).fetchall()
        ]
        # 机台关联的装配任务（含成员）：机台页任务分配与任务页联动
        device_ids = [r["id"] for r in device_rows]
        device_tasks: dict[str, list] = {did: [] for did in device_ids}
        if device_ids:
            ph = ",".join("?" for _ in device_ids)
            trows = c.execute(
                "SELECT id, order_no, device_id, device_no, status, progress_stage"
                f" FROM assembly_tasks WHERE device_id IN ({ph})"
                " ORDER BY order_no DESC, device_no",
                device_ids,
            ).fetchall()
            tids = [t["id"] for t in trows]
            members_by_task: dict[str, list] = {tid: [] for tid in tids}
            if tids:
                tph = ",".join("?" for _ in tids)
                for mr in c.execute(
                    "SELECT task_id, assembler_id, assignment_role FROM assembly_task_members"
                    f" WHERE task_id IN ({tph}) AND removed_at IS NULL",
                    tids,
                ).fetchall():
                    members_by_task[mr["task_id"]].append(
                        {"assembler_id": mr["assembler_id"], "assignment_role": mr["assignment_role"]}
                    )
            for t in trows:
                device_tasks[t["device_id"]].append({
                    "id": t["id"],
                    "order_no": t["order_no"],
                    "device_no": t["device_no"],
                    "status": t["status"],
                    "progress_stage": t["progress_stage"],
                    "members": members_by_task[t["id"]],
                })
        assemblers = c.execute(
            "SELECT id, username, display_name FROM users"
            " WHERE role='ASSEMBLER' AND active=1 ORDER BY username"
        ).fetchall()
        names = {row["id"]: row["display_name"] for row in assemblers}
        assign_ops: dict[str, str] = {}
        remove_ops: dict[str, str] = {}
        for tlist in device_tasks.values():
            for t in tlist:
                assign_ops[t["id"]] = str(uuid.uuid4())
                for m in t["members"]:
                    remove_ops[f"{t['id']}:{m['assembler_id']}"] = str(uuid.uuid4())
    finally:
        c.close()
    devices = [
        {
            "id": r["id"],
            "device_no": r["device_no"],
            "device_name": r["device_name"],
            "model_3d_code": r["model_3d_code"],
        }
        for r in device_rows
    ]
    return templates.TemplateResponse(
        request,
        "models.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "rows": rows,
            "code": code,
            "q": q,
            "page": page,
            "total_pages": (total + page_size - 1) // page_size if total else 1,
            "total": total,
            "published": published,
            "published_error": published_error,
            "status_labels": MODEL_STATUS_LABELS,
            "devices": devices,
            "published_codes": published_codes,
            "dq": dq,
            "dpage": dpage,
            "dtotal_pages": (dtotal + dpage_size - 1) // dpage_size if dtotal else 1,
            "dtotal": dtotal,
            "notice_text": NOTICE_TEXTS.get(request.query_params.get("notice", "")),
            "device_tasks": device_tasks,
            "assemblers": assemblers,
            "names": names,
            "assign_ops": assign_ops,
            "remove_ops": remove_ops,
            "task_status_labels": WORKSPACE_STATUS_LABELS,
        },
    )


# ==================== S6：审计日志查询（契约 §6.6）====================


@router.get("/admin/audit", response_class=HTMLResponse)
def admin_audit_logs(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    q = request.query_params
    filters = {
        "from": q.get("from") or None,
        "to": q.get("to") or None,
        "eventType": q.get("eventType") or None,
        "entityType": q.get("entityType") or None,
        "operatorId": q.get("operatorId") or None,
        "entityId": q.get("entityId") or None,
    }
    data = None
    error = None
    try:
        data = api_audit_logs(
            page=_page_param(request),
            pageSize=50,
            from_=filters["from"],
            to_=filters["to"],
            eventType=filters["eventType"],
            entityType=filters["entityType"],
            operatorId=filters["operatorId"],
            action=None,
            resourceType=None,
            resourceId=None,
            entityId=filters["entityId"],
            user=user,
        )
    except HTTPException as exc:
        error = f"{exc.status_code}：{exc.detail}"
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": (data or {}).get("items", []),
            "page": _page_param(request),
            "error": error,
            "filters": filters,
            "event_labels": {
                "ORDER_STATUS_CHANGED": "订单状态变更",
                "ORDER_CREATED": "订单创建",
                "HANDOVER_CREATED": "交接创建",
                "HANDOVER_CONFIRMED": "交接确认",
                "HANDOVER_REJECTED": "交接驳回",
                "HANDOVER_CANCELLED": "交接取消",
                "OUTBOUND_APPROVED": "出库审批通过",
                "AGG_BOM_IMPORT": "聚合BOM导入",
                "AGENT_IMPORT": "智能导入",
                "ADMIN_PROVISIONED_CLI": "管理员CLI开通",
            },
            "entity_labels": {
                "PRODUCTION_ORDER": "生产订单",
                "MATERIAL_HANDOVER": "物料交接",
                "TRANSFER_REQUEST": "流转申请",
                "AGG_BOM_BATCH": "聚合BOM批次",
                "AGENT_IMPORT_JOB": "智能导入任务",
                "USER": "用户",
            },
            "result_labels": {"SUCCESS": "成功", "FAIL": "失败", "FAILURE": "失败"},
            "role_labels": ROLE_LABELS,
        },
    )


# ==================== S7：生产订单查询（契约 §6.7）====================


@router.get("/admin/orders", response_class=HTMLResponse)
def admin_orders(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    order_no = str(request.query_params.get("order_no", "")).strip()
    c = db()
    try:
        rows = c.execute(
            "SELECT id, order_no, product_name, status, created_at, updated_at"
            " FROM production_orders ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    finally:
        c.close()
    detail = None
    error = None
    if order_no:
        try:
            detail = api_order_detail(
                order_no=order_no, page=1, pageSize=500, user=user
            )
        except ApiError as exc:
            error = _api_error_message(exc)
        except HTTPException as exc:
            error = f"{exc.status_code}：{exc.detail}"
    ctx = {
        "user": user,
        "csrf_token": user["csrf_token"],
        "rows": rows,
        "order_no": order_no,
        "detail": detail,
        "status_labels": WORKSPACE_STATUS_LABELS,
        "error": error,
    }
    if request.query_params.get("partial") == "materials":
        return templates.TemplateResponse(request, "_order_materials.html", ctx)
    return templates.TemplateResponse(
        request,
        "orders.html",
        ctx,
    )


@router.get("/admin/orders/{order_no}/tasks/{task_id}", response_class=HTMLResponse)
def admin_order_task(request: Request, order_no: str, task_id: str):
    """机台详情页：状态、进度、物料、条码。"""
    user, denied = _report_or_403(request)
    if denied:
        return denied
    detail = None
    error = None
    try:
        detail = api_order_task_detail(order_no=order_no, task_id=task_id, user=user)
    except ApiError as exc:
        error = _api_error_message(exc)
    except HTTPException as exc:
        error = f"{exc.status_code}：{exc.detail}"
    ctx = {
        "user": user,
        "csrf_token": user["csrf_token"],
        "order_no": order_no,
        "detail": detail,
        "error": error,
    }
    if request.query_params.get("partial") == "materials":
        return templates.TemplateResponse(request, "_order_task_materials.html", ctx)
    return templates.TemplateResponse(
        request,
        "order_task.html",
        ctx,
    )


@router.get("/admin/orders/{order_no}/barcodes", response_class=HTMLResponse)
def admin_order_barcodes(request: Request, order_no: str):
    """订单下全部机台的条码批量预览（二维码 + Code128）。"""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    c = db()
    try:
        order = c.execute(
            "SELECT order_no, product_name, status FROM production_orders WHERE order_no=?",
            (order_no,),
        ).fetchone()
        devices = [
            {"device_no": r["device_no"], "device_name": r["device_name"]}
            for r in c.execute(
                """SELECT t.device_no, d.device_name
                     FROM assembly_tasks t
                     LEFT JOIN devices d ON d.id = t.device_id
                    WHERE t.order_no = ? GROUP BY t.device_no ORDER BY t.device_no""",
                (order_no,),
            ).fetchall()
        ]
    finally:
        c.close()
    if not order:
        return HTMLResponse("订单不存在", status_code=404)
    items = []
    for d in devices:
        payload, error = _barcode_preview("device", d["device_no"], "qr")
        if error:
            continue
        items.append({
            "device_no": d["device_no"],
            "device_name": d["device_name"],
            "qr_img": _barcodes.data_uri(payload, "qr", "png", "机台"),
            "code128_img": _barcodes.data_uri(payload, "code128", "png", "机台"),
        })
    return templates.TemplateResponse(
        request,
        "order_barcodes.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "order_no": order_no,
            "order": dict(order),
            "items": items,
        },
    )


@router.get("/admin/orders/{order_no}/barcodes/print", response_class=HTMLResponse)
def admin_order_barcodes_print(request: Request, order_no: str):
    """订单下全部机台条码的 A4 批量打印页（二维码 + Code128 各一份）。"""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    kind = request.query_params.get("kind", "both")
    if kind not in {"qr", "code128", "both"}:
        kind = "both"
    c = db()
    try:
        order = c.execute(
            "SELECT order_no FROM production_orders WHERE order_no=?", (order_no,)
        ).fetchone()
        device_nos = [
            r["device_no"]
            for r in c.execute(
                "SELECT device_no FROM assembly_tasks WHERE order_no=? "
                "GROUP BY device_no ORDER BY device_no",
                (order_no,),
            ).fetchall()
        ]
    finally:
        c.close()
    if not order:
        return HTMLResponse("订单不存在", status_code=404)
    kinds = ["qr", "code128"] if kind == "both" else [kind]
    items = []
    for device_no in device_nos:
        payload, error = _barcode_preview("device", device_no, "qr")
        if error:
            continue
        for k in kinds:
            items.append({
                "device_no": device_no,
                "payload": payload,
                "kind": k,
                "kind_name": KIND_NAMES[k],
                "img": _barcodes.data_uri(payload, k, "svg", "机台"),
            })
    return templates.TemplateResponse(
        request,
        "order_barcodes_print.html",
        {
            "user": user,
            "order_no": order_no,
            "kind": kind,
            "items": items,
            "csrf_token": user["csrf_token"],
        },
    )


# ==================== S8：物料—库位—库存查询（契约 §6.8）====================

STOCKTAKE_STATUS_LABELS = {
    "PENDING_CONFIRM": "待确认",
    "CONFIRMED": "已确认",
}

EXCEPTION_STATUS_LABELS = {
    "PENDING": "待处理",
    "APPROVED": "已通过",
    "REJECTED": "已驳回",
}


@router.get("/admin/warehouse", response_class=HTMLResponse)
def admin_warehouse(request: Request):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    code = str(request.query_params.get("code", "")).strip()
    material = None
    material_error = None
    if code:
        try:
            material = api_inventory(code=code, user=user)
        except HTTPException as exc:
            material_error = f"{exc.status_code}：{exc.detail}"
    stocktakes = api_list_stocktakes(user=user)["items"]
    exceptions = api_list_exceptions(user=user)["items"]
    return templates.TemplateResponse(
        request,
        "warehouse.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "code": code,
            "material": material,
            "material_error": material_error,
            "stocktakes": stocktakes,
            "exceptions": exceptions,
            "stocktake_ops": {s["id"]: str(uuid.uuid4()) for s in stocktakes},
            "exception_ops": {e["id"]: str(uuid.uuid4()) for e in exceptions},
            "stocktake_labels": STOCKTAKE_STATUS_LABELS,
            "exception_labels": EXCEPTION_STATUS_LABELS,
        },
    )


# ==================== S10：盘点确认/异常审核动作（契约 §6.10）====================


@router.post("/admin/warehouse/stocktakes/{sid}/confirm")
async def admin_stocktake_confirm(request: Request, sid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        api_confirm_stocktake(
            sid,
            Decision(decision="CONFIRM", clientOperationId=operation_id),
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_warehouse(request, user, error=_api_error_message(exc))
    return _see_other("/admin/warehouse?notice=stocktake_confirmed")


@router.post("/admin/warehouse/exceptions/{eid}/review")
async def admin_exception_review(request: Request, eid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        api_review_exception(
            eid,
            Decision(
                decision=str(form.get("decision", "")),
                comment=str(form.get("comment", "")),
                clientOperationId=operation_id,
            ),
            request,
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _render_warehouse(request, user, error=_api_error_message(exc))
    return _see_other("/admin/warehouse?notice=exception_reviewed")


@router.get("/admin/exceptions", response_class=HTMLResponse)
def admin_exceptions(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return HTMLResponse("403 禁止访问", status_code=403)
    c = db()
    try:
        exceptions = [dict(r) for r in c.execute(
            "SELECT * FROM exceptions ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]
        import_errors = [dict(r) for r in c.execute(
            "SELECT * FROM agent_import_error_logs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]
        open_count = sum(1 for e in exceptions if e["status"] in ("OPEN", "PENDING"))
        in_progress_count = sum(1 for e in exceptions if e["status"] == "IN_PROGRESS")
        resolved_count = sum(1 for e in exceptions if e["status"] in ("RESOLVED", "CLOSED", "APPROVED"))
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "exceptions.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "exceptions": exceptions,
            "import_errors": import_errors,
            "open_count": open_count,
            "in_progress_count": in_progress_count,
            "resolved_count": resolved_count,
            "import_error_count": len(import_errors),
            "exc_status_labels": {"OPEN": "待处理", "PENDING": "待处理", "IN_PROGRESS": "处理中",
                                  "RESOLVED": "已解决", "CLOSED": "已关闭",
                                  "APPROVED": "已通过", "REJECTED": "已驳回"},
        },
    )


@router.post("/admin/exceptions/{eid}/review")
async def admin_exceptions_review(request: Request, eid: str):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        api_review_exception(
            eid,
            Decision(
                decision=str(form.get("decision", "")),
                comment=str(form.get("comment", "")),
                clientOperationId=operation_id,
            ),
            request,
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return HTMLResponse(_api_error_message(exc), status_code=400)
    return _see_other("/admin/exceptions?notice=exception_reviewed")


# ---------------------------------------------------------------- U9 集成页面
@router.get("/admin/u9", response_class=HTMLResponse)
def admin_u9(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    from app.u9.config import get_config
    from app.u9.sync import ENTITIES
    cfg = get_config()
    c = db()
    try:
        logs = []
        if c.execute("SELECT name FROM sqlite_master WHERE name='u9_sync_logs'").fetchone():
            logs = [dict(r) for r in c.execute(
                "SELECT * FROM u9_sync_logs ORDER BY started_at DESC LIMIT 30").fetchall()]
    finally:
        c.close()
    entities = [
        {"id": "items", "label": "料品档案",
         "desc": "U9 料品主数据 → 本地物料档案（编码/名称/规格/单位，不含数量）",
         "table": "materials"},
        {"id": "boms", "label": "物料清单 BOM",
         "desc": "按产品/设备编码拉取多层 BOM，替代人工导 Excel",
         "table": "agg_bom_batches / agg_bom_items"},
        {"id": "inventory", "label": "库存现存量",
         "desc": "U9 库存现存量 → 本地库存（按料品+库位）",
         "table": "inventory"},
        {"id": "orders", "label": "生产订单",
         "desc": "U9 生产订单 → 本地生产订单",
         "table": "production_orders"},
    ]
    return templates.TemplateResponse(
        request,
        "u9.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "config": cfg.safe_dict(),
            "entities": [e for e in entities if e["id"] in ENTITIES],
            "logs": logs,
            "entity_labels": {"items": "料品档案", "boms": "BOM",
                              "inventory": "库存", "orders": "生产订单"},
            "status_labels": {"OK": "成功", "PENDING_DOC": "待实施商文档",
                              "FAILED": "失败", "RUNNING": "运行中"},
            "message": request.query_params.get("msg", ""),
            "message_ok": request.query_params.get("ok", "") == "1",
            "now_ts": int(time.time()),
        },
    )


@router.post("/admin/u9/sync/{entity}")
async def admin_u9_sync(request: Request, entity: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    from urllib.parse import quote
    from app.u9.config import get_config
    from app.u9.sync import ENTITIES, run_sync
    cfg = get_config()
    if entity not in ENTITIES:
        return _see_other("/admin/u9?msg=" + quote("未知同步实体") + "&ok=0")
    if not cfg.enabled or not cfg.configured:
        return _see_other("/admin/u9?msg=" + quote("U9 未启用或连接信息不完整，同步未执行") + "&ok=0")
    dry_run = str(form.get("dry_run", "1")) != "0"
    kwargs: dict = {}
    if entity == "boms":
        codes = [p.strip() for p in str(form.get("product_codes", "")).split(",") if p.strip()]
        if not codes:
            return _see_other("/admin/u9?msg=" + quote("同步 BOM 需填写产品/设备编码") + "&ok=0")
        kwargs["product_codes"] = codes
    c = db()
    try:
        result = run_sync(entity, c, cfg, dry_run=dry_run, **kwargs)
    except Exception as e:  # noqa: BLE001 - 转中文提示，不吞真因
        c.close()
        return _see_other("/admin/u9?msg=" + quote(f"同步异常：{e}") + "&ok=0")
    c.close()
    if result["status"] == "PENDING_DOC":
        msg = "U9 接口尚未实现（待实施商提供 WSDL/字段文档），已记录占位日志"
        ok = "0"
    else:
        s = result["stats"]
        mode = "预览" if dry_run else "执行"
        total = s.get("total", s.get("total_lines", 0))
        inserted = s.get("inserted", s.get("inserted_lines", 0))
        msg = (f"{mode}完成：总数 {total}，新增 {inserted}，"
               f"更新 {s.get('updated', 0)}，跳过 {s.get('skipped', 0)}")
        ok = "1"
    return _see_other(f"/admin/u9?msg={quote(msg)}&ok={ok}")


def _render_warehouse(request: Request, user: sqlite3.Row, error: str):
    code = str(request.query_params.get("code", "")).strip()
    stocktakes = api_list_stocktakes(user=user)["items"]
    exceptions = api_list_exceptions(user=user)["items"]
    return templates.TemplateResponse(
        request,
        "warehouse.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "code": code,
            "material": None,
            "material_error": None,
            "error": error,
            "notice_text": None,
            "stocktakes": stocktakes,
            "exceptions": exceptions,
            "stocktake_ops": {s["id"]: str(uuid.uuid4()) for s in stocktakes},
            "exception_ops": {e["id"]: str(uuid.uuid4()) for e in exceptions},
            "stocktake_labels": STOCKTAKE_STATUS_LABELS,
            "exception_labels": EXCEPTION_STATUS_LABELS,
        },
    )


# ==================== S9：物料状态工作台（契约 §6.9）====================

WORKSPACE_ROLES = ("ADMIN", "WAREHOUSE_ADMIN", "WORKSHOP_SUPERVISOR")


def _workspace_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in WORKSPACE_ROLES:
        return None, HTMLResponse(
            "403 禁止访问：物料状态工作台仅对管理员/仓库管理员/车间主管开放",
            status_code=403,
        )
    return user, None


@router.get("/admin/workspace", response_class=HTMLResponse)
def admin_workspace(request: Request):
    user, denied = _workspace_or_403(request)
    if denied:
        return denied
    q = request.query_params
    page = _page_param(request)
    try:
        summary = api_workspace_summary(
            viewRole=None,
            user=user,
            x_request_id=str(uuid.uuid4()),
            x_client_operation_id=None,
        )
        items = api_workspace_items(
            viewRole=None,
            status=q.get("status") or None,
            orderNo=q.get("orderNo") or None,
            page=page,
            pageSize=20,
            user=user,
            x_request_id=str(uuid.uuid4()),
            x_client_operation_id=None,
        )
    except ApiError as exc:
        return HTMLResponse(_api_error_message(exc), status_code=exc.status_code)
    except HTTPException as exc:
        return _api_http_error_response(exc)
    return templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "summary": {**summary, "generatedAt": _local_time(summary.get("generatedAt"))},
            "items": items["items"],
            "page": items["page"],
            "total_pages": items["totalPages"],
            "total_rows": items["total"],
            "filters": {"status": q.get("status") or "", "orderNo": q.get("orderNo") or ""},
        },
    )


# ==================== S11：机台任务分配（契约 §6.11）====================


def _request_with_id(request: Request) -> Request:
    """合成带合法 X-Request-Id 的 Request（assembly 闭包从 headers 取 trace id）。"""
    scope = dict(request.scope)
    raw = [(k, v) for k, v in request.headers.raw if k.lower() != b"x-request-id"]
    raw.append((b"x-request-id", str(uuid.uuid4()).encode()))
    scope["headers"] = raw

    async def _no_body():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, _no_body)


def _tasks_page(request: Request, user: sqlite3.Row, error: str | None = None):
    page = _page_param(request)
    q = (request.query_params.get("q") or "").strip() or None
    f_status = (request.query_params.get("status") or "").strip() or None
    try:
        tasks_data = _api_endpoint("/api/v1/assembly/tasks")(
            page=page, pageSize=20, q=q, status=f_status, user=user
        )
    except HTTPException as exc:
        return _api_http_error_response(exc)
    c = db()
    try:
        assemblers = c.execute(
            "SELECT id, username, display_name FROM users"
            " WHERE role='ASSEMBLER' AND active=1 ORDER BY username"
        ).fetchall()
    finally:
        c.close()
    names = {row["id"]: row["display_name"] for row in assemblers}
    # /api/v1/assembly/tasks 返回的 members 是驼峰键（assemblerId/assignmentRole），
    # 模板按 snake_case 取值，此处统一归一化，避免 KeyError: 'assembler_id'。
    for _item in tasks_data["items"]:
        _norm = []
        for _m in _item.get("members", []):
            if isinstance(_m, dict):
                _norm.append({
                    "assembler_id": _m.get("assemblerId", _m.get("assembler_id")),
                    "assignment_role": _m.get("assignmentRole", _m.get("assignment_role")),
                })
            else:
                _norm.append(_m)
        _item["members"] = _norm
    assign_ops = {item["id"]: str(uuid.uuid4()) for item in tasks_data["items"]}
    remove_ops = {
        f"{item['id']}:{m['assembler_id']}": str(uuid.uuid4())
        for item in tasks_data["items"]
        for m in item.get("members", [])
    }
    return templates.TemplateResponse(
        request,
        "tasks.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": tasks_data["items"],
            "page": tasks_data["page"],
            "total_pages": tasks_data["totalPages"],
            "total_rows": tasks_data["total"],
            "assemblers": assemblers,
            "status_labels": WORKSPACE_STATUS_LABELS,
            "names": names,
            "assign_ops": assign_ops,
            "remove_ops": remove_ops,

            "error": error,
            "notice_text": NOTICE_TEXTS.get(request.query_params.get("notice", "")),
        },
    )


@router.get("/admin/tasks", response_class=HTMLResponse)
def admin_tasks(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    return _tasks_page(request, user)


@router.post("/admin/tasks/{tid}/assignments")
async def admin_task_assign(request: Request, tid: str):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    assembler_ids = [str(v) for v in form.getlist("assemblerIds")]
    try:
        _api_endpoint("/api/v1/assembly/tasks/{tid}/assignments", method="POST")(
            tid=tid,
            body={"clientOperationId": operation_id, "assemblerIds": assembler_ids},
            request=_request_with_id(request),
            user=user,
            idempotency_key=operation_id,
        )
    except HTTPException as exc:
        return _tasks_page(request, user, error=f"{exc.status_code}：{exc.detail}")
    next_url = str(form.get("next", "") or "")
    return _see_other(next_url if next_url.startswith("/admin/") else "/admin/tasks?notice=assigned")


@router.post("/admin/tasks/{tid}/assignments/{assembler_id}/remove")
async def admin_task_unassign(request: Request, tid: str, assembler_id: str):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        _api_endpoint("/api/v1/assembly/tasks/{tid}/assignments/{assembler_id}", method="DELETE")(
            tid=tid,
            assembler_id=assembler_id,
            request=_request_with_id(request),
            body={"clientOperationId": operation_id},
            user=user,
            idempotency_key=operation_id,
        )
    except HTTPException as exc:
        return _tasks_page(request, user, error=f"{exc.status_code}：{exc.detail}")
    next_url = str(form.get("next", "") or "")
    return _see_other(next_url if next_url.startswith("/admin/") else "/admin/tasks?notice=unassigned")


# ==================== S13：BOM 导入（契约 §6.12）====================

BOM_ROLES = ("ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR")


def _bom_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in BOM_ROLES:
        return None, HTMLResponse(
            "403 禁止访问：BOM 导入仅对管理员/计划员/车间主管开放", status_code=403
        )
    return user, None


@router.get("/admin/boms", response_class=HTMLResponse)
def admin_boms(request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    page = _page_param(request)
    q = request.query_params
    try:
        data = api_bom_versions(
            modelCode=q.get("modelCode") or None,
            status=q.get("status") or None,
            page=page,
            pageSize=20,
            user=user,
        )
    except ApiError as exc:
        return HTMLResponse(_api_error_message(exc), status_code=exc.status_code)
    except HTTPException as exc:
        return _api_http_error_response(exc)
    # 聚合 BOM 批次（智能导入）
    import sqlite3 as _sq3
    from pathlib import Path as _Path
    _db_path = _Path(__file__).parent.parent / "data" / "material_flow.db"
    _c = _sq3.connect(str(_db_path))
    _c.row_factory = _sq3.Row
    agg_batches = _c.execute(
        "SELECT * FROM agg_bom_batches ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    _c.close()
    return templates.TemplateResponse(
        request,
        "boms.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
            "agg_batches": agg_batches,
            "page": data["page"],
            "total_rows": data["total"],
            "filters": {"modelCode": q.get("modelCode") or "", "status": q.get("status") or ""},
            "error": None,
            "notice_text": NOTICE_TEXTS.get(q.get("notice", "")),
        },
    )


@router.post("/admin/boms/import", response_class=HTMLResponse)
async def admin_bom_preview(request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    upload = form.get("file")
    model_code = str(form.get("modelCode", "")).strip()
    if upload is None or not hasattr(upload, "filename") or not model_code:
        return _bom_error_page(request, user, "请选择文件并填写机型码")
    try:
        preview = await api_bom_preview(
            file=upload,
            modelCode=model_code,
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError) as exc:
        return _bom_error_page(request, user, _api_error_message(exc))
    except HTTPException as exc:
        return _bom_error_page(request, user, f"{exc.status_code}：{exc.detail}")
    return templates.TemplateResponse(
        request,
        "bom_preview.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "preview": preview,
            "commit_op": str(uuid.uuid4()),
        },
    )


def _bom_error_page(request: Request, user: sqlite3.Row, error: str):
    data = api_bom_versions(modelCode=None, status=None, page=1, pageSize=20, user=user)
    # 聚合 BOM 批次（智能导入）
    import sqlite3 as _sq3
    from pathlib import Path as _Path
    _db_path = _Path(__file__).parent.parent / "data" / "material_flow.db"
    _c = _sq3.connect(str(_db_path))
    _c.row_factory = _sq3.Row
    agg_batches = _c.execute(
        "SELECT * FROM agg_bom_batches ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    _c.close()
    return templates.TemplateResponse(
        request,
        "boms.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
            "agg_batches": agg_batches,
            "page": data["page"],
            "total_rows": data["total"],
            "filters": {"modelCode": "", "status": ""},
            "error": error,
            "notice_text": None,
        },
    )


@router.post("/admin/boms/import/commit")
async def admin_bom_commit(request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        api_bom_commit(
            BomCommitRequest(
                previewId=str(form.get("previewId", "")),
                clientOperationId=operation_id,
                publish=form.get("publish") == "on",
            ),
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        return _bom_error_page(request, user, _api_error_message(exc))
    except HTTPException as exc:
        return _bom_error_page(request, user, f"{exc.status_code}：{exc.detail}")
    return _see_other("/admin/boms?notice=bom_imported")


# ==================== S14：装配模型上传（契约 §6.13）====================

MODEL_UPLOAD_NOTICE = {
    "模型文件必须是 .glb": "upload_bad_type",
    "文件不是有效 GLB": "upload_bad_glb",
    "模型文件超过 15 MiB": "upload_too_large",
    "modelCode 格式无效": "upload_bad_code",
    "modelName 格式无效": "upload_bad_name",
    "仅 ADMIN 或 WAREHOUSE_ADMIN 可上传模型": "upload_forbidden",
}


@router.post("/admin/models/upload")
async def admin_model_upload(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    upload = form.get("file")
    try:
        await api_upload_model(
            modelCode=str(form.get("modelCode", "")),
            modelName=str(form.get("modelName", "")),
            file=upload,  # type: ignore[arg-type]  # form["file"] 即 UploadFile
            sha256=str(form.get("sha256", "")).strip() or None,
            user=user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError) as exc:
        notice = MODEL_UPLOAD_NOTICE.get(_api_error_message(exc), "upload_failed")
        return _see_other(f"/admin/models?notice={notice}")
    except HTTPException as exc:
        notice = MODEL_UPLOAD_NOTICE.get(str(getattr(exc, "detail", "")), "upload_failed")
        return _see_other(f"/admin/models?notice={notice}")
    return _see_other("/admin/models?notice=model_uploaded")


@router.post("/admin/models/bind-device")
async def admin_device_bind_model(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    device_id = str(form.get("deviceId", "")).strip()
    model_code = str(form.get("model3dCode", "")).strip() or None
    if not device_id:
        return _see_other("/admin/models?notice=bind_failed")
    try:
        api_bind_device_model_3d(
            device_id,
            DeviceModel3dBind(clientOperationId=uuid.uuid4(), model3dCode=model_code),
            user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError, HTTPException):
        return _see_other("/admin/models?notice=bind_failed")
    return _see_other("/admin/models?notice=model_bound")


# ==================== S15：CSV 数据导出（契约 §6.14）====================


def _csv_response(filename: str, rows: list[dict]) -> Response:
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    body = "\ufeff" + out.getvalue()  # Excel 友好的 UTF-8 BOM
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


def _collect_all(fn, page_size: int = 100, **kwargs) -> list[dict]:
    """按 API 单页上限分页收集全部行（导出不留尾页；page_size 须在各 API 合法范围内）。"""
    rows: list[dict] = []
    page = 1
    while True:
        data = fn(page=page, pageSize=page_size, **kwargs)
        batch = data.get("items") or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        page += 1


@router.get("/admin/audit/export")
def admin_audit_export(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    q = request.query_params
    rows = _collect_all(
        api_audit_logs,
        from_=q.get("from") or None,
        to_=q.get("to") or None,
        eventType=q.get("eventType") or None,
        entityType=q.get("entityType") or None,
        operatorId=q.get("operatorId") or None,
        action=None,
        resourceType=None,
        resourceId=None,
        entityId=q.get("entityId") or None,
        user=user,
    )
    return _csv_response("audit-logs", rows)


@router.get("/admin/workspace/export")
def admin_workspace_export(request: Request):
    user, denied = _workspace_or_403(request)
    if denied:
        return denied
    q = request.query_params
    rows = _collect_all(
        api_workspace_items,
        viewRole=None,
        status=q.get("status") or None,
        orderNo=q.get("orderNo") or None,
        user=user,
        x_request_id=str(uuid.uuid4()),
        x_client_operation_id=None,
    )
    return _csv_response("material-workspace", rows)


@router.get("/admin/reports/labor/export")
def admin_labor_export(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    q = request.query_params
    rows = _collect_all(
        _api_endpoint("/api/v1/workshop/labor-summary"),
        page_size=20,
        deviceId=q.get("deviceId") or None,
        orderNo=q.get("orderNo") or None,
        assemblerId=q.get("assemblerId") or None,
        user=user,
    )
    return _csv_response("labor-summary", rows)


@router.get("/admin/reports/machines/export")
def admin_machines_export(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    rows = _collect_all(_api_endpoint("/api/v1/workshop/machine-progress"), page_size=20, user=user, deviceId=None)
    return _csv_response("machine-progress", rows)


# ==================== S21：生产订单创建面（契约 §6.16）====================

ORDER_CREATE_ROLES = ("ADMIN", "PLANNER")


def _order_create_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in ORDER_CREATE_ROLES:
        return None, HTMLResponse(
            "403 禁止访问：创建订单仅对管理员/计划员开放", status_code=403
        )
    return user, None


def _order_form_page(request: Request, user: sqlite3.Row, values: dict, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "order_form.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "values": values,
            "error": error,
        },
    )


@router.get("/admin/orders/new", response_class=HTMLResponse)
def admin_order_new(request: Request):
    user, denied = _order_create_or_403(request)
    if denied:
        return denied
    return _order_form_page(request, user, values={})


@router.post("/admin/orders/create")
async def admin_order_create(request: Request):
    user, denied = _order_create_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    values = {k: str(v) for k, v in form.items() if k != "csrf_token"}
    models = []
    for i in range(1, 6):
        code = values.get(f"modelCode{i}", "").strip()
        if not code:
            continue
        models.append(
            OrderModelCreate(
                modelCode=code,
                modelName=values.get(f"modelName{i}", "").strip(),
                plannedQuantity=int(values.get(f"modelQty{i}", "0") or 0),
            )
        )
    try:
        api_create_order(
            OrderCreateRequest(
                clientOperationId=str(uuid.uuid4()),
                orderNo=values.get("orderNo", "").strip(),
                productName=values.get("productName", "").strip(),
                models=models,
            ),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError) as exc:
        return _order_form_page(request, user, values=values, error=_api_error_message(exc))
    except HTTPException as exc:
        return _order_form_page(request, user, values=values, error=f"{exc.status_code}：{exc.detail}")
    return _see_other("/admin/orders?notice=order_created")


# ==================== S22：订单状态推进（契约 §6.17）====================


@router.post("/admin/orders/{order_no}/status")
async def admin_order_status(request: Request, order_no: str):
    user, denied = _order_create_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        api_set_order_status(
            order_no,
            OrderStatusRequest(clientOperationId=operation_id, status=str(form.get("status", ""))),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError) as exc:
        msg = _api_error_message(exc)
        return HTMLResponse(
            f"<p class='error'>{msg}</p><p><a href='/admin/orders'>返回订单列表</a></p>",
            status_code=200,
        )
    except HTTPException as exc:
        return HTMLResponse(
            f"<p class='error'>{exc.status_code}：{exc.detail}</p><p><a href='/admin/orders'>返回订单列表</a></p>",
            status_code=200,
        )
    return _see_other("/admin/orders?notice=order_status_changed")


# ==================== S23：会话强制下线（契约 §6.18）====================


@router.post("/admin/users/{user_id}/revoke-sessions")
async def admin_revoke_sessions(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    try:
        api_revoke_sessions(
            user_id,
            SessionRevokeRequest(clientOperationId=str(uuid.uuid4())),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError) as exc:
        return HTMLResponse(
            f"<p class='error'>{_api_error_message(exc)}</p><p><a href='/admin/users'>返回用户列表</a></p>",
            status_code=200,
        )
    except HTTPException as exc:
        return HTMLResponse(
            f"<p class='error'>{exc.status_code}：{exc.detail}</p><p><a href='/admin/users'>返回用户列表</a></p>",
            status_code=200,
        )
    return _see_other("/admin/users?notice=sessions_revoked")


# ==================== S24：装配任务创建（契约 §6.19）====================

TASK_CREATE_ROLES = ("ADMIN", "WORKSHOP_SUPERVISOR")


@router.post("/admin/tasks/create")
async def admin_task_create(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in TASK_CREATE_ROLES:
        return HTMLResponse(
            "403 禁止访问：创建任务仅对管理员/车间主管开放", status_code=403
        )
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    try:
        api_create_task(
            TaskCreateRequest(
                clientOperationId=str(uuid.uuid4()),
                orderNo=str(form.get("orderNo", "")).strip(),
                deviceId=str(form.get("deviceId", "")).strip(),
                deviceNo=str(form.get("deviceNo", "")).strip(),
            ),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError) as exc:
        return HTMLResponse(
            f"<p class='error'>{_api_error_message(exc)}</p><p><a href='/admin/tasks'>返回任务列表</a></p>",
            status_code=200,
        )
    except HTTPException as exc:
        return HTMLResponse(
            f"<p class='error'>{exc.status_code}：{exc.detail}</p><p><a href='/admin/tasks'>返回任务列表</a></p>",
            status_code=200,
        )
    return _see_other("/admin/tasks?notice=task_created")


# ==================== 条码生成与打印 ====================
try:
    from . import barcodes as _barcodes
except ImportError:
    import barcodes as _barcodes

KIND_NAMES = {"qr": "二维码", "code128": "一维码 Code128"}


def _barcode_preview(entity: str, key: str, kind: str):
    """查出规范编号并生成预览数据；返回 (payload, error)。"""
    if entity not in _barcodes.ENTITIES or kind not in _barcodes.KINDS:
        return None, "参数无效"
    c = db()
    try:
        payload = _barcodes.resolve_payload(entity, key, c)
    finally:
        c.close()
    if payload is None:
        return None, "编号不存在"
    return payload, None


def _barcode_label(entity: str) -> str:
    """条码图下方的文字说明用的实体展示名，如 “机台”。"""
    return _barcodes.ENTITIES.get(entity, ("", "", ""))[2]


@router.get("/admin/barcodes", response_class=HTMLResponse)
def admin_barcodes(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    entity = request.query_params.get("entity", "order")
    key = request.query_params.get("key", "").strip()
    kind = request.query_params.get("kind", "qr")
    try:
        copies = max(1, min(50, int(request.query_params.get("copies", "1"))))
    except ValueError:
        copies = 1
    preview, error = None, None
    if key:
        payload, error = _barcode_preview(entity, key, kind)
        if payload is not None:
            preview = {
                "payload": payload,
                "label": _barcodes.ENTITIES[entity][2],
                "img": _barcodes.data_uri(payload, kind, "png", _barcode_label(entity)),
            }
    return templates.TemplateResponse(
        request,
        "barcodes.html",
        {
            "user": user,
            "entity": entity,
            "key": key,
            "kind": kind,
            "copies": copies,
            "preview": preview,
            "error": error,
            "csrf_token": user["csrf_token"],
        },
    )


@router.get("/admin/barcodes/print", response_class=HTMLResponse)
def admin_barcodes_print(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    entity = request.query_params.get("entity", "order")
    key = request.query_params.get("key", "").strip()
    kind = request.query_params.get("kind", "qr")
    try:
        copies = max(1, min(50, int(request.query_params.get("copies", "1"))))
    except ValueError:
        copies = 1
    if not key:
        return HTMLResponse("缺少编号参数", status_code=400)
    payload, error = _barcode_preview(entity, key, kind)
    if error:
        return HTMLResponse(error, status_code=404)
    label = _barcodes.ENTITIES[entity][2]
    img = _barcodes.data_uri(payload, kind, "svg", _barcode_label(entity))
    items = [{"payload": payload, "img": img} for _ in range(copies)]
    return templates.TemplateResponse(
        request,
        "barcodes_print.html",
        {
            "user": user,
            "entity": entity,
            "payload": payload,
            "label": label,
            "kind": kind,
            "kind_name": KIND_NAMES[kind],
            "copies": copies,
            "items": items,
            "csrf_token": user["csrf_token"],
        },
    )


MATERIAL_CATEGORIES = ("电气", "机械", "其他")


@router.post("/admin/materials/category")
async def admin_material_category(request: Request):
    """手动设置物料分类（暂定仅管理员可操作）。"""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return JSONResponse({"ok": False, "error": "CSRF 校验失败"}, status_code=403)
    code = str(form.get("code", "")).strip()
    category = str(form.get("category", "")).strip()
    if not code:
        return JSONResponse({"ok": False, "error": "缺少物料编码"}, status_code=400)
    if category and category not in MATERIAL_CATEGORIES:
        return JSONResponse({"ok": False, "error": "分类无效，仅支持：电气/机械/其他"}, status_code=400)
    c = db()
    try:
        row = c.execute("SELECT code FROM materials WHERE code=?", (code,)).fetchone()
        if row is None:
            return JSONResponse({"ok": False, "error": "物料不存在"}, status_code=404)
        c.execute("UPDATE materials SET category=? WHERE code=?", (category or None, code))
        c.commit()
    finally:
        c.close()
    return JSONResponse({"ok": True, "code": code, "category": category})


@router.get("/admin/barcodes/image")
def admin_barcodes_image(request: Request):
    """管理台会话鉴权的条码图片下载（浏览器直接点击可用，无需 Bearer Token）。
    条码内容即页面上已可见的编号本身，不新增数据暴露，故允许所有已登录用户访问，
    以便非管理员角色的列表页也能内嵌展示。"""
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    entity = request.query_params.get("entity", "order")
    key = request.query_params.get("key", "").strip()
    kind = request.query_params.get("kind", "qr")
    image = request.query_params.get("image", "png")
    if not key:
        raise HTTPException(400, "缺少编号参数")
    payload, error = _barcode_preview(entity, key, kind)
    if error:
        raise HTTPException(404, error)
    try:
        data, media = _barcodes.render(payload, kind, image, _barcode_label(entity))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in payload)
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{entity}-{safe}.{image}"',
            "Cache-Control": "private, max-age=86400",
        },
    )

@router.get("/admin/agent", response_class=HTMLResponse)
def admin_agent(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return HTMLResponse("403 禁止访问：管理界面仅对管理员开放", status_code=403)
    return templates.TemplateResponse(
        request,
        "agent.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
        },
    )

# 智能助手 session 认证包装接口（供 /admin/agent 页面 JS 调用）
from app.agent import service as agent_service
from app.agent.config import AgentConfig
from app.agent.routes import _agent_cfg as _get_agent_cfg

@router.post("/admin/agent/chat")
async def admin_agent_chat(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "消息不能为空"}}, status_code=400)
    session_id = body.get("session_id")
    import asyncio
    def _run():
        c = db()
        try:
            cfg = _agent_cfg_effective(c)
            return agent_service.run_chat(c, cfg, user["id"], session_id, message)
        finally:
            c.close()
    try:
        try:
            result = await asyncio.to_thread(_run)
        except Exception as e:
            return JSONResponse({"error": {"code": "AGENT_ERROR", "message": str(e)[:500]}}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": {"code": "AGENT_CONFIG_ERROR", "message": str(e)[:200]}}, status_code=503)
    return JSONResponse(result)


# 智能导入 Session 包装（管理后台用 Web Session，不走 Token）
from fastapi import UploadFile as _UploadFile, File as _File, Form as _Form
from app.agent import importer as _agent_importer
from app.agent.config import AgentConfig as _AgentConfig

@router.post("/admin/agent/import/preview")
async def admin_agent_import_preview(request: Request, file: _UploadFile = _File(...), target: str = _Form(...)):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    if user["role"] not in ("ADMIN", "WAREHOUSE_ADMIN", "PLANNER"):
        return JSONResponse({"error": {"code": "FORBIDDEN", "message": "无权执行智能导入"}}, status_code=403)
    if target not in _agent_importer.TARGETS:
        return JSONResponse({"error": {"code": "AGENT_BAD_TARGET", "message": "不支持的导入目标: " + target}}, status_code=400)
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        return JSONResponse({"error": {"code": "AGENT_FILE_TOO_LARGE", "message": "文件超过 10MB"}}, status_code=413)
    file_name = file.filename or "upload.bin"
    try:
        parsed = _agent_importer.parse_upload(file_name, raw)
    except ValueError as e:
        return JSONResponse({"error": {"code": "AGENT_BAD_FILE", "message": str(e)}}, status_code=400)
    c = db()
    try:
        cfg = _agent_cfg_effective(c)
        column_map = _agent_importer._fallback_mapping(target, parsed["headers"])
        mapped_rows = _agent_importer.apply_column_mapping(parsed, column_map)
        valid_rows, errors = _agent_importer.validate_rows(target, mapped_rows, c)
        job_id = _agent_importer.create_import_job(c, user["id"], target, file_name, raw, column_map, valid_rows, errors)
    finally:
        c.close()
    import hashlib as _hashlib
    return JSONResponse({
        "jobId": job_id, "target": target, "fileName": file_name,
        "fileSha256": _hashlib.sha256(raw).hexdigest(),
        "columnMap": column_map, "totalRows": parsed["total_rows"],
        "validRows": len(valid_rows), "invalidRows": len(errors),
        "canCommit": bool(valid_rows),
        "errors": errors[:100], "preview": valid_rows[:20],
    })

@router.post("/admin/agent/import/commit")
async def admin_agent_import_commit(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    if user["role"] not in ("ADMIN", "WAREHOUSE_ADMIN", "PLANNER"):
        return JSONResponse({"error": {"code": "FORBIDDEN", "message": "无权执行智能导入"}}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    job_id = body.get("job_id") or body.get("jobId")
    if not job_id:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "缺少 job_id"}}, status_code=400)
    import uuid as _uuid
    c = db()
    try:
        try:
            result = _agent_importer.commit_import_job(c, user["id"], job_id, body.get("client_operation_id") or str(_uuid.uuid4()))
        except ValueError as e:
            return JSONResponse({"error": {"code": "AGENT_IMPORT_ERROR", "message": str(e)[:500]}}, status_code=400)
    finally:
        c.close()
    return JSONResponse(result)


# BOM 导入 Session 包装（智能助手页用）
@router.post("/admin/agent/import/bom/preview")
async def admin_agent_bom_preview(request: Request, file: _UploadFile = _File(...), model_code: str = _Form(...)):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    try:
        preview = await api_bom_preview(file=file, modelCode=model_code.strip(), user=user, x_request_id=str(__import__("uuid").uuid4()))
    except Exception as e:
        from app.main import ApiError as _ApiError
        if isinstance(e, _ApiError):
            return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status_code)
        return JSONResponse({"error": {"code": "BOM_ERROR", "message": str(e)[:500]}}, status_code=500)
    return JSONResponse(preview)

@router.post("/admin/agent/import/bom/commit")
async def admin_agent_bom_commit(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    preview_id = body.get("preview_id") or body.get("previewId")
    if not preview_id:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "缺少 preview_id"}}, status_code=400)
    import uuid as _uuid2
    from pydantic import BaseModel as _BM
    class _BomCommit(_BM):
        previewId: str
        clientOperationId: str
    try:
        result = api_bom_commit(
            body=_BomCommit(previewId=preview_id, clientOperationId=body.get("client_operation_id") or str(_uuid2.uuid4())),
            user=user, x_request_id=str(_uuid2.uuid4()),
            idempotency_key=body.get("client_operation_id") or str(_uuid2.uuid4()),
        )
    except Exception as e:
        from app.main import ApiError as _ApiError2
        if isinstance(e, _ApiError2):
            return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status_code)
        return JSONResponse({"error": {"code": "BOM_ERROR", "message": str(e)[:500]}}, status_code=500)
    return JSONResponse(result)


# 多机型 BOM 聚合
@router.get("/admin/agent/bom/models")
async def admin_agent_bom_models(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    c = db()
    try:
        rows = c.execute("""
            SELECT m.model_code, m.model_name,
                   (SELECT COUNT(*) FROM bom_versions v WHERE v.model_code = m.model_code AND v.status = 'PUBLISHED') as pub_count
            FROM production_order_models m ORDER BY m.model_code
        """).fetchall()
        models = [{"model_code": r["model_code"], "model_name": r["model_name"], "has_bom": r["pub_count"] > 0} for r in rows]
    finally:
        c.close()
    return JSONResponse({"models": models})

@router.get("/admin/agent/bom/aggregate")
async def admin_agent_bom_aggregate(request: Request, model_codes: str = ""):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    codes = [c.strip() for c in model_codes.split(",") if c.strip()]
    if not codes:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请选择机型"}}, status_code=400)
    c = db()
    try:
        agg = {}
        per_model = {}
        for code in codes:
            v = c.execute(
                "SELECT id FROM bom_versions WHERE model_code = ? AND status = 'PUBLISHED' ORDER BY version_no DESC LIMIT 1",
                (code,)).fetchone()
            if not v:
                per_model[code] = {"error": "无已发布 BOM"}
                continue
            items = c.execute(
                "SELECT material_code, material_name, specification, unit, quantity FROM bom_items WHERE bom_version_id = ?",
                (v["id"],)).fetchall()
            per_model[code] = {"rows": len(items)}
            for it in items:
                mc = it["material_code"]
                if mc not in agg:
                    agg[mc] = {"material_code": mc, "material_name": it["material_name"],
                               "specification": it["specification"], "unit": it["unit"],
                               "total_quantity": 0, "models": []}
                agg[mc]["total_quantity"] += it["quantity"] or 0
                agg[mc]["models"].append(code)
        result = sorted(agg.values(), key=lambda x: -x["total_quantity"])
        for r in result:
            r["models"] = sorted(set(r["models"]))
            r["model_count"] = len(r["models"])
    finally:
        c.close()
    return JSONResponse({"materials": result, "per_model": per_model, "model_count": len(codes)})


# 聚合 BOM 导入 Session 包装（智能助手页用）
@router.post("/admin/agent/import/aggbom/preview")
async def admin_agent_aggbom_preview(request: Request, file: _UploadFile = _File(...)):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    form_data = await request.form()
    upload = _UploadFile(file=file.file, filename=file.filename)
    try:
        from app.main import preview_agg_bom as _preview_aggbom
        preview = await _preview_aggbom(file=upload, user=user, x_request_id=str(__import__("uuid").uuid4()))
    except Exception as e:
        from app.main import ApiError as _ApiError
        if isinstance(e, _ApiError):
            return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status_code)
        return JSONResponse({"error": {"code": "AGGBOM_ERROR", "message": str(e)[:500]}}, status_code=500)
    return JSONResponse(preview)

@router.post("/admin/agent/import/aggbom/commit")
async def admin_agent_aggbom_commit(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    preview_id = body.get("preview_id") or body.get("previewId")
    if not preview_id:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "缺少 preview_id"}}, status_code=400)
    import uuid as _uuid3
    from app.main import AggBomCommitRequest as _AggReq, commit_agg_bom as _commit_aggbom
    op_id = body.get("client_operation_id") or str(_uuid3.uuid4())
    try:
        result = _commit_aggbom(
            body=_AggReq(previewId=preview_id, clientOperationId=op_id),
            user=user, x_request_id=str(_uuid3.uuid4()), idempotency_key=op_id,
        )
    except Exception as e:
        from app.main import ApiError as _ApiError3
        if isinstance(e, _ApiError3):
            return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status_code)
        return JSONResponse({"error": {"code": "AGGBOM_ERROR", "message": str(e)[:500]}}, status_code=500)
    return JSONResponse(result)

@router.get("/admin/agent/aggbom/batches")
async def admin_agent_aggbom_batches(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    from app.main import list_agg_bom_batches as _list_batches
    return JSONResponse(_list_batches(user=user))

@router.get("/admin/agent/aggbom/aggregate")
async def admin_agent_aggbom_aggregate(request: Request, batch_id: str = "", device_codes: str = ""):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    from app.main import aggregate_agg_bom as _agg
    try:
        return JSONResponse(_agg(batch_id=batch_id, device_codes=device_codes, user=user))
    except Exception as e:
        from app.main import ApiError as _ApiError4
        if isinstance(e, _ApiError4):
            return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status_code)
        return JSONResponse({"error": {"code": "AGGBOM_ERROR", "message": str(e)[:500]}}, status_code=500)

# 智能助手 LLM 设置（存数据库，覆盖环境变量）
import time as _time

def _agent_settings_get(c):
    rows = c.execute("SELECT key, value FROM agent_settings").fetchall()
    return {r[0]: r[1] for r in rows}

def _agent_cfg_effective(c, trace_id="admin"):
    from dataclasses import replace
    cfg = _get_agent_cfg(trace_id)
    s = _agent_settings_get(c)
    kw = {}
    if s.get("base_url"): kw["base_url"] = s["base_url"]
    if s.get("api_key"): kw["api_key"] = s["api_key"]
    if s.get("model"): kw["model"] = s["model"]
    if s.get("enabled") in ("0", "1"): kw["enabled"] = s["enabled"] == "1"
    if kw: cfg = replace(cfg, **kw)
    return cfg

@router.get("/admin/agent/settings")
def admin_agent_settings_get(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    c = db()
    try:
        s = _agent_settings_get(c)
    finally:
        c.close()
    # api_key 脱敏显示
    masked = dict(s)
    if masked.get("api_key"):
        k = masked["api_key"]
        masked["api_key"] = k[:4] + "****" + k[-4:] if len(k) > 8 else "****"
        masked["api_key_set"] = True
    else:
        masked["api_key_set"] = False
    return JSONResponse({"settings": masked})

@router.post("/admin/agent/settings")
async def admin_agent_settings_set(request: Request):
    user = _session_user(request)
    if user is None:
        return JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    if user["role"] not in ("ADMIN",):
        return JSONResponse({"error": {"code": "FORBIDDEN", "message": "仅管理员可修改"}}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    allowed = ("base_url", "api_key", "model", "enabled")
    c = db()
    try:
        now_ts = int(_time.time())
        for k in allowed:
            if k in body and body[k] is not None:
                v = str(body[k]).strip()
                # api_key 为空字符串表示不修改（前端脱敏显示时）
                if k == "api_key" and v == "":
                    continue
                c.execute(
                    "INSERT INTO agent_settings(key, value, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (k, v, now_ts),
                )
        c.commit()
    finally:
        c.close()
    return JSONResponse({"ok": True})
