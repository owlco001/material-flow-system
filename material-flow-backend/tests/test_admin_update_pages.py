"""回归：管理台「APP更新」「系统更新」页面路由存在（曾被整文件覆盖误删，导航 404）。"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app import main as backend
from tests.test_admin_web_nav import seed, web_login


def test_release_and_system_pages_render_for_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RELEASES_DIR", str(tmp_path / "rel"))
    monkeypatch.setenv("DEPLOYED_SHA_FILE", str(tmp_path / "DEPLOYED_SHA"))
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/releases")
        assert r.status_code == 200
        assert 'action="/admin/releases/upload"' in r.text
        s = client.get("/admin/system")
        assert s.status_code == 200
        assert 'action="/admin/system/update/check"' in s.text


def test_every_sidebar_link_resolves():
    """导航里的每个 /admin/* 链接都必须有路由（防止菜单在、路由丢）。"""
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        html = client.get("/admin/workspace").text
        hrefs = sorted(set(re.findall(r'class="side-link[^"]*"[^>]*href="(/admin/[^"?#]*)"', html)))
        assert hrefs, "未解析到侧边栏链接"
        missing = [h for h in hrefs if client.get(h, follow_redirects=False).status_code == 404]
        assert not missing, f"侧边栏链接无路由：{missing}"
