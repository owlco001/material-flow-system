"""App 更新通道：管理员在管理台上传 APK，Android 端通过公开接口检查并下载新版本。

存储：{DATA_DIR}/app_release/latest.json + 对应 APK 文件（只保留当前一版）。
- GET  /api/app/latest      公开，无发布时 404；App 未登录也能检查更新。
- GET  /api/app/download    公开，下载当前 APK。
- GET  /admin/app-release   ADMIN 页面：查看当前版本、上传新版本。
- POST /admin/app-release   ADMIN 上传（CSRF + 审计）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app import main as _main

router = APIRouter()

MAX_APK_BYTES = 300 * 1024 * 1024
_VERSION_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$")
APK_FILENAME = "smart-factory.apk"


def _release_dir() -> Path:
    return Path(_main.DATA_DIR) / "app_release"


def load_release() -> dict | None:
    meta = _release_dir() / "latest.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (_release_dir() / APK_FILENAME).is_file():
        return None
    return data


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@router.get("/api/app/latest")
def app_latest():
    rel = load_release()
    if rel is None:
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "尚未发布 App 版本"}}, status_code=404)
    return {
        "versionCode": rel["versionCode"],
        "versionName": rel["versionName"],
        "notes": rel.get("notes", ""),
        "force": bool(rel.get("force", False)),
        "sha256": rel["sha256"],
        "size": rel["size"],
        "publishedAt": rel["publishedAt"],
        "downloadUrl": "/api/app/download",
    }


@router.get("/api/app/download")
def app_download():
    rel = load_release()
    if rel is None:
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "尚未发布 App 版本"}}, status_code=404)
    return FileResponse(
        str(_release_dir() / APK_FILENAME),
        media_type="application/vnd.android.package-archive",
        filename=f"smart-factory-{rel['versionName']}.apk",
    )


def _render(request: Request, user, error: str | None = None, notice: str | None = None, status: int = 200):
    from app.admin_web import templates
    return templates.TemplateResponse(
        request,
        "app_release.html",
        {"user": user, "csrf_token": user["csrf_token"], "release": load_release(),
         "error": error, "notice": notice},
        status_code=status,
    )


@router.get("/admin/app-release", response_class=HTMLResponse)
def admin_app_release(request: Request):
    from app.admin_web import _admin_or_403
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    notice = "已发布，App 下次检查更新时会提示" if request.query_params.get("ok") == "1" else None
    return _render(request, user, notice=notice)


@router.post("/admin/app-release")
async def admin_app_release_upload(request: Request):
    from app.admin_web import _admin_or_403, _csrf_ok, _see_other
    user, denied = _admin_or_403(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return HTMLResponse("CSRF 校验失败", status_code=403)

    def bad(msg: str):
        return _render(request, user, error=msg, status=400)

    try:
        version_code = int(str(form.get("version_code", "")).strip())
    except ValueError:
        return bad("versionCode 必须是正整数")
    if version_code <= 0:
        return bad("versionCode 必须是正整数")
    version_name = str(form.get("version_name", "")).strip()
    if not _VERSION_NAME_RE.match(version_name):
        return bad("versionName 格式不正确，如 0.5.35")
    notes = str(form.get("notes", "")).strip()[:2000]
    force = str(form.get("force", "")) == "1"

    current = load_release()
    if current and version_code <= int(current["versionCode"]):
        return bad(f"versionCode 必须大于当前已发布的 {current['versionCode']}，否则 App 不会提示更新")

    upload = form.get("apk")
    if upload is None or not hasattr(upload, "read"):
        return bad("请选择 APK 文件")
    data = await upload.read(MAX_APK_BYTES + 1)
    if len(data) > MAX_APK_BYTES:
        return bad("APK 超过 300 MB 上限")
    if not data.startswith(b"PK\x03\x04"):
        return bad("文件不是有效的 APK（应为 zip 格式）")

    sha = hashlib.sha256(data).hexdigest()
    rel_dir = _release_dir()
    _atomic_write(rel_dir / APK_FILENAME, data)
    meta = {
        "versionCode": version_code,
        "versionName": version_name,
        "notes": notes,
        "force": force,
        "sha256": sha,
        "size": len(data),
        "publishedAt": _main.now(),
        "publishedBy": user["username"],
    }
    _atomic_write(rel_dir / "latest.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    c = _main.db()
    try:
        _main.audit(c, user["id"], user["role"], "APP_RELEASE_PUBLISH", "APP_RELEASE",
                    f"{version_name}({version_code})", "SUCCESS", "web-admin")
        c.commit()
    finally:
        c.close()
    return _see_other("/admin/app-release?ok=1")
