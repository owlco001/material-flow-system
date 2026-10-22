"""设计同步 API（0.22.0）：桌面设计端把「设计包」（zip + manifest）推送到智慧工厂。

鉴权：机器身份 ``X-Design-Key``（与用户 Bearer / 管理台会话完全分离）。
- 明文密钥只在管理台生成时显示一次；库里只存前缀（查找用）+ Argon2id 哈希（复用 main.hash_password）。
- 每个密钥有来源 IP 网段（ip_scope，CIDR 列表），默认全部内网段；不在网段内 → 403。
- 可选机台范围（device_scope，devices.id 列表），为空 = 全部机台。

来源 IP：与现有审计 source_ip 同口径，取 ``request.client.host``。生产 uvicorn 只监听 127.0.0.1，
前置 nginx/Caddy；uvicorn 0.34 默认只信任 127.0.0.1 发来的 X-Forwarded-For，由它还原真实客户端，
本模块**不自行解析** X-Forwarded-For。额外一条只收紧不放宽的规则：请求带 ``Cf-Connecting-Ip``
（经 Cloudflare 隧道进来，该头由 Cloudflare 覆写，外部无法伪造成内网地址）时，它也必须落在网段内，
防止公网流量经 cloudflared → 本机反代后被误判为 127.0.0.1。

存储：``{DATA_DIR}/design_packages/<packageId>/package.zip`` + ``files/<manifest 路径>``。
设计包里的 BOM 只做归档与比对，**不写** bom_versions / materials。
绑定（bind）时：PDF/DXF 走 drawings.process_upload 注册为图纸并写 drawing_bindings；
DEVICE 绑定时 model.glb 注册为 assembly_model_versions 新版本（机型码 ``DS-<机台编号>``）
并设置 devices.model_3d_code；STEP 只归档。绑定幂等。
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import secrets
import shutil
import sqlite3
import stat
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.formparsers import MultiPartException, MultiPartParser

from app import main as _main
from app.main import ApiError

router = APIRouter()

# ------------------------------------------------------------------ limits
# 读取时都经模块属性，测试可 monkeypatch 调小。
ZIP_MAX_BYTES = 200 * 1024 * 1024
MANIFEST_MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 500
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_BOM_ROWS = 10000
MULTIPART_OVERHEAD = 64 * 1024
LIST_LIMIT_MAX = 100

DEFAULT_IP_SCOPE = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7")
FILE_KINDS = ("BOM", "PDF", "STEP", "GLTF", "DXF")
DRAWING_KINDS = ("PDF", "DXF")
TARGET_TYPES = ("DEVICE", "MATERIAL")
KEY_PREFIX = "mfds_"
ACTOR_ROLE = "DESIGN_KEY"

CODE_KEY_INVALID = "DESIGN_KEY_INVALID"
CODE_IP_FORBIDDEN = "DESIGN_KEY_IP_FORBIDDEN"
CODE_DEVICE_FORBIDDEN = "DESIGN_KEY_DEVICE_FORBIDDEN"
CODE_DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
CODE_MATERIAL_NOT_FOUND = "MATERIAL_NOT_FOUND"
CODE_PACKAGE_NOT_FOUND = "DESIGN_PACKAGE_NOT_FOUND"
CODE_TOO_LARGE = "DESIGN_PACKAGE_TOO_LARGE"
CODE_INVALID = "DESIGN_PACKAGE_INVALID"
CODE_MANIFEST_INVALID = "DESIGN_MANIFEST_INVALID"
CODE_UNSAFE_PATH = "DESIGN_PACKAGE_UNSAFE_PATH"
CODE_FILE_MISMATCH = "DESIGN_PACKAGE_FILE_MISMATCH"
CODE_IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
CODE_DEVICE_CONFLICT = "DESIGN_PACKAGE_DEVICE_CONFLICT"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_RE = re.compile(r"^[\w.\-]{1,128}$")
_PKG_ID_RE = re.compile(r"^dsp_[0-9a-f]{32}$")
_KEY_ID_RE = re.compile(r"^dk_[0-9a-f]{24}$")
_MODEL_CODE_BAD = re.compile(r"[^A-Za-z0-9._-]")


def init_tables(c: sqlite3.Connection) -> None:
    """由 main._init_db 调用：CREATE TABLE IF NOT EXISTS，幂等。"""
    c.executescript("""
    CREATE TABLE IF NOT EXISTS design_keys(
        id TEXT PRIMARY KEY, key_prefix TEXT NOT NULL UNIQUE, key_hash TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '', device_scope TEXT NOT NULL DEFAULT '', ip_scope TEXT NOT NULL,
        created_by TEXT, created_at TEXT NOT NULL, revoked_at TEXT, last_used_at TEXT);
    CREATE TABLE IF NOT EXISTS design_packages(
        id TEXT PRIMARY KEY, device_id TEXT NOT NULL, project_id TEXT NOT NULL, project_name TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL CHECK(version > 0), schema_version INTEGER NOT NULL, app_version TEXT NOT NULL DEFAULT '',
        manifest_created_at TEXT NOT NULL, manifest_json TEXT NOT NULL, bom_json TEXT NOT NULL DEFAULT '[]',
        bom_sha256 TEXT NOT NULL DEFAULT '', zip_sha256 TEXT NOT NULL UNIQUE, zip_size INTEGER NOT NULL,
        file_count INTEGER NOT NULL DEFAULT 0, uploaded_by TEXT NOT NULL, uploaded_at TEXT NOT NULL,
        UNIQUE(device_id, project_id, version));
    CREATE INDEX IF NOT EXISTS idx_design_packages_device_project ON design_packages(device_id, project_id, version);
    CREATE TABLE IF NOT EXISTS design_package_files(
        package_id TEXT NOT NULL REFERENCES design_packages(id) ON DELETE CASCADE, path TEXT NOT NULL,
        kind TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(package_id, path));
    CREATE TABLE IF NOT EXISTS design_package_drawings(
        package_id TEXT NOT NULL REFERENCES design_packages(id) ON DELETE CASCADE, path TEXT NOT NULL,
        drawing_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(package_id, path));
    CREATE TABLE IF NOT EXISTS design_package_bindings(
        id TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES design_packages(id) ON DELETE CASCADE,
        target_type TEXT NOT NULL CHECK(target_type IN ('DEVICE','MATERIAL')), target_key TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(package_id, target_type, target_key));
    CREATE INDEX IF NOT EXISTS idx_design_package_bindings_target ON design_package_bindings(target_type, target_key);
    """)


# ------------------------------------------------------------------ storage

def storage_root() -> Path:
    return Path(_main.DATA_DIR) / "design_packages"


def package_dir(package_id: str) -> Path:
    if not _PKG_ID_RE.fullmatch(package_id or ""):
        raise ApiError(404, CODE_PACKAGE_NOT_FOUND, "设计包不存在")
    return storage_root() / package_id


# --------------------------------------------------------------- ip scope

def parse_ip_scope(text: str | Iterable[str]) -> list[str]:
    """CIDR 列表（逗号/空白/换行分隔）→ 规范化字符串列表。非法或为空抛 ValueError。"""
    parts = text if not isinstance(text, str) else re.split(r"[\s,，;；]+", text)
    out: list[str] = []
    for raw in parts:
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            raise ValueError(f"网段格式无效：{raw[:60]}") from None
        if str(net) not in out:
            out.append(str(net))
    if not out:
        raise ValueError("至少需要一个来源网段")
    if len(out) > 64:
        raise ValueError("来源网段最多 64 个")
    return out


def _parse_ip(value: str | None):
    if not value:
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def client_ips(request: Request) -> list:
    """需要校验的来源地址。任一无法解析 → 返回含 None 的列表（一律拒绝）。"""
    ips = [_parse_ip(request.client.host if request.client else None)]
    cf = request.headers.get("cf-connecting-ip")
    if cf is not None:
        ips.append(_parse_ip(cf))
    return ips


def ip_allowed(ips: list, scope: list[str]) -> bool:
    nets = [ipaddress.ip_network(s, strict=False) for s in scope]
    for ip in ips:
        if ip is None or not any(ip.version == n.version and ip in n for n in nets):
            return False
    return True


def source_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# -------------------------------------------------------------- key auth

def generate_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    return prefix, f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"


def _split_scope(text: str | None) -> list[str]:
    return [p for p in (text or "").split(",") if p]


# Argon2id 校验缓存：同一把密钥的同一份原文在 TTL 内复用首次校验结论。
# 密钥行（含 revoked_at / ip_scope / device_scope）每个请求仍然读库，
# 所以吊销和收紧范围都是即时生效的，缓存只省掉重复的 Argon2 计算。
KEY_VERIFY_CACHE_TTL = 60.0
_verify_cache: dict[str, tuple[float, str]] = {}


def _key_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verify_key_hash(stored: str, raw: str) -> bool:
    try:
        return _main._ph.verify(stored, raw)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def _verify_cache_get(key_id: str, raw: str) -> bool | None:
    """命中返回 True；未命中/过期/原文不符返回 None（要求走一次真实校验）。"""
    hit = _verify_cache.get(key_id)
    if hit is None:
        return None
    expires, digest = hit
    if expires < time.monotonic() or digest != _key_digest(raw):
        _verify_cache.pop(key_id, None)
        return None
    return True


def _verify_cache_store(key_id: str, raw: str) -> None:
    if len(_verify_cache) > 256:  # 防止无界增长
        _verify_cache.clear()
    _verify_cache[key_id] = (time.monotonic() + KEY_VERIFY_CACHE_TTL, _key_digest(raw))


def _verify_cache_drop(key_id: str) -> None:
    _verify_cache.pop(key_id, None)


def authenticate(request: Request, trace_id: str) -> sqlite3.Row:
    raw = (request.headers.get("x-design-key") or "").strip()
    m = re.fullmatch(KEY_PREFIX + r"([0-9a-f]{8})_[A-Za-z0-9_\-]{20,128}", raw)
    if not m:
        raise ApiError(401, CODE_KEY_INVALID, "设计同步密钥无效或已吊销", trace_id=trace_id)
    c = _main.db()
    try:
        row = c.execute("SELECT * FROM design_keys WHERE key_prefix=?", (m.group(1),)).fetchone()
        ok = False
        if row is not None:
            ok = _verify_cache_get(row["id"], raw)
            if ok is None:
                ok = _verify_key_hash(row["key_hash"], raw)
                if ok:
                    _verify_cache_store(row["id"], raw)
            # 吊销状态每请求都读库，缓存只省 Argon2 计算，不影响吊销的即时性
        if not ok or row["revoked_at"]:
            raise ApiError(401, CODE_KEY_INVALID, "设计同步密钥无效或已吊销", trace_id=trace_id)
        scope = _split_scope(row["ip_scope"]) or list(DEFAULT_IP_SCOPE)
        if not ip_allowed(client_ips(request), scope):
            _main.audit(c, actor_id(row), ACTOR_ROLE, "DESIGN_SYNC_IP_DENIED", "DESIGN_KEY", row["id"], "DENIED", trace_id)
            c.commit()
            raise ApiError(403, CODE_IP_FORBIDDEN, "来源地址不在该密钥允许的网段内", trace_id=trace_id)
        c.execute("UPDATE design_keys SET last_used_at=? WHERE id=?", (_main.now(), row["id"]))
        c.commit()
        return row
    finally:
        c.close()


def actor_id(key: sqlite3.Row) -> str:
    return f"design-key:{key['id']}"


def _device_in_scope(key: sqlite3.Row | None, device_id: str) -> bool:
    if key is None:
        return True
    scope = _split_scope(key["device_scope"])
    return not scope or device_id in scope


def resolve_device(c: sqlite3.Connection, raw: str, trace_id: str) -> sqlite3.Row:
    raw = (raw or "").strip()
    row = c.execute("SELECT id, device_no, device_name FROM devices WHERE device_no=?", (raw,)).fetchone() if raw else None
    if row is None and raw:
        row = c.execute("SELECT id, device_no, device_name FROM devices WHERE id=?", (raw,)).fetchone()
    if row is None:
        raise ApiError(404, CODE_DEVICE_NOT_FOUND, "机台不存在", trace_id=trace_id)
    return row


def _event(c: sqlite3.Connection, request: Request, *, event_type: str, entity_type: str, entity_id: str,
           actor: str, role: str, device_id: str | None, trace_id: str, operation_id: str,
           after: dict[str, Any], result: str = "SUCCESS") -> None:
    _main._audit_event(c, event_type, entity_id, {"id": actor, "role": role, "session_device_id": device_id},
                       trace_id, operation_id, {}, after, request, result=result, entity_type=entity_type)


def _trace(request: Request) -> str:
    return _main._request_trace_id(request)


# --------------------------------------------------------------- manifest

def safe_rel_path(p: Any) -> str:
    """相对路径白名单：拒绝绝对路径、盘符、反斜杠、空段、. 与 ..、控制字符。"""
    if not isinstance(p, str) or not 1 <= len(p) <= 255:
        raise ValueError("路径无效")
    if "\\" in p or p.startswith("/") or any(ord(ch) < 32 for ch in p):
        raise ValueError(f"路径不安全：{p[:80]}")
    segs = p.split("/")
    if ":" in segs[0] or any(s in ("", ".", "..") for s in segs):
        raise ValueError(f"路径不安全：{p[:80]}")
    return p


def _bad_manifest(msg: str, trace_id: str):
    return ApiError(400, CODE_MANIFEST_INVALID, msg, trace_id=trace_id)


def _parse_iso(value: Any) -> str | None:
    if not isinstance(value, str) or not 10 <= len(value) <= 40 or "T" not in value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return value if dt.tzinfo is not None else None


def validate_manifest(raw: str, trace_id: str) -> dict[str, Any]:
    try:
        m = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise _bad_manifest("manifest 不是合法 JSON", trace_id) from None
    if not isinstance(m, dict):
        raise _bad_manifest("manifest 必须是 JSON 对象", trace_id)
    if m.get("schemaVersion") != 1 or isinstance(m.get("schemaVersion"), bool):
        raise _bad_manifest("schemaVersion 必须为 1", trace_id)
    pid = m.get("projectId")
    if not isinstance(pid, str) or not _PROJECT_RE.fullmatch(pid):
        raise _bad_manifest("projectId 无效（1-128 位字母、数字、汉字、._-）", trace_id)
    for f, n in (("projectName", 200), ("appVersion", 64)):
        if m.get(f) is not None and (not isinstance(m[f], str) or len(m[f]) > n):
            raise _bad_manifest(f"{f} 无效", trace_id)
    if _parse_iso(m.get("createdAt")) is None:
        raise _bad_manifest("createdAt 必须是带时区的 ISO 8601 时间，如 2026-10-22T14:00:00Z", trace_id)
    bom_sha = m.get("bomSha256")
    if bom_sha is not None and (not isinstance(bom_sha, str) or not _SHA_RE.fullmatch(bom_sha.lower())):
        raise _bad_manifest("bomSha256 必须是 64 位十六进制", trace_id)

    files = m.get("files")
    if not isinstance(files, list):
        raise _bad_manifest("files 必须是数组", trace_id)
    if len(files) > MAX_ENTRIES:
        raise _bad_manifest(f"文件数超过上限 {MAX_ENTRIES}", trace_id)
    seen: set[str] = set()
    clean_files = []
    for f in files:
        if not isinstance(f, dict):
            raise _bad_manifest("files 元素必须是对象", trace_id)
        try:
            path = safe_rel_path(f.get("path"))
        except ValueError as e:
            raise ApiError(400, CODE_UNSAFE_PATH, str(e), trace_id=trace_id) from None
        if path.lower() in seen:
            raise _bad_manifest(f"文件路径重复：{path}", trace_id)
        seen.add(path.lower())
        sha = f.get("sha256")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha.lower()):
            raise _bad_manifest(f"{path}：sha256 必须是 64 位十六进制", trace_id)
        size = f.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise _bad_manifest(f"{path}：size 必须是非负整数", trace_id)
        kind = f.get("kind")
        if kind not in FILE_KINDS:
            raise _bad_manifest(f"{path}：kind 必须是 {'/'.join(FILE_KINDS)}", trace_id)
        clean_files.append({"path": path, "sha256": sha.lower(), "size": size, "kind": kind})

    bom = m.get("bom", [])
    if not isinstance(bom, list):
        raise _bad_manifest("bom 必须是数组", trace_id)
    if len(bom) > MAX_BOM_ROWS:
        raise _bad_manifest(f"BOM 行数超过上限 {MAX_BOM_ROWS}", trace_id)
    nos: set[str] = set()
    for row in bom:
        if not isinstance(row, dict):
            raise _bad_manifest("bom 元素必须是对象", trace_id)
        no = row.get("no")
        if isinstance(no, bool) or not (isinstance(no, int) or (isinstance(no, str) and 0 < len(no) <= 64)):
            raise _bad_manifest("bom.no 必须是整数或非空字符串", trace_id)
        if str(no) in nos:
            raise _bad_manifest(f"bom.no 重复：{no}", trace_id)
        nos.add(str(no))
        qty = row.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            raise _bad_manifest(f"bom 第 {no} 行：qty 必须是正整数", trace_id)
        for f in ("name", "spec", "mat"):
            if row.get(f) is not None and not isinstance(row[f], str):
                raise _bad_manifest(f"bom 第 {no} 行：{f} 必须是字符串", trace_id)
    m["files"] = clean_files
    m["bom"] = bom
    return m


# ------------------------------------------------------------- multipart

class _LimitedMultiPart(MultiPartParser):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.max_part_size = MANIFEST_MAX_BYTES


class _BodyTooLarge(Exception):
    pass


async def _limited_stream(request: Request, limit: int):
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _BodyTooLarge()
        yield chunk


async def _read_multipart(request: Request, trace_id: str):
    ctype = request.headers.get("content-type", "")
    if not ctype.lower().startswith("multipart/form-data"):
        raise ApiError(400, CODE_INVALID, "请求必须是 multipart/form-data", trace_id=trace_id)
    limit = ZIP_MAX_BYTES + MANIFEST_MAX_BYTES + MULTIPART_OVERHEAD
    try:
        declared = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise ApiError(413, CODE_TOO_LARGE, f"设计包超过 {ZIP_MAX_BYTES // (1024 * 1024)} MB 上限", trace_id=trace_id)
    parser = _LimitedMultiPart(request.headers, _limited_stream(request, limit), max_files=1, max_fields=8)
    try:
        form = await parser.parse()
    except _BodyTooLarge:
        raise ApiError(413, CODE_TOO_LARGE, f"设计包超过 {ZIP_MAX_BYTES // (1024 * 1024)} MB 上限", trace_id=trace_id) from None
    except MultiPartException as e:
        if "maximum size" in str(e):
            raise ApiError(413, CODE_TOO_LARGE, "manifest 超过 1 MB 上限", trace_id=trace_id) from None
        raise ApiError(400, CODE_INVALID, "multipart 请求格式无效", trace_id=trace_id) from None
    return form


# ------------------------------------------------------------- zip check

def _sha_stream(fp, limit: int | None = None) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    while chunk := fp.read(1 << 20):
        n += len(chunk)
        if limit is not None and n > limit:
            raise _BodyTooLarge()
        h.update(chunk)
    return h.hexdigest(), n


def inspect_zip(fp, manifest: dict[str, Any], trace_id: str) -> None:
    """只读校验 zip（不落盘）：条目路径、符号链接、加密、条目数、解压总量、manifest 每个文件的大小与 sha256。"""
    try:
        zf = zipfile.ZipFile(fp)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ApiError(400, CODE_INVALID, "文件不是有效的 zip", trace_id=trace_id) from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ApiError(400, CODE_INVALID, f"zip 条目数超过上限 {MAX_ENTRIES}", trace_id=trace_id)
        names: dict[str, zipfile.ZipInfo] = {}
        seen_lower: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            is_dir = name.endswith("/")
            try:
                safe_rel_path(name.rstrip("/") if is_dir else name)
            except ValueError as e:
                raise ApiError(400, CODE_UNSAFE_PATH, str(e), trace_id=trace_id) from None
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                raise ApiError(400, CODE_UNSAFE_PATH, f"zip 不允许包含符号链接：{name[:80]}", trace_id=trace_id)
            if info.flag_bits & 0x1:
                raise ApiError(400, CODE_INVALID, "zip 不允许加密条目", trace_id=trace_id)
            low = name.lower()
            if low in seen_lower:  # 大小写不敏感的重名检查，O(1) 查表
                raise ApiError(400, CODE_INVALID, f"zip 条目重复：{name[:80]}", trace_id=trace_id)
            seen_lower.add(low)
            names[name] = info
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ApiError(400, CODE_INVALID, "zip 解压总量超过上限", trace_id=trace_id)
        actual_total = 0
        for f in manifest["files"]:
            info = names.get(f["path"])
            if info is None or info.filename.endswith("/"):
                raise ApiError(400, CODE_FILE_MISMATCH, f"zip 中缺少 manifest 声明的文件：{f['path']}", trace_id=trace_id)
            if info.file_size != f["size"]:
                raise ApiError(400, CODE_FILE_MISMATCH, f"{f['path']}：大小与 manifest 不一致", trace_id=trace_id)
            try:
                with zf.open(info) as src:
                    sha, n = _sha_stream(src, MAX_UNCOMPRESSED_BYTES - actual_total)
            except _BodyTooLarge:
                raise ApiError(400, CODE_INVALID, "zip 解压总量超过上限", trace_id=trace_id) from None
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError):
                raise ApiError(400, CODE_INVALID, f"{f['path']}：zip 条目损坏或压缩方式不支持", trace_id=trace_id) from None
            actual_total += n
            if n != f["size"] or sha != f["sha256"]:
                raise ApiError(400, CODE_FILE_MISMATCH, f"{f['path']}：sha256 与 manifest 不一致", trace_id=trace_id)


def _extract(fp, manifest: dict[str, Any], dest: Path) -> None:
    files_root = dest / "files"
    with zipfile.ZipFile(fp) as zf:
        for f in manifest["files"]:
            target = (files_root / f["path"]).resolve()
            if files_root.resolve() not in target.parents:
                raise ValueError("unsafe path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(f["path"]) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)


# --------------------------------------------------------------- payloads

def _row_payload(r: sqlite3.Row, device_no: str | None = None) -> dict[str, Any]:
    return {
        "packageId": r["id"], "deviceId": r["device_id"], "deviceNo": device_no,
        "projectId": r["project_id"], "projectName": r["project_name"], "version": r["version"],
        "bomSha256": r["bom_sha256"] or None, "zipSha256": r["zip_sha256"], "zipSize": r["zip_size"],
        "fileCount": r["file_count"], "createdAt": r["manifest_created_at"], "uploadedAt": r["uploaded_at"],
        "uploadedBy": r["uploaded_by"],
    }


def _device_no(c: sqlite3.Connection, device_id: str) -> str | None:
    row = c.execute("SELECT device_no FROM devices WHERE id=?", (device_id,)).fetchone()
    return row["device_no"] if row else None


def _load_package(c: sqlite3.Connection, package_id: str, key: sqlite3.Row | None, trace_id: str) -> sqlite3.Row:
    if not _PKG_ID_RE.fullmatch(package_id or ""):
        raise ApiError(404, CODE_PACKAGE_NOT_FOUND, "设计包不存在", trace_id=trace_id)
    row = c.execute("SELECT * FROM design_packages WHERE id=?", (package_id,)).fetchone()
    if row is None or not _device_in_scope(key, row["device_id"]):
        raise ApiError(404, CODE_PACKAGE_NOT_FOUND, "设计包不存在", trace_id=trace_id)
    return row


def package_detail(c: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    files = c.execute("SELECT path, kind, sha256, size FROM design_package_files WHERE package_id=? ORDER BY path",
                      (row["id"],)).fetchall()
    binds = c.execute("SELECT target_type, target_key, result_json, created_by, created_at FROM design_package_bindings"
                      " WHERE package_id=? ORDER BY created_at", (row["id"],)).fetchall()
    out = _row_payload(row, _device_no(c, row["device_id"]))
    out.update({
        "appVersion": row["app_version"] or None,
        "files": [dict(f) for f in files],
        "bom": json.loads(row["bom_json"] or "[]"),
        "bindings": [{"targetType": b["target_type"], "targetKey": b["target_key"],
                      "targetNo": _device_no(c, b["target_key"]) if b["target_type"] == "DEVICE" else b["target_key"],
                      "createdBy": b["created_by"], "createdAt": b["created_at"],
                      **json.loads(b["result_json"] or "{}")} for b in binds],
    })
    return out


# ------------------------------------------------------------------- diff

def _bom_key(row: dict[str, Any]) -> str:
    return str(row.get("no"))


def compute_diff(base_bom: list, new_bom: list, base_files: list[dict], new_files: list[dict]) -> dict[str, Any]:
    old = {_bom_key(r): r for r in base_bom}
    new = {_bom_key(r): r for r in new_bom}
    added = [new[k] for k in new if k not in old]
    removed = [old[k] for k in old if k not in new]
    changed = []
    for k in new:
        if k not in old:
            continue
        a, b = old[k], new[k]
        for field in sorted((set(a) | set(b)) - {"no"}):
            if a.get(field) != b.get(field):
                changed.append({"no": b.get("no"), "field": field, "from": a.get(field), "to": b.get(field)})
    of = {f["path"]: f for f in base_files}
    nf = {f["path"]: f for f in new_files}
    return {
        "bomAdded": added, "bomRemoved": removed, "bomChanged": changed,
        "filesAdded": [nf[p] for p in sorted(nf) if p not in of],
        "filesRemoved": [of[p] for p in sorted(of) if p not in nf],
        "filesChanged": [{"path": p, "kind": nf[p]["kind"], "fromSha256": of[p]["sha256"], "toSha256": nf[p]["sha256"],
                          "fromSize": of[p]["size"], "toSize": nf[p]["size"]}
                         for p in sorted(nf) if p in of and of[p]["sha256"] != nf[p]["sha256"]],
    }


def _files_of(c: sqlite3.Connection, package_id: str) -> list[dict]:
    return [dict(r) for r in c.execute("SELECT path, kind, sha256, size FROM design_package_files WHERE package_id=?"
                                       " ORDER BY path", (package_id,)).fetchall()]


def diff_package(c: sqlite3.Connection, row: sqlite3.Row, against: str, key: sqlite3.Row | None,
                 trace_id: str) -> dict[str, Any]:
    against = (against or "latest").strip()
    if against == "latest":
        base = c.execute("SELECT * FROM design_packages WHERE device_id=? AND project_id=? AND id<>?"
                         " ORDER BY version DESC LIMIT 1", (row["device_id"], row["project_id"], row["id"])).fetchone()
    elif against == "previous":
        base = c.execute("SELECT * FROM design_packages WHERE device_id=? AND project_id=? AND version<?"
                         " ORDER BY version DESC LIMIT 1", (row["device_id"], row["project_id"], row["version"])).fetchone()
    else:
        base = _load_package(c, against, key, trace_id)
    d = compute_diff(json.loads(base["bom_json"]) if base else [], json.loads(row["bom_json"]),
                     _files_of(c, base["id"]) if base else [], _files_of(c, row["id"]))
    return {"packageId": row["id"], "version": row["version"],
            "base": {"packageId": base["id"], "version": base["version"]} if base else None, **d}


# ------------------------------------------------------------------- bind

def _model_code_for(device: sqlite3.Row) -> str:
    code = "DS-" + _MODEL_CODE_BAD.sub("-", device["device_no"])
    if not _main.MODEL_CODE_RE.fullmatch(code):
        code = "DS-" + _MODEL_CODE_BAD.sub("-", device["id"])
    return code[:64]


def _register_glb(c: sqlite3.Connection, src: Path, sha: str, size: int, device: sqlite3.Row, model_name: str,
                  actor: str, created: list[Path]) -> tuple[dict[str, Any] | None, str | None]:
    """把 model.glb 注册为 assembly_model_versions 的已发布版本（同 /api/v1/assembly-models 的存储与发布规则）。"""
    if size <= 0 or size > _main.GLB_MAX_BYTES:
        return None, f"{src.name}：超过 3D 模型 15 MiB 上限，仅归档"
    with src.open("rb") as f:
        if f.read(4) != _main.GLB_MAGIC:
            return None, f"{src.name}：不是有效 GLB，仅归档"
    code = _model_code_for(device)
    prior = c.execute("SELECT * FROM assembly_model_versions WHERE model_code=? AND sha256=?", (code, sha)).fetchone()
    if prior is not None:
        if prior["status"] != "PUBLISHED":
            c.execute("UPDATE assembly_model_versions SET status='ARCHIVED' WHERE model_code=? AND status='PUBLISHED'", (code,))
            c.execute("UPDATE assembly_model_versions SET status='PUBLISHED' WHERE id=?", (prior["id"],))
        version = prior["version"]
    else:
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM assembly_model_versions WHERE model_code=?",
                            (code,)).fetchone()[0]
        model_id = "asmmdl_" + uuid.uuid4().hex
        storage_key = f"assembly-models/{model_id}/v{version}.glb"
        target = Path(_main.UPLOAD_DIR) / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        created.append(target.parent)
        c.execute("UPDATE assembly_model_versions SET status='ARCHIVED' WHERE model_code=? AND status='PUBLISHED'", (code,))
        c.execute("INSERT INTO assembly_model_versions(id,model_code,model_name,version,format,byte_size,sha256,"
                  "storage_key,status,created_by,created_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (model_id, code, model_name[:128] or code, version, "GLB", size, sha, storage_key, "PUBLISHED",
                   actor, _main.now(), None))
    c.execute("UPDATE devices SET model_3d_code=?, updated_at=? WHERE id=?", (code, _main.now(), device["id"]))
    return {"modelCode": code, "version": version}, None


def bind_package(c: sqlite3.Connection, pkg: sqlite3.Row, target_type: str, target_key: str, *,
                 actor: str, role: str, key: sqlite3.Row | None, trace_id: str) -> dict[str, Any]:
    """绑定设计包。调用方已 BEGIN IMMEDIATE，负责 commit/rollback；失败时本函数清理新建文件。"""
    from app import drawings as _drawings

    if target_type not in TARGET_TYPES:
        raise ApiError(400, CODE_INVALID, "targetType 必须是 DEVICE 或 MATERIAL", trace_id=trace_id)
    device = None
    if target_type == "DEVICE":
        device = resolve_device(c, target_key, trace_id)
        if not _device_in_scope(key, device["id"]):
            raise ApiError(403, CODE_DEVICE_FORBIDDEN, "该密钥无权操作此机台", trace_id=trace_id)
        resolved = device["id"]
    else:
        m = c.execute("SELECT code FROM materials WHERE code=?", ((target_key or "").strip(),)).fetchone()
        if m is None:
            raise ApiError(404, CODE_MATERIAL_NOT_FOUND, "物料不存在", trace_id=trace_id)
        resolved = m["code"]

    existing = c.execute("SELECT result_json FROM design_package_bindings WHERE package_id=? AND target_type=? AND target_key=?",
                         (pkg["id"], target_type, resolved)).fetchone()
    if existing is not None:
        return {"bound": True, "alreadyBound": True, "packageId": pkg["id"], "targetType": target_type,
                "targetKey": resolved, **json.loads(existing["result_json"] or "{}")}

    user = {"id": actor, "role": role}
    files = c.execute("SELECT path, kind, sha256, size FROM design_package_files WHERE package_id=? ORDER BY path",
                      (pkg["id"],)).fetchall()
    root = package_dir(pkg["id"]) / "files"
    created_drawings: list[str] = []
    created_paths: list[Path] = []
    drawings_out: list[dict[str, Any]] = []
    warnings: list[str] = []
    model = None
    label = pkg["project_name"] or pkg["project_id"]
    try:
        for f in files:
            src = root / f["path"]
            if f["kind"] in DRAWING_KINDS:
                mapped = c.execute("SELECT d.id, d.status FROM design_package_drawings p JOIN drawings d ON d.id=p.drawing_id"
                                   " WHERE p.package_id=? AND p.path=?", (pkg["id"], f["path"])).fetchone()
                if mapped is None:
                    if not src.is_file():
                        warnings.append(f"{f['path']}：归档文件缺失")
                        continue
                    tmp_dir = Path(_main.UPLOAD_DIR) / ".tmp"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    tmp = tmp_dir / ("dsync_" + uuid.uuid4().hex)
                    shutil.copyfile(src, tmp)
                    try:
                        res = _drawings.process_upload(
                            c, data_path=tmp, original_name=Path(f["path"]).name, title=f"{label} · {Path(f['path']).name}"[:120],
                            drawing_no=pkg["project_id"][:80], revision=f"V{pkg['version']}", user=user)
                    except ValueError as e:
                        warnings.append(f"{f['path']}：{e}")
                        continue
                    finally:
                        tmp.unlink(missing_ok=True)
                    created_drawings.append(res["id"])
                    c.execute("INSERT INTO design_package_drawings(package_id, path, drawing_id, created_at) VALUES(?,?,?,?)",
                              (pkg["id"], f["path"], res["id"], _main.now()))
                    did, dstatus = res["id"], res["status"]
                else:
                    did, dstatus = mapped["id"], mapped["status"]
                _drawings.bind(c, did, target_type, [resolved], user)
                drawings_out.append({"drawingId": did, "path": f["path"], "status": dstatus})
                if dstatus != "READY":
                    warnings.append(f"{f['path']}：图纸未能转换（{dstatus}），终端暂不显示")
            elif f["kind"] == "GLTF":
                if target_type != "DEVICE":
                    continue
                if model is not None:
                    warnings.append(f"{f['path']}：已注册其他模型，仅归档")
                    continue
                if not f["path"].lower().endswith(".glb"):
                    warnings.append(f"{f['path']}：仅支持 .glb 注册为 3D 模型，仅归档")
                    continue
                model, warn = _register_glb(c, src, f["sha256"], f["size"], device, label, actor, created_paths)
                if warn:
                    warnings.append(warn)
        result = {"drawings": drawings_out, "model": model, "warnings": warnings}
        c.execute("INSERT INTO design_package_bindings(id, package_id, target_type, target_key, result_json, created_by, created_at)"
                  " VALUES(?,?,?,?,?,?,?)", ("dpb_" + uuid.uuid4().hex, pkg["id"], target_type, resolved,
                                              json.dumps(result, ensure_ascii=False), actor, _main.now()))
        _main.audit(c, actor, role, "DESIGN_PACKAGE_BIND", "DESIGN_PACKAGE", pkg["id"], "SUCCESS", trace_id)
    except Exception:
        for did in created_drawings:
            _drawings.delete_drawing_files(did)
        for p in created_paths:
            shutil.rmtree(p, ignore_errors=True)
        raise
    return {"bound": True, "alreadyBound": False, "packageId": pkg["id"], "targetType": target_type,
            "targetKey": resolved, **result}


# -------------------------------------------------------------------- API

def _json(status: int, payload: dict[str, Any], trace_id: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={**payload, "traceId": trace_id})


@router.post("/api/v1/design-sync/packages")
async def upload_package(request: Request):
    trace_id = _trace(request)
    key = authenticate(request, trace_id)
    idem = (request.headers.get("idempotency-key") or "").strip().lower()
    if not _SHA_RE.fullmatch(idem):
        raise ApiError(400, "VALIDATION_ERROR", "Idempotency-Key 必须是 zip 的 SHA-256（64 位十六进制）", trace_id=trace_id)
    form = await _read_multipart(request, trace_id)
    upload = form.get("file")
    try:
        if upload is None or isinstance(upload, str):
            raise ApiError(400, CODE_INVALID, "缺少 file（zip）", trace_id=trace_id)
        device_raw, manifest_raw = form.get("deviceId"), form.get("manifest")
        if not isinstance(device_raw, str) or not device_raw.strip():
            raise ApiError(400, CODE_INVALID, "缺少 deviceId", trace_id=trace_id)
        if not isinstance(manifest_raw, str) or not manifest_raw.strip():
            raise ApiError(400, CODE_INVALID, "缺少 manifest", trace_id=trace_id)
        if len(manifest_raw.encode("utf-8")) > MANIFEST_MAX_BYTES:
            raise ApiError(413, CODE_TOO_LARGE, "manifest 超过 1 MB 上限", trace_id=trace_id)
        fp = upload.file
        fp.seek(0, io.SEEK_END)
        zip_size = fp.tell()
        if zip_size > ZIP_MAX_BYTES:
            raise ApiError(413, CODE_TOO_LARGE, f"设计包超过 {ZIP_MAX_BYTES // (1024 * 1024)} MB 上限", trace_id=trace_id)
        c = _main.db()
        try:
            device = resolve_device(c, device_raw, trace_id)
        finally:
            c.close()
        if not _device_in_scope(key, device["id"]):
            raise ApiError(403, CODE_DEVICE_FORBIDDEN, "该密钥无权操作此机台", trace_id=trace_id)
        manifest = validate_manifest(manifest_raw, trace_id)
        fp.seek(0)
        zip_sha, _ = _sha_stream(fp)
        if zip_sha != idem:
            raise ApiError(400, CODE_IDEMPOTENCY_MISMATCH, "Idempotency-Key 与 zip 的 SHA-256 不一致", trace_id=trace_id)

        def duplicate_response(c: sqlite3.Connection, prior: sqlite3.Row) -> JSONResponse:
            if prior["device_id"] != device["id"]:
                raise ApiError(409, CODE_DEVICE_CONFLICT, "相同设计包已上传到其他机台，请用 bind 绑定", trace_id=trace_id,
                               details={"packageId": prior["id"]})
            return _json(200, {"packageId": prior["id"], "version": prior["version"], "uploadedAt": prior["uploaded_at"],
                               "duplicated": True, "deviceId": device["id"], "deviceNo": device["device_no"],
                               "projectId": prior["project_id"]}, trace_id)

        c = _main.db()
        try:
            prior = c.execute("SELECT * FROM design_packages WHERE zip_sha256=?", (zip_sha,)).fetchone()
            if prior is not None:
                return duplicate_response(c, prior)
        finally:
            c.close()

        fp.seek(0)
        inspect_zip(fp, manifest, trace_id)

        package_id = "dsp_" + uuid.uuid4().hex
        dest = package_dir(package_id)
        try:
            dest.mkdir(parents=True, exist_ok=False)
            fp.seek(0)
            _extract(fp, manifest, dest)
            fp.seek(0)
            with (dest / "package.zip").open("wb") as out:
                shutil.copyfileobj(fp, out, 1 << 20)
            ts = _main.now()
            bom = manifest["bom"]
            with _main.tx("设计包入库失败", trace_id) as c:
                prior = c.execute("SELECT * FROM design_packages WHERE zip_sha256=?", (zip_sha,)).fetchone()
                if prior is not None:
                    shutil.rmtree(dest, ignore_errors=True)
                    return duplicate_response(c, prior)
                version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM design_packages WHERE device_id=? AND project_id=?",
                                    (device["id"], manifest["projectId"])).fetchone()[0]
                c.execute(
                    "INSERT INTO design_packages(id, device_id, project_id, project_name, version, schema_version, app_version,"
                    " manifest_created_at, manifest_json, bom_json, bom_sha256, zip_sha256, zip_size, file_count, uploaded_by,"
                    " uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (package_id, device["id"], manifest["projectId"], manifest.get("projectName") or "", version,
                     manifest["schemaVersion"], manifest.get("appVersion") or "", manifest["createdAt"],
                     json.dumps(manifest, ensure_ascii=False), json.dumps(bom, ensure_ascii=False),
                     (manifest.get("bomSha256") or "").lower(), zip_sha, zip_size, len(manifest["files"]),
                     actor_id(key), ts))
                c.executemany("INSERT INTO design_package_files(package_id, path, kind, sha256, size) VALUES(?,?,?,?,?)",
                              [(package_id, f["path"], f["kind"], f["sha256"], f["size"]) for f in manifest["files"]])
                _main.audit(c, actor_id(key), ACTOR_ROLE, "DESIGN_PACKAGE_UPLOAD", "DESIGN_PACKAGE", package_id,
                            "SUCCESS", trace_id)
                _event(c, request, event_type="DESIGN_PACKAGE_UPLOADED", entity_type="DESIGN_PACKAGE",
                       entity_id=package_id, actor=actor_id(key), role=ACTOR_ROLE, device_id=device["id"],
                       trace_id=trace_id, operation_id=zip_sha,
                       after={"deviceNo": device["device_no"], "projectId": manifest["projectId"], "version": version,
                              "zipSha256": zip_sha, "zipSize": zip_size, "fileCount": len(manifest["files"])})
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return _json(201, {"packageId": package_id, "version": version, "uploadedAt": ts, "duplicated": False,
                           "deviceId": device["id"], "deviceNo": device["device_no"],
                           "projectId": manifest["projectId"]}, trace_id)
    finally:
        for _, v in form.multi_items():
            if not isinstance(v, str):
                await v.close()


@router.get("/api/v1/design-sync/packages")
def list_packages(request: Request, deviceId: str | None = None, projectId: str | None = None,
                  limit: int = Query(20, ge=1, le=LIST_LIMIT_MAX)):
    trace_id = _trace(request)
    key = authenticate(request, trace_id)
    c = _main.db()
    try:
        where, args = [], []
        if deviceId:
            device = resolve_device(c, deviceId, trace_id)
            if not _device_in_scope(key, device["id"]):
                raise ApiError(403, CODE_DEVICE_FORBIDDEN, "该密钥无权操作此机台", trace_id=trace_id)
            where.append("p.device_id=?")
            args.append(device["id"])
        scope = _split_scope(key["device_scope"])
        if scope:
            where.append("p.device_id IN (%s)" % ",".join("?" * len(scope)))
            args.extend(scope)
        if projectId:
            where.append("p.project_id=?")
            args.append(projectId)
        sql = ("SELECT p.*, d.device_no FROM design_packages p LEFT JOIN devices d ON d.id=p.device_id"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY p.uploaded_at DESC, p.version DESC LIMIT ?")
        rows = c.execute(sql, args + [limit]).fetchall()
    finally:
        c.close()
    return {"items": [_row_payload(r, r["device_no"]) for r in rows], "traceId": trace_id}


@router.get("/api/v1/design-sync/packages/{package_id}")
def get_package(package_id: str, request: Request):
    trace_id = _trace(request)
    key = authenticate(request, trace_id)
    c = _main.db()
    try:
        return {**package_detail(c, _load_package(c, package_id, key, trace_id)), "traceId": trace_id}
    finally:
        c.close()


@router.get("/api/v1/design-sync/packages/{package_id}/diff")
def get_diff(package_id: str, request: Request, against: str = "latest"):
    trace_id = _trace(request)
    key = authenticate(request, trace_id)
    c = _main.db()
    try:
        row = _load_package(c, package_id, key, trace_id)
        return {**diff_package(c, row, against, key, trace_id), "traceId": trace_id}
    finally:
        c.close()


@router.get("/api/v1/design-sync/packages/{package_id}/download")
def download_package(package_id: str, request: Request):
    trace_id = _trace(request)
    key = authenticate(request, trace_id)
    c = _main.db()
    try:
        row = _load_package(c, package_id, key, trace_id)
    finally:
        c.close()
    return _zip_response(row, trace_id)


def _zip_response(row: sqlite3.Row, trace_id: str) -> FileResponse:
    p = package_dir(row["id"]) / "package.zip"
    if not p.is_file():
        raise ApiError(404, CODE_PACKAGE_NOT_FOUND, "设计包原件缺失", trace_id=trace_id)
    return FileResponse(str(p), media_type="application/zip", filename=f"{row['project_id']}-v{row['version']}.zip",
                        headers={"ETag": f'"{row["zip_sha256"]}"'})


@router.post("/api/v1/design-sync/bind")
async def bind(request: Request):
    trace_id = _trace(request)
    key = authenticate(request, trace_id)
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise ApiError(400, CODE_INVALID, "请求体必须是 JSON", trace_id=trace_id) from None
    if not isinstance(body, dict) or not all(isinstance(body.get(k), str) for k in ("packageId", "targetType", "targetKey")):
        raise ApiError(400, CODE_INVALID, "需要 packageId、targetType、targetKey", trace_id=trace_id)
    with _main.tx("绑定失败", trace_id) as c:
        pkg = _load_package(c, body["packageId"], key, trace_id)
        res = bind_package(c, pkg, body["targetType"], body["targetKey"], actor=actor_id(key), role=ACTOR_ROLE,
                           key=key, trace_id=trace_id)
        if not res["alreadyBound"]:
            _event(c, request, event_type="DESIGN_PACKAGE_BOUND", entity_type="DESIGN_PACKAGE", entity_id=pkg["id"],
                   actor=actor_id(key), role=ACTOR_ROLE, device_id=pkg["device_id"], trace_id=trace_id,
                   operation_id=str(uuid.uuid4()),
                   after={"targetType": res["targetType"], "targetKey": res["targetKey"],
                          "drawingIds": [d["drawingId"] for d in res["drawings"]], "model": res["model"]})
    return _json(200, res, trace_id)


# -------------------------------------------------------------- admin web

ADMIN_NOTICES: dict[str, tuple[str, str]] = {
    "bound": ("notice", "已绑定"),
    "already_bound": ("notice", "该设计包此前已绑定到这个目标，无需重复绑定"),
    "bind_failed": ("error", "绑定失败：目标不存在、类型无效或文件处理出错"),
    "key_updated": ("notice", "密钥设置已保存"),
    "key_revoked": ("notice", "密钥已吊销，使用它的客户端将立即无法上传"),
    "key_bad_scope": ("error", "来源网段格式无效（每行一个 CIDR，如 192.168.1.0/24）"),
    "key_bad_device": ("error", "机台范围里有不存在的机台编号"),
    "key_not_found": ("error", "密钥不存在"),
}


def _notice_ctx(qp) -> dict[str, str | None]:
    kind, text = ADMIN_NOTICES.get(str(qp.get("notice", "") or ""), ("", ""))
    return {"notice_text": text if kind == "notice" and text else None,
            "notice_error": text if kind == "error" and text else None}


def _admin(request: Request):
    from app.admin_web import _admin_or_403
    return _admin_or_403(request)


def _csrf_fail(request: Request, form, user):
    from app.admin_web import _csrf_ok, _error_page
    if not _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"]):
        return _error_page(request, 403, "CSRF 校验失败")
    return None


def _redirect(path: str, notice: str):
    from app.admin_web import _see_other
    assert notice in ADMIN_NOTICES
    return _see_other(f"{path}?notice={quote(notice)}")


def _keys_view(c: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for k in c.execute("SELECT * FROM design_keys ORDER BY revoked_at IS NOT NULL, created_at DESC").fetchall():
        ids = _split_scope(k["device_scope"])
        nos = []
        for i in ids:
            r = c.execute("SELECT device_no FROM devices WHERE id=?", (i,)).fetchone()
            nos.append(r["device_no"] if r else i)
        out.append({"id": k["id"], "prefix": KEY_PREFIX + k["key_prefix"], "label": k["label"],
                    "device_scope": ", ".join(nos), "ip_scope": "\n".join(_split_scope(k["ip_scope"])),
                    "created_at": k["created_at"], "revoked_at": k["revoked_at"], "last_used_at": k["last_used_at"]})
    return out


def _render_index(request: Request, user, *, new_key: str | None = None, form_error: str | None = None,
                  status_code: int = 200):
    device_q = str(request.query_params.get("device", "")).strip()
    project_q = str(request.query_params.get("project", "")).strip()
    c = _main.db()
    try:
        where, args = [], []
        if device_q:
            where.append("(d.device_no LIKE ? OR p.device_id=?)")
            args += [f"%{device_q}%", device_q]
        if project_q:
            where.append("(p.project_id LIKE ? OR p.project_name LIKE ?)")
            args += [f"%{project_q}%", f"%{project_q}%"]
        rows = c.execute(
            "SELECT p.*, d.device_no, d.device_name, (SELECT COUNT(*) FROM design_package_bindings b WHERE b.package_id=p.id)"
            " AS bind_count FROM design_packages p LEFT JOIN devices d ON d.id=p.device_id"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY p.uploaded_at DESC LIMIT 200", args).fetchall()
        keys = _keys_view(c)
    finally:
        c.close()
    packages = [{**_row_payload(r, r["device_no"]), "device_name": r["device_name"],
                 "zip_mb": round(r["zip_size"] / 1048576, 2), "bind_count": r["bind_count"]} for r in rows]
    from app.admin_web import templates
    ctx = {"user": user, "csrf_token": user["csrf_token"], "packages": packages, "keys": keys,
           "device_q": device_q, "project_q": project_q, "new_key": new_key,
           "default_ip_scope": "\n".join(DEFAULT_IP_SCOPE), **_notice_ctx(request.query_params)}
    if form_error:
        ctx["notice_error"] = form_error
    return templates.TemplateResponse(request, "design_packages.html", ctx, status_code=status_code,
                                      headers={"Cache-Control": "no-store"})


@router.get("/admin/design-packages", response_class=HTMLResponse)
def admin_index(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    return _render_index(request, user)


@router.get("/admin/design-packages/{package_id}", response_class=HTMLResponse)
def admin_detail(package_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    trace_id = _trace(request)
    c = _main.db()
    try:
        row = _load_package(c, package_id, None, trace_id)
        detail = package_detail(c, row)
        diff = diff_package(c, row, "previous", None, trace_id)
    finally:
        c.close()
    from app.admin_web import templates
    return templates.TemplateResponse(request, "design_package_detail.html", {
        "user": user, "csrf_token": user["csrf_token"], "p": detail, "diff": diff, **_notice_ctx(request.query_params),
    })


@router.get("/admin/design-packages/{package_id}/download")
def admin_download(package_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    trace_id = _trace(request)
    c = _main.db()
    try:
        row = _load_package(c, package_id, None, trace_id)
    finally:
        c.close()
    return _zip_response(row, trace_id)


@router.post("/admin/design-packages/{package_id}/bind")
async def admin_bind(package_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if (fail := _csrf_fail(request, form, user)) is not None:
        return fail
    trace_id = _trace(request)
    path = f"/admin/design-packages/{package_id}" if _PKG_ID_RE.fullmatch(package_id) else "/admin/design-packages"
    try:
        with _main.tx("绑定失败", trace_id) as c:
            pkg = _load_package(c, package_id, None, trace_id)
            res = bind_package(c, pkg, str(form.get("target_type", "")), str(form.get("target_key", "")),
                               actor=user["id"], role=user["role"], key=None, trace_id=trace_id)
    except ApiError:
        return _redirect(path, "bind_failed")
    return _redirect(path, "already_bound" if res["alreadyBound"] else "bound")


def _resolve_device_scope(c: sqlite3.Connection, text: str) -> list[str] | None:
    from app.drawings import resolve_device_keys, split_keys
    ids, missing = resolve_device_keys(c, split_keys(text))
    return None if missing else list(dict.fromkeys(ids))


@router.post("/admin/design-packages/keys/create")
async def admin_key_create(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if (fail := _csrf_fail(request, form, user)) is not None:
        return fail
    label = str(form.get("label", "")).strip()[:80]
    try:
        ip_scope = parse_ip_scope(str(form.get("ip_scope", "")).strip() or DEFAULT_IP_SCOPE)
    except ValueError as e:
        return _render_index(request, user, form_error=str(e), status_code=400)
    c = _main.db()
    try:
        devices = _resolve_device_scope(c, str(form.get("device_scope", "")))
        if devices is None:
            return _render_index(request, user, form_error=ADMIN_NOTICES["key_bad_device"][1], status_code=400)
        for _ in range(5):
            prefix, plain = generate_key()
            if not c.execute("SELECT 1 FROM design_keys WHERE key_prefix=?", (prefix,)).fetchone():
                break
        key_id = "dk_" + secrets.token_hex(12)
        c.execute("INSERT INTO design_keys(id, key_prefix, key_hash, label, device_scope, ip_scope, created_by, created_at)"
                  " VALUES(?,?,?,?,?,?,?,?)", (key_id, prefix, _main.hash_password(plain), label, ",".join(devices),
                                               ",".join(ip_scope), user["id"], _main.now()))
        _main.audit(c, user["id"], user["role"], "DESIGN_KEY_CREATE", "DESIGN_KEY", key_id, "SUCCESS",
                    _trace(request))
        c.commit()
    finally:
        c.close()
    # 明文只在本次响应里出现一次（不放进跳转 URL，不落库、不进日志）
    return _render_index(request, user, new_key=plain)


@router.post("/admin/design-packages/keys/{key_id}/update")
async def admin_key_update(key_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if (fail := _csrf_fail(request, form, user)) is not None:
        return fail
    if not _KEY_ID_RE.fullmatch(key_id):
        return _redirect("/admin/design-packages", "key_not_found")
    try:
        # 空值回落默认网段，与 key 创建时的语义保持一致
        ip_scope = parse_ip_scope(str(form.get("ip_scope", "")).strip() or DEFAULT_IP_SCOPE)
    except ValueError:
        return _redirect("/admin/design-packages", "key_bad_scope")
    c = _main.db()
    try:
        devices = _resolve_device_scope(c, str(form.get("device_scope", "")))
        if devices is None:
            return _redirect("/admin/design-packages", "key_bad_device")
        cur = c.execute("UPDATE design_keys SET label=?, device_scope=?, ip_scope=? WHERE id=?",
                        (str(form.get("label", "")).strip()[:80], ",".join(devices), ",".join(ip_scope), key_id))
        if not cur.rowcount:
            return _redirect("/admin/design-packages", "key_not_found")
        _verify_cache_drop(key_id)
        _main.audit(c, user["id"], user["role"], "DESIGN_KEY_UPDATE", "DESIGN_KEY", key_id, "SUCCESS",
                    _trace(request))
        c.commit()
    finally:
        c.close()
    return _redirect("/admin/design-packages", "key_updated")


@router.post("/admin/design-packages/keys/{key_id}/revoke")
async def admin_key_revoke(key_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if (fail := _csrf_fail(request, form, user)) is not None:
        return fail
    c = _main.db()
    try:
        cur = c.execute("UPDATE design_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (_main.now(), key_id))
        if cur.rowcount:
            _verify_cache_drop(key_id)
            _main.audit(c, user["id"], user["role"], "DESIGN_KEY_REVOKE", "DESIGN_KEY", key_id, "SUCCESS",
                        _trace(request))
        c.commit()
    finally:
        c.close()
    return _redirect("/admin/design-packages", "key_revoked")
