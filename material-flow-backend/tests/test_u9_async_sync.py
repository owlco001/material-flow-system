"""U9 同步页：长耗时同步改后台执行（不再被 nginx 60s 超时掐断）。

覆盖：
- 表单带 background=1 时 POST 立刻 303 返回，不等同步跑完；
- 同步在跑期间页面显示「同步进行中」并带 5 秒自动刷新。
"""
from __future__ import annotations

import os
import re
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

from app import main as backend  # noqa: E402
from app.u9 import config as u9_config  # noqa: E402
from app.u9 import sync as u9_sync  # noqa: E402

PASSWORD = "Async@2026"


def _make_admin() -> None:
    c = backend.db()
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("async-admin", "async-admin", "async-admin", "ADMIN",
         backend.hash_password(PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def _admin_client() -> TestClient:
    _make_admin()
    client = TestClient(backend.app)
    client.get("/admin/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text)
    response = client.post("/admin/login", data={"username": "async-admin", "password": PASSWORD,
                                                 "csrf_token": match.group(1)})
    assert response.status_code in (200, 303), response.text
    return client


def _csrf(client: TestClient) -> str:
    page = client.get("/admin/u9")
    assert page.status_code == 200
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)


def test_form_ships_background_flag():
    client = _admin_client()
    page = client.get("/admin/u9")
    assert 'name="background" value="1"' in page.text


def test_background_post_returns_before_sync_finishes(monkeypatch):
    """同步本体还没跑完，HTTP 就该返回——否则 nginx 60s 超时会让浏览器「点了没反应」。"""
    monkeypatch.setattr(u9_config, "get_config",
                        lambda: SimpleNamespace(ready=True, safe_dict=lambda: {}))

    def slow_run_sync(entity, conn, cfg, **kwargs):
        time.sleep(2.0)  # 模拟真实 Po 全量取数
        return {"run_id": "x", "log_id": "log-x", "entity": entity, "status": "OK", "stats": {}}

    monkeypatch.setattr(u9_sync, "run_sync", slow_run_sync)

    client = _admin_client()
    token = _csrf(client)
    started = time.monotonic()
    response = client.post("/admin/u9/sync/po",
                           data={"csrf_token": token, "dry_run": "1", "since_days": "180",
                                 "background": "1"},
                           follow_redirects=False)
    elapsed = time.monotonic() - started

    assert response.status_code == 303
    assert elapsed < 1.6, f"同步仍在请求内阻塞，耗时 {elapsed:.1f}s"
    message = parse_qs(urlparse(response.headers["location"]).query)["msg"][0]
    assert "后台启动" in message


def test_running_banner_autorefreshes(monkeypatch):
    client = _admin_client()
    token = _csrf(client)  # 先登录拿页面，再注入运行态
    monkeypatch.setattr(u9_sync, "_RUNNING",
                        {"entity": "po", "started_at": "2026-11-25T09:10:28", "dry_run": True})
    page = client.get("/admin/u9")
    assert "同步进行中" in page.text
    assert 'http-equiv="refresh" content="5"' in page.text
    assert "预览，不写库" in page.text
    assert token
