"""v0.6.3 管理台 P2 整改回归。

- 修复：/admin/agent/import/bom/preview 非法文件不再 500（ApiError 无 .message），返回 400 + 中文文案。
- P2-9  公共 CSS/JS 抽到 /static（内容哈希 + immutable 长缓存）；文本响应 gzip，SSE / 二进制透传，CSV 仍流式。
- P2-10 /admin/ 仍重定向到异常处理页，死代码与 home.html 已删除。
- P2-11 /admin 页面 403/404/500 渲染带 traceId 的样式化错误页；智能助手接口不再回传 str(e)。
- P2-12 共享分页 / 提示宏；物料主档 ?notice= 只认代码，不回显原文。
- P2-13 管理台 GET 页面每请求一个 SQLite 连接；WAL 只在启动时确认；_api_endpoint 缓存。
- P2-14 会话滑动续期 + 过期会话清理。
- 用户页行内操作改为一个共享弹窗。
"""
from __future__ import annotations

import gzip
import io
import re
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from app import main as backend
from app import admin_web
from app.web_assets import SelectiveGZipMiddleware, asset_hash, static_url

from tests.test_admin_perf_p0_p1 import login, seed_users

APP_DIR = Path(backend.__file__).resolve().parent
TEMPLATES = APP_DIR / "templates"
STATIC = APP_DIR / "static"


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_users()
    yield


def static_urls(html: str) -> list[str]:
    return re.findall(r'(?:href|src)="(/static/[^"]+)"', html)


# ---------------------------------------------------------------- 修复：bom/preview 非法文件


@pytest.mark.parametrize("path,data", [
    ("/admin/agent/import/bom/preview", {"model_code": "M-1"}),
    ("/admin/agent/import/aggbom/preview", {}),
])
def test_import_preview_invalid_file_is_clean_400(path, data):
    with TestClient(backend.app) as client:  # raise_server_exceptions=True：不允许任何 500
        token = login(client, "owlco")
        r = client.post(path, data=data, files={"file": ("a.xlsx", b"not a zip", "application/octet-stream")},
                        headers={"X-CSRF-Token": token})
        assert r.status_code == 400, r.text
        err = r.json()["error"]
        assert err["message"] == "XLSX 文件无法解析"
        assert err["traceId"]


def test_import_commit_missing_preview_is_clean_error_not_500():
    with TestClient(backend.app) as client:
        token = login(client, "owlco")
        r = client.post("/admin/agent/import/bom/commit", json={"preview_id": "00000000-0000-4000-8000-000000000000"},
                        headers={"X-CSRF-Token": token})
        assert 400 <= r.status_code < 500, r.text
        assert r.json()["error"]["message"]
        r = client.post("/admin/agent/import/aggbom/commit", json={"preview_id": "nope"},
                        headers={"X-CSRF-Token": token})
        assert 400 <= r.status_code < 500, r.text


def test_no_apierror_message_attribute_left():
    src = (APP_DIR / "admin_web.py").read_text(encoding="utf-8")
    assert "e.message" not in src
    assert 'str(e)[:500]}}, status_code=500' not in src


# ---------------------------------------------------------------- P2-11 智能助手通用错误


def test_agent_unexpected_error_returns_generic_message_and_trace_id(monkeypatch, caplog):
    def boom(**_kw):
        raise RuntimeError("sqlite3 at /srv/secret/path.db exploded")

    monkeypatch.setattr(admin_web, "api_bom_commit", boom)
    with TestClient(backend.app) as client:
        token = login(client, "owlco")
        with caplog.at_level("ERROR", logger="material_flow.admin"):
            r = client.post("/admin/agent/import/bom/commit", json={"preview_id": "p"},
                            headers={"X-CSRF-Token": token})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "BOM_ERROR" and err["message"] == admin_web.AGENT_GENERIC_MESSAGE
    assert re.fullmatch(r"[0-9a-f-]{36}", err["traceId"])
    assert "secret" not in r.text
    assert any(err["traceId"] in rec.getMessage() for rec in caplog.records), "完整异常须写服务端日志并带 traceId"


