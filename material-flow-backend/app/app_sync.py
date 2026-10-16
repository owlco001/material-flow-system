"""从 GitHub / Gitee Release 同步 APP 安装包到本后端的 APP 更新通道。

来源：
    GitHub  https://api.github.com/repos/{UPDATE_GITHUB_REPO}/releases
    Gitee   https://gitee.com/api/v5/repos/{UPDATE_GITEE_REPO}/releases
    只认附件名形如 app-{versionCode}-{versionName}.apk 的 Release（build-apk.yml 推 v* 标签产出）。
    同名 .sha256 附件存在时校验 SHA-256。

国内服务器直连 GitHub 下载很慢或不通：先直连，失败再走 APP_SYNC_GITHUB_PROXY
（默认 https://gh-proxy.com/，置空关闭）。Gitee 有同版本时 auto 模式优先 Gitee。

下载在后台线程执行（APK 几十 MB，慢网要几分钟），绝不放进请求线程或事件循环；
跨进程用 app-releases/.sync.lock 文件锁保证同一时间只有一个同步任务，
进度写 app-releases/.sync-status.json。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Body, Depends

try:
    from app.main import ApiError, CODE_FORBIDDEN, current_user
    from app import updates as _updates
except ImportError:  # 以 app 目录为 cwd 运行时的兜底
    from main import ApiError, CODE_FORBIDDEN, current_user  # type: ignore
    import updates as _updates  # type: ignore

router = APIRouter()

SOURCES = ("auto", "github", "gitee")
_TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SHA256_RE = re.compile(r"\b([0-9a-f]{64})\b")
_UA = {"User-Agent": "material-flow-app-sync"}
_STALE_SECONDS = 40 * 60
_ACTIVE = ("queued", "downloading", "verifying")


def _github_proxy() -> str:
    p = os.environ.get("APP_SYNC_GITHUB_PROXY", "https://gh-proxy.com/").strip()
    return p if not p or p.endswith("/") else p + "/"


def _max_seconds() -> int:
    try:
        return max(60, int(os.environ.get("APP_SYNC_MAX_SECONDS", "1800")))
    except ValueError:
        return 1800


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _admin(user: sqlite3.Row) -> None:
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可同步 APP 版本")


def _dir() -> Path:
    return _updates._releases_dir()


def _status_file() -> Path:
    return _dir() / ".sync-status.json"


# --------------------------------------------------------------------------
# 远端 Release 列表
# --------------------------------------------------------------------------

def _get_json(url: str, timeout: int) -> object:
    req = urllib.request.Request(url, headers={**_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _parse_releases(data: object, source: str) -> list[dict]:
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for rel in data:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        assets = [a for a in (rel.get("assets") or []) if isinstance(a, dict)]
        names = {str(a.get("name") or ""): a for a in assets}
        for name, a in names.items():
            m = _updates._APK_RE.fullmatch(name)
            url = str(a.get("browser_download_url") or "")
            if not m or not url.startswith("https://"):
                continue
            sha_asset = names.get(name + ".sha256")
            out.append({
                "source": source,
                "tag": str(rel.get("tag_name") or ""),
                "versionCode": int(m.group(1)),
                "versionName": m.group(2),
                "assetName": name,
                "downloadUrl": url,
                "sha256Url": str(sha_asset.get("browser_download_url") or "") if sha_asset else "",
                "size": int(a.get("size") or 0),
                "changelog": str(rel.get("body") or rel.get("name") or "").strip()[:2000],
                "publishedAt": str(rel.get("published_at") or rel.get("created_at") or ""),
            })
            break  # 每个 Release 只取一个 APK
    return out


def list_remote(source: str = "auto", limit: int = 10) -> dict:
    """查询远端 Release。返回 {releases: [...按 versionCode 降序], errors: [...]}。"""
    if source not in SOURCES:
        raise ApiError(400, "INVALID_SOURCE", "来源只能是 auto / github / gitee")
    order = ["gitee", "github"] if source == "auto" else [source]
    found: dict[int, dict] = {}
    errors: list[str] = []
    for src in order:
        try:
            if src == "github":
                data = _get_json(f"{_updates.GITHUB_API}/releases?per_page={limit}", 15)
            else:
                data = _get_json(f"{_updates.GITEE_API}/releases?per_page={limit}&direction=desc", 15)
            for r in _parse_releases(data, src):
                found.setdefault(r["versionCode"], r)  # auto：同版本先到先得（Gitee 优先）
        except Exception as e:  # noqa: BLE001 - 网络错误记下继续查下一个来源
            errors.append(f"{src}: {str(e)[:160]}")
    if not found and errors:
        raise ApiError(502, "APP_SYNC_SOURCE_ERROR", "查询远端版本失败：" + "；".join(errors)[:300])
    local = {r["versionCode"] for r in _updates.list_releases()}
    rels = sorted(found.values(), key=lambda r: r["versionCode"], reverse=True)[:limit]
    for r in rels:
        r["synced"] = r["versionCode"] in local
    return {"releases": rels, "errors": errors, "checkedAt": _now()}


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------

def _write_status(d: dict) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    d = {**d, "at": _now()}
    tmp = _status_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_status_file())


def get_status() -> dict:
    try:
        st = json.loads(_status_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle"}
    if st.get("state") in _ACTIVE:
        try:
            age = time.time() - _status_file().stat().st_mtime
        except OSError:
            age = 0
        if age > _STALE_SECONDS:
            st["state"] = "failed"
            st["error"] = st.get("error") or "同步长时间无进展（服务可能重启过），可重新发起"
    return st


# --------------------------------------------------------------------------
# 下载与发布
# --------------------------------------------------------------------------

def _candidate_urls(rel: dict, url: str) -> list[str]:
    if rel["source"] == "github" and _github_proxy():
        return [url, _github_proxy() + url]
    return [url]


def _fetch_text(rel: dict, url: str) -> str:
    last: Exception | None = None
    for u in _candidate_urls(rel, url):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=_UA), timeout=30) as resp:
                return resp.read(4096).decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"下载校验文件失败：{last}")


def _download(rel: dict, part: Path, deadline: float, progress) -> tuple[str, str]:
    errors: list[str] = []
    for u in _candidate_urls(rel, rel["downloadUrl"]):
        h = hashlib.sha256()
        got = 0
        try:
            req = urllib.request.Request(u, headers=_UA)
            with urllib.request.urlopen(req, timeout=30) as resp, part.open("wb") as f:
                total = int(resp.headers.get("Content-Length") or rel.get("size") or 0)
                if total > _updates._APK_MAX_BYTES:
                    raise RuntimeError("安装包超过 300MB 上限")
                last_report = 0.0
                while True:
                    if time.time() > deadline:
                        raise RuntimeError(f"下载超时（>{_max_seconds()} 秒）")
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > _updates._APK_MAX_BYTES:
                        raise RuntimeError("安装包超过 300MB 上限")
                    h.update(chunk)
                    f.write(chunk)
                    if time.time() - last_report > 2:
                        progress(got, total, u)
                        last_report = time.time()
            if total and got != total:
                raise RuntimeError(f"下载不完整（{got}/{total} 字节）")
            progress(got, total, u)
            return h.hexdigest(), u.split("/")[2]
        except Exception as e:  # noqa: BLE001 - 换下一个地址
            errors.append(f"{u.split('/')[2]}: {str(e)[:160]}")
            part.unlink(missing_ok=True)
    raise RuntimeError("下载失败：" + "；".join(errors))


def _run(rel: dict, actor: str, lock_fd: int) -> None:
    part = _dir() / f".sync-{rel['versionCode']}.part"
    base = {"source": rel["source"], "tag": rel["tag"], "versionCode": rel["versionCode"],
            "versionName": rel["versionName"], "actor": actor, "startedAt": _now()}
    try:
        deadline = time.time() + _max_seconds()

        def progress(got: int, total: int, url: str) -> None:
            _write_status({**base, "state": "downloading", "bytes": got, "total": total,
                           "via": url.split("/")[2]})

        progress(0, rel.get("size") or 0, rel["downloadUrl"])
        digest, via = _download(rel, part, deadline, progress)
        base["via"] = via
        _write_status({**base, "state": "verifying"})
        if rel.get("sha256Url"):
            m = _SHA256_RE.search(_fetch_text(rel, rel["sha256Url"]).lower())
            if not m:
                raise RuntimeError("校验文件格式无效")
            if m.group(1) != digest:
                raise RuntimeError("SHA-256 校验不一致，已丢弃下载内容")
        data = part.read_bytes()
        changelog = rel.get("changelog") or f"同步自 {rel['source']} {rel['tag']}"
        meta = _updates.save_apk_upload(data, rel["assetName"], rel["versionCode"], rel["versionName"],
                                        changelog, f"{actor}（{rel['source']} 同步）")
        _write_status({**base, "state": "done", "bytes": len(data), "total": len(data),
                       "sha256": meta["sha256"], "finishedAt": _now()})
    except ApiError as e:
        _write_status({**base, "state": "failed", "error": str(e.detail),
                       "code": getattr(e, "code", "")})
    except Exception as e:  # noqa: BLE001 - 后台线程：记录失败，不抛出
        _write_status({**base, "state": "failed", "error": str(e)[:400]})
    finally:
        part.unlink(missing_ok=True)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def start_sync(source: str, tag: str, actor: str, wait: bool = False) -> dict:
    """发起同步：tag 为空则取远端最新版本。已同步过的版本直接返回 already。

    wait=True 仅供测试：在当前线程跑完。
    """
    if tag and not _TAG_RE.fullmatch(tag):
        raise ApiError(400, "INVALID_TAG", "Release 标签格式无效")
    remote = list_remote(source)
    rels = remote["releases"]
    if tag:
        rels = [r for r in rels if r["tag"] == tag]
    if not rels:
        raise ApiError(404, "APP_SYNC_NOT_FOUND",
                       "远端没有符合命名（app-版本号-版本名.apk）的安装包" + (f"：{tag}" if tag else ""))
    rel = rels[0]
    if rel["synced"]:
        return {"state": "already", "versionCode": rel["versionCode"], "versionName": rel["versionName"]}
    _dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_dir() / ".sync.lock"), os.O_CREAT | os.O_RDWR, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ApiError(409, "APP_SYNC_BUSY", "已有同步任务在进行，请稍候")
    _write_status({"state": "queued", "source": rel["source"], "tag": rel["tag"],
                   "versionCode": rel["versionCode"], "versionName": rel["versionName"], "actor": actor})
    if wait:
        _run(rel, actor, fd)
    else:
        threading.Thread(target=_run, args=(rel, actor, fd), name="app-sync", daemon=True).start()
    return {"state": "queued", "source": rel["source"], "tag": rel["tag"],
            "versionCode": rel["versionCode"], "versionName": rel["versionName"]}


# --------------------------------------------------------------------------
# API（仅 ADMIN；同步 def 路由，FastAPI 放线程池执行）
# --------------------------------------------------------------------------

@router.get("/api/v1/admin/app/releases/remote")
def api_remote(source: str = "auto", user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    return list_remote(source)


@router.post("/api/v1/admin/app/releases/sync")
def api_sync(body: dict = Body(default={}), user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    source = str((body or {}).get("source") or "auto").strip().lower()
    tag = str((body or {}).get("tag") or "").strip()
    return start_sync(source, tag, user["username"])


@router.get("/api/v1/admin/app/releases/sync/status")
def api_status(user: sqlite3.Row = Depends(current_user)) -> dict:
    _admin(user)
    return get_status()
