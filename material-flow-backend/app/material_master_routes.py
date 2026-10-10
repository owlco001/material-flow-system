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


def _redirect(path: str, notice: str = ""):
    from app.admin_web import _see_other
    return _see_other(path + (f"?notice={quote(notice)}" if notice else ""))


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
        "facets": fc, "batches": batches, "notice": qp.get("notice"),
        "max_mb": mm.MAX_UPLOAD_BYTES // 1048576,
    })


@router.post("/admin/material-master/import")
async def admin_import(request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf(form, user):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read") or not getattr(upload, "filename", ""):
        return _redirect("/admin/material-master", "请选择 .xlsx 文件")
    if not upload.filename.lower().endswith(".xlsx"):
        return _redirect("/admin/material-master", "仅支持 .xlsx 文件")
    try:
        tmp, _ = await _save_upload(upload)
        out = await run_in_threadpool(_preview_sync, tmp, upload.filename, user["id"])
    except mm.TemplateError as e:
        return _redirect("/admin/material-master", str(e))
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
        return _redirect("/admin/material-master", "批次不存在")
    return _tpl(request, "material_master_preview.html", {
        "user": user, "csrf_token": user["csrf_token"], "b": mm.batch_view(r),
        "notice": request.query_params.get("notice"), "op_id": str(uuid.uuid4()),
    })


@router.post("/admin/material-master/import/{batch_id}/commit")
async def admin_commit(batch_id: str, request: Request):
    user, denied = _admin(request)
    if denied:
        return denied
    form = await request.form()
    if not _csrf(form, user):
        return HTMLResponse("CSRF 校验失败", status_code=403)
    skip = str(form.get("skip_invalid", "")) in ("1", "on", "true")
    try:
        op = str(uuid.UUID(str(form.get("op_id", ""))))
    except ValueError:
        op = str(uuid.uuid4())
    payload = json.dumps({"clientOperationId": op, "previewId": batch_id, "skipInvalid": skip}, sort_keys=True)
    try:
        res = await run_in_threadpool(_commit_sync, batch_id, user, op, payload, skip, str(uuid.uuid4()))
    except ApiError as e:
        return _redirect(f"/admin/material-master/import/{batch_id}", str(e.detail))
    except ValueError:
        return _redirect("/admin/material-master", "批次不存在")
    msg = (f"已导入：主档新增 {res['masterInserted']}、更新 {res['masterUpdated']}、未变化 {res['masterUnchanged']}；"
           f"物料新建 {res['materialsCreated']}、同步 {res['materialsUpdated']}")
    return _redirect(f"/admin/material-master/import/{batch_id}", msg)
