"""Agent HTTP 接口：数据分析对话 + 智能导入。

挂载方式与 admin_web 一致：由 app.main 在模块末尾
`from app.agent.routes import router` 后 include，避免循环导入。
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from pydantic import BaseModel, Field

from app.main import CODE_FORBIDDEN, ApiError, _safe_trace_id, current_user, db, now
from app.agent import importer as agent_importer
from app.agent import service as agent_service
from app.agent.config import AgentConfig
from app.agent.llm import LLMError

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 智能导入涉及写库：复用各业务导入的角色口径并集
AGENT_IMPORT_ROLES = {"ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR", "WAREHOUSE_ADMIN", "MATERIAL"}


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ImportCommitRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    client_operation_id: str = Field(min_length=1, max_length=128)


def _agent_cfg(trace_id: str) -> AgentConfig:
    cfg = AgentConfig.from_env()
    if not cfg.enabled:
        raise ApiError(503, "AGENT_DISABLED", "Agent 功能未启用", trace_id=trace_id)
    return cfg


def _check_import_role(user: sqlite3.Row, trace_id: str) -> None:
    if user["role"] not in AGENT_IMPORT_ROLES:
        raise ApiError(403, CODE_FORBIDDEN, "无权执行智能导入", trace_id=trace_id)


def _trace(x_request_id: str | None) -> str:
    return _safe_trace_id(x_request_id) or str(uuid.uuid4())


def _import_error(e: ValueError, trace_id: str) -> ApiError:
    msg = str(e)
    if "不存在" in msg:
        return ApiError(404, "AGENT_IMPORT_NOT_FOUND", msg, trace_id=trace_id)
    if "幂等键冲突" in msg:
        return ApiError(409, "AGENT_IDEMPOTENCY_CONFLICT", msg, trace_id=trace_id)
    if "校验错误" in msg:
        return ApiError(422, "AGENT_VALIDATION_FAILED", msg, trace_id=trace_id)
    return ApiError(400, "AGENT_BAD_REQUEST", msg, trace_id=trace_id)


@router.post("/api/v1/agent/chat")
def agent_chat(
    body: ChatRequest,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    cfg = _agent_cfg(trace_id)
    c = db()
    try:
        try:
            result = agent_service.run_chat(c, cfg, user["id"], body.session_id, body.message)
        except LLMError as e:
            raise ApiError(503, "AGENT_LLM_ERROR", str(e), retryable=True, trace_id=trace_id)
        except ValueError as e:
            raise ApiError(400, "AGENT_BAD_REQUEST", str(e), trace_id=trace_id)
    finally:
        c.close()
    result["traceId"] = trace_id
    result["serverTime"] = now()
    return result


@router.get("/api/v1/agent/sessions")
def agent_list_sessions(
    user: sqlite3.Row = Depends(current_user),
    limit: int = 20,
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    _agent_cfg(trace_id)
    c = db()
    try:
        sessions = agent_service.list_sessions(c, user["id"], max(1, min(limit, 50)))
    finally:
        c.close()
    return {"sessions": sessions, "traceId": trace_id, "serverTime": now()}


@router.get("/api/v1/agent/sessions/{session_id}/messages")
def agent_get_messages(
    session_id: str,
    user: sqlite3.Row = Depends(current_user),
    limit: int = 50,
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    _agent_cfg(trace_id)
    c = db()
    try:
        try:
            messages = agent_service.get_messages(c, user["id"], session_id, max(1, min(limit, 100)))
        except ValueError as e:
            raise ApiError(404, "AGENT_SESSION_NOT_FOUND", str(e), trace_id=trace_id)
    finally:
        c.close()
    return {"session_id": session_id, "messages": messages, "traceId": trace_id, "serverTime": now()}


@router.post("/api/v1/agent/import/preview")
async def agent_import_preview(
    file: UploadFile = File(...),
    target: str = Form(...),
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    cfg = _agent_cfg(trace_id)
    _check_import_role(user, trace_id)
    if target not in agent_importer.TARGETS:
        raise ApiError(
            400, "AGENT_BAD_TARGET",
            f"不支持的导入目标: {target}，可选: {', '.join(sorted(agent_importer.TARGETS))}",
            trace_id=trace_id,
        )
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "AGENT_FILE_TOO_LARGE", "文件超过 10MB", trace_id=trace_id)
    file_name = file.filename or "upload.bin"
    try:
        parsed = agent_importer.parse_upload(file_name, raw)
    except ValueError as e:
        raise ApiError(400, "AGENT_BAD_FILE", str(e), trace_id=trace_id)
    column_map = agent_importer.suggest_column_mapping(cfg, target, parsed["headers"])
    mapped_rows = agent_importer.apply_column_mapping(parsed, column_map)
    c = db()
    try:
        valid_rows, errors = agent_importer.validate_rows(target, mapped_rows, c)
        job_id = agent_importer.create_import_job(
            c, user["id"], target, file_name, raw, column_map, valid_rows, errors,
        )
    finally:
        c.close()
    return {
        "jobId": job_id,
        "target": target,
        "fileName": file_name,
        "fileSha256": hashlib.sha256(raw).hexdigest(),
        "columnMap": column_map,
        "totalRows": parsed["total_rows"],
        "validRows": len(valid_rows),
        "invalidRows": len(errors),
        "canCommit": not errors and bool(valid_rows),
        "errors": errors[:100],
        "preview": valid_rows[:20],
        "traceId": trace_id,
        "serverTime": now(),
    }


@router.get("/api/v1/agent/import/jobs/{job_id}")
def agent_import_job(
    job_id: str,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    _agent_cfg(trace_id)
    _check_import_role(user, trace_id)
    c = db()
    try:
        job = agent_importer.get_import_job(c, user["id"], job_id)
    finally:
        c.close()
    if job is None:
        raise ApiError(404, "AGENT_IMPORT_NOT_FOUND", "导入任务不存在", trace_id=trace_id)
    job["preview"] = job["rows"][:20]
    del job["rows"]
    job["traceId"] = trace_id
    job["serverTime"] = now()
    return job


@router.post("/api/v1/agent/import/commit")
def agent_import_commit(
    body: ImportCommitRequest,
    user: sqlite3.Row = Depends(current_user),
    x_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    trace_id = _trace(x_request_id)
    _agent_cfg(trace_id)
    _check_import_role(user, trace_id)
    c = db()
    try:
        try:
            result = agent_importer.commit_import_job(c, user["id"], body.job_id, body.client_operation_id)
        except ValueError as e:
            raise _import_error(e, trace_id)
    finally:
        c.close()
    result["traceId"] = trace_id
    result["serverTime"] = now()
    return result
