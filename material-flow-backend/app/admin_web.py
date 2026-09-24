"""Web 管理界面（SSR）。契约：docs/admin-web-contract.md。

路由挂载于 /admin/*；鉴权基于独立 web_sessions 表 + mf_session Cookie，
不与 APP 的 Bearer/refresh 会话语义混用。所有 POST 表单必须携带 CSRF token。
"""
from __future__ import annotations

import hmac
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.main import (
    APP_VERSION,
    LOGIN_LOCK_SECONDS,
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    audit,
    check_password,
    db,
    now,
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
            " u.id AS user_id, u.username AS username, u.display_name AS display_name,"
            " u.role AS role"
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
