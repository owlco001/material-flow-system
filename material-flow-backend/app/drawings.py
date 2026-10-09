"""机台图纸 / 物料图纸绑定。

大图纸不在终端做 SVG 渲染：上传时在服务器上一次性处理——
- PDF 按页拆成单页 PDF（Ghostscript 可用时再压缩，只在变小时采用）；
- 每页生成 WebP 缩略图（长边 480px）；
- DWG/DXF 需服务器配置转换命令 DRAWING_CAD_TO_PDF（如 ODA File Converter + 打印脚本），
  命令模板里用 {in} / {out} 占位；未配置时标记为 UNSUPPORTED，提示先在 CAD 中打印为 PDF。
终端按页下载单页 PDF，用系统 PdfRenderer 渲染（缩放时按比例重渲，线条始终清晰）。

存储：{UPLOAD_DIR}/drawings/{drawing_id}/original.<ext>、pages/{n}.pdf、thumbs/{n}.webp
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app import main as _main
from app.main import current_user

router = APIRouter()

MAX_DRAWING_BYTES = 200 * 1024 * 1024
MAX_PAGES = 500
THUMB_LONG_EDGE = 480
_KINDS = {".pdf": "PDF", ".dxf": "DXF", ".dwg": "DWG"}
TARGET_TYPES = ("DEVICE", "MATERIAL")


# ----------------------------------------------------------------- storage

def drawing_dir(drawing_id: str) -> Path:
    if not drawing_id.startswith("drw_") or not drawing_id[4:].isalnum():
        raise HTTPException(404, "图纸不存在")
    return Path(_main.UPLOAD_DIR) / "drawings" / drawing_id


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -------------------------------------------------------------- processing

def _gs_optimize(src: Path) -> None:
    """Ghostscript 重写 PDF（去重字体/图片、压缩），只有变小才替换。"""
    if os.environ.get("DRAWING_GS_OPTIMIZE", "1") == "0":
        return
    gs = shutil.which("gs")
    if not gs:
        return
    tmp = src.with_suffix(".gs.pdf")
    try:
        subprocess.run(
            [gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
             "-dCompatibilityLevel=1.6", "-dPDFSETTINGS=/printer",
             "-dDetectDuplicateImages=true", "-dCompressFonts=true",
             f"-sOutputFile={tmp}", str(src)],
            check=True, timeout=180, capture_output=True,
        )
        if tmp.is_file() and 0 < tmp.stat().st_size < src.stat().st_size:
            os.replace(tmp, src)
    except (subprocess.SubprocessError, OSError):
        pass
    finally:
        tmp.unlink(missing_ok=True)


def split_pdf(src: Path, out_dir: Path) -> list[dict[str, Any]]:
    """拆页 + 缩略图。返回每页的尺寸/大小/哈希。PDF 无效或加密时抛 ValueError。"""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(src))
    except pdfium.PdfiumError as e:
        raise ValueError(f"无法打开 PDF（可能已加密或损坏）：{e}") from e
    try:
        n = len(pdf)
        if n == 0:
            raise ValueError("PDF 没有页面")
        if n > MAX_PAGES:
            raise ValueError(f"页数 {n} 超过上限 {MAX_PAGES}")
        (out_dir / "pages").mkdir(parents=True, exist_ok=True)
        (out_dir / "thumbs").mkdir(parents=True, exist_ok=True)
        pages: list[dict[str, Any]] = []
        for i in range(n):
            no = i + 1
            page_path = out_dir / "pages" / f"{no}.pdf"
            single = pdfium.PdfDocument.new()
            try:
                single.import_pages(pdf, [i])
                single.save(str(page_path))
            finally:
                single.close()
            _gs_optimize(page_path)

            page = pdf[i]
            try:
                w, h = page.get_size()
                scale = THUMB_LONG_EDGE / max(w, h, 1)
                img = page.render(scale=scale).to_pil().convert("RGB")
                img.save(out_dir / "thumbs" / f"{no}.webp", "WEBP", quality=70, method=4)
            finally:
                page.close()
            pages.append({
                "page": no,
                "widthPt": round(w, 1),
                "heightPt": round(h, 1),
                "sizeBytes": page_path.stat().st_size,
                "sha256": _sha256_file(page_path),
            })
        return pages
    finally:
        pdf.close()


def _cad_to_pdf(src: Path, out: Path) -> str | None:
    """调用服务器配置的 CAD→PDF 转换命令。返回错误信息，成功返回 None。"""
    tpl = os.environ.get("DRAWING_CAD_TO_PDF", "").strip()
    if not tpl:
        return "服务器未配置 CAD 转换器：请在 CAD 中「打印为 PDF」后上传，或由管理员配置 DRAWING_CAD_TO_PDF"
    cmd = [part.replace("{in}", str(src)).replace("{out}", str(out)) for part in shlex.split(tpl)]
    try:
        subprocess.run(cmd, check=True, timeout=600, capture_output=True)
    except (subprocess.SubprocessError, OSError) as e:
        return f"CAD 转换失败：{type(e).__name__}"
    if not out.is_file() or out.stat().st_size == 0:
        return "CAD 转换失败：未生成 PDF"
    return None


def process_upload(c: sqlite3.Connection, *, data_path: Path, original_name: str, title: str,
                   drawing_no: str, revision: str, user: sqlite3.Row) -> dict[str, Any]:
    """保存原件、转换、写库。返回 drawings 行字典。调用方负责 commit。"""
    ext = Path(original_name).suffix.lower()
    kind = _KINDS.get(ext)
    if kind is None:
        raise ValueError("只支持 PDF、DWG、DXF 文件")
    if kind == "PDF":
        with data_path.open("rb") as f:
            if not f.read(1024).lstrip().startswith(b"%PDF"):
                raise ValueError("文件不是有效的 PDF")

    did = "drw_" + uuid.uuid4().hex
    ddir = drawing_dir(did)
    ddir.mkdir(parents=True, exist_ok=True)
    original = ddir / f"original{ext}"
    shutil.move(str(data_path), original)
    sha = _sha256_file(original)
    size = original.stat().st_size

    status, error, pages = "READY", None, []
    pdf_src = original
    if kind != "PDF":
        pdf_src = ddir / "converted.pdf"
        error = _cad_to_pdf(original, pdf_src)
        if error:
            status = "UNSUPPORTED" if "未配置" in error else "FAILED"
    if status == "READY":
        try:
            pages = split_pdf(pdf_src, ddir)
        except ValueError as e:
            status, error = "FAILED", str(e)
        except Exception as e:  # pdfium 内部异常不应让上传 500
            status, error = "FAILED", f"转换失败：{type(e).__name__}"
    if pdf_src != original:
        pdf_src.unlink(missing_ok=True)

    ts = _main.now()
    c.execute(
        "INSERT INTO drawings(id, title, drawing_no, revision, original_name, file_kind, original_sha256,"
        " original_size, status, page_count, pages_json, error, created_by, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (did, title, drawing_no, revision, original_name[:200], kind, sha, size, status,
         len(pages), json.dumps(pages), error, user["id"], ts),
    )
    _main.audit(c, user["id"], user["role"], "DRAWING_UPLOAD", "DRAWING", did,
                "SUCCESS" if status == "READY" else status, "web-admin")
    return {"id": did, "status": status, "error": error, "page_count": len(pages)}


def delete_drawing_files(drawing_id: str) -> None:
    shutil.rmtree(drawing_dir(drawing_id), ignore_errors=True)


# --------------------------------------------------------------- bindings

def resolve_device_keys(c: sqlite3.Connection, raw: list[str]) -> tuple[list[str], list[str]]:
    """机台编号/ID → devices.id。返回 (ids, 未找到的输入)。"""
    ids, missing = [], []
    for v in raw:
        row = c.execute("SELECT id FROM devices WHERE id=? OR device_no=?", (v, v)).fetchone()
        (ids.append(row["id"]) if row else missing.append(v))
    return ids, missing


def resolve_material_keys(c: sqlite3.Connection, raw: list[str]) -> tuple[list[str], list[str]]:
    codes, missing = [], []
    for v in raw:
        row = c.execute("SELECT code FROM materials WHERE code=?", (v,)).fetchone()
        (codes.append(row["code"]) if row else missing.append(v))
    return codes, missing


def bind(c: sqlite3.Connection, drawing_id: str, target_type: str, keys: list[str], user: sqlite3.Row) -> int:
    added = 0
    ts = _main.now()
    for k in dict.fromkeys(keys):
        cur = c.execute(
            "INSERT OR IGNORE INTO drawing_bindings(id, drawing_id, target_type, target_key, created_by, created_at)"
            " VALUES(?,?,?,?,?,?)",
            ("dbd_" + uuid.uuid4().hex, drawing_id, target_type, k, user["id"], ts),
        )
        added += cur.rowcount
    if added:
        _main.audit(c, user["id"], user["role"], "DRAWING_BIND", "DRAWING", drawing_id, "SUCCESS", "web-admin")
    return added


def split_keys(text: str) -> list[str]:
    out = []
    for part in text.replace("，", ",").replace("\n", ",").replace(" ", ",").split(","):
        part = part.strip()
        if part:
            out.append(part[:80])
    return out


# ------------------------------------------------------------------- API

def _drawing_payload(r: sqlite3.Row) -> dict[str, Any]:
    pages = json.loads(r["pages_json"] or "[]")
    base = f"/api/v1/drawings/{r['id']}"
    return {
        "drawingId": r["id"],
        "title": r["title"],
        "drawingNo": r["drawing_no"],
        "revision": r["revision"],
        "fileKind": r["file_kind"],
        "status": r["status"],
        "pageCount": r["page_count"],
        "createdAt": r["created_at"],
        "thumbUrl": f"{base}/pages/1/thumb" if pages else None,
        "pages": [
            {**p, "url": f"{base}/pages/{p['page']}", "thumbUrl": f"{base}/pages/{p['page']}/thumb"}
            for p in pages
        ],
    }


def _list_for(target_type: str, key: str) -> list[dict[str, Any]]:
    c = _main.db()
    try:
        rows = c.execute(
            "SELECT d.* FROM drawing_bindings b JOIN drawings d ON d.id=b.drawing_id"
            " WHERE b.target_type=? AND b.target_key=? AND d.status='READY'"
            " ORDER BY d.drawing_no, d.created_at DESC",
            (target_type, key),
        ).fetchall()
    finally:
        c.close()
    return [_drawing_payload(r) for r in rows]


@router.get("/api/v1/devices/{device_id}/drawings")
def device_drawings(device_id: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = _main.db()
    try:
        d = c.execute("SELECT id, device_no FROM devices WHERE id=? OR device_no=?", (device_id, device_id)).fetchone()
    finally:
        c.close()
    if not d:
        raise HTTPException(404, "机台不存在")
    return {"deviceId": d["id"], "deviceNo": d["device_no"], "drawings": _list_for("DEVICE", d["id"])}


@router.get("/api/v1/materials/{code}/drawings")
def material_drawings(code: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    c = _main.db()
    try:
        m = c.execute("SELECT code FROM materials WHERE code=?", (code,)).fetchone()
    finally:
        c.close()
    if not m:
        raise HTTPException(404, "物料不存在")
    return {"materialCode": m["code"], "drawings": _list_for("MATERIAL", m["code"])}


def _ready_drawing(drawing_id: str) -> sqlite3.Row:
    ddir = drawing_dir(drawing_id)  # 校验 ID 格式
    c = _main.db()
    try:
        r = c.execute("SELECT * FROM drawings WHERE id=?", (drawing_id,)).fetchone()
    finally:
        c.close()
    if not r or r["status"] != "READY" or not ddir.is_dir():
        raise HTTPException(404, "图纸不存在")
    return r


def _page_file(drawing_id: str, page: int, thumb: bool) -> Path:
    r = _ready_drawing(drawing_id)
    if page < 1 or page > r["page_count"]:
        raise HTTPException(404, "页码不存在")
    p = drawing_dir(drawing_id) / ("thumbs" if thumb else "pages") / (f"{page}.webp" if thumb else f"{page}.pdf")
    if not p.is_file():
        raise HTTPException(404, "页面文件缺失")
    return p


# 内容按 drawing_id 不可变（改图需重新上传），终端可长期缓存
_CACHE = {"Cache-Control": "private, max-age=2592000, immutable"}


@router.get("/api/v1/drawings/{drawing_id}")
def drawing_detail(drawing_id: str, user: sqlite3.Row = Depends(current_user)) -> dict[str, Any]:
    return _drawing_payload(_ready_drawing(drawing_id))


@router.get("/api/v1/drawings/{drawing_id}/pages/{page}")
def drawing_page(drawing_id: str, page: int, user: sqlite3.Row = Depends(current_user)):
    return FileResponse(str(_page_file(drawing_id, page, False)), media_type="application/pdf", headers=_CACHE)


@router.get("/api/v1/drawings/{drawing_id}/pages/{page}/thumb")
def drawing_thumb(drawing_id: str, page: int, user: sqlite3.Row = Depends(current_user)):
    return FileResponse(str(_page_file(drawing_id, page, True)), media_type="image/webp", headers=_CACHE)


# ------------------------------------------------------------- admin web

def _admin(request: Request):
    from app.admin_web import _admin_or_403
    return _admin_or_403(request)


def _check_csrf(form, user) -> bool:
    from app.admin_web import _csrf_ok
    return _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"])


def _redirect(notice: str, drawing_id: str | None = None):
    from urllib.parse import quote
    from app.admin_web import _see_other
    anchor = f"#{drawing_id}" if drawing_id else ""
    return _see_other(f"/admin/drawings?notice={quote(notice)}{anchor}")


@router.get("/admin/drawings", response_class=HTMLResponse)
def admin_drawings(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    q = str(request.query_params.get("q", "")).strip()
    c = _main.db()
    try:
        where, args = "", []
        if q:
            where = (" WHERE d.title LIKE ? OR d.drawing_no LIKE ? OR d.id IN ("
                     "SELECT b.drawing_id FROM drawing_bindings b LEFT JOIN devices v"
                     " ON b.target_type='DEVICE' AND v.id=b.target_key"
                     " WHERE b.target_key LIKE ? OR v.device_no LIKE ?)")
            like = f"%{q}%"
            args = [like] * 4
        rows = c.execute(f"SELECT d.* FROM drawings d{where} ORDER BY d.created_at DESC LIMIT 200", args).fetchall()
        binds = c.execute(
            "SELECT b.*, v.device_no, v.device_name, m.name AS material_name FROM drawing_bindings b"
            " LEFT JOIN devices v ON b.target_type='DEVICE' AND v.id=b.target_key"
            " LEFT JOIN materials m ON b.target_type='MATERIAL' AND m.code=b.target_key"
            " ORDER BY b.target_type, b.target_key"
        ).fetchall()
    finally:
        c.close()
    by_drawing: dict[str, list] = {}
    for b in binds:
        label = (f"{b['device_no']} {b['device_name'] or ''}".strip() if b["target_type"] == "DEVICE"
                 else f"{b['target_key']} {b['material_name'] or ''}".strip())
        by_drawing.setdefault(b["drawing_id"], []).append(
            {"id": b["id"], "type": b["target_type"], "label": label or b["target_key"]})
    drawings = []
    for r in rows:
        pages = json.loads(r["pages_json"] or "[]")
        drawings.append({
            "id": r["id"], "title": r["title"], "drawing_no": r["drawing_no"], "revision": r["revision"],
            "original_name": r["original_name"], "file_kind": r["file_kind"], "status": r["status"],
            "error": r["error"], "page_count": r["page_count"], "created_at": r["created_at"],
            "original_mb": round(r["original_size"] / 1048576, 1),
            "pages_mb": round(sum(p["sizeBytes"] for p in pages) / 1048576, 1),
            "max_page_mb": round(max((p["sizeBytes"] for p in pages), default=0) / 1048576, 1),
            "bindings": by_drawing.get(r["id"], []),
        })
    from app.admin_web import templates
    return templates.TemplateResponse(request, "drawings.html", {
        "user": user, "csrf_token": user["csrf_token"], "drawings": drawings, "q": q,
        "notice": request.query_params.get("notice"),
        "cad_converter": bool(os.environ.get("DRAWING_CAD_TO_PDF", "").strip()),
    })


@router.post("/admin/drawings/upload")
async def admin_drawing_upload(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _check_csrf(form, user):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read") or not getattr(upload, "filename", ""):
        return _redirect("请选择图纸文件")
    title = str(form.get("title", "")).strip()[:120] or Path(upload.filename).stem[:120]
    drawing_no = str(form.get("drawing_no", "")).strip()[:80]
    revision = str(form.get("revision", "")).strip()[:20]

    tmp_dir = Path(_main.UPLOAD_DIR) / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / ("drawing_" + uuid.uuid4().hex)
    size = 0
    try:
        with tmp.open("wb") as f:
            while chunk := await upload.read(1 << 20):
                size += len(chunk)
                if size > MAX_DRAWING_BYTES:
                    return _redirect("文件超过 200 MB 上限")
                f.write(chunk)
        c = _main.db()
        try:
            try:
                res = process_upload(c, data_path=tmp, original_name=upload.filename, title=title,
                                     drawing_no=drawing_no, revision=revision, user=user)
            except ValueError as e:
                return _redirect(str(e))
            dev_ids, dev_missing = resolve_device_keys(c, split_keys(str(form.get("devices", ""))))
            mat_codes, mat_missing = resolve_material_keys(c, split_keys(str(form.get("materials", ""))))
            bind(c, res["id"], "DEVICE", dev_ids, user)
            bind(c, res["id"], "MATERIAL", mat_codes, user)
            c.commit()
        finally:
            c.close()
    finally:
        tmp.unlink(missing_ok=True)

    if res["status"] == "READY":
        msg = f"已上传，共 {res['page_count']} 页"
    else:
        msg = f"已保存原件，但未能转换：{res['error']}"
    if dev_missing or mat_missing:
        msg += "；未找到：" + "、".join(dev_missing + mat_missing)
    return _redirect(msg, res["id"])


@router.post("/admin/drawings/{drawing_id}/bind")
async def admin_drawing_bind(drawing_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _check_csrf(form, user):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    target_type = str(form.get("target_type", ""))
    if target_type not in TARGET_TYPES:
        return _redirect("绑定类型无效", drawing_id)
    c = _main.db()
    try:
        if not c.execute("SELECT 1 FROM drawings WHERE id=?", (drawing_id,)).fetchone():
            return _redirect("图纸不存在")
        raw = split_keys(str(form.get("keys", "")))
        resolver = resolve_device_keys if target_type == "DEVICE" else resolve_material_keys
        keys, missing = resolver(c, raw)
        added = bind(c, drawing_id, target_type, keys, user)
        c.commit()
    finally:
        c.close()
    msg = f"已绑定 {added} 项"
    if missing:
        msg += "；未找到：" + "、".join(missing)
    return _redirect(msg, drawing_id)


@router.post("/admin/drawings/{drawing_id}/unbind")
async def admin_drawing_unbind(drawing_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _check_csrf(form, user):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    c = _main.db()
    try:
        cur = c.execute("DELETE FROM drawing_bindings WHERE id=? AND drawing_id=?",
                        (str(form.get("binding_id", "")), drawing_id))
        if cur.rowcount:
            _main.audit(c, user["id"], user["role"], "DRAWING_UNBIND", "DRAWING", drawing_id, "SUCCESS", "web-admin")
        c.commit()
    finally:
        c.close()
    return _redirect("已解除绑定", drawing_id)


@router.post("/admin/drawings/{drawing_id}/delete")
async def admin_drawing_delete(drawing_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _check_csrf(form, user):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    drawing_dir(drawing_id)
    c = _main.db()
    try:
        c.execute("DELETE FROM drawing_bindings WHERE drawing_id=?", (drawing_id,))
        cur = c.execute("DELETE FROM drawings WHERE id=?", (drawing_id,))
        if cur.rowcount:
            _main.audit(c, user["id"], user["role"], "DRAWING_DELETE", "DRAWING", drawing_id, "SUCCESS", "web-admin")
        c.commit()
    finally:
        c.close()
    delete_drawing_files(drawing_id)
    return _redirect("图纸已删除")


def _admin_file(request: Request, drawing_id: str, page: int, thumb: bool):
    user, denied = _admin(request)
    if denied:
        return denied
    return FileResponse(str(_page_file(drawing_id, page, thumb)),
                        media_type="image/webp" if thumb else "application/pdf", headers=_CACHE)


@router.get("/admin/drawings/{drawing_id}/pages/{page}")
def admin_drawing_page(drawing_id: str, page: int, request: Request):
    return _admin_file(request, drawing_id, page, False)


@router.get("/admin/drawings/{drawing_id}/pages/{page}/thumb")
def admin_drawing_thumb(drawing_id: str, page: int, request: Request):
    return _admin_file(request, drawing_id, page, True)


@router.get("/admin/drawings/{drawing_id}/original")
def admin_drawing_original(drawing_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    c = _main.db()
    try:
        r = c.execute("SELECT original_name, file_kind FROM drawings WHERE id=?", (drawing_id,)).fetchone()
    finally:
        c.close()
    if not r:
        raise HTTPException(404, "图纸不存在")
    p = drawing_dir(drawing_id) / f"original.{r['file_kind'].lower()}"
    if not p.is_file():
        raise HTTPException(404, "原件缺失")
    return FileResponse(str(p), filename=r["original_name"])