def test_agent_llm_error_message_drops_upstream_snippet():
    from app.agent.llm import LLMError
    assert admin_web._agent_llm_public_message(LLMError("LLM 返回 HTTP 401：invalid key sk-xxx")) == "LLM 返回 HTTP 401"
    assert admin_web._agent_llm_public_message(LLMError("LLM 请求失败: [Errno -2] host x.internal")) == "LLM 请求失败"
    assert admin_web._agent_llm_public_message(LLMError("AGENT_LLM_API_KEY 未配置")) == "AGENT_LLM_API_KEY 未配置"


def test_agent_stream_error_event_is_generic(monkeypatch):
    def run_chat(*_a, **_kw):
        raise RuntimeError("internal detail /etc/passwd")

    monkeypatch.setattr(admin_web.agent_service, "run_chat", run_chat)
    with TestClient(backend.app) as client:
        token = login(client, "owlco")
        r = client.post("/admin/agent/chat/stream", json={"message": "hi"},
                        headers={"X-CSRF-Token": token, "Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "internal detail" not in r.text
    assert admin_web.AGENT_GENERIC_MESSAGE in r.text and "traceId" in r.text


# ---------------------------------------------------------------- P2-11 HTML 错误页


def test_forbidden_admin_page_renders_styled_error_with_trace_id():
    with TestClient(backend.app) as client:
        login(client, "wh-admin")
        r = client.get("/admin/users")
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("text/html")
    assert 'class="error-code">403<' in r.text and "禁止访问" in r.text
    assert re.search(r"traceId）：<code>[0-9a-f-]{36}</code>", r.text)
    assert "admin/base.css" in r.text  # 继承 base.html 样式
    assert 'class="side-link' in r.text  # 已登录：保留侧边栏


def test_unknown_admin_path_html_vs_json():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        html = client.get("/admin/no-such-page", headers={"Accept": "text/html,application/xhtml+xml"})
        assert html.status_code == 404 and "页面不存在" in html.text and "traceId" in html.text
        api = client.get("/admin/no-such-page")
        assert api.status_code == 404 and api.json()["error"]["traceId"]
        agent = client.get("/admin/agent/no-such", headers={"Accept": "text/html"})
        assert agent.headers["content-type"].startswith("application/json")
        assert client.get("/api/v1/no-such", headers={"Accept": "text/html"}).headers["content-type"].startswith(
            "application/json")


def test_unhandled_admin_page_error_renders_html_500(monkeypatch, caplog):
    def broken(*_a, **_kw):
        raise RuntimeError("db secret leaked?")

    monkeypatch.setattr(admin_web, "_render_users", broken)
    with TestClient(backend.app, raise_server_exceptions=False) as client:
        login(client, "owlco")
        with caplog.at_level("ERROR", logger="material_flow.errors"):
            r = client.get("/admin/users", headers={"Accept": "text/html"})
    assert r.status_code == 500
    assert "服务器内部错误" in r.text and "db secret" not in r.text
    trace = re.search(r"<code>([0-9a-f-]{36})</code>", r.text).group(1)
    assert any(trace in rec.getMessage() for rec in caplog.records)


def test_csrf_failure_page_is_html():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        r = client.post("/admin/users/u_op/delete", data={"csrf_token": "bad"})
    assert r.status_code == 403 and "CSRF" in r.text and "刷新页面" in r.text and "error-code" in r.text


def test_no_plain_text_error_responses_left_in_admin_modules():
    for name in ("admin_web.py", "drawings.py", "material_master_routes.py"):
        src = (APP_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r'HTMLResponse\("(?:403|404|CSRF)', src), name


# ---------------------------------------------------------------- P2-10 死代码


def test_admin_root_still_redirects_and_home_removed():
    assert not (TEMPLATES / "home.html").exists()
    src = (APP_DIR / "admin_web.py").read_text(encoding="utf-8")
    body = src[src.index("def admin_home"):src.index("# ==================== S2：用户管理")]
    assert body.count("\n") < 12, "admin_home 只保留登录检查 + 重定向"
    with TestClient(backend.app) as client:
        assert client.get("/admin/", follow_redirects=False).headers["location"] == "/admin/login"
        login(client, "owlco")
        r = client.get("/admin/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/exceptions"


# ---------------------------------------------------------------- P2-9 静态资源 + gzip


def test_base_and_agent_templates_have_no_inline_scripts_or_big_styles():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "<script>" not in base and "<style>" not in base
    assert '<meta name="csrf-token" content="{{ user[\'csrf_token\'] }}">' in base  # csrf meta 仍内联
    agent = (TEMPLATES / "agent.html").read_text(encoding="utf-8")
    assert "<script>" not in agent
    assert len(base) < 16000


def test_static_assets_long_cache_and_gzip():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        page = client.get("/admin/agent", headers={"Accept-Encoding": "gzip"})
        assert page.status_code == 200
        assert page.headers.get("content-encoding") == "gzip"
        urls = static_urls(page.text)
        assert {u.split("?")[0] for u in urls} == {
            "/static/admin/base.css", "/static/admin/base-head.js", "/static/admin/base-body.js",
            "/static/admin/agent.js"}
        for url in urls:
            r = client.get(url, headers={"Accept-Encoding": "gzip"})
            assert r.status_code == 200, url
            assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert r.headers.get("content-encoding") == "gzip", url
            rel = url.split("?")[0][len("/static/"):]
            assert r.content == (STATIC / rel).read_bytes()
        bare = client.get("/static/admin/base.css")
        assert bare.headers["cache-control"] == "no-cache"
        stale = client.get("/static/admin/base.css?v=000000000000")
        assert stale.headers["cache-control"] == "no-cache"
        assert client.get("/static/admin/nope.js").status_code == 404
        assert client.get("/static/../main.py").status_code == 404


def test_static_url_uses_content_hash():
    rel = "admin/base.css"
    assert static_url(rel) == f"/static/{rel}?v={asset_hash(rel)}"
    assert len(asset_hash(rel)) == 12


def test_login_page_gzip_and_small_responses_untouched():
    with TestClient(backend.app) as client:
        r = client.get("/admin/login", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
        assert "accept-encoding" in r.headers.get("vary", "").lower()


def test_csv_export_still_streams_and_matches_uncompressed():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        plain = client.get("/admin/audit/export", headers={"Accept-Encoding": "identity"})
        gz = client.get("/admin/audit/export", headers={"Accept-Encoding": "gzip"})
    assert plain.status_code == gz.status_code == 200
    assert plain.content == gz.content  # httpx 自动解压
    assert plain.content.startswith("\ufeff".encode("utf-8"))


def _mini_app():
    big = b"\x89PNG" + bytes(range(256)) * 40

    async def tiny(_request):
        return Response('{"ok": true}', media_type="application/json")

    async def png(_request):
        return Response(big, media_type="image/png")

    async def apk(_request):
        return Response(big, media_type="application/vnd.android.package-archive")

    async def sse(_request):
        async def gen():
            for i in range(3):
                yield f"data: {i}" + " " * 600 + "\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    async def csv_stream(_request):
        async def gen():
            for i in range(50):
                yield (f"{i},{'x' * 100}\n").encode()
        return StreamingResponse(gen(), media_type="text/csv; charset=utf-8")

    app = Starlette(routes=[Route("/tiny", tiny), Route("/png", png), Route("/apk", apk), Route("/sse", sse), Route("/csv", csv_stream)])
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)
    return app, big


def test_selective_gzip_skips_binary_and_event_stream():
    app, big = _mini_app()
    sent: list[dict] = []

    with TestClient(app) as client:
        for path, ctype in (("/png", "image/png"), ("/apk", "application/vnd.android.package-archive")):
            r = client.get(path, headers={"Accept-Encoding": "gzip"})
            assert r.headers.get("content-encoding") is None, path
            assert r.headers["content-length"] == str(len(big))
            assert r.content == big
        r = client.get("/sse", headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") is None
        assert r.text.count("data: ") == 3
        r = client.get("/csv", headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") == "gzip" and "content-length" not in r.headers
        assert r.text.splitlines()[49].startswith("49,")
        assert client.get("/tiny", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") is None
        r = client.get("/png", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
        assert r.headers.get("content-encoding") is None

    # SSE 逐条下发：每个事件对应一个独立 body 消息（未被压缩缓冲合并）
    import anyio

    async def run():
        scope = {"type": "http", "method": "GET", "path": "/sse", "raw_path": b"/sse", "query_string": b"",
                 "headers": [(b"accept-encoding", b"gzip")], "http_version": "1.1", "scheme": "http",
                 "server": ("t", 80), "client": ("c", 1), "root_path": ""}

        async def receive():
            await anyio.sleep(1)
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)

    anyio.run(run)
    bodies = [m for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert len(bodies) == 3 and all(b["body"].startswith(b"data: ") for b in bodies)


# ---------------------------------------------------------------- P2-12 宏 / notice


def test_pagination_macro_urlencodes_and_supports_simple_mode():
    mod = admin_web.templates.env.get_template("_pagination.html").module
    html = str(mod.pager(2, 5, 100, "/admin/x", {"q": "a b&c", "empty": ""}))
    assert 'href="/admin/x?q=a%20b%26c&amp;page=1"' in html and "empty=" not in html
    assert "共 5 页 / 100 条" in html
    html = str(mod.pager(1, 3, None, "", {"order_no": "O1"}, "mat_page", partial=True))
    assert 'href="?order_no=O1&amp;mat_page=2" data-partial-link' in html and "共 3 页<" in html
    html = str(mod.simple_pager(1, True, "/admin/audit", {"from": "2026-01-01"}))
    assert "第 1 页" in html and 'href="/admin/audit?from=2026-01-01&amp;page=2"' in html
    assert str(mod.simple_pager(1, False, "/admin/audit")).strip() == ""


def test_templates_use_shared_macros():
    hand_written = []
    for path in TEMPLATES.glob("*.html"):
        if path.name == "_pagination.html":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r">上一页</a>", text):
            hand_written.append(path.name)
        if re.search(r'\{% if notice_text %\}<p class="notice">', text):
            hand_written.append(path.name + ":notice")
    assert hand_written == []


def test_material_master_notice_codes_do_not_echo_raw_text():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        r = client.get("/admin/material-master", params={"notice": "系统将于今晚停机，请转账到 6222..."})
        assert r.status_code == 200 and "转账" not in r.text
        r = client.get("/admin/material-master?notice=imported&mi=3&mu=2&mn=1&mc=4&ms=5")
        assert "已导入：主档新增 3、更新 2、未变化 1；物料新建 4、同步 5" in r.text
        r = client.get("/admin/material-master?notice=imported&mi=<b>x</b>")
        assert "主档新增 0" in r.text and "<b>x</b>" not in r.text
        r = client.get("/admin/material-master?notice=no_file")
        assert '<p class="error" role="alert">请选择 .xlsx 文件</p>' in r.text


def test_material_master_upload_errors_redirect_with_codes():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        page = client.get("/admin/material-master")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        r = client.post("/admin/material-master/import", data={"csrf_token": token},
                        files={"file": ("a.csv", b"x", "text/csv")}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/material-master?notice=bad_type"
        r = client.post("/admin/material-master/import", data={"csrf_token": token},
                        files={"file": ("a.xlsx", b"not a zip", "application/octet-stream")}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/material-master?notice=tpl_parse"


def test_release_and_system_notices_are_mapped():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        r = client.get("/admin/releases?notice=RELEASE_EXISTS")
        assert "该 versionCode 已存在" in r.text
        r = client.get("/admin/releases", params={"notice": "伪造的提示"})
        assert "伪造的提示" not in r.text
        r = client.get("/admin/system", params={"notice": "伪造的提示"})
        assert "伪造的提示" not in r.text and "操作失败" in r.text


# ---------------------------------------------------------------- P2-13 连接 / WAL / 路由缓存


def _count_connections(monkeypatch):
    real_connect = sqlite3.connect
    stats = {"connects": 0, "statements": []}

    def counting_connect(*args, **kwargs):
        stats["connects"] += 1
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(stats["statements"].append)
        return conn

    monkeypatch.setattr(backend.sqlite3, "connect", counting_connect)
    return stats


@pytest.mark.parametrize("path", ["/admin/users", "/admin/exceptions", "/admin/reports", "/admin/tasks",
                                  "/admin/flows", "/admin/orders", "/admin/warehouse", "/admin/audit"])
def test_admin_get_page_uses_one_connection(monkeypatch, path):
    with TestClient(backend.app) as client:
        login(client, "owlco")
        client.get("/admin/users")  # 预热：WAL 已确认
        stats = _count_connections(monkeypatch)
        r = client.get(path)
        assert r.status_code == 200
    assert stats["connects"] == 1, (path, stats["connects"])
    assert not [s for s in stats["statements"] if "journal_mode" in s.lower()]
    assert not [s for s in stats["statements"] if "busy_timeout" in s.lower()]
    assert any("foreign_keys" in s.lower() for s in stats["statements"])


def test_shared_connection_close_rolls_back_uncommitted_work():
    holder = backend._RequestConnectionHolder()
    token = backend._REQUEST_CONN.set(holder)
    try:
        c1 = backend.db()
        c1.execute("UPDATE users SET display_name='tmp' WHERE id='u_op'")
        c1.close()  # 未 commit：与真正 close 一样丢弃
        c2 = backend.db()
        assert c2.execute("SELECT display_name FROM users WHERE id='u_op'").fetchone()[0] == "operator1"
        assert holder.opened == 1
        with backend.db() as c3:
            c3.execute("UPDATE users SET display_name='kept' WHERE id='u_op'")
    finally:
        backend._REQUEST_CONN.reset(token)
        holder.close()
    c = backend.db()
    try:
        assert c.execute("SELECT display_name FROM users WHERE id='u_op'").fetchone()[0] == "kept"
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        c.close()


def test_post_and_export_do_not_share_connection():
    assert backend._shares_request_conn({"method": "GET", "path": "/admin/users"})
    assert not backend._shares_request_conn({"method": "POST", "path": "/admin/users/new"})
    assert not backend._shares_request_conn({"method": "GET", "path": "/admin/audit/export"})
    assert not backend._shares_request_conn({"method": "GET", "path": "/admin/agent/sessions"})
    assert not backend._shares_request_conn({"method": "GET", "path": "/api/v1/materials"})


def test_api_endpoint_lookup_is_cached():
    admin_web._api_endpoint.cache_clear()
    first = admin_web._api_endpoint("/api/v1/workshop/summary")
    second = admin_web._api_endpoint("/api/v1/workshop/summary")
    assert first is second
    assert admin_web._api_endpoint.cache_info().hits >= 1


# ---------------------------------------------------------------- P2-14 会话


def _session_row(user_id="u_admin"):
    c = backend.db()
    try:
        return c.execute("SELECT id, expires_at, last_seen_at FROM web_sessions WHERE user_id=?", (user_id,)).fetchone()
    finally:
        c.close()


def _set_expiry(session_id: str, expires_at: int):
    c = backend.db()
    c.execute("UPDATE web_sessions SET expires_at=? WHERE id=?", (expires_at, session_id))
    c.commit()
    c.close()


def test_session_slides_when_more_than_half_elapsed():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        row = _session_row()
        fresh = client.get("/admin/users")
        assert "mf_session=" not in fresh.headers.get("set-cookie", "")  # 刚登录不续期
        assert _session_row()["expires_at"] == row["expires_at"]
        _set_expiry(row["id"], int(time.time()) + 3600)  # 只剩 1 小时
        r = client.get("/admin/users")
        assert r.status_code == 200
        cookie = r.headers.get("set-cookie", "")
        assert f"mf_session={row['id']}" in cookie and f"Max-Age={admin_web.SESSION_SECONDS}" in cookie
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("SameSite=Lax", "SameSite=lax")
        new = _session_row()
        assert new["expires_at"] >= int(time.time()) + admin_web.SESSION_SECONDS - 5
        assert new["last_seen_at"] >= int(time.time()) - 5
        again = client.get("/admin/users")
        assert "mf_session=" not in again.headers.get("set-cookie", "")


def test_expired_session_is_rejected_and_purged_on_login():
    c = backend.db()
    c.execute("INSERT INTO web_sessions(id,user_id,csrf_token,created_at,expires_at,last_seen_at)"
              " VALUES('old-session','u_plan','x',?,?,?)", (backend.now(), int(time.time()) - 10, 0))
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        client.cookies.set("mf_session", "old-session")
        assert client.get("/admin/users", follow_redirects=False).headers["location"] == "/admin/login"
        client.cookies.clear()
        login(client, "owlco")
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM web_sessions WHERE id='old-session'").fetchone()[0] == 0
    finally:
        c.close()


def test_startup_purges_expired_sessions():
    c = backend.db()
    c.execute("INSERT INTO web_sessions(id,user_id,csrf_token,created_at,expires_at,last_seen_at)"
              " VALUES('stale','u_plan','x',?,?,?)", (backend.now(), int(time.time()) - 10, 0))
    c.commit()
    c.close()
    with TestClient(backend.app):
        pass
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM web_sessions WHERE id='stale'").fetchone()[0] == 0
    finally:
        c.close()


# ---------------------------------------------------------------- 用户页共享弹窗


def test_users_page_uses_one_shared_action_modal():
    c = backend.db()
    for i in range(30):
        c.execute("INSERT INTO users(id,username,display_name,role,password_hash,must_change_password,active,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?)", (f"u_bulk{i}", f"bulk{i}", f"批量{i}", "OPERATOR", "x", 0, i % 2, backend.now()))
    c.commit()
    c.close()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        r = client.get("/admin/users")
    html = r.text
    assert html.count('id="userActionForm"') == 1
    assert not re.search(r'<form[^>]+action="/admin/users/[^"]+/(password-reset|delete|hard-delete|revoke-sessions)"', html)
    rows = html.count('data-user-action="reset"')
    assert rows == min(37, admin_web.USERS_PAGE_SIZE)  # 7 个种子用户 + 30 个批量用户
    assert html.count('data-user-action="revoke"') == rows
    assert html.count('data-user-action="delete"') + html.count('data-user-action="hard-delete"') == rows
    assert html.count('name="newPassword"') == 1
    assert len(html.encode()) < 60_000  # 旧版每行约 2 KB 表单（201 用户 412 KB）


def test_users_modal_post_flow_still_works():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        page = client.get("/admin/users")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        op = re.search(r'id="userActionOp" value="([^"]+)"', page.text).group(1)
        r = client.post("/admin/users/u_op/password-reset",
                        data={"csrf_token": token, "clientOperationId": op, "newPassword": "Reset@2026x"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/users?notice=reset"
        r = client.post("/admin/users/u_op/revoke-sessions", data={"csrf_token": token}, follow_redirects=False)
        assert r.status_code == 303


# ---------------------------------------------------------------- 全部管理页 200 + 静态资源


SKIP_GET = {"/admin/logout"}
# 需要查询参数的 JSON / 资源接口：不带参数时应返回 4xx
NEEDS_PARAMS = {"/admin/agent/aggbom/aggregate", "/admin/agent/bom/aggregate", "/admin/barcodes/image",
                "/admin/barcodes/print", "/admin/models/content", "/admin/models/view"}


def _admin_get_paths():
    paths = []
    for route in backend.app.routes:
        path = getattr(route, "path", "")
        if (path.startswith("/admin") and "{" not in path and "GET" in getattr(route, "methods", set())
                and path not in SKIP_GET):
            paths.append(path)
    return sorted(set(paths))


def test_every_admin_get_page_returns_200_and_assets_load():
    paths = _admin_get_paths()
    assert len(paths) > 25
    bad = []
    asset_urls: set[str] = set()
    with TestClient(backend.app) as client:
        login(client, "owlco")
        for path in paths:
            r = client.get(path, headers={"Accept": "text/html", "Accept-Encoding": "gzip"}, follow_redirects=False)
            if path in ("/admin/", "/admin/login"):
                ok = r.status_code == 303
            elif path == "/admin/agent/usage":
                ok = r.status_code in (200, 503)  # 未配置 LLM 时 503（既有行为）
            elif path in NEEDS_PARAMS:
                ok = 400 <= r.status_code < 500  # 缺参数：干净的 4xx，不是 500
            else:
                ok = r.status_code == 200
            if not ok:
                bad.append((path, r.status_code))
            if r.headers.get("content-type", "").startswith("text/html"):
                asset_urls.update(static_urls(r.text))
        assert asset_urls, "页面应引用 /static 资源"
        for url in sorted(asset_urls):
            a = client.get(url)
            if a.status_code != 200:
                bad.append((url, a.status_code, ""))
    assert bad == []


# ---------------------------------------------------------------- POST 失败分支不再拼未转义 HTML


XSS = "<script>alert('x')</script>"


@pytest.mark.parametrize("username,attr,path,data,back", [
    ("planner1", "api_set_order_status", "/admin/orders/PO-1/status", {"status": "IN_PROGRESS"}, "/admin/orders"),
    ("owlco", "api_revoke_sessions", "/admin/users/u_op/revoke-sessions", {}, "/admin/users"),
    ("supervisor1", "api_create_task", "/admin/tasks/create",
     {"orderNo": "PO-1", "deviceId": "d1", "deviceNo": "MC-1"}, "/admin/tasks"),
])
@pytest.mark.parametrize("exc_factory,status", [
    (lambda: backend.ApiError(409, "CONFLICT", XSS), 409),
    (lambda: admin_web.HTTPException(status_code=404, detail=XSS), 404),
])
def test_post_failure_branches_render_escaped_error_page(monkeypatch, username, attr, path, data, back,
                                                         exc_factory, status):
    def boom(*a, **k):
        raise exc_factory()
    monkeypatch.setattr(admin_web, attr, boom)
    with TestClient(backend.app) as client:
        token = login(client, username)
        r = client.post(path, data={"csrf_token": token, **data}, follow_redirects=False)
        assert r.status_code == status
        assert XSS not in r.text
        assert "&lt;script&gt;" in r.text
        assert "操作失败" in r.text and f'href="{back}"' in r.text
        assert "error-page" in r.text  # 继承 base.html 的样式化错误页


def test_action_failed_validation_error_is_422_and_offsite_back_url_dropped():
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        x: int
    try:
        M(x="nope")
    except ValidationError as e:
        verr = e
    with TestClient(backend.app) as client:
        login(client, "owlco")
        captured = {}

        @backend.app.get("/__t_action_failed")
        def _t(request: admin_web.Request):
            captured["r"] = admin_web._action_failed(request, verr, "https://evil.example/", "x")
            return captured["r"]
        try:
            r = client.get("/__t_action_failed")
        finally:
            backend.app.router.routes[:] = [rt for rt in backend.app.router.routes
                                            if getattr(rt, "path", "") != "/__t_action_failed"]
        assert r.status_code == 422
        assert "evil.example" not in r.text


# ---------------------------------------------------------------- 图纸页 ?notice= 只认代码


def test_drawings_notice_is_code_only():
    with TestClient(backend.app) as client:
        login(client, "owlco")
        forged = client.get("/admin/drawings", params={"notice": "系统已被接管，请联系 evil@example.com"})
        assert forged.status_code == 200 and "系统已被接管" not in forged.text
        ok = client.get("/admin/drawings?notice=deleted")
        assert "图纸已删除" in ok.text and 'class="notice"' in ok.text
        err = client.get("/admin/drawings?notice=bad_pdf")
        assert "文件不是有效的 PDF" in err.text and 'class="error"' in err.text
        up = client.get("/admin/drawings", params={"notice": "uploaded", "n": "3", "miss": f"MAT-9,{XSS},a b"})
        assert "已上传，共 3 页；未找到：MAT-9" in up.text
        assert XSS not in up.text and "alert('x')" not in up.text and "&lt;script" not in up.text
        bad_n = client.get("/admin/drawings", params={"notice": "bound", "n": "9" * 30})
        assert "已绑定 0 项" in bad_n.text


def test_drawings_redirect_helpers():
    from app import drawings
    with pytest.raises(ValueError):
        drawings._redirect("任意文本")
    loc = drawings._redirect("uploaded", "dw_1", n=2, missing=["X-1", "<b>", "Y/2"]).headers["location"]
    assert loc == "/admin/drawings?notice=uploaded&n=2&miss=X-1%2CY/2#dw_1"
    assert drawings._upload_error_notice("页数 900 超过上限 500") == ("too_many_pages", 900)
    assert drawings._upload_error_notice("文件不是有效的 PDF") == ("bad_pdf", None)
    assert drawings._upload_error_notice("无法打开 PDF（可能已加密或损坏）：xyz") == ("pdf_unreadable", None)
    assert drawings._upload_error_notice("???") == ("upload_failed", None)
    for code in drawings.DRAWING_NOTICES:  # 每个代码都能渲染
        assert drawings.drawing_notice_ctx({"notice": code, "n": "1"}) != {"notice_text": None, "notice_error": None}
