"""Web 管理界面（SSR）。契约：docs/admin-web-contract.md。

路由挂载于 /admin/*；鉴权基于独立 web_sessions 表 + mf_session Cookie，
不与 APP 的 Bearer/refresh 会话语义混用。所有 POST 表单必须携带 CSRF token。
"""
from __future__ import annotations

import csv
import hmac
import html
import io
import logging
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.main import (
    APP_VERSION,
    LOGIN_LOCK_SECONDS,
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    ApiError,
    _request_trace_id,
    _safe_trace_id,
    BomCommitRequest,
    Decision,
    DeleteUserRequest,
    EditUserRequest,
    EmployeeCreate,
    PasswordResetRequest,
    ROLES,
    TransferAction,
    TransferApproval,
    Transfer,
    TransferItem,
    ExceptionCreate,
    TRANSFER_CREATE_ROLES,
    EXCEPTION_CREATE_ROLES,
    create_transfer as api_create_transfer,
    create_exception as api_create_exception,
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
    stocktakes_page,
    stocktakes_count,
    exceptions_page,
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
    _workspace_context,
    _workspace_filter_status,
    _workspace_items,
    _workspace_summary_flow_counts,
    _workspace_summary_payload,
    audit_logs_export_items,
    machine_progress_export_items,
    UPLOAD_DIR,
    MODEL_CODE_RE,
    WORKSPACE_STATUS_LABELS,
)

router = APIRouter()
_log = logging.getLogger("material_flow.admin")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
from app.web_assets import static_url as _static_url  # noqa: E402
templates.env.globals["static_url"] = _static_url
from app import ui_labels as _ui_labels  # noqa: E402
_ui_labels.register(templates.env)

SESSION_COOKIE = "mf_session"
ANON_CSRF_COOKIE = "anon_csrf"
SESSION_SECONDS = 8 * 3600  # 8 小时；v0.6.3 起滑动续期：活跃使用时剩余不足一半即顺延（契约 §5.2 已更新）
DEFAULT_PAGE_SIZE = 20  # 列表页默认分页大小
# S1 准入仅 ADMIN；S3 起放宽 WAREHOUSE_ADMIN（契约 §1）
WEB_ROLES = ("ADMIN",)


def _see_other(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


def _csrf_ok(form_value: str | None, expected: str | None) -> bool:
    return bool(form_value) and bool(expected) and hmac.compare_digest(form_value, expected)


# ---------- 管理页 HTML 错误页（v0.6.3）----------
ERROR_TITLES = {400: "请求有误", 403: "禁止访问", 404: "页面不存在", 405: "请求方式不支持",
                413: "内容过大", 500: "服务器内部错误", 503: "服务暂时不可用"}
_ERROR_PREFIX_RE = re.compile(r"^\s*\d{3}\s*")


def _error_page(request: Request, status_code: int, message: str = "", *, trace_id: str | None = None,
                user: sqlite3.Row | None = None, title: str | None = None,
                back_url: str | None = None, back_label: str | None = None) -> HTMLResponse:
    """/admin 页面的 403/404/500 等：继承 base.html 的样式化错误页，带 traceId。"""
    if user is None:
        try:
            user = _session_user(request)
        except Exception:  # noqa: BLE001 — 错误页本身不能再抛
            user = None
    title = title or ERROR_TITLES.get(status_code, "出错了")
    text = _ERROR_PREFIX_RE.sub("", message or "") or title
    if text.startswith(title + "：") or text.startswith(title + ":"):
        text = text[len(title) + 1:].strip() or title
    if status_code == 403 and "CSRF" in text and "刷新" not in text:
        text += "，请刷新页面后重试"
    trace = trace_id or _request_trace_id(request)
    try:
        return templates.TemplateResponse(
            request, "error.html",
            {"user": user, "status_code": status_code, "title": title, "message": text, "trace_id": trace,
             "back_url": back_url if (back_url or "").startswith("/admin/") else None, "back_label": back_label},
            status_code=status_code, headers={"Cache-Control": "no-store"},
        )
    except Exception:  # noqa: BLE001 — 模板异常时退回纯文本，保证错误页可用
        _log.exception("error page render failed traceId=%s", trace)
        return HTMLResponse(html.escape(f"{status_code} {title}（traceId: {trace}）"), status_code=status_code)


def _action_failed(request: Request, exc: Exception, back_url: str, back_label: str) -> HTMLResponse:
    """管理页表单 POST 业务失败（v0.6.3）：统一走样式化错误页，文案经模板自动转义，不再拼 f-string HTML。"""
    if isinstance(exc, HTTPException) and not isinstance(exc, ApiError):
        status, msg = exc.status_code, str(exc.detail or "")
    else:
        status = exc.status_code if isinstance(exc, ApiError) else 422
        msg = _api_error_message(exc)
    if status < 400:
        status = 400
    return _error_page(request, status, msg, title="操作失败", back_url=back_url, back_label=back_label)


def wants_admin_html(request: Request) -> bool:
    """全局异常处理器用：浏览器导航到 /admin 页面（非 JSON 接口）时渲染 HTML 错误页。"""
    path = request.url.path
    if not path.startswith("/admin") or path.startswith("/admin/agent/"):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def _session_user(request: Request) -> sqlite3.Row | None:
    """当前 web 会话用户。同一请求内结果缓存在 request.state（错误页 / 守卫重复调用不再查库）。

    滑动续期（v0.6.3，评审 P2-14）：剩余有效期不足一半时把 expires_at 顺延到 now + SESSION_SECONDS，
    并由 SessionCookieRefreshMiddleware 在响应里重发 Cookie（Max-Age 同步顺延）。每个会话最多每 4 小时写一次库。
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    state = request.scope.setdefault("state", {})
    cached = state.get("mf_session_user")
    if cached is not None and cached[0] == token:
        return cached[1]
    now_ts = int(time.time())
    c = db()
    try:
        row = c.execute(
            "SELECT ws.csrf_token AS csrf_token, ws.expires_at AS expires_at,"
            " 'web' AS session_device_id, u.*"
            " FROM web_sessions ws JOIN users u ON u.id = ws.user_id"
            " WHERE ws.id=? AND u.active=1",
            (token,),
        ).fetchone()
        if row is not None and row["expires_at"] >= now_ts and row["expires_at"] - now_ts < SESSION_SECONDS // 2:
            c.execute(
                "UPDATE web_sessions SET expires_at=?, last_seen_at=? WHERE id=?",
                (now_ts + SESSION_SECONDS, now_ts, token),
            )
            c.commit()
            state["mf_session_refresh"] = token
    finally:
        c.close()
    if row is None or row["expires_at"] < now_ts:
        row = None
    state["mf_session_user"] = (token, row)
    return row


def _purge_expired_web_sessions(c: sqlite3.Connection) -> int:
    """删除已过期的 web 会话（登录成功时与服务启动时执行）。"""
    return c.execute("DELETE FROM web_sessions WHERE expires_at < ?", (int(time.time()),)).rowcount


class SessionCookieRefreshMiddleware:
    """_session_user 续期了会话时，在响应头追加刷新后的 mf_session Cookie（Max-Age 顺延）。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/admin"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                token = (scope.get("state") or {}).get("mf_session_refresh")
                if token:
                    from http.cookies import SimpleCookie
                    cookie = SimpleCookie()
                    cookie[SESSION_COOKIE] = token
                    morsel = cookie[SESSION_COOKIE]
                    morsel["max-age"] = SESSION_SECONDS
                    morsel["path"] = "/"
                    morsel["httponly"] = True
                    morsel["samesite"] = "lax"
                    headers = list(message.get("headers", []))
                    headers.append((b"set-cookie", morsel.OutputString().encode("latin-1")))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


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
        return _error_page(request, 403, "CSRF 校验失败")
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    user = _authenticate(username, password) if username and password else None
    if user is None:
        return _see_other("/admin/login?error=1")
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    c = db()
    try:
        _purge_expired_web_sessions(c)
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
        return _error_page(request, 403, "CSRF 校验失败")
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
    # 首页即异常处理页（原仪表盘 home.html 已下线，v0.6.3 删除死代码）
    return _see_other("/admin/exceptions")


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


def _coded_notice(request: Request, mapping: dict[str, tuple[str, str]], unknown_error: str | None = None) -> dict:
    """?notice=<代码> → 固定文案（{"notice_text", "notice_error"}）。不回显原始参数，避免伪造提示。"""
    code = request.query_params.get("notice", "")
    if not code:
        return {"notice_text": None, "notice_error": None}
    kind, text = mapping.get(code, ("error", unknown_error) if unknown_error else ("notice", None))
    return {"notice_text": text if kind == "notice" else None, "notice_error": text if kind == "error" else None}


RELEASE_NOTICES: dict[str, tuple[str, str]] = {
    "uploaded": ("notice", "上传成功，已发布。"),
    "deleted": ("notice", "已删除。"),
    "not_found": ("error", "未找到该版本。"),
    "no_file": ("error", "请选择 APK 文件。"),
    "bad_version": ("error", "版本号必须为正整数。"),
    "INVALID_VERSION_CODE": ("error", "版本号必须为正整数。"),
    "INVALID_APK": ("error", "文件不是有效的 APK。"),
    "APK_TOO_LARGE": ("error", "APK 超过 300MB 上限。"),
    "RELEASE_EXISTS": ("error", "该版本号已存在，请先删除旧版。"),
    "INVALID_VERSION_NAME": ("error", "版本名称格式无效（仅允许字母、数字和 . _ -）。"),
    "sync_started": ("notice", "已开始从远端同步，下载完成后自动发布。页面会自动刷新进度。"),
    "sync_already": ("notice", "该版本已同步过，本地已是该版本。"),
    "APP_SYNC_BUSY": ("error", "已有同步任务在进行，请稍候。"),
    "APP_SYNC_NOT_FOUND": ("error", "远端没有找到可同步的安装包（附件需命名为 app-版本号-版本名.apk）。"),
    "APP_SYNC_SOURCE_ERROR": ("error", "查询远端版本失败（GitHub 与 Gitee 均不可访问），请稍后重试。"),
    "INVALID_SOURCE": ("error", "同步来源无效。"),
    "INVALID_TAG": ("error", "Release 标签格式无效。"),
}

SYSTEM_NOTICES: dict[str, tuple[str, str]] = {
    "GITHUB_ERROR": ("error", "查询更新源失败（GitHub 与 Gitee 均不可访问），请稍后重试"),
    "INVALID_SHA": ("error", "版本号格式无效（需 40 位 commit sha）"),
    "UPDATE_IN_PROGRESS": ("error", "已有更新正在执行，请稍候"),
    "UPDATE_REQUEST_FAILED": ("error", "提交更新请求失败，请查看服务器日志"),
    "UPDATE_FAILED": ("error", "更新异常，请查看服务器日志"),
    "FORBIDDEN": ("error", "仅管理员可操作"),
    "DEMO_PURGED": ("notice", "演示 / 测试数据已清除，启动时不再补种演示订单。"),
    "DEMO_CONFIRM": ("error", "请在确认框输入「清除」后再执行"),
    "DEMO_PURGE_FAILED": ("error", "清除失败，数据未改动，请查看服务器日志"),
}


def _admin_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return None, _error_page(request, 403, "403 禁止访问：管理界面仅对管理员开放")
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
        where = ""
        if q:
            where = " WHERE u.username LIKE ? OR u.display_name LIKE ?"
            args = [f"%{q}%", f"%{q}%"]
        total = c.execute(f"SELECT COUNT(*) FROM users u{where}", args).fetchone()[0]
        paging = _paging(_page_param(request), total, USERS_PAGE_SIZE)
        sql += where + " ORDER BY u.created_at, u.id LIMIT ? OFFSET ?"
        rows = c.execute(sql, [*args, USERS_PAGE_SIZE, paging["offset"]]).fetchall()
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
            "paging": paging,
            "q": q,
            "role_labels": ROLE_LABELS,
            "notice_text": NOTICE_TEXTS.get(notice_code),
            "error": error,
            # 行内操作共用一个弹窗：服务端给一个兜底幂等键，浏览器每次打开弹窗用 crypto.randomUUID() 重新生成
            "modal_op_id": str(uuid.uuid4()),
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 404, "404 用户不存在")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
    "RETURN": "退料",
}

