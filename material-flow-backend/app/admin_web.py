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
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse
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
    execute_transfer as api_execute_transfer,
    get_transfer,
    handover_timeline,
    inventory as api_inventory,
    list_transfers,
    list_exceptions as api_list_exceptions,
    list_stocktakes as api_list_stocktakes,
    list_bom_versions as api_bom_versions,
    now,
    order_detail as api_order_detail,
    preview_bom_import as api_bom_preview,
    reset_employee_password,
    review_exception as api_review_exception,
    upload_assembly_model as api_upload_model,
    app as backend_app,
    workspace_material_items as api_workspace_items,
    workspace_summary as api_workspace_summary,
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
        return _see_other("/admin/")
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
    c = db()
    try:
        rows = c.execute(
            "SELECT u.id, u.username, u.display_name, u.role, u.active,"
            " u.must_change_password, u.created_at, m.display_name AS manager_name"
            " FROM users u LEFT JOIN employee_managers em ON em.employee_id=u.id"
            " LEFT JOIN users m ON m.id=em.manager_id ORDER BY u.created_at"
        ).fetchall()
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


# ==================== S3：流转审批与交接留痕（契约 §6.3）====================

MANAGER_ROLES = ("ADMIN", "WAREHOUSE_ADMIN")

TRANSFER_STATUS_LABELS = {
    "PENDING_APPROVAL": "待审批",
    "APPROVED": "已审批",
    "REJECTED": "已驳回",
    "EXECUTED": "已执行",
}


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
        data = list_transfers(status=status, user=user)
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
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "flow_detail.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "item": item,
            "handovers": handover_rows,
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
    return templates.TemplateResponse(
        request,
        "handovers.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "query": query,
            "result": result,
            "error": error,
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
        },
    )


# ==================== S4：工时汇总与车间概览（契约 §6.4）====================

REPORT_ROLES = ("ADMIN", "WORKSHOP_SUPERVISOR")


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
            user=user, page=page, pageSize=20
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
    c = db()
    try:
        if code:
            rows = c.execute(
                "SELECT id, model_code, model_name, version, format, byte_size, sha256,"
                " status, created_by, created_at FROM assembly_model_versions"
                " WHERE model_code=? ORDER BY version DESC LIMIT 200",
                (code,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, model_code, model_name, version, format, byte_size, sha256,"
                " status, created_by, created_at FROM assembly_model_versions"
                " ORDER BY model_code, version DESC LIMIT 200"
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
    return templates.TemplateResponse(
        request,
        "models.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "rows": rows,
            "code": code,
            "published": published,
            "published_error": published_error,
            "status_labels": MODEL_STATUS_LABELS,
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
                order_no=order_no, page=_page_param(request), pageSize=20, user=user
            )
        except ApiError as exc:
            error = _api_error_message(exc)
        except HTTPException as exc:
            error = f"{exc.status_code}：{exc.detail}"
    return templates.TemplateResponse(
        request,
        "orders.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "rows": rows,
            "order_no": order_no,
            "detail": detail,
            "error": error,
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
            "summary": summary,
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
    device_id = request.query_params.get("deviceId") or None
    try:
        tasks_data = _api_endpoint("/api/v1/assembly/tasks")(
            page=page, pageSize=20, deviceId=device_id, user=user
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
            "names": names,
            "assign_ops": assign_ops,
            "remove_ops": remove_ops,
            "device_id": device_id or "",
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
    return _see_other(f"/admin/tasks?notice=assigned")


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
    return _see_other(f"/admin/tasks?notice=unassigned")


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
    return templates.TemplateResponse(
        request,
        "boms.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
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
    return templates.TemplateResponse(
        request,
        "boms.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
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
    rows = _collect_all(_api_endpoint("/api/v1/workshop/machine-progress"), page_size=20, user=user)
    return _csv_response("machine-progress", rows)
