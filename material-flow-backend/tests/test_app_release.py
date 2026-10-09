"""App 更新通道：管理员上传 APK，公开接口返回最新版本并提供下载。"""
from __future__ import annotations

import io
import re
import shutil

from fastapi.testclient import TestClient

from app import main as backend
from app import app_release
from tests.test_admin_web_nav import seed, web_login

APK = b"PK\x03\x04" + b"fake-apk-body" * 10


def _reset():
    shutil.rmtree(app_release._release_dir(), ignore_errors=True)


def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _upload(client, code, name="0.5.35", body=APK, notes="修复扫码"):
    page = client.get("/admin/app-release")
    return client.post(
        "/admin/app-release",
        data={"csrf_token": _csrf(page.text), "version_code": str(code), "version_name": name, "notes": notes},
        files={"apk": ("app.apk", io.BytesIO(body), "application/vnd.android.package-archive")},
        follow_redirects=False,
    )


def test_latest_404_when_nothing_published():
    _reset()
    with TestClient(backend.app) as client:
        assert client.get("/api/app/latest").status_code == 404
        assert client.get("/api/app/download").status_code == 404


def test_admin_publish_then_public_latest_and_download():
    _reset()
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        assert _upload(client, 42).status_code == 303
        client.cookies.clear()
        r = client.get("/api/app/latest")
        assert r.status_code == 200
        j = r.json()
        assert j["versionCode"] == 42 and j["versionName"] == "0.5.35"
        assert j["notes"] == "修复扫码" and j["size"] == len(APK)
        assert j["downloadUrl"] == "/api/app/download"
        d = client.get("/api/app/download")
        assert d.status_code == 200 and d.content == APK
        assert d.headers["content-type"] == "application/vnd.android.package-archive"


def test_publish_rejects_non_increasing_code_and_bad_file():
    _reset()
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        assert _upload(client, 42).status_code == 303
        r = _upload(client, 42)
        assert r.status_code == 400 and "必须大于" in r.text
        r = _upload(client, 43, body=b"not a zip")
        assert r.status_code == 400 and "不是有效的 APK" in r.text
        r = _upload(client, 44, name="bad name!")
        assert r.status_code == 400
        assert client.get("/api/app/latest").json()["versionCode"] == 42


def test_non_admin_cannot_publish():
    _reset()
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/app-release").status_code == 403
