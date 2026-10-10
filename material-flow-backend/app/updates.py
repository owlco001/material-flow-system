"""APP 更新通道与后端自更新通道。

APP 更新通道：
    APK 存放在 app-releases/（默认 /srv/material-flow/app-releases，
    APP_RELEASES_DIR 环境变量可覆盖，便于测试）。
    文件命名：app-{versionCode}-{versionName}.apk
    同名 .json 为元信息：versionCode/versionName/changelog/fileSize/
    sha256/uploadedAt/uploadedBy。
    公开接口（无需登录，APP 用自己配置的后端地址调用）：
        GET /api/v1/app/updates/latest    最新版本信息；无发布时 404
        GET /api/v1/app/updates/download  下载最新 APK；无发布时 404
    downloadUrl 为相对路径，APP 以「设置的后端地址」为基地址拼接，
    后端地址设在哪，更新源就在哪。

后端自更新通道（GitHub，手动触发）：
    DEPLOYED_SHA 文件（默认 /srv/material-flow/DEPLOYED_SHA）记录当前部署的 commit。
    管理接口（仅 ADMIN）：
        GET  /api/v1/admin/system/version        当前版本与 SHA
        POST /api/v1/admin/system/update/check   查询 GitHub main 最新 commit
        POST /api/v1/admin/system/update/apply   下载并应用更新（body: {sha})
    apply 只写 backups/update-request；systemd 的 material-flow-update.path 监听到后
    以 root 运行 deploy/update.sh --from-request（下载、备份库与代码、替换、迁移、
    重启、健康检查失败自动回滚）。命令行一键更新：sudo bash deploy/update.sh
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

try:
    from app.main import ApiError, CODE_FORBIDDEN, APP_VERSION, current_user
except ImportError:  # 直接以 app 目录为 cwd 运行时的兜底
    from main import ApiError, CODE_FORBIDDEN, APP_VERSION, current_user  # type: ignore

router = APIRouter()

# --------------------------------------------------------------------------
# 公共基础
# --------------------------------------------------------------------------

GITHUB_REPO = os.environ.get("UPDATE_GITHUB_REPO", "owlco001/material-flow-system")
GITHUB_BRANCH = os.environ.get("UPDATE_GITHUB_BRANCH", "main")
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_APK_RE = re.compile(r"^app-(\d+)-([A-Za-z0-9._-]+)\.apk$")
_VERSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_APK_MAGIC = b"PK\x03\x04"
_APK_MAX_BYTES = 300 * 1024 * 1024


def _prefix() -> Path:
    return Path(os.environ.get("MATERIAL_FLOW_PREFIX", "/srv/material-flow"))


def _releases_dir() -> Path:
    return Path(os.environ.get("APP_RELEASES_DIR", str(_prefix() / "app-releases")))


def _deployed_sha_file() -> Path:
    return Path(os.environ.get("DEPLOYED_SHA_FILE", str(_prefix() / "DEPLOYED_SHA")))


def _backups_dir() -> Path:
    return _prefix() / "backups"


def _admin(user: sqlite3.Row) -> None:
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可操作更新通道")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


# --------------------------------------------------------------------------
# APP 更新通道：APK 托管
# --------------------------------------------------------------------------

def _parse_release(apk_path: Path) -> dict | None:
    m = _APK_RE.fullmatch(apk_path.name)
    if not m:
        return None
    version_code = int(m.group(1))
    version_name = m.group(2)
    meta_path = apk_path.with_suffix(".json")
    meta: dict = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    try:
        size = apk_path.stat().st_size
    except OSError:
        return None
    return {
        "versionCode": version_code,
        "versionName": version_name,
        "fileName": apk_path.name,
        "fileSize": size,
        "sha256": meta.get("sha256", ""),
        "changelog": meta.get("changelog", ""),
        "uploadedAt": meta.get("uploadedAt", ""),
        "uploadedBy": meta.get("uploadedBy", ""),
    }


def list_releases() -> list[dict]:
    d = _releases_dir()
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("app-*.apk"):
        r = _parse_release(p)
        if r:
            out.append(r)
    out.sort(key=lambda r: r["versionCode"], reverse=True)
    return out


def get_latest_release() -> dict | None:
    rs = list_releases()
    return rs[0] if rs else None


def save_apk_upload(data: bytes, original_name: str, version_code: int,
                    version_name: str, changelog: str, uploaded_by: str) -> dict:
    if version_code <= 0:
        raise ApiError(400, "INVALID_VERSION_CODE", "versionCode 必须为正整数")
    if not _VERSION_NAME_RE.fullmatch(version_name):
        raise ApiError(400, "INVALID_VERSION_NAME", "versionName 仅允许字母数字 . _ -（最长32）")
    if len(data) < 4 or data[:4] != _APK_MAGIC:
        raise ApiError(400, "INVALID_APK", "文件不是有效的 APK（ZIP 格式校验失败）")
    if len(data) > _APK_MAX_BYTES:
        raise ApiError(400, "APK_TOO_LARGE", "APK 超过 300MB 上限")
    d = _releases_dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"app-{version_code}-{version_name}.apk"
    apk_path = d / name
    # 同 versionCode 已存在则拒绝，避免覆盖（先删旧版再传新版）
    if apk_path.exists():
        raise ApiError(409, "RELEASE_EXISTS", f"versionCode={version_code} 已存在，请先删除旧版")
    apk_path.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    meta = {
        "versionCode": version_code,
        "versionName": version_name,
        "changelog": changelog[:2000],
        "fileSize": len(data),
        "sha256": sha256,
        "uploadedAt": _now(),
        "uploadedBy": uploaded_by,
    }
    apk_path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def delete_release(version_code: int) -> bool:
    d = _releases_dir()
    removed = False
    for p in d.glob(f"app-{version_code}-*.apk"):
        # 精确匹配 versionCode，避免 100 误删 1000
        if _parse_release(p) and _parse_release(p)["versionCode"] == version_code:
            p.unlink(missing_ok=True)
            p.with_suffix(".json").unlink(missing_ok=True)
            removed = True
    return removed


@router.get("/api/v1/app/updates/latest")
def app_updates_latest() -> dict:
    """APP 查询最新版本（公开）。downloadUrl 为相对路径，APP 拼接自身配置的后端地址。"""
    r = get_latest_release()
    if not r:
        raise ApiError(404, "NO_RELEASE", "暂无可用的 APP 版本")
    return {
        "versionCode": r["versionCode"],
        "versionName": r["versionName"],
        "downloadUrl": "/api/v1/app/updates/download",
        "fileSize": r["fileSize"],
        "sha256": r["sha256"],
        "changelog": r["changelog"],
        "updatedAt": r["uploadedAt"],
    }


@router.get("/api/v1/app/updates/download")
def app_updates_download() -> FileResponse:
    """下载最新 APK（公开）。"""
    r = get_latest_release()
    if not r:
        raise ApiError(404, "NO_RELEASE", "暂无可用的 APP 版本")
    path = _releases_dir() / r["fileName"]
    if not path.is_file():
        raise ApiError(404, "NO_RELEASE", "安装包文件缺失")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=r["fileName"],
        headers={"ETag": f'"{r["sha256"]}"'} if r["sha256"] else None,
    )


# --------------------------------------------------------------------------
# 后端自更新通道（GitHub，手动）
# --------------------------------------------------------------------------

def get_deployed_sha() -> str:
    try:
        return _deployed_sha_file().read_text(encoding="ascii").strip() or "unknown"
    except OSError:
        return "unknown"


def _github_json(url: str, timeout: int = 15) -> object:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "material-flow-updater",
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def check_github_update() -> dict:
    """查询 GitHub main 分支最新 commit。"""
    data = _github_json(f"{GITHUB_API}/commits/{GITHUB_BRANCH}")
    if not isinstance(data, dict) or not data.get("sha"):
        raise ApiError(502, "GITHUB_ERROR", "GitHub 返回异常")
    sha: str = data.get("sha", "")
    commit = data.get("commit", {}) or {}
    message: str = (commit.get("message") or "").split("\n")[0][:200]
    date: str = ((commit.get("author") or {}).get("date")) or ""
    deployed = get_deployed_sha()
    return {
        "repo": GITHUB_REPO,
        "branch": GITHUB_BRANCH,
        "deployedSha": deployed,
        "latestSha": sha,
        "latestMessage": message,
        "latestDate": date,
        "updateAvailable": bool(sha) and sha != deployed,
        "checkedAt": _now(),
    }


def _update_status_file() -> Path:
    return _backups_dir() / "update-status.json"


def _update_request_file() -> Path:
    return _backups_dir() / "update-request"


# 状态超过该时长仍停在进行中，视为卡死（例如机器重启），允许重新发起
_STALE_SECONDS = 20 * 60
_ACTIVE_STATES = ("queued", "downloading", "applying")


def get_update_status() -> dict:
    try:
        st = json.loads(_update_status_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle", "at": ""}
    if st.get("state") in _ACTIVE_STATES:
        try:
            age = time.time() - _update_status_file().stat().st_mtime
        except OSError:
            age = 0
        if age > _STALE_SECONDS:
            st["state"] = "failed"
            st["error"] = st.get("error") or "更新长时间无进展，可能未执行。请在服务器上运行 deploy/update.sh 查看原因"
    return st


def _set_update_status(state: str, **kw: object) -> None:
    _backups_dir().mkdir(parents=True, exist_ok=True)
    d = {"state": state, "at": _now()}
    d.update(kw)
    tmp = _update_status_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_update_status_file())


def start_update(sha: str, actor: str) -> dict:
    """提交更新请求：写 backups/update-request，由 root 的 material-flow-update.path
    触发 deploy/update.sh --from-request 完成下载、备份、替换、迁移、重启与回滚。

    服务本身开着 NoNewPrivileges，不能也不应提权；这里只落一个请求文件。
    """
    if not _SHA_RE.fullmatch(sha):
        raise ApiError(400, "INVALID_SHA", "sha 格式无效（需 40 位 hex）")
    st = get_update_status()
    if st.get("state") in _ACTIVE_STATES:
        raise ApiError(409, "UPDATE_IN_PROGRESS", "已有更新正在执行，请稍候")
    try:
        _backups_dir().mkdir(parents=True, exist_ok=True)
        _set_update_status("queued", sha=sha, actor=actor)
        req = _update_request_file()
        tmp = req.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sha": sha, "actor": actor, "at": _now()}), encoding="utf-8")
        tmp.replace(req)
    except OSError as e:
        raise ApiError(500, "UPDATE_REQUEST_FAILED",
                       f"无法写入更新请求（{e}）。请在服务器上执行：sudo bash {_prefix()}/deploy/update.sh")
    return {"sha": sha, "state": "queued", "startedAt": _now()}


@router.get("/api/v1/admin/system/version")
def system_version(user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    return {
        "appVersion": APP_VERSION,
        "deployedSha": get_deployed_sha(),
        "repo": GITHUB_REPO,
        "branch": GITHUB_BRANCH,
    }


@router.post("/api/v1/admin/system/update/check")
def system_update_check(user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    try:
        return check_github_update()
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(502, "GITHUB_ERROR", f"查询 GitHub 失败：{e}")


@router.post("/api/v1/admin/system/update/apply")
def system_update_apply(body: dict, user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    sha = str((body or {}).get("sha", "")).strip().lower()
    try:
        return start_update(sha, user["username"])
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(500, "UPDATE_FAILED", f"更新异常：{e}")


@router.get("/api/v1/admin/system/update/status")
def system_update_status(user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    return get_update_status()
