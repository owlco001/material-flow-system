"""U9 集成管理接口（Phase 1 只读同步）。

  GET  /api/v1/u9/status            配置状态（脱敏）+ 最近同步记录
  POST /api/v1/u9/sync/{entity}    触发同步，entity ∈ items/boms/inventory/orders
                                   参数：dry_run=true（默认，仅预览不写库）
  GET  /api/v1/u9/logs             同步日志（分页）

全部接口要求 ADMIN。U9 未启用/未配置时返回 503 中文说明，不抛技术栈。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query

try:
    from app.main import ApiError, CODE_FORBIDDEN, current_user, db
    from app.u9.config import get_config
    from app.u9.sync import ENTITIES, run_sync
except ImportError:  # 直接以 app 目录为 cwd 运行时的兜底
    from main import ApiError, CODE_FORBIDDEN, current_user, db
    from u9.config import get_config
    from u9.sync import ENTITIES, run_sync

router = APIRouter()


def _admin(user: sqlite3.Row) -> None:
    if user["role"] != "ADMIN":
        raise ApiError(403, CODE_FORBIDDEN, "仅 ADMIN 可操作 U9 集成")


def _require_ready() -> dict:
    cfg = get_config()
    if not cfg.enabled:
        raise ApiError(503, "U9_DISABLED",
                       "U9 集成未启用：请在服务端设置 U9_ENABLED=1 并配置连接信息后重启")
    if not cfg.configured:
        raise ApiError(503, "U9_NOT_CONFIGURED",
                       "U9 连接信息不完整：经典版需配 U9_WSDL_URL/U9_USERNAME/U9_PASSWORD，"
                       "云版需配 U9_BASE_URL/U9_APP_KEY/U9_APP_SECRET")
    return cfg


@router.get("/api/v1/u9/status")
def u9_status(user: sqlite3.Row = Depends(current_user)):
    _admin(user)
    cfg = get_config()
    c = db()
    try:
        rows = c.execute(
            "SELECT entity, status, dry_run, total, inserted, updated, skipped,"
            " started_at, finished_at, error FROM u9_sync_logs"
            " ORDER BY started_at DESC LIMIT 20").fetchall() \
            if c.execute("SELECT name FROM sqlite_master WHERE name='u9_sync_logs'").fetchone() \
            else []
    finally:
        c.close()
    return {"config": cfg.safe_dict(),
            "recent": [dict(r) for r in rows],
            "entities": list(ENTITIES),
            "phase": "1-只读同步",
            "note": "U9 接口待实施商提供 WSDL/字段文档后实现，当前为占位状态"}


@router.post("/api/v1/u9/sync/{entity}")
def u9_sync(entity: str,
            dry_run: bool = Query(default=True, description="true=仅预览不写库"),
            product_codes: str = Query(default="", description="boms 专用：逗号分隔的产品/设备编码"),
            user: sqlite3.Row = Depends(current_user)):
    _admin(user)
    cfg = _require_ready()
    if entity not in ENTITIES:
        raise ApiError(400, "U9_UNKNOWN_ENTITY", f"未知同步实体: {entity}")
    c = db()
    try:
        kwargs: dict = {}
        if entity == "boms":
            kwargs["product_codes"] = [p.strip() for p in product_codes.split(",") if p.strip()]
            if not kwargs["product_codes"]:
                raise ApiError(400, "U9_BOM_NEEDS_CODES", "同步 BOM 需指定 product_codes（产品/设备编码）")
        result = run_sync(entity, c, cfg, dry_run=dry_run, **kwargs)
        return result
    except ApiError:
        raise
    except Exception as e:  # noqa: BLE001 - 转中文业务报错，不吞真因
        raise ApiError(500, "U9_SYNC_FAILED", f"U9 同步异常：{e}")
    finally:
        c.close()


@router.get("/api/v1/u9/logs")
def u9_logs(limit: int = Query(default=50, le=200),
            offset: int = Query(default=0, ge=0),
            user: sqlite3.Row = Depends(current_user)):
    _admin(user)
    c = db()
    try:
        if not c.execute("SELECT name FROM sqlite_master WHERE name='u9_sync_logs'").fetchone():
            return {"items": [], "total": 0}
        total = c.execute("SELECT COUNT(*) FROM u9_sync_logs").fetchone()[0]
        rows = c.execute(
            "SELECT * FROM u9_sync_logs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
        return {"items": [dict(r) for r in rows], "total": total,
                "offset": offset, "limit": limit}
    finally:
        c.close()
