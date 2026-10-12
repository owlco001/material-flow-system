"""物料主档导入 / 查询接口。

终端/API（Bearer）：
  POST /api/v1/material-master/import/preview   multipart file → 预览（新增/变更/未变化/错误）
  POST /api/v1/material-master/import/commit    {previewId, clientOperationId, skipInvalid}，需 Idempotency-Key
  GET  /api/v1/material-master/import/batches   最近批次
  GET  /api/v1/material-master/import/batches/{id}
  GET  /api/v1/material-master                  搜索（q/itemForm/storageLocation/deviceNo/projectNo…）
  GET  /api/v1/material-master/facets           形态/存储地点/财务分类计数
  GET  /api/v1/materials/{code}/master          单个料号主档

管理台（Web Session，ADMIN）：
  GET  /admin/material-master                   查询 + 最近批次 + 上传
  POST /admin/material-master/import            上传 → 预览页
  GET  /admin/material-master/import/{id}       预览/结果页
  POST /admin/material-master/import/{id}/commit
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import main as _main
from app import material_master as mm
from app.main import ApiError, CODE_FORBIDDEN, current_user

router = APIRouter()


def _trace(x_request_id: str | None) -> str:
    return _main._safe_trace_id(x_request_id) or str(uuid.uuid4())


def _require_import_role(user: sqlite3.Row, trace_id: str) -> None:
    if user["role"] not in mm.IMPORT_ROLES:
        raise ApiError(403, CODE_FORBIDDEN, "无权导入物料主档", trace_id=trace_id)


def _template_error(e: mm.TemplateError, trace_id: str) -> ApiError:
    status = {"MATERIAL_MASTER_FILE_TOO_LARGE": 413, "MATERIAL_MASTER_PREVIEW_EXPIRED": 409,
              "MATERIAL_MASTER_PREVIEW_STALE": 409}.get(e.code, 422)
    return ApiError(status, e.code, str(e), trace_id=trace_id, details=e.details)


async def _save_upload(upload: Any) -> tuple[Path, int]:
    tmp_dir = Path(_main.DATA_DIR) / "imports" / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / ("mm_" + uuid.uuid4().hex + ".xlsx")
    size = 0
    try:
        with tmp.open("wb") as f:
            while chunk := await upload.read(1 << 20):
                size += len(chunk)
                if size > mm.MAX_UPLOAD_BYTES:
                    raise mm.TemplateError("MATERIAL_MASTER_FILE_TOO_LARGE",
                                           f"文件超过 {mm.MAX_UPLOAD_BYTES // 1048576} MB")
                f.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, size


def _preview_sync(tmp: Path, file_name: str, user_id: str) -> dict[str, Any]:
    c = _main.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        mm.cleanup_expired(c, _main.DATA_DIR)
        out = mm.create_preview(c, _main.DATA_DIR, tmp_path=tmp, file_name=file_name, user_id=user_id)
        c.commit()
        return out
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()
        tmp.unlink(missing_ok=True)


def _commit_sync(batch_id: str, user: sqlite3.Row, operation_id: str, payload: str, skip_invalid: bool,
                 trace_id: str) -> dict[str, Any]:
    c = _main.db()
    try:
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute("SELECT * FROM material_master_import_operations WHERE client_operation_id=?",
                          (operation_id,)).fetchone()
        if prior:
            c.rollback()
            if prior["created_by"] != user["id"] or prior["payload_json"] != payload:
                raise ApiError(409, _main.CODE_IDEMPOTENCY_PAYLOAD_MISMATCH, "相同幂等键的请求体不一致",
                               trace_id=trace_id)
            result = json.loads(prior["result_json"])
            result.update(idempotent=True, traceId=trace_id)
            return result
        result = mm.commit_batch(c, _main.DATA_DIR, batch_id=batch_id, user_id=user["id"],
                                 skip_invalid=skip_invalid)
        ts = _main.now()
        c.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,request_id,"
            "client_operation_id,before_json,after_json,server_time,device_id,source_ip,result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("MATERIAL_MASTER_IMPORTED", "MATERIAL_MASTER_IMPORT", batch_id, user["id"], user["role"], trace_id,
             operation_id, "{}", json.dumps(result, ensure_ascii=False), ts, None, None, "SUCCESS"),
        )
        c.execute("INSERT INTO material_master_import_operations VALUES(?,?,?,?,?,?)",
                  (operation_id, batch_id, user["id"], payload, json.dumps(result, ensure_ascii=False), ts))
        c.commit()
    except mm.TemplateError as e:
        c.rollback()
        raise _template_error(e, trace_id) from None
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()
    mm.finish_commit_files(_main.DATA_DIR, batch_id)
    return {**result, "traceId": trace_id, "idempotent": False}


# ============================================================== API
@router.post("/api/v1/material-master/import/preview")
async def api_preview(file: UploadFile = File(...), user: sqlite3.Row = Depends(current_user),
                      x_request_id: str | None = Header(default=None)) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    _require_import_role(user, trace_id)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise ApiError(422, "MATERIAL_MASTER_TEMPLATE_INVALID", "仅支持 .xlsx 文件", trace_id=trace_id)
    try:
        tmp, _size = await _save_upload(file)
        out = await run_in_threadpool(_preview_sync, tmp, file.filename or "upload.xlsx", user["id"])
    except mm.TemplateError as e:
        raise _template_error(e, trace_id) from None
    return {**out, "traceId": trace_id, "serverTime": _main.now()}


class CommitRequest(BaseModel):
    previewId: str
    clientOperationId: uuid.UUID
    skipInvalid: bool = False


@router.post("/api/v1/material-master/import/commit")
async def api_commit(body: CommitRequest, user: sqlite3.Row = Depends(current_user),
                     x_request_id: str | None = Header(default=None),
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    _require_import_role(user, trace_id)
    _main.require_idempotency_key(idempotency_key, body.clientOperationId, trace_id)
    payload = json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    try:
        return await run_in_threadpool(_commit_sync, body.previewId, user, str(body.clientOperationId), payload,
                                       body.skipInvalid, trace_id)
    except ValueError as e:  # bad batch id
        if isinstance(e, mm.TemplateError):
            raise _template_error(e, trace_id) from None
        raise ApiError(409, "MATERIAL_MASTER_PREVIEW_EXPIRED", "预览不存在", trace_id=trace_id) from None


@router.get("/api/v1/material-master/import/batches")
def api_batches(limit: int = Query(default=20, ge=1, le=100), user: sqlite3.Row = Depends(current_user)):
    _require_import_role(user, "")
    c = _main.db()
    try:
        rows = c.execute("SELECT * FROM material_master_import_batches ORDER BY created_at DESC LIMIT ?",
                         (limit,)).fetchall()
    finally:
        c.close()
    return {"items": [mm.batch_view(r, with_details=False) for r in rows]}


@router.get("/api/v1/material-master/import/batches/{batch_id}")
def api_batch(batch_id: str, user: sqlite3.Row = Depends(current_user)):
    _require_import_role(user, "")
    c = _main.db()
    try:
        r = c.execute("SELECT * FROM material_master_import_batches WHERE id=?", (batch_id,)).fetchone()
    finally:
        c.close()
    if r is None:
        raise ApiError(404, "NOT_FOUND", "批次不存在")
    return mm.batch_view(r)


@router.get("/api/v1/material-master")
def api_search(q: str = "", itemForm: str = "", storageLocation: str = "", mainCategoryCode: str = "",
               deviceNo: str = "", projectNo: str = "",
               limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
               user: sqlite3.Row = Depends(current_user)):
    c = _main.db()
    try:
        total, rows = mm.search(c, q=q.strip(), item_form=itemForm, storage_location=storageLocation,
                                main_category_code=mainCategoryCode, device_no=deviceNo, project_no=projectNo,
                                limit=limit, offset=offset)
    finally:
        c.close()
    inc = user["role"] == "ADMIN"
    return {"total": total, "items": [mm.master_view(r, include_cost=inc) for r in rows]}


@router.get("/api/v1/material-master/facets")
def api_facets(user: sqlite3.Row = Depends(current_user)):
    c = _main.db()
    try:
        total = c.execute("SELECT COUNT(*) FROM material_master").fetchone()[0]
        return {"total": total, **mm.facets(c)}
    finally:
        c.close()


@router.get("/api/v1/materials/{code}/master")
def api_material_master(code: str, user: sqlite3.Row = Depends(current_user)):
    c = _main.db()
    try:
        r = c.execute("SELECT * FROM material_master WHERE code=?", (code,)).fetchone()
    finally:
        c.close()
    if r is None:
        raise ApiError(404, "NOT_FOUND", "该料号没有主档信息")
    return mm.master_view(r, include_cost=user["role"] == "ADMIN")


# ============================================================== 管理台
def _admin(request: Request):
    from app.admin_web import _admin_or_403
    return _admin_or_403(request)


def _csrf(form, user) -> bool:
    from app.admin_web import _csrf_ok
    return _csrf_ok(str(form.get("csrf_token", "")), user["csrf_token"])


# 页面提示一律用「代码 → 固定文案」（v0.6.3，评审 P2-12）：不再把任意文本塞进 ?notice= 原样回显，
# 防止构造链接伪造提示。动态数字只接受整数参数。
MM_NOTICES: dict[str, tuple[str, str]] = {
    "no_file": ("error", "请选择 .xlsx 文件"),
    "bad_type": ("error", "仅支持 .xlsx 文件"),
    "tpl_header": ("error", "未找到物料主档表头（需要「料号」「品名」列）"),
    "tpl_parse": ("error", "XLSX 文件无法解析"),
    "tpl_unit": ("error", "缺少库存单位列（「库存单位.名称」或「库存主单位」）"),
    "tpl_invalid": ("error", "文件不符合物料主档模板"),
    "too_many_rows": ("error", f"行数超过 {mm.MAX_ROWS}"),
    "too_large": ("error", "文件过大"),
    "empty": ("error", "表头之后没有数据行"),
    "preview_expired": ("error", "预览不存在、已过期或已提交，请重新上传"),
    "preview_lost": ("error", "预览文件已丢失，请重新上传"),
    "no_valid_rows": ("error", "没有可导入的有效行"),
    "has_invalid": ("error", "有 {n} 行错误；修正后重新上传，或勾选「跳过错误行」"),
    "stale": ("error", "数据已变化，请重新预览"),
    "batch_not_found": ("error", "批次不存在"),
    "import_failed": ("error", "导入失败，请重试"),
    "imported": ("notice", "已导入：主档新增 {mi}、更新 {mu}、未变化 {mn}；物料新建 {mc}、同步 {ms}"),
}
_MM_MESSAGE_CODES = (
    ("未找到物料主档表头", "tpl_header"), ("XLSX 文件无法解析", "tpl_parse"), ("缺少库存单位列", "tpl_unit"),
    ("行数超过", "too_many_rows"), ("表头之后没有数据行", "empty"), ("预览文件已丢失", "preview_lost"),
    ("预览不存在", "preview_expired"), ("预览已过期", "preview_expired"), ("没有可导入的有效行", "no_valid_rows"),
    ("数据已变化", "stale"), ("仅支持 .xlsx", "bad_type"),
)
_MM_CODE_FALLBACK = {
    "MATERIAL_MASTER_TEMPLATE_INVALID": "tpl_invalid", "MATERIAL_MASTER_FILE_TOO_LARGE": "too_large",
    "MATERIAL_MASTER_EMPTY": "empty", "MATERIAL_MASTER_PREVIEW_EXPIRED": "preview_expired",
    "MATERIAL_MASTER_PREVIEW_STALE": "stale", "MATERIAL_MASTER_VALIDATION_FAILED": "no_valid_rows",
}


def _mm_error_notice(code: str, message: str) -> tuple[str, dict[str, int]]:
    """业务异常（code + 本项目固定文案）→ 提示代码 + 整数参数。"""
    import re as _re
    m = _re.match(r"有 (\d+) 行错误", message or "")
    if m:
        return "has_invalid", {"n": int(m.group(1))}
    for prefix, notice in _MM_MESSAGE_CODES:
        if (message or "").startswith(prefix):
            return notice, {}
    return _MM_CODE_FALLBACK.get(code, "import_failed"), {}


def mm_notice_ctx(query_params) -> dict[str, str | None]:
    code = str(query_params.get("notice", "") or "")
    kind, text = MM_NOTICES.get(code, ("", ""))
    if not text:
        return {"notice_text": None, "notice_error": None}
    if "{" in text:
        values = {}
        for key in ("n", "mi", "mu", "mn", "mc", "ms"):
            raw = str(query_params.get(key, "0") or "0")
            values[key] = int(raw) if raw.isdigit() and len(raw) <= 9 else 0
        text = text.format(**values)
    return {"notice_text": text if kind == "notice" else None, "notice_error": text if kind == "error" else None}


def _redirect(path: str, notice: str = "", **params: int):
    from app.admin_web import _see_other
    if notice and notice not in MM_NOTICES:
        raise ValueError(f"unknown material-master notice code: {notice}")
    qs = "&".join([f"notice={quote(notice)}"] + [f"{k}={int(v)}" for k, v in params.items()]) if notice else ""
    return _see_other(path + (f"?{qs}" if qs else ""))


def _tpl(request: Request, name: str, ctx: dict[str, Any]):
    from app.admin_web import templates
    return templates.TemplateResponse(request, name, ctx)


@router.get("/admin/material-master", response_class=HTMLResponse)
def admin_page(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    qp = request.query_params
    q = str(qp.get("q", "")).strip()
    form = str(qp.get("form", "")).strip()
    storage = str(qp.get("storage", "")).strip()
    try:
        page = max(1, int(qp.get("page", "1")))
    except ValueError:
        page = 1
    size = 50
    c = _main.db()
    try:
        total, rows = mm.search(c, q=q, item_form=form, storage_location=storage, limit=size,
                                offset=(page - 1) * size)
        fc = mm.facets(c)
        batches = [mm.batch_view(r, with_details=False) for r in c.execute(
            "SELECT * FROM material_master_import_batches ORDER BY created_at DESC LIMIT 10").fetchall()]
        grand = c.execute("SELECT COUNT(*) FROM material_master").fetchone()[0]
    finally:
        c.close()
    return _tpl(request, "material_master.html", {
        "user": user, "csrf_token": user["csrf_token"], "rows": rows, "total": total, "grand_total": grand,
        "q": q, "form": form, "storage": storage, "page": page, "pages": max(1, (total + size - 1) // size),
        "facets": fc, "batches": batches, **mm_notice_ctx(qp),
        "max_mb": mm.MAX_UPLOAD_BYTES // 1048576,
    })


@router.post("/admin/material-master/import")
async def admin_import(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf(form, user):
        from app.admin_web import _error_page
        return _error_page(request, 403, "CSRF 校验失败")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read") or not getattr(upload, "filename", ""):
        return _redirect("/admin/material-master", "no_file")
    if not upload.filename.lower().endswith(".xlsx"):
        return _redirect("/admin/material-master", "bad_type")
    try:
        tmp, _ = await _save_upload(upload)
        out = await run_in_threadpool(_preview_sync, tmp, upload.filename, user["id"])
    except mm.TemplateError as e:
        notice, params = _mm_error_notice(getattr(e, "code", ""), str(e))
        return _redirect("/admin/material-master", notice, **params)
    return _redirect(f"/admin/material-master/import/{out['batchId']}")


@router.get("/admin/material-master/import/{batch_id}", response_class=HTMLResponse)
def admin_batch(batch_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    c = _main.db()
    try:
        r = c.execute("SELECT * FROM material_master_import_batches WHERE id=?", (batch_id,)).fetchone()
    finally:
        c.close()
    if r is None:
        return _redirect("/admin/material-master", "batch_not_found")
    return _tpl(request, "material_master_preview.html", {
        "user": user, "csrf_token": user["csrf_token"], "b": mm.batch_view(r),
        **mm_notice_ctx(request.query_params), "op_id": str(uuid.uuid4()),
    })


@router.post("/admin/material-master/import/{batch_id}/commit")
async def admin_commit(batch_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf(form, user):
        from app.admin_web import _error_page
        return _error_page(request, 403, "CSRF 校验失败")
    skip = str(form.get("skip_invalid", "")) in ("1", "on", "true")
    try:
        op = str(uuid.UUID(str(form.get("op_id", ""))))
    except ValueError:
        op = str(uuid.uuid4())
    payload = json.dumps({"clientOperationId": op, "previewId": batch_id, "skipInvalid": skip}, sort_keys=True)
    try:
        res = await run_in_threadpool(_commit_sync, batch_id, user, op, payload, skip, str(uuid.uuid4()))
    except ApiError as e:
        notice, params = _mm_error_notice(e.code, str(e.detail))
        return _redirect(f"/admin/material-master/import/{batch_id}", notice, **params)
    except ValueError:
        return _redirect("/admin/material-master", "batch_not_found")
    return _redirect(f"/admin/material-master/import/{batch_id}", "imported",
                     mi=res["masterInserted"], mu=res["masterUpdated"], mn=res["masterUnchanged"],
                     mc=res["materialsCreated"], ms=res["materialsUpdated"])