TRANSFER_EXECUTE_VERBS = {
    "INBOUND": "执行入库",
    "OUTBOUND": "执行出库",
    "TRANSFER": "执行调拨",
    "STOCKTAKE": "执行盘点",
    "RETURN": "执行退料",
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


def _user_display_names(c: sqlite3.Connection, user_ids) -> dict[str, str]:
    """批量版 _user_display_name：一次 IN 查询，口径相同（无 display_name 时回退 id）。"""
    ids = sorted({str(u) for u in user_ids if u})
    names = {uid: uid for uid in ids}
    if ids:
        ph = ",".join("?" for _ in ids)
        for row in c.execute(f"SELECT id, display_name FROM users WHERE id IN ({ph})", ids).fetchall():
            if row["display_name"]:
                names[row["id"]] = row["display_name"]
    return names


def _paging(page: int, total: int, page_size: int) -> dict[str, int]:
    """分页元数据（模板 _pagination.html 的 pager 宏使用）。"""
    pages = (total + page_size - 1) // page_size if total else 0
    return {"page": page, "pages": pages, "total": total, "page_size": page_size,
            "offset": (page - 1) * page_size}


LIST_PAGE_SIZE = 100  # 原硬截断 100 条的列表：首页内容与旧版一致，其余可翻页
USERS_PAGE_SIZE = 50


def _manager_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in MANAGER_ROLES:
        return None, _error_page(request, 403, "403 禁止访问：审批与留痕页面仅对管理员/仓库管理员开放")
    return user, None


def _api_http_error_response(exc: HTTPException, request: Request | None = None):
    if request is None:
        return HTMLResponse(html.escape(f"{exc.status_code}：{exc.detail}"), status_code=exc.status_code)
    return _error_page(request, exc.status_code, str(exc.detail or ""))


def _search_transfers(q: str, status: str | None, page: int, user: sqlite3.Row) -> dict[str, Any]:
    """流转列表按单据号（订单号等）/ 申请单 ID 搜索（仅管理台；可见范围与列表接口一致）。"""
    from app.main import TRANSFER_STATES, _transfer_visibility_sql
    if status is not None:
        status = status.strip().upper()
        if status not in TRANSFER_STATES:
            raise HTTPException(400, "status 不是有效的流转申请状态")
    scope, args = _transfer_visibility_sql(user, "t")
    where = " WHERE " + scope + " AND (t.document_no LIKE ? OR t.id = ?)"
    args = [*args, f"%{q}%", q]
    if status:
        where += " AND t.status=?"
        args.append(status)
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) FROM transfer_requests t" + where, args).fetchone()[0]
        rows = c.execute(
            "SELECT t.id, t.type, t.document_no, t.status, t.created_by, t.created_at FROM transfer_requests t"
            + where + " ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?",
            [*args, LIST_PAGE_SIZE, (page - 1) * LIST_PAGE_SIZE],
        ).fetchall()
    finally:
        c.close()
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/admin/flows", response_class=HTMLResponse)
def admin_flows_list(request: Request):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    status = request.query_params.get("status") or None
    q = str(request.query_params.get("q", "")).strip()
    page = _page_param(request)
    try:
        if q:
            data = _search_transfers(q, status, page, user)
        else:
            data = _list_transfers(status=status, page=page, page_size=LIST_PAGE_SIZE, user=user)
    except HTTPException as exc:
        return _api_http_error_response(exc, request)
    return templates.TemplateResponse(
        request,
        "flows.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": data["items"],
            "paging": _paging(page, data["total"], LIST_PAGE_SIZE),
            "status_filter": status or "",
            "q": q,
            "status_labels": TRANSFER_STATUS_LABELS,
            "type_labels": TRANSFER_TYPE_LABELS,
            "notice_text": NOTICE_TEXTS.get(request.query_params.get("notice", "")),
        },
    )


def _render_flow_detail(request: Request, user: sqlite3.Row, rid: str, error: str | None = None):
    try:
        item = get_transfer(rid, user=user)
    except HTTPException as exc:
        return _api_http_error_response(exc, request)
    c = db()
    try:
        handover_rows = c.execute(
            "SELECT id, status, quantity, receiver_user_id, created_at"
            " FROM material_handovers WHERE transfer_request_id=? ORDER BY created_at",
            (rid,),
        ).fetchall()
        names = _user_display_names(
            c, [item.get("created_by"), item.get("approved_by"),
                *(h["receiver_user_id"] for h in handover_rows)],
        )

        def display(uid: Any) -> str:
            return names.get(str(uid), str(uid)) if uid else "—"

        created_by_name = display(item.get("created_by"))
        approved_by_name = display(item.get("approved_by"))
        payload_list = (item.get("payload") or {}).get("items", []) or []
        mat_ids = sorted({str(p.get("materialId")) for p in payload_list if p.get("materialId")})
        materials: dict[Any, sqlite3.Row] = {}
        if mat_ids:
            ph = ",".join("?" for _ in mat_ids)
            materials = {r["id"]: r for r in c.execute(
                f"SELECT id, code, name, specification, unit FROM materials WHERE id IN ({ph})",
                mat_ids,
            ).fetchall()}
        payload_items: list[dict[str, Any]] = []
        for p in payload_list:
            mat_id = p.get("materialId")
            mrow = materials.get(str(mat_id)) if mat_id else None
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
            "receiver_name": display(h["receiver_user_id"]),
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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


_HANDOVER_LIST_SQL = (
    "SELECT h.id, h.work_item_id, h.status, h.quantity, h.from_location, h.created_at,"
    " u.display_name AS creator_name, t.document_no, t.type AS transfer_type,"
    " m.code AS material_code, m.name AS material_name, d.device_no"
    " FROM material_handovers h"
    " LEFT JOIN users u ON u.id = h.created_by"
    " LEFT JOIN transfer_requests t ON t.id = h.transfer_request_id"
    " LEFT JOIN material_work_items w ON w.id = h.work_item_id"
    " LEFT JOIN materials m ON m.id = w.material_id"
    " LEFT JOIN devices d ON d.id = COALESCE(h.device_id, w.device_id)"
)
_HANDOVER_SEARCH_WHERE = (
    " WHERE h.id = ? OR h.work_item_id = ? OR t.document_no = ? OR m.code = ? OR d.device_no = ?"
    " OR t.document_no LIKE ? OR m.code LIKE ? OR m.name LIKE ? OR d.device_no LIKE ?"
)


def _handover_search_args(query: str) -> tuple:
    like = f"%{query}%"
    return (query, query, query, query, query, like, like, like, like)


