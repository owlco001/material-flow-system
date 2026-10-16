"""U9 集成管理接口（Phase 1 只读同步）。

  GET  /api/v1/u9/status            配置状态（脱敏）+ 最近同步记录
  POST /api/v1/u9/health            测试连接（U9C 授权登录 + 耗时）
  GET  /api/v1/u9/orders/lookup     按年份查生产订单号（只读，供 sync/orders 用）
  POST /api/v1/u9/sync/{entity}    触发同步，entity ∈ items/boms/inventory/orders
                                   dry_run=true（默认，仅预览不写库）
                                   items:     codes（可选，逗号分隔）+ include_local（默认 true=本地全部物料编码）
                                   boms:      product_codes（必填）
                                   inventory: codes（可选）/ wh（可选，默认 U9_DEFAULT_WH）/ update_totals
                                              / scope=local（默认，本地全部料号逐个查）|all（组织整体查）
                                   orders:    doc_nos（可选）+ include_local（默认 true=本地已有订单号）
  GET  /api/v1/u9/logs             同步日志（分页）
  GET  /api/v1/u9/logs/{log_id}    单次同步详情（含预览样例）

全部接口要求 ADMIN。U9 未启用/未配置时返回 503 中文说明，不抛技术栈。
"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Query

try:
    from app.main import ApiError, CODE_FORBIDDEN, current_user, db
    from app.u9.client import U9Error, get_client
    from app.u9.config import get_config
    from app.u9.sync import ENTITIES, U9SyncBusy, lookup_orders_by_year, run_sync
except ImportError:  # 直接以 app 目录为 cwd 运行时的兜底
    from main import ApiError, CODE_FORBIDDEN, current_user, db
    from u9.client import U9Error, get_client
    from u9.config import get_config
    from u9.sync import ENTITIES, U9SyncBusy, lookup_orders_by_year, run_sync

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
                       "U9 连接信息不完整，缺少：" + "、".join(cfg.missing_fields()))
    return cfg


def build_sync_kwargs(entity: str, *, codes: str = "", product_codes: str = "", doc_nos: str = "",
                      wh: str = "", include_local: bool = True, update_totals: bool = True,
                      user_id: str = "", scope: str = "local") -> dict:
    """把接口/表单参数整理成 run_sync 的 kwargs；BOM 缺编码时抛 ApiError(400)。"""
    split = lambda v: [p.strip() for p in (v or "").replace("\n", ",").replace("，", ",").split(",") if p.strip()]
    if entity == "items":
        return {"codes": split(codes), "include_local": include_local}
    if entity == "boms":
        pc = split(product_codes)
        if not pc:
            raise ApiError(400, "U9_BOM_NEEDS_CODES", "同步 BOM 需指定 product_codes（产品/设备编码）")
        return {"product_codes": pc, "user_id": user_id}
    if entity == "inventory":
        return {"codes": split(codes), "wh": wh.strip(), "update_totals": update_totals,
                "scope": "all" if (scope or "").strip().lower() == "all" else "local"}
    if entity == "orders":
        return {"doc_nos": split(doc_nos), "include_local": include_local}
    return {}


@router.post("/api/v1/u9/health")
def u9_health(user: sqlite3.Row = Depends(current_user)):
    _admin(user)
    cfg = _require_ready()
    try:
        return get_client(cfg).health_check()
    except U9Error as e:
        raise ApiError(502, "U9_UPSTREAM_ERROR", str(e))


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
            "note": "U9C REST 只读同步（料品/BOM/库存/生产订单）" if cfg.mode == "u9c"
                    else "经典 U9 接口待实施商提供 WSDL/字段文档后实现，当前为占位状态"}


@router.post("/api/v1/u9/sync/{entity}")
def u9_sync(entity: str,
            dry_run: bool = Query(default=True, description="true=仅预览不写库"),
            product_codes: str = Query(default="", description="boms：逗号分隔的产品/设备编码"),
            codes: str = Query(default="", description="items/inventory：逗号分隔的料号"),
            doc_nos: str = Query(default="", description="orders：逗号分隔的生产订单号"),
            wh: str = Query(default="", description="inventory：存储地点编码"),
            include_local: bool = Query(default=True, description="items/orders：是否包含本地已有编码/单号"),
            update_totals: bool = Query(default=True, description="inventory：是否回写物料总量/可用量"),
            scope: str = Query(default="local", description="inventory：local=按本地全部料号逐个查（默认）；all=按组织整体查"),
            user: sqlite3.Row = Depends(current_user)):
    _admin(user)
    cfg = _require_ready()
    if entity not in ENTITIES:
        raise ApiError(400, "U9_UNKNOWN_ENTITY", f"未知同步实体: {entity}")
    kwargs = build_sync_kwargs(entity, codes=codes, product_codes=product_codes, doc_nos=doc_nos,
                               wh=wh, include_local=include_local, update_totals=update_totals,
                               user_id=user["id"], scope=scope)
    c = db()
    try:
        return run_sync(entity, c, cfg, dry_run=dry_run, **kwargs)
    except ApiError:
        raise
    except U9SyncBusy as e:
        raise ApiError(409, "U9_SYNC_BUSY", str(e))
    except U9Error as e:
        raise ApiError(502, "U9_UPSTREAM_ERROR", f"U9 同步失败：{e}")
    except Exception as e:  # noqa: BLE001 - 转中文业务报错，不吞真因
        raise ApiError(500, "U9_SYNC_FAILED", f"U9 同步异常：{e}")
    finally:
        c.close()


@router.get("/api/v1/u9/orders/lookup")
def u9_orders_lookup(year: int = Query(..., description="年份，如 2026"),
                     limit: int = Query(default=2000, ge=1, le=5000,
                                        description="从 U9 取最近 N 个单号再按年份过滤"),
                     doc_prefix: str = Query(default="",
                                             description="单号前缀过滤，如 JSBY-26B"),
                     user: sqlite3.Row = Depends(current_user)):
    """按年份查生产订单号（只读）：返回该年订单的 doc_nos 与产品料号，
    可直接喂给 POST /api/v1/u9/sync/orders?doc_nos=… 与 sync/boms。"""
    _admin(user)
    cfg = _require_ready()
    try:
        return lookup_orders_by_year(cfg, year, limit, doc_prefix=doc_prefix)
    except U9Error as e:
        raise ApiError(502, "U9_UPSTREAM_ERROR", f"U9 查询失败：{e}")
    except Exception as e:  # noqa: BLE001 - 转中文业务报错，不吞真因
        raise ApiError(500, "U9_SYNC_FAILED", f"U9 查询异常：{e}")


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


@router.get("/api/v1/u9/logs/{log_id}")
def u9_log_detail(log_id: str, user: sqlite3.Row = Depends(current_user)):
    _admin(user)
    c = db()
    try:
        if not c.execute("SELECT name FROM sqlite_master WHERE name='u9_sync_logs'").fetchone():
            raise ApiError(404, "U9_LOG_NOT_FOUND", "同步记录不存在")
        r = c.execute("SELECT * FROM u9_sync_logs WHERE id=?", (log_id,)).fetchone()
        if not r:
            raise ApiError(404, "U9_LOG_NOT_FOUND", "同步记录不存在")
        d = dict(r)
        try:
            d["detail"] = json.loads(d.pop("detail_json") or "{}")
        except ValueError:
            d["detail"] = {}
        return d
    finally:
        c.close()
