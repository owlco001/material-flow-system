"""从 GitHub / Gitee Release 同步 APK 到 APP 更新通道。"""
from __future__ import annotations

import hashlib
import io
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import main as backend  # 先导入 main（它注册 app_sync 路由）
from app import app_sync, updates
from tests.test_admin_web_nav import seed, web_login

APK = b"PK\x03\x04" + b"x" * 300_000
SHA = hashlib.sha256(APK).hexdigest()


class _Resp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gh_release(code=46, name="0.5.39", with_sha=True, sha=SHA):
    base = f"https://github.com/o/r/releases/download/v{name}/"
    assets = [{"name": f"app-{code}-{name}.apk", "size": len(APK),
               "browser_download_url": base + f"app-{code}-{name}.apk"}]
    if with_sha:
        assets.append({"name": f"app-{code}-{name}.apk.sha256", "size": 90,
                       "browser_download_url": base + f"app-{code}-{name}.apk.sha256",
                       "_body": f"{sha}  app-{code}-{name}.apk\n"})
    return {"tag_name": f"v{name}", "body": "批量勾选操作", "published_at": "2026-10-16T01:00:00Z",
            "assets": assets}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RELEASES_DIR", str(tmp_path / "rel"))
    monkeypatch.setenv("APP_SYNC_GITHUB_PROXY", "https://proxy.test/")
    state = {"github": [_gh_release(), {"tag_name": "v0.5.6", "assets": [{"name": "logistics-debug.apk",
                                                                           "browser_download_url": "https://x/y"}]}],
             "gitee": [], "fail_direct": False, "calls": []}

    def fake_urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        state["calls"].append(url)
        if url.startswith(updates.GITHUB_API + "/releases"):
            return _Resp(json.dumps(state["github"], default=str).encode())
        if url.startswith(updates.GITEE_API + "/releases"):
            if state["gitee"] is None:
                raise OSError("gitee down")
            return _Resp(json.dumps(state["gitee"]).encode())
        if state["fail_direct"] and url.startswith("https://github.com/"):
            raise OSError("timed out")
        target = url.removeprefix("https://proxy.test/")
        for rel in state["github"] + state["gitee"]:
            for a in rel["assets"]:
                if a["browser_download_url"] == target:
                    return _Resp(a["_body"].encode() if "_body" in a else APK)
        raise OSError("404 " + url)

    monkeypatch.setattr(app_sync.urllib.request, "urlopen", fake_urlopen)
    return state


def test_list_remote_filters_named_apks(env):
    r = app_sync.list_remote("github")
    assert [x["versionCode"] for x in r["releases"]] == [46]
    assert r["releases"][0]["sha256Url"].endswith(".sha256") and r["releases"][0]["synced"] is False


def test_sync_downloads_verifies_and_publishes(env):
    res = app_sync.start_sync("github", "", "owlco", wait=True)
    assert res["state"] == "queued"
    st = app_sync.get_status()
    assert st["state"] == "done", st
    latest = updates.get_latest_release()
    assert latest["versionCode"] == 46 and latest["sha256"] == SHA
    assert latest["changelog"] == "批量勾选操作" and "github" in latest["uploadedBy"]
    # 再同步同一版本：直接返回 already，不重复下载
    n = len(env["calls"])
    assert app_sync.start_sync("github", "", "owlco", wait=True)["state"] == "already"
    assert not any("download" in u for u in env["calls"][n:])


def test_sync_falls_back_to_proxy_when_github_direct_fails(env):
    env["fail_direct"] = True
    app_sync.start_sync("github", "v0.5.39", "owlco", wait=True)
    st = app_sync.get_status()
    assert st["state"] == "done" and st.get("via") == "proxy.test"


def test_sync_rejects_sha_mismatch(env):
    env["github"] = [_gh_release(sha="0" * 64)]
    app_sync.start_sync("github", "", "owlco", wait=True)
    st = app_sync.get_status()
    assert st["state"] == "failed" and "SHA-256" in st["error"]
    assert updates.list_releases() == []
    assert not list((updates._releases_dir()).glob("*.part"))


def test_auto_prefers_gitee_and_tolerates_one_source_down(env):
    gitee = _gh_release(with_sha=False)
    for a in gitee["assets"]:
        a["browser_download_url"] = a["browser_download_url"].replace("https://github.com/", "https://gitee.com/")
    env["gitee"] = [gitee]
    r = app_sync.list_remote("auto")
    assert r["releases"][0]["source"] == "gitee"
    env["gitee"] = None
    r = app_sync.list_remote("auto")
    assert r["releases"][0]["source"] == "github" and r["errors"]


def test_busy_lock_and_invalid_input(env):
    import fcntl, os
    updates._releases_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(str(updates._releases_dir() / ".sync.lock"), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(backend.ApiError) as e:
            app_sync.start_sync("github", "", "owlco", wait=True)
        assert e.value.code == "APP_SYNC_BUSY"
    finally:
        os.close(fd)
    with pytest.raises(backend.ApiError):
        app_sync.start_sync("svn", "", "owlco")
    with pytest.raises(backend.ApiError):
        app_sync.start_sync("github", "v1;rm", "owlco")
    with pytest.raises(backend.ApiError) as e:
        app_sync.start_sync("github", "v9.9.9", "owlco")
    assert e.value.code == "APP_SYNC_NOT_FOUND"


def test_admin_page_check_and_sync(env, monkeypatch):
    seed()
    real_start = app_sync.start_sync
    monkeypatch.setattr(app_sync, "start_sync", lambda s, t, a: real_start(s, t, a, wait=True))
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/releases").text
        assert "从 GitHub / Gitee 同步" in page
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        chk = client.post("/admin/releases/sync/check", data={"csrf_token": csrf, "source": "github"})
        assert chk.status_code == 200 and "v0.5.39" in chk.text and 'name="tag" value="v0.5.39"' in chk.text
        r = client.post("/admin/releases/sync", data={"csrf_token": csrf, "source": "github", "tag": "v0.5.39"},
                        follow_redirects=False)
        assert r.status_code == 303 and "sync_started" in r.headers["location"]
        page = client.get("/admin/releases").text
        assert "已同步" in page and "0.5.39" in page
        assert client.get("/api/v1/app/updates/latest").json()["versionCode"] == 46
        bad = client.post("/admin/releases/sync", data={"csrf_token": "x", "source": "github"})
        assert bad.status_code == 403
