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


def test_web_update_apply_uses_session_and_writes_request(tmp_path, monkeypatch):
    """网页「开始更新」走会话路由（旧版 fetch Bearer 接口恒 401），只落请求文件，不提权。"""
    import json as _json
    monkeypatch.setenv("MATERIAL_FLOW_PREFIX", str(tmp_path))
    monkeypatch.setenv("DEPLOYED_SHA_FILE", str(tmp_path / "DEPLOYED_SHA"))
    seed()
    sha = "a" * 40
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/system").text
        assert "/api/v1/admin/system/update/apply" not in page
        csrf = re.search(r'const CSRF = "([^"]+)"', page).group(1)
        bad = client.post("/admin/system/update/apply", json={"sha": sha})
        assert bad.status_code == 403
        r = client.post("/admin/system/update/apply", json={"sha": sha},
                        headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "queued"
        req = _json.loads((tmp_path / "backups" / "update-request").read_text())
        assert req["sha"] == sha and req["actor"] == "owlco"
        again = client.post("/admin/system/update/apply", json={"sha": sha},
                            headers={"X-CSRF-Token": csrf})
        assert again.status_code == 409
        st = client.get("/admin/system/update/status").json()
        assert st["state"] == "queued"
        assert client.post("/admin/system/update/apply", json={"sha": "zz"},
                           headers={"X-CSRF-Token": csrf}).status_code in (400, 409)


def test_stale_update_status_is_reported_failed(tmp_path, monkeypatch):
    import os
    from app import updates
    monkeypatch.setenv("MATERIAL_FLOW_PREFIX", str(tmp_path))
    updates._set_update_status("applying", sha="b" * 40)
    f = tmp_path / "backups" / "update-status.json"
    old = f.stat().st_mtime - 3600
    os.utime(f, (old, old))
    st = updates.get_update_status()
    assert st["state"] == "failed"
    updates.start_update("c" * 40, "owlco")  # 卡死状态不再阻塞新的更新


def test_service_unit_allows_writing_backups_and_releases():
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "material-flow.service").read_text()
    rw = next(l for l in unit.splitlines() if l.startswith("ReadWritePaths="))
    assert "/srv/material-flow/backups" in rw and "/srv/material-flow/app-releases" in rw


def test_agent_stream_endpoint(monkeypatch):
    from app.agent import llm as _llm
    from app.agent.llm import ChatResult as _CR

    def fake_stream(cfg, messages, tools=None, on_delta=None):
        on_delta and on_delta("你好")
        return _CR(content="你好")

    monkeypatch.setattr(_llm, "chat_stream", fake_stream)
    monkeypatch.setenv("AGENT_LLM_API_KEY", "k")
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.post("/admin/agent/chat/stream", json={"message": "hi"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        import json as _j
        evs = [_j.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:")]
        assert [e["type"] for e in evs] == ["session", "delta", "done"]
        assert evs[-1]["reply"] == "你好"


def test_agent_session_feedback_usage_endpoints(monkeypatch):
    from app.agent import llm as _llm
    from app.agent.llm import ChatResult as _CR
    monkeypatch.setattr(_llm, "chat_stream", lambda cfg, messages, tools=None, on_delta=None: _CR(content="好"))
    monkeypatch.setenv("AGENT_LLM_API_KEY", "k")
    seed()
    import json as _j
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.post("/admin/agent/chat/stream", json={"message": "会话管理测试"})
        done = [_j.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:")][-1]
        sid, mid = done["session_id"], done["message_id"]
        assert done["sources"] == []
        assert client.post(f"/admin/agent/sessions/{sid}", json={"title": "改名", "pinned": True}).status_code == 200
        s = client.get("/admin/agent/sessions").json()["sessions"][0]
        assert s["id"] == sid and s["title"] == "改名" and s["pinned"]
        msgs = client.get(f"/admin/agent/sessions/{sid}/messages").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert client.post("/admin/agent/feedback", json={"message_id": mid, "rating": 1}).status_code == 200
        assert client.post("/admin/agent/feedback", json={"message_id": mid, "rating": 5}).status_code == 400
        u = client.get("/admin/agent/usage").json()
        assert u["total_calls"] >= 1 and u["feedback"]["up"] >= 1 and "daily_token_limit" in u
        assert client.post("/admin/agent/settings", json={"daily_token_limit": "50000"}).status_code == 200
        assert client.get("/admin/agent/usage").json()["daily_token_limit"] == 50000
        client.post("/admin/agent/settings", json={"daily_token_limit": "0"})
        assert client.delete(f"/admin/agent/sessions/{sid}").status_code == 200
        assert client.get(f"/admin/agent/sessions/{sid}/messages").status_code == 404
        page = client.get("/admin/agent").text
        assert "用量看板" in page and "session-select" in page