@router.get("/admin/handovers", response_class=HTMLResponse)
def admin_handovers_search(request: Request):
    user, denied = _manager_or_403(request)
    if denied:
        return denied
    query = str(request.query_params.get("query", "")).strip()
    result = None
    error = None
    page = _page_param(request)
    page_size = DEFAULT_PAGE_SIZE
    c = db()
    try:
        if query:
            # 内部 ID（ho_/wi_/需求单）走原有时间线接口；否则按流转单号/订单号/物料编码/机台号找最近一条交接
            try:
                result = handover_timeline(query, user=user)
            except HTTPException as exc:
                if exc.status_code != 404:
                    error = f"{exc.status_code}：{exc.detail}"
                else:
                    hit = c.execute(
                        _HANDOVER_LIST_SQL + _HANDOVER_SEARCH_WHERE + " ORDER BY h.created_at DESC LIMIT 1",
                        _handover_search_args(query),
                    ).fetchone()
                    if hit is not None:
                        try:
                            result = handover_timeline(hit["id"], user=user)
                        except HTTPException:
                            result = None
                    if result is None:
                        error = f"未找到匹配的交接记录：{query}（可输入交接单号、流转单号/订单号、物料编码或机台号）"
            where, args = (_HANDOVER_SEARCH_WHERE, _handover_search_args(query))
        else:
            where, args = ("", ())
        total = c.execute(
            "SELECT COUNT(*) FROM (" + _HANDOVER_LIST_SQL + where + ")", args,
        ).fetchone()[0]
        rows = c.execute(
            _HANDOVER_LIST_SQL + where + " ORDER BY h.created_at DESC LIMIT ? OFFSET ?",
            (*args, page_size, (page - 1) * page_size),
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
        return _api_http_error_response(exc, request)
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
        return None, _error_page(request, 403, "403 禁止访问：统计页面仅对管理员/车间主管开放")
    return user, None


def _with_plain_defaults(fn):
    """直接调用路由函数时，把未传参数的 Query()/Header() 等 FieldInfo 默认值替换为其真实默认值。

    否则 FieldInfo 对象会被原样传进 SQL（sqlite3.ProgrammingError: type 'Query' is not supported），
    例如给 list_assembly_tasks 新增 deviceId 后 /admin/tasks 整页 500。
    """
    import functools
    import inspect
    from fastapi.params import Depends as _DependsParam
    from pydantic.fields import FieldInfo
    from pydantic_core import PydanticUndefined

    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = sig.bind_partial(*args, **kwargs)
        for name, param in sig.parameters.items():
            if name in bound.arguments:
                continue
            default = param.default
            if isinstance(default, FieldInfo) and not isinstance(default, _DependsParam):
                if default.default is not PydanticUndefined:
                    kwargs[name] = default.default
                elif default.default_factory is not None:
                    kwargs[name] = default.default_factory()
        return fn(*args, **kwargs)

    return wrapper


@lru_cache(maxsize=64)
def _api_endpoint(path: str, method: str = "GET"):
    """从 FastAPI 路由表解析既有 API 处理函数并直接调用（统计口径零漂移）。

    workshop 统计端点注册在 assembly_routes.register() 闭包内，无法按模块名导入；
    路由表解析保持「同一函数、同一 SQL」语义。
    """
    for route in backend_app.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return _with_plain_defaults(route.endpoint)
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
        return _api_http_error_response(exc, request)
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
    q = request.query_params
    filters = {
        "deviceId": q.get("deviceId") or None,
        "orderNo": (q.get("orderNo") or "").strip() or None,
        "assemblerId": q.get("assemblerId") or None,
        # v0.7.0：页面按机台编号 / 人员姓名筛选，服务端换算成 ID 再调接口（接口本身不变）
        "deviceNo": (q.get("deviceNo") or "").strip() or None,
        "assembler": (q.get("assembler") or "").strip() or None,
    }
    device_id, assembler_id = filters["deviceId"], filters["assemblerId"]
    no_match = False
    if filters["deviceNo"] or filters["assembler"]:
        c = db()
        try:
            if filters["deviceNo"]:
                row = c.execute(
                    "SELECT device_id FROM assembly_tasks WHERE device_no=? LIMIT 1", (filters["deviceNo"],)
                ).fetchone() or c.execute(
                    "SELECT id AS device_id FROM devices WHERE device_no=? OR id=?",
                    (filters["deviceNo"], filters["deviceNo"]),
                ).fetchone()
                device_id = row["device_id"] if row else None
                no_match = no_match or row is None
            if filters["assembler"]:
                rows = c.execute(
                    "SELECT id FROM users WHERE id=? OR username=? OR display_name=? LIMIT 2",
                    (filters["assembler"],) * 3,
                ).fetchall()
                assembler_id = rows[0]["id"] if len(rows) == 1 else None
                no_match = no_match or len(rows) != 1
        finally:
            c.close()
    try:
        data = _api_endpoint("/api/v1/workshop/labor-summary")(
            page=page,
            pageSize=20,
            deviceId=device_id,
            orderNo=filters["orderNo"],
            assemblerId=assembler_id,
            user=user,
        )
        if no_match:
            data = {**data, "items": [], "total": 0, "totalPages": 0, "totalLaborMinutes": 0}
    except HTTPException as exc:
        return _api_http_error_response(exc, request)
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

# GLB 零件英文名 → 中文（与 App 端 PartNameCn 保持一致）
_PART_CN_DICT = {
    "arm": "机械臂", "base": "底座", "column": "立柱", "upper": "大臂",
    "forearm": "小臂", "wrist": "腕部", "roll": "旋转", "pitch": "俯仰",
    "flange": "法兰盘", "gripper": "夹爪", "jaw": "夹指",
    "shoulder": "肩部", "elbow": "肘部",
    "conveyor": "传送带", "belt": "皮带", "roller": "滚筒", "workstation": "工作站",
    "line": "产线", "station": "工位",
    "motor": "电机", "gear": "齿轮", "gearbox": "减速箱", "bearing": "轴承",
    "shaft": "轴", "frame": "机架", "bracket": "支架", "plate": "板",
    "cover": "罩盖", "housing": "壳体", "bolt": "螺栓", "nut": "螺母",
    "screw": "螺钉", "washer": "垫圈", "spring": "弹簧",
    "wheel": "轮", "cylinder": "油缸", "piston": "活塞", "chain": "链条",
    "pump": "泵", "valve": "阀", "pipe": "管", "tank": "箱",
    "sensor": "传感器", "controller": "控制器", "cabinet": "控制柜",
    "panel": "面板", "screen": "屏幕", "button": "按钮",
    "fork": "货叉", "mast": "门架", "forklift": "叉车",
    "pallet": "托盘", "generator": "发电机",
    "left": "左", "right": "右", "front": "前", "rear": "后",
    "top": "上", "bottom": "下", "inner": "内", "outer": "外",
    "l": "左", "r": "右",
}
_PART_SPLIT_RE = re.compile(r"[_.\-]+")
_PART_JUNK_RE = re.compile(r"^(group\d+|rootnode|node\d*|mesh\d*|object\d*)$", re.IGNORECASE)


def part_display_name(raw: str) -> str:
    """'arm_base' → '机械臂 底座'；无命中词元时返回原名。"""
    tokens = [t for t in _PART_SPLIT_RE.split(raw.lower()) if t]
    if not tokens:
        return raw
    translated = [_PART_CN_DICT.get(t, t) for t in tokens]
    if translated == tokens:
        return raw
    return " ".join(translated)


def extract_glb_parts(storage_key: str) -> list[str]:
    """从 GLB 文件的 JSON 块提取零件节点名（去重、过滤无意义名称）。"""
    import struct as _struct
    import json as _json
    path = (UPLOAD_DIR.resolve() / storage_key).resolve()
    if UPLOAD_DIR.resolve() not in path.parents:
        raise ValueError("非法存储路径")
    data = path.read_bytes()
    if len(data) < 20 or data[0:4] != b"glTF":
        raise ValueError("不是有效 GLB 文件")
    json_len = _struct.unpack("<I", data[12:16])[0]
    glb_json = _json.loads(data[20:20 + json_len])
    seen: list[str] = []
    for node in glb_json.get("nodes", []):
        name = str(node.get("name", "")).strip()
        if not name or name in seen:
            continue
        if _PART_JUNK_RE.match(name) or name.isdigit():
            continue
        seen.append(name)
    return seen


@router.get("/admin/models", response_class=HTMLResponse)
def admin_models_list(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    code = str(request.query_params.get("code", "")).strip()
    q = str(request.query_params.get("q", "")).strip()
    page = _page_param(request)
    page_size = DEFAULT_PAGE_SIZE
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
    dpage_size = DEFAULT_PAGE_SIZE
    c = db()
    try:
        # 零件清单：从已发布版本的 GLB 解析节点名
        parts: list[dict] = []
        parts_error: str | None = None
        if published:
            try:
                prow = c.execute(
                    "SELECT storage_key FROM assembly_model_versions"
                    " WHERE model_code=? AND status='PUBLISHED' ORDER BY version DESC LIMIT 1",
                    (published["modelCode"],),
                ).fetchone()
                if prow and prow["storage_key"]:
                    parts = [
                        {"name": n, "cn": part_display_name(n)}
                        for n in extract_glb_parts(prow["storage_key"])
                    ]
            except Exception as exc:
                parts_error = f"零件解析失败：{exc}"
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
            "parts": parts,
            "parts_error": parts_error,
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


def _audit_entity_names(c: sqlite3.Connection, items: list[dict]) -> dict[str, str]:
    """审计对象 ID → 可读名称（用户姓名 / 流转单据号 / BOM 机型版本），查不到的不放进字典。"""
    by_type: dict[str, set[str]] = {}
    for i in items:
        if i.get("entity_id"):
            by_type.setdefault(i.get("entity_type") or "", set()).add(str(i["entity_id"]))
    out: dict[str, str] = {}

    def fetch(sql: str, ids: set[str]):
        ids = sorted(ids)
        if not ids:
            return []
        return c.execute(sql.format(ph=",".join("?" for _ in ids)), ids).fetchall()

    user_ids = by_type.get("USER", set())
    out.update({k: v for k, v in _user_display_names(c, user_ids).items() if k != v})
    for r in fetch("SELECT id, document_no FROM transfer_requests WHERE id IN ({ph})",
                   by_type.get("TRANSFER_REQUEST", set()) | by_type.get("MATERIAL_FLOW", set())):
        if r["document_no"]:
            out[r["id"]] = r["document_no"]
    for r in fetch("SELECT id, model_code, version_no FROM bom_versions WHERE id IN ({ph})",
                   by_type.get("BOM_VERSION", set())):
        out[r["id"]] = f"{r['model_code']} v{r['version_no']}"
    return out


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
    # v0.7.0：时间筛选为日期输入（北京时间），换算成 UTC ISO 边界；完整 ISO 原样透传（兼容旧链接）
    api_from = _ui_labels.beijing_day_bounds(filters["from"])
    api_to = _ui_labels.beijing_day_bounds(filters["to"], end=True)
    # 操作人可填姓名 / 用户名 / ID：能唯一匹配到用户则换成 ID
    operator = filters["operatorId"]
    if operator:
        c = db()
        try:
            hits = c.execute(
                "SELECT id FROM users WHERE id=? OR username=? OR display_name=? LIMIT 2",
                (operator, operator, operator),
            ).fetchall()
        finally:
            c.close()
        if len(hits) == 1:
            operator = hits[0]["id"]
    data = None
    error = None
    try:
        data = api_audit_logs(
            page=_page_param(request),
            pageSize=50,
            from_=api_from,
            to_=api_to,
            eventType=filters["eventType"],
            entityType=filters["entityType"],
            operatorId=operator,
            action=None,
            resourceType=None,
            resourceId=None,
            entityId=filters["entityId"],
            user=user,
        )
    except HTTPException as exc:
        error = f"{exc.status_code}：{exc.detail}"
    items = (data or {}).get("items", [])
    c = db()
    try:
        names = _user_display_names(c, [i.get("actor_user_id") for i in items])
        entity_names = _audit_entity_names(c, items)
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "items": items,
            "names": names,
            "entity_names": entity_names,
            "page": _page_param(request),
            "error": error,
            "filters": filters,
            "date_from": filters["from"] if filters["from"] and len(filters["from"]) == 10 else "",
            "date_to": filters["to"] if filters["to"] and len(filters["to"]) == 10 else "",
            "event_labels": _ui_labels.LABELS["event"],
            "entity_labels": _ui_labels.LABELS["entity"],
            "result_labels": _ui_labels.LABELS["result"],
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
    page = _page_param(request)
    c = db()
    try:
        total = c.execute("SELECT COUNT(*) FROM production_orders").fetchone()[0]
        paging = _paging(page, total, LIST_PAGE_SIZE)
        rows = c.execute(
            "SELECT id, order_no, product_name, status, created_at, updated_at"
            " FROM production_orders ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (LIST_PAGE_SIZE, paging["offset"]),
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
        "paging": paging,
        "order_no": order_no,
        "detail": detail,
        "status_labels": WORKSPACE_STATUS_LABELS,
        "error": error,
        "notice_text": NOTICE_TEXTS.get(request.query_params.get("notice", "")),
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


# ==================== 0.14.0：订单 / 机台物料勾选批量操作 ====================

# 批量动作 → (中文名, 底层能力)。出库/退料/调拨走 /api/v1/transfer-requests，异常走 /api/v1/exceptions。
MATERIAL_BATCH_ACTIONS: dict[str, str] = {
    "OUTBOUND": "出库申请",
    "RETURN": "退料",
    "TRANSFER": "厂内调拨",
    "EXCEPTION": "异常提报",
}
MATERIAL_BATCH_MAX_ITEMS = 100
EXCEPTION_TYPE_LABELS: dict[str, str] = {
    "QUANTITY_MISMATCH": "数量不符",
    "DAMAGE": "损坏",
    "LOSS": "丢失",
    "QUALITY": "质量问题",
    "OTHER": "其他",
}


def material_batch_actions_for(role: str | None) -> list[tuple[str, str]]:
    """当前角色可发起的批量动作（与 API 创建接口的角色口径一致，页面只展示可用按钮）。"""
    allowed: list[tuple[str, str]] = []
    for code, label in MATERIAL_BATCH_ACTIONS.items():
        roles = EXCEPTION_CREATE_ROLES if code == "EXCEPTION" else TRANSFER_CREATE_ROLES.get(code)
        if roles is not None and role in roles:
            allowed.append((code, label))
    return allowed


templates.env.globals["material_batch_actions_for"] = material_batch_actions_for
templates.env.globals["exception_type_labels"] = EXCEPTION_TYPE_LABELS


def _batch_json(ok: bool, status_code: int = 200, **payload: Any) -> JSONResponse:
    return JSONResponse({"ok": ok, **payload}, status_code=status_code,
                        headers={"Cache-Control": "no-store"})


def _form_int(form: Any, key: str) -> int | None:
    raw = str(form.get(key, "") or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{1,9}", raw):
        raise ValueError(key)
    return int(raw)


def _pick_source_location(c: sqlite3.Connection, material_id: str, quantity: int) -> str | None:
    """调拨来源库位：优先库存足量的库位，其次任一有库存的库位。"""
    rows = c.execute(
        "SELECT l.code, i.quantity FROM inventory i JOIN locations l ON l.id=i.location_id"
        " WHERE i.material_id=? AND i.quantity>0 ORDER BY i.quantity DESC, l.code",
        (material_id,),
    ).fetchall()
    for row in rows:
        if row["quantity"] >= quantity:
            return row["code"]
    return rows[0]["code"] if rows else None


@router.post("/admin/orders/{order_no}/materials/batch")
async def admin_order_material_batch(request: Request, order_no: str):
    """订单页 / 机台页勾选物料批量操作：逐条调用既有 API（同一会话用户、同一 RBAC 与审计）。

    每条物料独立提交，部分失败不影响已成功条目；返回逐条结果 JSON。
    """
    user = _session_user(request)
    if user is None:
        return _batch_json(False, 401, error="登录已失效，请重新登录")
    if user["role"] not in REPORT_ROLES:
        return _batch_json(False, 403, error="当前角色无权访问订单页面")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _batch_json(False, 403, error="CSRF 校验失败，请刷新页面后重试")
    action = str(form.get("action", "")).strip().upper()
    if action not in MATERIAL_BATCH_ACTIONS:
        return _batch_json(False, 400, error="不支持的批量操作")
    if action not in dict(material_batch_actions_for(user["role"])):
        return _batch_json(False, 403, error=f"当前角色不能发起{MATERIAL_BATCH_ACTIONS[action]}")
    req_ids: list[str] = []
    for raw in form.getlist("requirementId"):
        rid = str(raw).strip()
        if rid and rid not in req_ids:
            req_ids.append(rid)
    if not req_ids:
        return _batch_json(False, 400, error="请先勾选物料")
    if len(req_ids) > MATERIAL_BATCH_MAX_ITEMS:
        return _batch_json(False, 400, error=f"单次最多勾选 {MATERIAL_BATCH_MAX_ITEMS} 条物料")
    remark = str(form.get("remark", "") or "").strip()
    if len(remark) > 400:
        return _batch_json(False, 400, error="备注最多 400 字")
    target_location = str(form.get("targetLocation", "") or "").strip() or None
    source_location = str(form.get("sourceLocation", "") or "").strip() or None
    exception_type = str(form.get("exceptionType", "") or "OTHER").strip().upper()
    if action == "EXCEPTION":
        if exception_type not in EXCEPTION_TYPE_LABELS:
            return _batch_json(False, 400, error="异常类型无效")
        if not remark:
            return _batch_json(False, 400, error="异常提报必须填写描述")
    if action == "TRANSFER" and not target_location:
        return _batch_json(False, 400, error="厂内调拨必须填写目标库位")
    try:
        quantities = {rid: _form_int(form, f"qty_{rid}") for rid in req_ids}
    except ValueError:
        return _batch_json(False, 400, error="数量必须是非负整数")
    # 批次号：前端每次打开对话框生成一次，重试沿用。每条的幂等键由批次号 + 动作 + 需求 ID 派生，
    # 同一批次重复提交时既有 API 按幂等键回放，不会重复建单。旧客户端未传时退化为每次新建。
    try:
        batch_id = uuid.UUID(str(form.get("batchId", "") or "").strip())
    except ValueError:
        batch_id = uuid.uuid4()

    # 下面是逐条的同步 SQLite 写入（最多 MATERIAL_BATCH_MAX_ITEMS 条），放到线程池里执行，
    # 不阻塞事件循环（见 0.8.2 事故：async 路由里不得直接做阻塞 IO）。
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(
        _run_material_batch, request, user, order_no, action, req_ids, quantities, remark,
        target_location, source_location, exception_type, batch_id,
    )


def _run_material_batch(request: Request, user: Any, order_no: str, action: str, req_ids: list[str],
                        quantities: dict[str, int | None], remark: str, target_location: str | None,
                        source_location: str | None, exception_type: str, batch_id: uuid.UUID):
    c = db()
    try:
        order = c.execute("SELECT id FROM production_orders WHERE order_no=?", (order_no,)).fetchone()
        if order is None:
            return _batch_json(False, 404, error="订单不存在")
        for code in (target_location, source_location):
            if code and not c.execute("SELECT 1 FROM locations WHERE code=?", (code,)).fetchone():
                return _batch_json(False, 400, error=f"库位不存在：{code}")
        marks = ",".join("?" for _ in req_ids)
        rows = {
            r["id"]: r for r in c.execute(
                f"""SELECT r.id, r.device_id, r.material_id, r.required_quantity, r.in_stock_quantity,
                           m.code AS material_code, m.version AS material_version, od.device_no
                      FROM order_material_requirements r
                      JOIN materials m ON m.id = r.material_id
                      LEFT JOIN order_devices od ON od.id = r.device_id
                     WHERE r.order_id = ? AND r.id IN ({marks})""",
                [order["id"], *req_ids],
            ).fetchall()
        }
        sources = {
            rid: (source_location or _pick_source_location(
                c, rows[rid]["material_id"],
                quantities[rid] if quantities.get(rid) is not None else rows[rid]["required_quantity"]))
            for rid in req_ids if rid in rows and action == "TRANSFER"
        }
    finally:
        c.close()

    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for rid in req_ids:
        row = rows.get(rid)
        if row is None:
            failed.append({"requirementId": rid, "materialCode": rid, "error": "物料需求不存在或不属于该订单"})
            continue
        code = row["material_code"]
        if action == "EXCEPTION":
            book = int(row["in_stock_quantity"] or 0)
            qty = quantities.get(rid)
            qty = book if qty is None else qty
        else:
            qty = quantities.get(rid)
            if qty is None:  # 未填才取需求数；显式填 0 视为无效，不悄悄替换
                qty = int(row["required_quantity"] or 0)
            if qty < 1:
                failed.append({"requirementId": rid, "materialCode": code, "error": "数量必须为正整数"})
                continue
        device_no = row["device_no"] or ""
        full_remark = (f"机台：{device_no}；" if device_no else "") + remark
        op_id = str(uuid.uuid5(batch_id, f"{action}:{rid}"))
        try:
            if action == "EXCEPTION":
                result = api_create_exception(
                    ExceptionCreate(
                        orderNo=order_no, deviceId=row["device_id"], materialId=row["material_id"],
                        type=exception_type, bookQuantity=book, actualQuantity=qty,
                        description=full_remark[:500], clientOperationId=op_id,
                    ),
                    user=user, x_request_id=str(uuid.uuid4()), idempotency_key=op_id,
                )
                succeeded.append({"requirementId": rid, "materialCode": code, "id": result.get("exceptionId")})
                continue
            item_kwargs: dict[str, Any] = {
                "materialId": row["material_id"], "quantity": qty,
                "expectedInventoryVersion": int(row["material_version"] or 1),
            }
            if action == "RETURN" and target_location:
                item_kwargs["targetLocationCode"] = target_location
            if action == "TRANSFER":
                src = sources.get(rid)
                if not src:
                    failed.append({"requirementId": rid, "materialCode": code, "error": "该物料无可用来源库位"})
                    continue
                item_kwargs["sourceLocationCode"] = src
                item_kwargs["targetLocationCode"] = target_location
            result = api_create_transfer(
                Transfer(
                    clientOperationId=op_id, type=action, documentNo=order_no,
                    items=[TransferItem(**item_kwargs)], remark=full_remark[:500] or None,
                ),
                user=user, x_request_id=str(uuid.uuid4()), idempotency_key=op_id,
            )
            succeeded.append({"requirementId": rid, "materialCode": code, "id": result.get("requestId")})
        except ApiError as exc:
            failed.append({"requirementId": rid, "materialCode": code, "error": _api_error_message(exc)})
        except HTTPException as exc:
            failed.append({"requirementId": rid, "materialCode": code, "error": str(exc.detail or "提交失败")})
        except ValidationError:
            failed.append({"requirementId": rid, "materialCode": code, "error": "输入校验未通过"})

    outcome = "SUCCESS" if not failed else ("FAILURE" if not succeeded else "PARTIAL")
    c = db()
    try:
        audit(c, user["id"], user["role"], f"BATCH_{action}", "ORDER_MATERIALS", order_no,
              outcome, _request_trace_id(request) or "web-admin")
        c.commit()
    finally:
        c.close()
    label = MATERIAL_BATCH_ACTIONS[action]
    if not failed:
        message = f"已提交 {len(succeeded)} 条{label}"
    elif not succeeded:
        message = f"批量{label}全部失败"
    else:
        message = f"已提交 {len(succeeded)} 条{label}，失败 {len(failed)} 条"
    return _batch_json(bool(succeeded), 200, action=action, message=message,
                       succeeded=succeeded, failed=failed)


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
        return _error_page(request, 404, "订单不存在")
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
        return _error_page(request, 404, "订单不存在")
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


EXPIRY_WARN_DAYS = 30
_EXPIRY_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")


def _parse_expiry(value: Any):
    """宽松解析物料效期（导入数据格式不统一），失败返回 None。"""
    text = str(value or "").strip()[:10]
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(text if fmt != "%Y%m%d" else text[:8], fmt).date()
        except ValueError:
            continue
    return None


def _expiring_materials(c, warn_days: int = EXPIRY_WARN_DAYS) -> list[dict]:
    """有可用库存且已过期 / warn_days 天内到期的物料，按到期日升序。以北京时间计“今天”。"""
    today = datetime.now(timezone(timedelta(hours=8))).date()
    rows = c.execute(
        "SELECT code, name, batch_no, expiry_date, available_quantity, unit FROM materials"
        " WHERE expiry_date IS NOT NULL AND TRIM(expiry_date) <> '' AND available_quantity > 0"
    ).fetchall()
    result = []
    for r in rows:
        d = _parse_expiry(r["expiry_date"])
        if d is None:
            continue
        days_left = (d - today).days
        if days_left <= warn_days:
            item = dict(r)
            item["days_left"] = days_left
            result.append(item)
    result.sort(key=lambda x: x["days_left"])
    return result


def _int_param(request: Request, name: str) -> int:
    try:
        return max(1, int(request.query_params.get(name, "1") or 1))
    except ValueError:
        return 1


LOW_STOCK_THRESHOLD = 10  # 可用数量低于此值标为「低库存」（≤0 为「缺料」）；与助手 low_stock 同口径按可用量升序
INVENTORY_PAGE_SIZE = 20
WAREHOUSE_EXCEPTION_PREVIEW = 5


def _inventory_list(c, q: str, cat: str, page: int) -> tuple[list[dict], dict[str, int], dict[str, int]]:
    """仓库页默认库存列表：缺料/低库存置顶（按可用数量升序），关键字匹配编码/名称/规格，可按分类筛选。"""
    where, args = [], []
    if q:
        like = f"%{q}%"
        where.append("(code LIKE ? OR name LIKE ? OR IFNULL(specification,'') LIKE ?)")
        args += [like, like, like]
    if cat == "__none__":
        where.append("(category IS NULL OR TRIM(category) = '')")
    elif cat.startswith("c:"):
        # 分类树节点：按分类编码前缀匹配（含全部下级）
        where.append("category_code LIKE ? ESCAPE '\\'")
        args.append(cat[2:].replace("%", "").replace("_", "\\_") + "%")
    elif cat:
        where.append("category = ?")
        args.append(cat)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    counts = c.execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN available_quantity <= 0 THEN 1 ELSE 0 END) AS short,"
        f" SUM(CASE WHEN available_quantity > 0 AND available_quantity < {LOW_STOCK_THRESHOLD} THEN 1 ELSE 0 END) AS low"
        " FROM materials" + where_sql, args,
    ).fetchone()
    paging = _paging(page, counts["total"] or 0, INVENTORY_PAGE_SIZE)
    rows = c.execute(
        "SELECT code, name, specification, unit, category, batch_no, expiry_date,"
        " total_quantity, available_quantity FROM materials" + where_sql +
        " ORDER BY available_quantity ASC, code ASC LIMIT ? OFFSET ?",
        (*args, INVENTORY_PAGE_SIZE, paging["offset"]),
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        avail = d["available_quantity"] or 0
        d["stock_state"] = "short" if avail <= 0 else ("low" if avail < LOW_STOCK_THRESHOLD else "ok")
        items.append(d)
    return items, paging, {"short": counts["short"] or 0, "low": counts["low"] or 0}


def _category_tree_view(c, cat: str) -> dict[str, Any]:
    """分类树：下拉显示前两级（带物料数），选中节点时给出面包屑与下级分类统计。
    物料数 = 该分类及全部下级分类下的本地物料数。"""
    try:
        nodes = {r["code"]: dict(r) for r in c.execute(
            "SELECT code, name, parent_code, level FROM material_categories")}
    except sqlite3.OperationalError:
        nodes = {}
    if not nodes:
        return {"tree_options": [], "tree_children": [], "tree_path": []}
    counts: dict[str, int] = {}
    for r in c.execute("SELECT category_code, COUNT(*) AS n FROM materials"
                       " WHERE category_code IS NOT NULL GROUP BY category_code"):
        code = r["category_code"]
        seen = set()
        while code and code not in seen:
            seen.add(code)
            counts[code] = counts.get(code, 0) + r["n"]
            code = (nodes.get(code) or {}).get("parent_code")
    def label(n):
        return {"value": "c:" + n["code"], "code": n["code"], "name": n["name"],
                "level": n["level"], "count": counts.get(n["code"], 0)}
    opts = sorted((label(n) for n in nodes.values() if n["level"] <= 2 and counts.get(n["code"])),
                  key=lambda o: o["code"])
    sel = cat[2:] if cat.startswith("c:") else ""
    path, children = [], []
    if sel in nodes:
        code = sel
        while code in nodes:
            path.insert(0, label(nodes[code]))
            code = nodes[code]["parent_code"]
        children = sorted((label(n) for n in nodes.values()
                           if n["parent_code"] == sel and counts.get(n["code"])),
                          key=lambda o: -o["count"])
    elif not sel:
        children = [o for o in opts if o["level"] == 1]
    return {"tree_options": opts, "tree_children": children[:40], "tree_path": path}


def _warehouse_lists(request: Request) -> dict[str, Any]:
    """仓库页：库存列表（inv_page）+ 盘点（st_page）分页；异常只取摘要（全部在 /admin/exceptions）。"""
    q = str(request.query_params.get("q", "")).strip()
    cat = str(request.query_params.get("cat", "")).strip()
    c = db()
    try:
        inventory, inv_paging, inv_counts = _inventory_list(c, q, cat, _int_param(request, "inv_page"))
        tree = _category_tree_view(c, cat)
        categories = [] if tree["tree_options"] else [r[0] for r in c.execute(
            "SELECT DISTINCT category FROM materials WHERE category IS NOT NULL AND TRIM(category) <> ''"
            " ORDER BY category"
        ).fetchall()]
        st_paging = _paging(_int_param(request, "st_page"), stocktakes_count(c), LIST_PAGE_SIZE)
        stocktakes = stocktakes_page(c, LIST_PAGE_SIZE, st_paging["offset"])
        ex_counts = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) AS n FROM exceptions GROUP BY status"
        ).fetchall()}
        ex_paging = _paging(
            _int_param(request, "ex_page"),
            (ex_counts.get("PENDING") or 0) + (ex_counts.get("OPEN") or 0),
            WAREHOUSE_EXCEPTION_PREVIEW,
        )
        ex_pending = [dict(r) for r in c.execute(
            "SELECT e.id, e.type, e.status, e.book_quantity, e.actual_quantity, e.difference,"
            " e.description, e.order_no, e.created_at, m.code AS material_code, m.name AS material_name"
            " FROM exceptions e LEFT JOIN materials m ON m.id = e.material_id"
            " WHERE e.status IN ('PENDING','OPEN') ORDER BY e.created_at DESC, e.id DESC LIMIT ? OFFSET ?",
            (WAREHOUSE_EXCEPTION_PREVIEW, ex_paging["offset"]),
        ).fetchall()]
    finally:
        c.close()
    return {
        "inventory": inventory,
        "inv_paging": inv_paging,
        "inv_counts": inv_counts,
        "categories": categories,
        **tree,
        "inv_q": q,
        "inv_cat": cat,
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
        "stocktakes": stocktakes,
        "st_paging": st_paging,
        "ex_counts": ex_counts,
        "ex_pending": ex_pending,
        "ex_paging": ex_paging,
        "page_params": {
            "code": str(request.query_params.get("code", "")).strip(),
            "q": q,
            "cat": cat,
            "inv_page": inv_paging["page"] if inv_paging["page"] > 1 else "",
            "st_page": st_paging["page"] if st_paging["page"] > 1 else "",
            "ex_page": ex_paging["page"] if ex_paging["page"] > 1 else "",
        },
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
    lists = _warehouse_lists(request)
    stocktakes = lists["stocktakes"]
    c = db()
    try:
        expiring = _expiring_materials(c)
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "warehouse.html",
        {
            **lists,
            "user": user,
            "csrf_token": user["csrf_token"],
            "code": code,
            "material": material,
            "material_error": material_error,
            "expiring": expiring,
            "expiry_warn_days": EXPIRY_WARN_DAYS,
            "stocktake_ops": {s["id"]: str(uuid.uuid4()) for s in stocktakes},
            "exception_ops": {e["id"]: str(uuid.uuid4()) for e in lists["ex_pending"]},
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "403 禁止访问")
    c = db()
    try:
        status_counts = {r[0]: r[1] for r in c.execute(
            "SELECT status, COUNT(*) FROM exceptions GROUP BY status"
        ).fetchall()}
        paging = _paging(_page_param(request), sum(status_counts.values()), LIST_PAGE_SIZE)
        exceptions = exceptions_page(c, LIST_PAGE_SIZE, paging["offset"])
        import_errors = [dict(r) for r in c.execute(
            "SELECT * FROM agent_import_error_logs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]
        # 统计卡片按全部异常计数（原先只统计了截断后的前 100 条）
        open_count = sum(status_counts.get(k, 0) for k in ("OPEN", "PENDING"))
        in_progress_count = status_counts.get("IN_PROGRESS", 0)
        resolved_count = sum(status_counts.get(k, 0) for k in ("RESOLVED", "CLOSED", "APPROVED"))
        # 首页待办：/admin/ 直接落在本页，把跨模块需要人处理的数量集中展示
        todo_counts = {
            "pending_transfers": c.execute(
                "SELECT COUNT(*) FROM transfer_requests WHERE status='PENDING_APPROVAL'"
            ).fetchone()[0],
            "pending_handovers": c.execute(
                "SELECT COUNT(*) FROM material_handovers WHERE status='PENDING'"
            ).fetchone()[0],
            "waiting_material_tasks": c.execute(
                "SELECT COUNT(*) FROM assembly_tasks WHERE status='WAITING_MATERIAL'"
            ).fetchone()[0],
            "in_progress_tasks": c.execute(
                "SELECT COUNT(*) FROM assembly_tasks WHERE status='IN_PROGRESS'"
            ).fetchone()[0],
            "expiring_materials": len(_expiring_materials(c)),
        }
    finally:
        c.close()
    return templates.TemplateResponse(
        request,
        "exceptions.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "exceptions": exceptions,
            "paging": paging,
            "import_errors": import_errors,
            "exception_ops": {e["id"]: str(uuid.uuid4()) for e in exceptions},
            "open_count": open_count,
            "in_progress_count": in_progress_count,
            "resolved_count": resolved_count,
            "import_error_count": len(import_errors),
            "todo": todo_counts,
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 400, _api_error_message(exc))
    return _see_other("/admin/exceptions?notice=exception_reviewed")


# ---------------------------------------------------------------- U9 集成页面
U9_ENTITY_LABELS = {"items": "料品档案", "categories": "物料分类", "boms": "BOM", "inventory": "库存", "orders": "生产订单", "projects": "订单/机台（按项目）"}
U9_STATUS_LABELS = {"OK": "成功", "PENDING_DOC": "待实施商文档", "FAILED": "失败", "RUNNING": "运行中"}


@router.get("/admin/u9", response_class=HTMLResponse)
def admin_u9(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    import json as _json
    from app.u9.config import get_config
    from app.u9.sync import ENTITIES
    cfg = get_config()
    run_log = None
    c = db()
    try:
        logs = []
        if c.execute("SELECT name FROM sqlite_master WHERE name='u9_sync_logs'").fetchone():
            logs = [dict(r) for r in c.execute(
                "SELECT id, run_id, entity, started_at, status, dry_run, total, inserted, updated,"
                " skipped, error FROM u9_sync_logs ORDER BY started_at DESC LIMIT 30").fetchall()]
            run = str(request.query_params.get("run", "")).strip()
            if run:
                r = c.execute("SELECT * FROM u9_sync_logs WHERE id=?", (run,)).fetchone()
                if r:
                    run_log = dict(r)
                    try:
                        run_log["detail"] = _json.loads(run_log.pop("detail_json") or "{}")
                    except ValueError:
                        run_log["detail"] = {}
        local_material_count = c.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        local_order_count = c.execute("SELECT COUNT(*) FROM production_orders").fetchone()[0]
    finally:
        c.close()
    entities = [
        {"id": "items", "label": "料品档案",
         "desc": f"按料号查 U9 料品（ItemMaster/Query）→ 本地物料档案（品名/规格/单位，不改数量）。"
                 f"默认包含本地全部 {local_material_count} 个物料编码。",
         "table": "materials"},
        {"id": "categories", "label": "物料分类",
         "desc": f"读 U9 物料主分类全集（按编码分层：大类→中类→小类）→ 本地分类树，并按料号回填本地 "
                 f"{local_material_count} 个物料的分类。只改分类，不新增物料、不动数量。",
         "table": "material_categories / materials"},
        {"id": "boms", "label": "物料清单 BOM",
         "desc": "按母件/设备编码查 U9 BOM（BOM/Query）并逐层展开，生成一个聚合 BOM 批次（内容未变不重复建）。",
         "table": "agg_bom_batches / agg_bom_items"},
        {"id": "inventory", "label": "库存现存量",
         "desc": (f"直读 U9 库存表（InvTrans_WhQoh）→ 本地库存（按料号+存储地点-库位），"
                  f"并回写物料总量/可用量。默认按本地 {local_material_count} 个料号查询。"
                  if getattr(cfg, "inventory_via_sql", False) else
                  f"查 U9 现存量/可用量（Invtrans/QueryQohAndAvailable）→ 本地库存（按料号+存储地点-库位），"
                  f"并回写物料总量/可用量。默认按本地 {local_material_count} 个料号逐个查询。"),
         "table": "inventory / locations / materials"},
        {"id": "projects", "label": "订单 / 机台（按项目）",
         "desc": "U9 项目号（如 JSBY-26B-001）→ 本地订单；项目下每张 MO → 一台机台，"
                 "机台物料需求取 MO 备料表（BOM 清单）。没挂项目号的 MO 不导入。",
         "table": "production_orders / order_devices / devices / order_material_requirements"},
    ]
    return templates.TemplateResponse(
        request,
        "u9.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "config": cfg.safe_dict(),
            "ready": cfg.ready,
            "entities": [e for e in entities if e["id"] in ENTITIES],
            "logs": logs,
            "run_log": run_log,
            "entity_labels": U9_ENTITY_LABELS,
            "status_labels": U9_STATUS_LABELS,
            "message": request.query_params.get("msg", ""),
            "message_ok": request.query_params.get("ok", "") == "1",
            "now_ts": int(time.time()),
        },
    )


@router.get("/admin/u9/orders/lookup", response_class=HTMLResponse)
def admin_u9_orders_lookup(request: Request):
    """按年份查找 U9 生产订单号（管理台页面版）：与 /api/v1/u9/orders/lookup 同一后端，
    查出的单号可直接一键预览/执行订单同步，产品料号可一键同步 BOM。"""
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    from app.u9.config import get_config
    from app.u9.sync import lookup_orders_by_year
    from app.u9.client import U9Error
    cfg = get_config()
    year = (request.query_params.get("year", "2026") or "").strip()
    limit = (request.query_params.get("limit", "2000") or "2000").strip()
    project = (request.query_params.get("project", "") or "").strip()
    result, error = None, ""
    if request.query_params.get("do") == "1":
        if not cfg.ready:
            error = "U9 未启用或连接信息不完整，无法查找"
        else:
            try:
                result = lookup_orders_by_year(
                    cfg, (int(year) if year else None), max(1, min(5000, int(limit))),
                    project=project)
            except U9Error as e:
                error = f"U9 查询失败：{e}"
            except (TypeError, ValueError):
                error = "年份或数量参数无效"
            except Exception as e:  # noqa: BLE001 - 转中文提示，不吞真因
                error = f"查询异常：{e}"
    return templates.TemplateResponse(
        request,
        "u9_orders_lookup.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "config": cfg.safe_dict(),
            "ready": cfg.ready,
            "year": year,
            "limit": limit,
            "project": project,
            "result": result,
            "error": error,
            "now_ts": int(time.time()),
        },
    )


@router.post("/admin/u9/orders/cleanup")
async def admin_u9_orders_cleanup(request: Request):
    """清理误导入的 U9 订单（按单号前缀，如 MO-）：只删 id 为 u9mo-<prefix> 开头的订单，
    其物料需求经外键级联删除；手工订单不受影响。"""
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    from urllib.parse import quote
    from app.u9.sync import delete_u9_orders_by_doc_prefix
    prefix = str(form.get("doc_prefix", "")).strip()
    c = db()
    try:
        try:
            r = delete_u9_orders_by_doc_prefix(c, prefix)
        except ValueError as e:
            return _see_other("/admin/u9?msg=" + quote(str(e)) + "&ok=0")
        c.commit()
        msg = (f"已清理 U9 导入订单：前缀 {prefix}，删除订单 {r['deleted_orders']} 个，"
               f"连带物料需求 {r['deleted_requirements']} 条")
        return _see_other("/admin/u9?msg=" + quote(msg) + "&ok=1")
    finally:
        c.close()


@router.post("/admin/u9/health")
async def admin_u9_health(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    from urllib.parse import quote
    from app.u9.client import U9Error, get_client
    from app.u9.config import get_config
    cfg = get_config()
    if not cfg.ready:
        return _see_other("/admin/u9?msg=" + quote("U9 未启用或连接信息不完整，无法测试连接") + "&ok=0")
    from starlette.concurrency import run_in_threadpool
    try:
        # 阻塞式 HTTP 调用放到线程池，避免卡住事件循环
        r = await run_in_threadpool(lambda: get_client(cfg).health_check())
    except U9Error as e:
        return _see_other("/admin/u9?msg=" + quote(f"连接失败：{e}") + "&ok=0")
    except Exception as e:  # noqa: BLE001
        return _see_other("/admin/u9?msg=" + quote(f"连接异常：{e}") + "&ok=0")
    return _see_other("/admin/u9?msg=" + quote(f"连接成功：U9C 授权登录正常，耗时 {r['latency_ms']} ms") + "&ok=1")


@router.post("/admin/u9/sync/{entity}")
async def admin_u9_sync(request: Request, entity: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    from urllib.parse import quote
    from fastapi import HTTPException as _HTTPException
    from app.u9.config import get_config
    from app.u9.routes import build_sync_kwargs
    from app.u9.sync import ENTITIES, log_id, run_sync
    cfg = get_config()
    if entity not in ENTITIES:
        return _see_other("/admin/u9?msg=" + quote("未知同步实体") + "&ok=0")
    if not cfg.ready:
        return _see_other("/admin/u9?msg=" + quote("U9 未启用或连接信息不完整，同步未执行") + "&ok=0")
    dry_run = str(form.get("dry_run", "1")) != "0"
    try:
        kwargs = build_sync_kwargs(
            entity, codes=str(form.get("codes", "")), product_codes=str(form.get("product_codes", "")),
            doc_nos=str(form.get("doc_nos", "")), projects=str(form.get("projects", "")),
            wh=str(form.get("wh", "")),
            include_local=str(form.get("include_local", "")) in ("1", "on", "true"),
            update_totals=str(form.get("update_totals", "1")) in ("1", "on", "true"),
            user_id=user["id"], scope=str(form.get("scope", "local")))
    except _HTTPException as e:
        return _see_other("/admin/u9?msg=" + quote(str(getattr(e, "detail", "") or "参数错误")) + "&ok=0")
    from starlette.concurrency import run_in_threadpool
    from app.u9.sync import U9SyncBusy

    def _work():
        # 连接在工作线程内创建与关闭（sqlite 连接不跨线程）；U9 调用为阻塞式，必须离开事件循环
        c = db()
        try:
            return run_sync(entity, c, cfg, dry_run=dry_run, **kwargs)
        finally:
            c.close()

    try:
        result = await run_in_threadpool(_work)
    except U9SyncBusy as e:
        return _see_other("/admin/u9?msg=" + quote(str(e)) + "&ok=0")
    except Exception as e:  # noqa: BLE001 - 转中文提示，不吞真因
        return _see_other("/admin/u9?msg=" + quote(f"同步失败：{e}") + "&ok=0")
    run = quote(result.get("log_id") or log_id(result["run_id"], entity))
    if result["status"] == "PENDING_DOC":
        return _see_other(f"/admin/u9?msg={quote('经典 U9 接口尚未实现，已记录占位日志')}&ok=0&run={run}")
    s = result["stats"]
    mode = "预览" if dry_run else "执行"
    if entity == "projects":
        msg = (f"{U9_ENTITY_LABELS[entity]}{mode}完成：项目 {s.get('projects', 0)} 个、MO {s.get('mos', 0)} 张；"
               f"新增订单 {s.get('orders_inserted', 0)}、新增机台 {s.get('devices_inserted', 0)}，"
               f"已有机台 {s.get('devices_existing', 0)}"
               + ("" if dry_run else f"，新增物料需求 {s.get('requirements_inserted', 0)}、"
                  f"更新 {s.get('requirements_updated', 0)}")
               + (f"；U9 查不到的项目 {s.get('not_found_count')} 个" if s.get("not_found_count") else ""))
        return _see_other(f"/admin/u9?msg={quote(msg)}&ok=1&run={run}#run-detail")
    msg = (f"{U9_ENTITY_LABELS.get(entity, entity)}{mode}完成：U9 返回 {s.get('total', 0)} 条，"
           f"新增 {s.get('inserted', 0)}，更新 {s.get('updated', 0)}，"
           f"不变 {s.get('unchanged', 0)}，跳过 {s.get('skipped', 0)}")
    return _see_other(f"/admin/u9?msg={quote(msg)}&ok=1&run={run}#run-detail")


def _render_warehouse(request: Request, user: sqlite3.Row, error: str):
    code = str(request.query_params.get("code", "")).strip()
    lists = _warehouse_lists(request)
    stocktakes = lists["stocktakes"]
    return templates.TemplateResponse(
        request,
        "warehouse.html",
        {
            **lists,
            "user": user,
            "csrf_token": user["csrf_token"],
            "code": code,
            "material": None,
            "material_error": None,
            "error": error,
            "notice_text": None,
            "expiring": [],
            "expiry_warn_days": EXPIRY_WARN_DAYS,
            "stocktake_ops": {s["id"]: str(uuid.uuid4()) for s in stocktakes},
            "exception_ops": {e["id"]: str(uuid.uuid4()) for e in lists["ex_pending"]},
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
        return None, _error_page(request, 403, "403 禁止访问：物料状态工作台仅对管理员/仓库管理员/车间主管开放")
    return user, None


def _workspace_snapshot(user: sqlite3.Row, order_no: str | None, status: str | None):
    """管理台工作台：一次计算全量工作项，汇总与列表/导出都从同一结果派生。

    语义与分别调用 api_workspace_summary + api_workspace_items 完全一致：
    汇总按全量（不受 orderNo/status 过滤），列表先按 orderNo 再按 status 过滤。
    """
    trace_id = str(uuid.uuid4())
    effective_role, preview, trace_id, _ = _workspace_context(user, None, trace_id, None)
    c = db()
    try:
        all_items = _workspace_items(c, user)
        flow_counts = _workspace_summary_flow_counts(c, user, None)
    finally:
        c.close()
    summary = _workspace_summary_payload(
        all_items, flow_counts, effective_role=effective_role, preview=preview,
        user=user, trace_id=trace_id,
    )
    visible = all_items
    if order_no is not None:
        normalized = order_no.strip()
        visible = [item for item in visible if item["orderNo"] == normalized]
    visible = _workspace_filter_status(visible, status, trace_id)
    return summary, visible


WORKSPACE_FILTER_ORDER = (
    "OUT_OF_STOCK", "ARRIVED", "IN_STOCK", "OUTBOUND_PENDING", "OUTBOUND_APPROVED", "OUTBOUND_CONFIRMED",
    "PENDING", "PICKED_UP", "AT_STATION", "REJECTED", "CANCELLED",
)


@router.get("/admin/workspace", response_class=HTMLResponse)
def admin_workspace(request: Request):
    user, denied = _workspace_or_403(request)
    if denied:
        return denied
    q = request.query_params
    page = _page_param(request)
    page_size = 20
    try:
        summary, visible = _workspace_snapshot(
            user, q.get("orderNo") or None, q.get("status") or None,
        )
    except ApiError as exc:
        return _error_page(request, exc.status_code, _api_error_message(exc))
    except HTTPException as exc:
        return _api_http_error_response(exc, request)
    total = len(visible)
    start = (page - 1) * page_size
    return templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "user": user,
            "csrf_token": user["csrf_token"],
            "summary": {**summary, "generatedAt": _local_time(summary.get("generatedAt"))},
            "items": visible[start:start + page_size],
            "page": page,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
            "total_rows": total,
            "filters": {"status": q.get("status") or "", "orderNo": q.get("orderNo") or ""},
            # 状态筛选下拉：只列 API 接受的工作台状态码，显示中文
            "status_options": [(code, WORKSPACE_STATUS_LABELS.get(code, code)) for code in WORKSPACE_FILTER_ORDER],
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
        return _api_http_error_response(exc, request)
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return None, _error_page(request, 403, "403 禁止访问：BOM 导入仅对管理员/计划员/车间主管开放")
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
        return _error_page(request, exc.status_code, _api_error_message(exc))
    except HTTPException as exc:
        return _api_http_error_response(exc, request)
    # 聚合 BOM 批次（智能导入）
    # 走统一 db()，尊重 DATA_DIR 配置（原先硬编码 app/../data 路径，部署目录不同即打不开库）
    _c = db()
    try:
        agg_batches = _c.execute(
            "SELECT * FROM agg_bom_batches ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    finally:
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


@router.get("/admin/boms/versions/{version_id}", response_class=HTMLResponse)
def admin_bom_detail(version_id: str, request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    _c = db()
    try:
        ver = _c.execute("SELECT * FROM bom_versions WHERE id=?", (version_id,)).fetchone()
        if not ver:
            return _error_page(request, 404, "版本不存在")
        items = _c.execute(
            "SELECT material_code, material_name, specification, unit, quantity, scrap_rate, line_no FROM bom_items WHERE bom_version_id=? ORDER BY line_no",
            (version_id,)
        ).fetchall()
    finally:
        _c.close()
    return templates.TemplateResponse(
        request,
        "bom_detail.html",
        {"user": user, "csrf_token": user["csrf_token"], "ver": dict(ver), "items": [dict(r) for r in items]},
    )


@router.get("/admin/boms/versions/{version_id}/edit", response_class=HTMLResponse)
def admin_bom_edit_form(version_id: str, request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    _c = db()
    try:
        ver = _c.execute("SELECT * FROM bom_versions WHERE id=?", (version_id,)).fetchone()
        if not ver:
            return _error_page(request, 404, "版本不存在")
        items = _c.execute(
            "SELECT * FROM bom_items WHERE bom_version_id=? ORDER BY line_no",
            (version_id,)
        ).fetchall()
    finally:
        _c.close()
    return templates.TemplateResponse(
        request, "bom_edit.html",
        {"user": user, "csrf_token": user["csrf_token"], "ver": dict(ver), "items": [dict(r) for r in items]},
    )


@router.post("/admin/boms/versions/{version_id}/edit")
async def admin_bom_edit_save(version_id: str, request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    new_model = str(form.get("model_code", "")).strip()
    if not new_model:
        return _bom_error_page(request, user, "机型码不能为空")
    # 解析 rows
    rows = {}
    for k in form.keys():
        if k.startswith("rows["):
            # rows[0][material_code]
            parts = k[5:].split("][")
            idx = int(parts[0])
            field = parts[1].rstrip("]")
            rows.setdefault(idx, {})[field] = str(form.get(k, "")).strip()
    row_list = [rows[i] for i in sorted(rows.keys()) if rows[i].get("material_code")]
    if not row_list:
        return _bom_error_page(request, user, "至少需要一行物料")
    _c = db()
    try:
        ver = _c.execute("SELECT * FROM bom_versions WHERE id=?", (version_id,)).fetchone()
        if not ver:
            return _error_page(request, 404, "版本不存在")
        old_model = ver["model_code"]
        # 新版本号
        if new_model == old_model:
            new_ver_no = _c.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM bom_versions WHERE model_code=?", (new_model,)).fetchone()[0]
        else:
            new_ver_no = _c.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM bom_versions WHERE model_code=?", (new_model,)).fetchone()[0]
        import uuid as _uuid, time as _time
        new_vid = str(_uuid.uuid4())
        ts = _time.strftime("%Y-%m-%d %H:%M:%S")
        # 创建新版本
        _c.execute(
            "INSERT INTO bom_versions(id, model_code, version_no, status, source_file_sha256, row_count, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_vid, new_model, new_ver_no, "PUBLISHED", ver["source_file_sha256"], len(row_list), user["id"], ts)
        )
        # 复制物料行（需要 material_id，查 materials 表）
        for r in row_list:
            mat = _c.execute("SELECT id FROM materials WHERE code=?", (r["material_code"],)).fetchone()
            mat_id = mat["id"] if mat else str(_uuid.uuid4())
            _c.execute(
                "INSERT INTO bom_items(id, bom_version_id, material_id, material_code, material_name, specification, unit, quantity, scrap_rate, substitute_material_codes, line_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), new_vid, mat_id, r["material_code"], r["material_name"], r.get("specification", ""), r["unit"], float(r["quantity"] or 0), float(r.get("scrap_rate") or 0), "[]", int(r.get("line_no") or 0))
            )
        # 旧版本归档（同机型才归档）
        if new_model == old_model:
            _c.execute("UPDATE bom_versions SET status='ARCHIVED' WHERE model_code=? AND status='PUBLISHED' AND id<>?", (new_model, new_vid))
        _c.commit()
    finally:
        _c.close()
    return _see_other(f"/admin/boms/versions/{new_vid}?notice=bom_imported")


@router.post("/admin/boms/import", response_class=HTMLResponse)
async def admin_bom_preview(request: Request):
    user, denied = _bom_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
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
    # 走统一 db()，尊重 DATA_DIR 配置（原先硬编码 app/../data 路径，部署目录不同即打不开库）
    _c = db()
    try:
        agg_batches = _c.execute(
            "SELECT * FROM agg_bom_batches ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    finally:
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
    device_id = str(form.get("deviceId", "")).strip()
    model_code = str(form.get("model3dCode", "")).strip() or None
    if not device_id:
        return _see_other("/admin/models?notice=bind_failed")
    try:
        operation_id = str(uuid.uuid4())
        api_bind_device_model_3d(
            device_id,
            DeviceModel3dBind(clientOperationId=uuid.UUID(operation_id), model3dCode=model_code),
            user,
            x_request_id=str(uuid.uuid4()),
            idempotency_key=operation_id,
        )
    except (ApiError, ValidationError, HTTPException):
        return _see_other("/admin/models?notice=bind_failed")
    return _see_other("/admin/models?notice=model_bound")


@router.get("/admin/models/view", response_class=HTMLResponse)
def admin_model_view(request: Request, code: str = "", version: str = ""):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    model_code = (code or "").strip()
    try:
        ver = int(version or 0)
    except ValueError:
        ver = 0
    if not MODEL_CODE_RE.fullmatch(model_code) or ver < 1:
        return _error_page(request, 404, "模型不存在")
    c = db()
    row = c.execute(
        "SELECT model_code, model_name, version, status, byte_size FROM assembly_model_versions WHERE model_code=? AND version=?",
        (model_code, ver),
    ).fetchone()
    c.close()
    if not row:
        return _error_page(request, 404, "模型不存在")
    return templates.TemplateResponse(
        request,
        "model_view.html",
        {
            "user": user,
            "model_code": row["model_code"],
            "model_name": row["model_name"],
            "version": row["version"],
            "status": row["status"],
            "byte_size": row["byte_size"],
            "content_url": f"/admin/models/content?code={row['model_code']}&version={row['version']}",
            "status_labels": MODEL_STATUS_LABELS,
        },
    )


@router.get("/admin/models/content")
def admin_model_content(request: Request, code: str = "", version: str = ""):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    model_code = (code or "").strip()
    try:
        ver = int(version or 0)
    except ValueError:
        ver = 0
    if not MODEL_CODE_RE.fullmatch(model_code) or ver < 1:
        raise HTTPException(404, "资源不存在")
    c = db()
    row = c.execute(
        "SELECT storage_key, sha256 FROM assembly_model_versions WHERE model_code=? AND version=?",
        (model_code, ver),
    ).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "资源不存在")
    root = UPLOAD_DIR.resolve()
    path = (root / row["storage_key"]).resolve()
    if root not in path.parents or path.suffix.lower() != ".glb" or not path.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(
        path,
        media_type="model/gltf-binary",
        headers={"ETag": f'"{row["sha256"]}"', "Cache-Control": "private, max-age=86400"},
    )


# ==================== S15：CSV 数据导出（契约 §6.14）====================


def _csv_chunks(rows, chunk_rows: int = 500):
    """逐块生成 CSV 字节：UTF-8 BOM + DictWriter（列取首行键），格式与旧 _csv_response 逐字节一致。"""
    buf = io.StringIO()
    buf.write("\ufeff")  # Excel 友好的 UTF-8 BOM
    it = iter(rows)
    first = next(it, None)
    if first is not None:
        writer = csv.DictWriter(buf, fieldnames=list(first.keys()))
        writer.writeheader()
        writer.writerow(first)
        n = 1
        for row in it:
            writer.writerow(row)
            n += 1
            if n % chunk_rows == 0:
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate(0)
    tail = buf.getvalue()
    if tail:
        yield tail.encode("utf-8")


def _csv_response(filename: str, rows) -> StreamingResponse:
    """单次计算的结果流式写出 CSV（不再按 API 单页 OFFSET 反复查询）。"""
    return StreamingResponse(
        _csv_chunks(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


@router.get("/admin/audit/export")
def admin_audit_export(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    q = request.query_params
    rows = audit_logs_export_items(
        user,
        from_=q.get("from") or None,
        to_=q.get("to") or None,
        eventType=q.get("eventType") or None,
        entityType=q.get("entityType") or None,
        operatorId=q.get("operatorId") or None,
        entityId=q.get("entityId") or None,
    )
    return _csv_response("audit-logs", rows)


@router.get("/admin/workspace/export")
def admin_workspace_export(request: Request):
    user, denied = _workspace_or_403(request)
    if denied:
        return denied
    q = request.query_params
    _, rows = _workspace_snapshot(user, q.get("orderNo") or None, q.get("status") or None)
    return _csv_response("material-workspace", rows)


@router.get("/admin/reports/labor/export")
def admin_labor_export(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    q = request.query_params
    rows = backend_app.state.workshop_labor_summary_export(
        user,
        deviceId=q.get("deviceId") or None,
        orderNo=q.get("orderNo") or None,
        assemblerId=q.get("assemblerId") or None,
    )
    return _csv_response("labor-summary", rows)


@router.get("/admin/reports/machines/export")
def admin_machines_export(request: Request):
    user, denied = _report_or_403(request)
    if denied:
        return denied
    rows = machine_progress_export_items(user, None)
    return _csv_response("machine-progress", rows)


# ==================== S21：生产订单创建面（契约 §6.16）====================

ORDER_CREATE_ROLES = ("ADMIN", "PLANNER")


def _order_create_or_403(request: Request):
    user = _session_user(request)
    if user is None:
        return None, _see_other("/admin/login")
    if user["role"] not in ORDER_CREATE_ROLES:
        return None, _error_page(request, 403, "403 禁止访问：创建订单仅对管理员/计划员开放")
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
        return _error_page(request, 403, "CSRF 校验失败")
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
        return _error_page(request, 403, "CSRF 校验失败")
    operation_id = str(form.get("clientOperationId", "")) or str(uuid.uuid4())
    try:
        api_set_order_status(
            order_no,
            OrderStatusRequest(clientOperationId=operation_id, status=str(form.get("status", ""))),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError, HTTPException) as exc:
        return _action_failed(request, exc, "/admin/orders", "返回订单列表")
    return _see_other("/admin/orders?notice=order_status_changed")


# ==================== S23：会话强制下线（契约 §6.18）====================


@router.post("/admin/users/{user_id}/revoke-sessions")
async def admin_revoke_sessions(request: Request, user_id: str):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    try:
        api_revoke_sessions(
            user_id,
            SessionRevokeRequest(clientOperationId=str(uuid.uuid4())),
            user=user,
            x_request_id=str(uuid.uuid4()),
        )
    except (ApiError, ValidationError, HTTPException) as exc:
        return _action_failed(request, exc, "/admin/users", "返回用户列表")
    return _see_other("/admin/users?notice=sessions_revoked")


# ==================== S24：装配任务创建（契约 §6.19）====================

TASK_CREATE_ROLES = ("ADMIN", "WORKSHOP_SUPERVISOR")


@router.post("/admin/tasks/create")
async def admin_task_create(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in TASK_CREATE_ROLES:
        return _error_page(request, 403, "403 禁止访问：创建任务仅对管理员/车间主管开放")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
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
    except (ApiError, ValidationError, HTTPException) as exc:
        return _action_failed(request, exc, "/admin/tasks", "返回任务列表")
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
        return _error_page(request, 400, "缺少编号参数")
    payload, error = _barcode_preview(entity, key, kind)
    if error:
        return _error_page(request, 404, error)
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
        return _error_page(request, 403, "403 禁止访问：管理界面仅对管理员开放")
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

# ---------- 智能助手接口统一守卫（v0.6.2）----------
# 会话 + 角色 + X-CSRF-Token（与 /admin/system/update/apply 同一模式）+ JSON Content-Type。
# 之前只校验会话存在，靠 SameSite=Lax 兜底；text/plain 表单可构造 JSON 体。
# 对话/会话：管理台业务角色（悬浮球在各页面可用）；BOM/聚合 BOM：BOM 导入角色 + 仓库管理员（仓库需用聚合 BOM）；
# 智能导入：管理员/仓库管理员/计划员；LLM 设置与用量：仅管理员。
AGENT_CHAT_ROLES = ("ADMIN", "WAREHOUSE_ADMIN", "WORKSHOP_SUPERVISOR", "PLANNER")
AGENT_IMPORT_ROLES = ("ADMIN", "WAREHOUSE_ADMIN", "PLANNER")
AGENT_BOM_ROLES = ("ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR", "WAREHOUSE_ADMIN")
AGENT_SETTINGS_ROLES = ("ADMIN",)


class _AgentGuardDenied(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


async def _agent_guard_denied_handler(request: Request, exc: _AgentGuardDenied):
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code)


backend_app.add_exception_handler(_AgentGuardDenied, _agent_guard_denied_handler)


def _agent_guard(roles: tuple[str, ...], *, csrf: bool = False, json_body: bool = False,
                 forbidden_message: str = "无权使用该功能"):
    """FastAPI 依赖：返回会话用户，否则以 {"error": {...}} JSON 拒绝。"""

    def dependency(request: Request) -> sqlite3.Row:
        user = _session_user(request)
        if user is None:
            raise _AgentGuardDenied(401, "UNAUTHORIZED", "未登录")
        if user["role"] not in roles:
            raise _AgentGuardDenied(403, "FORBIDDEN", forbidden_message)
        if csrf and not _csrf_ok(request.headers.get("X-CSRF-Token", ""), user["csrf_token"]):
            raise _AgentGuardDenied(403, "CSRF_FAILED", "CSRF 校验失败，请刷新页面")
        if json_body:
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                raise _AgentGuardDenied(415, "UNSUPPORTED_MEDIA_TYPE", "请求体必须是 application/json")
        return user

    return dependency


_agent_chat_read = _agent_guard(AGENT_CHAT_ROLES)
_agent_chat_write = _agent_guard(AGENT_CHAT_ROLES, csrf=True, json_body=True)
_agent_chat_delete = _agent_guard(AGENT_CHAT_ROLES, csrf=True)
_agent_import_upload = _agent_guard(AGENT_IMPORT_ROLES, csrf=True, forbidden_message="无权执行智能导入")
_agent_import_write = _agent_guard(AGENT_IMPORT_ROLES, csrf=True, json_body=True, forbidden_message="无权执行智能导入")
_agent_bom_read = _agent_guard(AGENT_BOM_ROLES, forbidden_message="无权执行 BOM 导入")
_agent_bom_upload = _agent_guard(AGENT_BOM_ROLES, csrf=True, forbidden_message="无权执行 BOM 导入")
_agent_bom_write = _agent_guard(AGENT_BOM_ROLES, csrf=True, json_body=True, forbidden_message="无权执行 BOM 导入")
_agent_settings_read = _agent_guard(AGENT_SETTINGS_ROLES, forbidden_message="仅管理员可查看")
_agent_settings_write = _agent_guard(AGENT_SETTINGS_ROLES, csrf=True, json_body=True, forbidden_message="仅管理员可修改")


@router.post("/admin/agent/chat")
async def admin_agent_chat(request: Request, user: sqlite3.Row = Depends(_agent_chat_write)):
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
        result = await asyncio.to_thread(_run)
    except ValueError as e:  # 会话不存在 / 不属于当前用户等业务校验
        return _agent_bad(str(e))
    except Exception as e:  # noqa: BLE001
        return _agent_fail(request, e, "AGENT_ERROR", 503)
    return JSONResponse(result)


@router.post("/admin/agent/chat/stream")
async def admin_agent_chat_stream(request: Request, user: sqlite3.Row = Depends(_agent_chat_write)):
    """流式对话（SSE）：逐字输出 + 工具步骤事件。事件见 agent.service.run_chat 的 on_event。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "消息不能为空"}}, status_code=400)
    session_id = body.get("session_id")
    import asyncio
    import json as _json
    import threading
    from fastapi.responses import StreamingResponse

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def push(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    trace_id = _request_trace_id(request)

    def worker() -> None:
        c = db()
        try:
            cfg = _agent_cfg_effective(c)
            result = agent_service.run_chat(c, cfg, user["id"], session_id, message, on_event=push)
            push({"type": "done", **result})
        except ValueError as e:  # 业务校验文案（会话不存在等）
            push({"type": "error", "message": str(e)[:200], "traceId": trace_id})
        except Exception as e:  # noqa: BLE001
            from app.agent.llm import LLMError
            if isinstance(e, LLMError):
                _log.warning("agent stream LLM error traceId=%s: %s", trace_id, e)
                msg = _agent_llm_public_message(e)
            else:
                _log.error("agent stream failed traceId=%s", trace_id, exc_info=e)
                msg = AGENT_GENERIC_MESSAGE
            push({"type": "error", "message": msg, "traceId": trace_id})
        finally:
            c.close()
            push(None)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            ev = await queue.get()
            if ev is None:
                break
            yield "data: " + _json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 会话管理 / 反馈 / 用量（管理台） ----------

def _agent_json_user(request: Request):
    user = _session_user(request)
    if user is None:
        return None, JSONResponse({"error": {"code": "UNAUTHORIZED", "message": "未登录"}}, status_code=401)
    return user, None


def _agent_bad(msg: str, code: int = 400):
    return JSONResponse({"error": {"code": "BAD_REQUEST" if code == 400 else "NOT_FOUND", "message": msg}},
                        status_code=code)


AGENT_GENERIC_MESSAGE = "服务暂时不可用，请稍后重试"


def _agent_llm_public_message(exc: Exception) -> str:
    """LLMError 文案由本项目编写，但可能拼接上游响应片段 / 网络原因：只保留冒号前的固定部分。"""
    msg = str(exc) or "LLM 调用失败"
    for sep in ("：", ": "):
        if sep in msg:
            msg = msg.split(sep, 1)[0]
    return msg[:120]


def _agent_fail(request: Request, exc: Exception, code: str, status: int = 500, *, upload: bool = False):
    """智能助手接口统一错误：ApiError 透传其中文文案；其他异常只返回通用文案 + traceId，完整异常写服务端日志。

    upload=True（文件预览接口）：文件校验类 415/422 统一为 400，页面按「请求有误」提示。
    """
    trace_id = _request_trace_id(request)
    if isinstance(exc, ApiError):
        status_code = 400 if upload and exc.status_code in (415, 422) else exc.status_code
        return JSONResponse({"error": {
            "code": exc.code, "message": str(exc.detail or "操作失败"),
            "traceId": _safe_trace_id(exc.trace_id) or trace_id,
        }}, status_code=status_code)
    from app.agent.llm import LLMError
    if isinstance(exc, LLMError):
        _log.warning("agent LLM error code=%s traceId=%s path=%s: %s", code, trace_id, request.url.path, exc)
        return JSONResponse({"error": {"code": code, "message": _agent_llm_public_message(exc),
                                       "traceId": trace_id}}, status_code=status)
    _log.error("agent endpoint failed code=%s traceId=%s path=%s", code, trace_id, request.url.path, exc_info=exc)
    return JSONResponse({"error": {"code": code, "message": AGENT_GENERIC_MESSAGE, "traceId": trace_id}},
                        status_code=status)


@router.get("/admin/agent/sessions")
def admin_agent_sessions(request: Request, user: sqlite3.Row = Depends(_agent_chat_read)):
    c = db()
    try:
        from app.agent.db import ensure_agent_tables
        ensure_agent_tables(c)
        return JSONResponse({"sessions": agent_service.list_sessions(c, user["id"], limit=50)})
    finally:
        c.close()


@router.get("/admin/agent/sessions/{session_id}/messages")
def admin_agent_session_messages(request: Request, session_id: str,
                                 user: sqlite3.Row = Depends(_agent_chat_read)):
    c = db()
    try:
        msgs = agent_service.get_messages(c, user["id"], session_id, limit=200)
    except ValueError as e:
        return _agent_bad(str(e), 404)
    finally:
        c.close()
    # 前端只展示用户提问与最终回答
    out = [m for m in msgs if m["role"] == "user" or (m["role"] == "assistant" and not m["tool_calls_json"] and m["content"])]
    for m in out:
        m.pop("tool_calls_json", None)
    return JSONResponse({"messages": out})


@router.post("/admin/agent/sessions/{session_id}")
async def admin_agent_session_update(request: Request, session_id: str,
                                     user: sqlite3.Row = Depends(_agent_chat_write)):
    """body: {"title": "..."} 重命名；{"pinned": true/false} 置顶。"""
    try:
        body = await request.json()
    except Exception:
        return _agent_bad("请求体解析失败")
    c = db()
    try:
        if "title" in body:
            agent_service.rename_session(c, user["id"], session_id, str(body.get("title") or ""))
        if "pinned" in body:
            agent_service.pin_session(c, user["id"], session_id, bool(body.get("pinned")))
    except ValueError as e:
        return _agent_bad(str(e))
    finally:
        c.close()
    return JSONResponse({"ok": True})


@router.delete("/admin/agent/sessions/{session_id}")
def admin_agent_session_delete(request: Request, session_id: str,
                               user: sqlite3.Row = Depends(_agent_chat_delete)):
    c = db()
    try:
        agent_service.delete_session(c, user["id"], session_id)
    except ValueError as e:
        return _agent_bad(str(e), 404)
    finally:
        c.close()
    return JSONResponse({"ok": True})


@router.post("/admin/agent/feedback")
async def admin_agent_feedback(request: Request, user: sqlite3.Row = Depends(_agent_chat_write)):
    try:
        body = await request.json()
        rating = int(body.get("rating"))
    except Exception:
        return _agent_bad("参数错误")
    c = db()
    try:
        agent_service.set_feedback(c, user["id"], str(body.get("message_id") or ""), rating, body.get("comment"))
    except ValueError as e:
        return _agent_bad(str(e))
    finally:
        c.close()
    return JSONResponse({"ok": True})


@router.get("/admin/agent/usage")
def admin_agent_usage(request: Request, days: int = 14):
    user, err = _agent_json_user(request)
    if err: return err
    if user["role"] != "ADMIN":
        return JSONResponse({"error": {"code": "FORBIDDEN", "message": "仅管理员可查看用量"}}, status_code=403)
    c = db()
    try:
        stats = agent_service.usage_stats(c, days)
        stats["daily_token_limit"] = _agent_cfg_effective(c).daily_token_limit
    finally:
        c.close()
    return JSONResponse(stats)


# 智能导入 Session 包装（管理后台用 Web Session，不走 Token）
from fastapi import UploadFile as _UploadFile, File as _File, Form as _Form
from app.agent import importer as _agent_importer
from app.agent.config import AgentConfig as _AgentConfig

@router.post("/admin/agent/import/preview")
async def admin_agent_import_preview(request: Request, file: _UploadFile = _File(...), target: str = _Form(...),
                                     user: sqlite3.Row = Depends(_agent_import_upload)):
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
async def admin_agent_import_commit(request: Request, user: sqlite3.Row = Depends(_agent_import_write)):
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
async def admin_agent_bom_preview(request: Request, file: _UploadFile = _File(...), model_code: str = _Form(...),
                                  user: sqlite3.Row = Depends(_agent_bom_upload)):
    try:
        preview = await api_bom_preview(file=file, modelCode=model_code.strip(), user=user, x_request_id=str(__import__("uuid").uuid4()))
    except Exception as e:  # noqa: BLE001
        return _agent_fail(request, e, "BOM_ERROR", upload=True)
    return JSONResponse(preview)

@router.post("/admin/agent/import/bom/commit")
async def admin_agent_bom_commit(request: Request, user: sqlite3.Row = Depends(_agent_bom_write)):
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
    except Exception as e:  # noqa: BLE001
        return _agent_fail(request, e, "BOM_ERROR")
    return JSONResponse(result)


# 多机型 BOM 聚合
@router.get("/admin/agent/bom/models")
async def admin_agent_bom_models(request: Request, user: sqlite3.Row = Depends(_agent_bom_read)):
    c = db()
    try:
        rows = c.execute("""
            SELECT m.model_code, m.model_name, m.planned_quantity, o.order_no, o.product_name, o.status AS order_status
            FROM production_order_models m LEFT JOIN production_orders o ON o.id = m.order_id
            ORDER BY o.created_at DESC, m.model_code
        """).fetchall()
        bom = {r["model_code"]: (r["pub"], r["draft"]) for r in c.execute("""
            SELECT model_code, SUM(status = 'PUBLISHED') AS pub, SUM(status = 'DRAFT') AS draft
            FROM bom_versions GROUP BY model_code""").fetchall()}
        models = []
        for r in rows:
            pub, draft = bom.get(r["model_code"], (0, 0))
            models.append({"model_code": r["model_code"], "model_name": r["model_name"], "has_bom": (pub or 0) > 0,
                           "draft_only": not pub and (draft or 0) > 0,
                           "planned_quantity": r["planned_quantity"] or 0, "order_no": r["order_no"] or "",
                           "product_name": r["product_name"] or "", "order_status": r["order_status"] or ""})
        # 有 BOM 但没挂在任何订单上的机型也可手选
        listed = {m["model_code"] for m in models}
        for code, (pub, draft) in sorted(bom.items()):
            if code not in listed:
                models.append({"model_code": code, "model_name": "", "has_bom": (pub or 0) > 0,
                               "draft_only": not pub and (draft or 0) > 0, "planned_quantity": 0,
                               "order_no": "", "product_name": "", "order_status": ""})
        # 有 BOM 的排前面
        models.sort(key=lambda m: (not m["has_bom"], m["order_no"] == "", m["model_code"]))
        orders = []
        seen = {}
        for m in models:
            if not m["order_no"]:
                continue
            if m["order_no"] not in seen:
                seen[m["order_no"]] = {"order_no": m["order_no"], "product_name": m["product_name"],
                                       "status": m["order_status"], "model_codes": [], "with_bom": 0}
                orders.append(seen[m["order_no"]])
            seen[m["order_no"]]["model_codes"].append(m["model_code"])
            seen[m["order_no"]]["with_bom"] += 1 if m["has_bom"] else 0
        # 没关联机型的订单也列出；U9 项目订单的机台带 MO 备料表（机台级物料需求），可直接按订单聚合
        req_devs = {r[0]: r[1] for r in c.execute(
            "SELECT order_id, COUNT(DISTINCT device_id) FROM order_material_requirements"
            " WHERE device_id IS NOT NULL GROUP BY order_id").fetchall()}
        for r in c.execute("SELECT id, order_no, product_name, status FROM production_orders ORDER BY created_at DESC").fetchall():
            if r["order_no"] not in seen:
                orders.append({"order_no": r["order_no"], "product_name": r["product_name"] or "",
                               "status": r["status"], "model_codes": [], "with_bom": 0,
                               "req_devices": req_devs.get(r["id"], 0)})
    finally:
        c.close()
    return JSONResponse({"models": models, "orders": orders})


def _aggregate_order_requirements(order_no: str) -> dict | None:
    """按订单聚合机台级物料需求（U9 项目订单：每台机台 = 一张 MO，需求 = MO 备料表）。
    输出与机型 BOM 聚合同一结构，per_model 的键是机台号。"""
    from app.agent.tools import _stock_by_code
    c = db()
    try:
        o = c.execute("SELECT id FROM production_orders WHERE order_no=?", (order_no,)).fetchone()
        if not o:
            return None
        rows = c.execute(
            "SELECT od.device_no, m.code, m.name, m.specification, m.unit, r.required_quantity"
            " FROM order_material_requirements r JOIN order_devices od ON od.id=r.device_id"
            " JOIN materials m ON m.id=r.material_id WHERE r.order_id=? ORDER BY od.sequence_no",
            (o["id"],)).fetchall()
        if not rows:
            return None
        agg: dict = {}
        per: dict = {}
        for dn, code, name, spec, unit, q in rows:
            per.setdefault(dn, {"rows": 0, "quantity": 1})["rows"] += 1
            a = agg.setdefault(code, {"material_code": code, "material_name": name, "specification": spec,
                                      "unit": unit, "total_quantity": 0.0, "models": []})
            a["total_quantity"] += q or 0
            a["models"].append(dn)
        stock, src = _stock_by_code(c, list(agg))
    finally:
        c.close()
    result = sorted(agg.values(), key=lambda x: -x["total_quantity"])
    short = 0
    for r in result:
        r["models"] = sorted(set(r["models"]))
        r["model_count"] = len(r["models"])
        r["available"] = round(stock.get(r["material_code"], 0.0), 4)
        r["shortage"] = round(max(r["total_quantity"] - r["available"], 0.0), 4)
        r["no_stock_record"] = r["material_code"] not in stock
        short += 1 if r["shortage"] > 0 else 0
    return {"materials": result, "per_model": per, "model_count": len(per), "short_types": short,
            "stock_source": src, "source": "order_requirements", "order_no": order_no}


def _parse_bom_agg_items(items: str, model_codes: str) -> list[tuple[str, float]]:
    """items=CODE:台数,CODE2:台数；兼容旧参数 model_codes（每个机型按 1 台）。"""
    out: dict[str, float] = {}
    for part in (items or "").split(","):
        part = part.strip()
        if not part:
            continue
        code, _, q = part.partition(":")
        try:
            qty = float(q) if q.strip() else 1.0
        except ValueError:
            raise ValueError(f"台数无效：{part}")
        if qty <= 0 or qty > 100000:
            raise ValueError(f"台数需在 0-100000 之间：{part}")
        out[code.strip()] = out.get(code.strip(), 0) + qty
    for code in (model_codes or "").split(","):
        if code.strip() and code.strip() not in out:
            out[code.strip()] = 1.0
    return [(k, v) for k, v in out.items() if k]


@router.get("/admin/agent/bom/aggregate")
async def admin_agent_bom_aggregate(request: Request, model_codes: str = "", items: str = "",
                                    order_no: str = "", user: sqlite3.Row = Depends(_agent_bom_read)):
    if order_no.strip():
        from starlette.concurrency import run_in_threadpool
        res = await run_in_threadpool(_aggregate_order_requirements, order_no.strip())
        if res is None:
            return JSONResponse({"error": {"code": "NOT_FOUND", "message": "订单不存在或没有机台备料表"}},
                                status_code=404)
        return JSONResponse(res)
    try:
        pairs = _parse_bom_agg_items(items, model_codes)
    except ValueError as e:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": str(e)}}, status_code=400)
    if not pairs:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请选择机型"}}, status_code=400)
    from app.agent.tools import _stock_by_code
    c = db()
    try:
        agg = {}
        per_model = {}
        for code, qty in pairs:
            v = c.execute(
                "SELECT id, version_no FROM bom_versions WHERE model_code = ? AND status = 'PUBLISHED' ORDER BY version_no DESC LIMIT 1",
                (code,)).fetchone()
            if not v:
                per_model[code] = {"error": "无已发布 BOM", "quantity": qty}
                continue
            rows = c.execute(
                "SELECT material_code, material_name, specification, unit, quantity, scrap_rate FROM bom_items WHERE bom_version_id = ?",
                (v["id"],)).fetchall()
            per_model[code] = {"rows": len(rows), "quantity": qty, "version_no": v["version_no"]}
            for it in rows:
                mc = it["material_code"]
                if mc not in agg:
                    agg[mc] = {"material_code": mc, "material_name": it["material_name"],
                               "specification": it["specification"], "unit": it["unit"],
                               "total_quantity": 0.0, "models": []}
                agg[mc]["total_quantity"] += (it["quantity"] or 0) * qty / (1 - (it["scrap_rate"] or 0))
                agg[mc]["models"].append(code)
        stock, stock_src = _stock_by_code(c, list(agg)) if agg else ({}, "")
        result = sorted(agg.values(), key=lambda x: -x["total_quantity"])
        short_types = 0
        for r in result:
            r["total_quantity"] = round(r["total_quantity"], 4)
            r["models"] = sorted(set(r["models"]))
            r["model_count"] = len(r["models"])
            r["available"] = round(stock.get(r["material_code"], 0.0), 4)
            r["shortage"] = round(max(r["total_quantity"] - r["available"], 0.0), 4)
            r["no_stock_record"] = r["material_code"] not in stock
            short_types += 1 if r["shortage"] > 0 else 0
    finally:
        c.close()
    return JSONResponse({"materials": result, "per_model": per_model, "model_count": len(pairs),
                         "short_types": short_types, "stock_source": stock_src})


# 聚合 BOM 导入 Session 包装（智能助手页用）
@router.post("/admin/agent/import/aggbom/preview")
async def admin_agent_aggbom_preview(request: Request, file: _UploadFile = _File(...),
                                     user: sqlite3.Row = Depends(_agent_bom_upload)):
    form_data = await request.form()
    upload = _UploadFile(file=file.file, filename=file.filename)
    try:
        from app.main import preview_agg_bom as _preview_aggbom
        preview = await _preview_aggbom(file=upload, user=user, x_request_id=str(__import__("uuid").uuid4()))
    except Exception as e:  # noqa: BLE001
        return _agent_fail(request, e, "AGGBOM_ERROR", upload=True)
    return JSONResponse(preview)

@router.post("/admin/agent/import/aggbom/commit")
async def admin_agent_aggbom_commit(request: Request, user: sqlite3.Row = Depends(_agent_bom_write)):
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
    except Exception as e:  # noqa: BLE001
        return _agent_fail(request, e, "AGGBOM_ERROR")
    return JSONResponse(result)

@router.get("/admin/agent/aggbom/batches")
async def admin_agent_aggbom_batches(request: Request, user: sqlite3.Row = Depends(_agent_bom_read)):
    from app.main import list_agg_bom_batches as _list_batches
    return JSONResponse(_list_batches(user=user))

@router.get("/admin/agent/aggbom/aggregate")
async def admin_agent_aggbom_aggregate(request: Request, batch_id: str = "", device_codes: str = "",
                                       user: sqlite3.Row = Depends(_agent_bom_read)):
    from app.main import aggregate_agg_bom as _agg
    try:
        return JSONResponse(_agg(batch_id=batch_id, device_codes=device_codes, user=user))
    except Exception as e:  # noqa: BLE001
        return _agent_fail(request, e, "AGGBOM_ERROR")

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
    if str(s.get("daily_token_limit") or "").isdigit(): kw["daily_token_limit"] = int(s["daily_token_limit"])
    if kw: cfg = replace(cfg, **kw)
    return cfg

@router.get("/admin/agent/settings")
def admin_agent_settings_get(request: Request, user: sqlite3.Row = Depends(_agent_settings_read)):
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
async def admin_agent_settings_set(request: Request, user: sqlite3.Row = Depends(_agent_settings_write)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": "请求体解析失败"}}, status_code=400)
    allowed = ("base_url", "api_key", "model", "enabled", "daily_token_limit")
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


# --------------------------------------------------------------------------
# APP 更新通道：APK 发布管理
# --------------------------------------------------------------------------

@router.get("/admin/releases", response_class=HTMLResponse)
def admin_releases(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    from app import updates as _updates
    from app import app_sync as _app_sync
    releases = _updates.list_releases()
    return _render_releases(request, user, releases, _app_sync.get_status(), None)


def _render_releases(request: Request, user: Any, releases: list, sync_status: dict, remote: dict | None,
                     remote_error: str | None = None, remote_source: str = "auto"):
    return templates.TemplateResponse(
        request, "releases.html",
        {"user": user,
         "releases": releases,
         "sync_status": sync_status,
         "remote": remote,
         "remote_error": remote_error,
         "remote_source": remote_source,
         **_coded_notice(request, RELEASE_NOTICES, "上传失败"),
         "csrf_token": user["csrf_token"]},
    )


@router.post("/admin/releases/upload")
async def admin_releases_upload(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return _error_page(request, 403, "403 禁止访问")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return _see_other("/admin/releases?notice=no_file")
    try:
        version_code = int(str(form.get("version_code", "")).strip())
    except (ValueError, AttributeError):
        return _see_other("/admin/releases?notice=bad_version")
    from app import updates as _updates
    try:
        data = await upload.read()
        _updates.save_apk_upload(
            data,
            str(upload.filename),
            version_code,
            str(form.get("version_name", "")).strip(),
            str(form.get("changelog", "")).strip(),
            user["username"],
        )
    except ApiError as exc:
        return _see_other(f"/admin/releases?notice={exc.code}")
    except HTTPException as exc:
        return _see_other("/admin/releases?notice=upload_failed")
    return _see_other("/admin/releases?notice=uploaded")


@router.post("/admin/releases/sync/check")
async def admin_releases_sync_check(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    source = str(form.get("source", "auto") or "auto").strip().lower()
    from app import updates as _updates
    from app import app_sync as _app_sync
    from starlette.concurrency import run_in_threadpool
    remote, err = None, None
    try:
        # 网络请求（最多两个来源各 15 秒）放线程池，不阻塞事件循环
        remote = await run_in_threadpool(_app_sync.list_remote, source)
    except ApiError as exc:
        err = str(exc.detail)
    return _render_releases(request, user, _updates.list_releases(), _app_sync.get_status(),
                            remote, err, source)


@router.post("/admin/releases/sync")
async def admin_releases_sync(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    source = str(form.get("source", "auto") or "auto").strip().lower()
    tag = str(form.get("tag", "") or "").strip()
    from app import app_sync as _app_sync
    from starlette.concurrency import run_in_threadpool
    try:
        r = await run_in_threadpool(_app_sync.start_sync, source, tag, user["username"])
    except ApiError as exc:
        return _see_other(f"/admin/releases?notice={exc.code}")
    c = db()
    try:
        audit(c, user["id"], user["role"], "APP_RELEASE_SYNC", "app_release",
              f'{r.get("source", source)}:{r.get("tag", tag)}:{r.get("versionCode", "")}',
              "SUCCESS", _request_trace_id(request) or "web-admin")
        c.commit()
    finally:
        c.close()
    return _see_other("/admin/releases?notice=" + ("sync_already" if r["state"] == "already" else "sync_started"))


@router.post("/admin/releases/delete")
async def admin_releases_delete(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return _error_page(request, 403, "403 禁止访问")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    try:
        version_code = int(str(form.get("version_code", "")).strip())
    except (ValueError, AttributeError):
        return _see_other("/admin/releases?notice=bad_version")
    from app import updates as _updates
    ok = _updates.delete_release(version_code)
    return _see_other(f"/admin/releases?notice={'deleted' if ok else 'not_found'}")


# --------------------------------------------------------------------------
# 后端自更新通道（GitHub，手动）
# --------------------------------------------------------------------------

@router.get("/admin/system", response_class=HTMLResponse)
def admin_system(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    from app import updates as _updates
    from app.main import APP_VERSION
    import json as _json2
    from app import demo_purge as _demo_purge
    c = db()
    try:
        demo_plan = _demo_purge.plan(c)
        demo_plan.pop("rows", None)
        demo_plan["purged"] = _demo_purge.demo_purged(c)
    finally:
        c.close()
    check_raw = request.query_params.get("check", "")
    try:
        check_result = _json2.loads(check_raw) if check_raw else None
    except ValueError:
        check_result = None
    return templates.TemplateResponse(
        request, "system.html",
        {"user": user,
         "app_version": APP_VERSION,
         "deployed_sha": _updates.get_deployed_sha(),
         "deployed_sha_diag": _updates.deployed_sha_diagnose(),
         "update_status": _updates.get_update_status(),
         "check_result": check_result,
         "demo_plan": demo_plan,
         **_coded_notice(request, SYSTEM_NOTICES, "操作失败，请稍后重试"),
         "csrf_token": user["csrf_token"]},
    )


@router.post("/admin/system/purge-demo")
async def admin_system_purge_demo(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    if str(form.get("confirm", "")).strip() != "清除":
        return _see_other("/admin/system?notice=DEMO_CONFIRM#demo")
    from app import demo_purge as _demo_purge
    from app.main import now as _now
    from starlette.concurrency import run_in_threadpool

    def _work():
        # 连接在工作线程内创建与关闭；整库扫描与删除是阻塞操作，不放在事件循环里
        c = db()
        try:
            result = _demo_purge.purge(c, _now())
            try:
                audit(c, user["id"], user["role"], "PURGE_DEMO_DATA", "system", "demo", "SUCCESS")
                c.commit()
            except Exception:  # noqa: BLE001 - 审计失败不影响已完成的清除
                _log.exception("demo purge audit failed")
            return result
        finally:
            c.close()

    try:
        result = await run_in_threadpool(_work)
        _log.info("demo data purged by %s: %s", user["id"], result.get("counts"))
    except Exception:  # noqa: BLE001
        _log.exception("demo purge failed")
        return _see_other("/admin/system?notice=DEMO_PURGE_FAILED#demo")
    return _see_other("/admin/system?notice=DEMO_PURGED#demo")


@router.post("/admin/system/update/check")
async def admin_system_update_check(request: Request):
    user = _session_user(request)
    if user is None:
        return _see_other("/admin/login")
    if user["role"] not in WEB_ROLES:
        return _error_page(request, 403, "403 禁止访问")
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    from app import updates as _updates
    try:
        info = _updates.check_github_update()
    except ApiError as exc:
        return _see_other(f"/admin/system?notice={exc.code}")
    import json as _json
    from urllib.parse import quote as _quote
    payload = _quote(_json.dumps({
        "latestSha": info["latestSha"][:12],
        "fullSha": info["latestSha"],
        "message": info["latestMessage"],
        "date": info["latestDate"],
        "updateAvailable": info["updateAvailable"],
    }, ensure_ascii=False))
    return _see_other(f"/admin/system?check={payload}")


@router.post("/admin/system/update/apply")
async def admin_system_update_apply(request: Request):
    """网页会话触发后端更新（原先 fetch 的是 Bearer 鉴权的 /api 接口，网页调用恒 401）。"""
    user, denied = _admin_or_403(request)
    if denied:
        return JSONResponse({"ok": False, "message": "未登录或无权限"}, status_code=403)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    token = request.headers.get("X-CSRF-Token", "") or str((body or {}).get("csrf_token", ""))
    if not _csrf_ok(token, user["csrf_token"]):
        return JSONResponse({"ok": False, "message": "CSRF 校验失败，请刷新页面"}, status_code=403)
    from app import updates as _updates
    sha = str((body or {}).get("sha", "")).strip().lower()
    try:
        r = _updates.start_update(sha, user["username"])
    except ApiError as exc:
        return JSONResponse({"ok": False, "code": exc.code, "message": str(exc.detail)},
                            status_code=exc.status_code)
    return JSONResponse({"ok": True, **r})


@router.get("/admin/system/update/status")
def admin_system_update_status(request: Request):
    user, denied = _admin_or_403(request)
    if denied:
        return JSONResponse({"state": "unknown"}, status_code=403)
    from app import updates as _updates
    st = _updates.get_update_status()
    st["deployedSha"] = _updates.get_deployed_sha()
    return JSONResponse(st)


# ==================== 机台/物料图纸（拆页 + 缩略图，终端按页渲染）====================
from app.drawings import router as _drawings_router  # noqa: E402

router.include_router(_drawings_router)
