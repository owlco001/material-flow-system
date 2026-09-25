"""智慧工厂 Agent 分析工具集（只读）。

本模块定义供 LLM 通过 function calling 调用的数据分析工具。
所有工具均为只读：run() 仅执行 SELECT 语句，不修改任何数据；
返回值为可 JSON 序列化的数据（dict / list / str / int / float / None）。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable


class ToolError(Exception):
    """工具参数错误或执行失败时抛出。"""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str          # 中文描述，给 LLM 看
    parameters: dict          # JSON Schema
    run: Callable[[sqlite3.Connection, dict], Any]  # 只读，返回可 JSON 序列化结果


_MAX_LIMIT = 50


def _clamp_limit(args: dict, default: int = 20) -> int:
    """limit 参数钳制到 1..50。"""
    raw = args.get("limit", default)
    if raw is None:
        return default
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ToolError(f"limit 必须是整数，收到: {raw!r}")
    return max(1, min(_MAX_LIMIT, limit))


def _require(args: dict, key: str) -> Any:
    """取必填参数，缺失或为空时抛 ToolError。"""
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolError(f"缺少必填参数: {key}")
    return value


def _fetch_dicts(cur: sqlite3.Cursor) -> list[dict]:
    """把 cursor 结果转成 dict 列表（不改动 connection 的 row_factory）。"""
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_one(cur: sqlite3.Cursor) -> dict | None:
    rows = _fetch_dicts(cur)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# 工具实现（全部只读）
# ---------------------------------------------------------------------------

def _order_detail(conn: sqlite3.Connection, args: dict) -> dict:
    order_no = _require(args, "order_no")
    order = _fetch_one(conn.execute(
        "SELECT * FROM production_orders WHERE order_no = ?", (order_no,)))
    if order is None:
        raise ToolError(f"订单不存在: {order_no}")
    models = _fetch_dicts(conn.execute(
        """SELECT model_code, model_name, planned_quantity, bom_version_id, created_at
           FROM production_order_models WHERE order_id = ? ORDER BY created_at""",
        (order["id"],)))
    task_summary = _fetch_dicts(conn.execute(
        """SELECT status, COUNT(*) AS count FROM assembly_tasks
           WHERE order_no = ? GROUP BY status ORDER BY status""",
        (order_no,)))
    return {
        "order": order,
        "model_count": len(models),
        "models": models,
        "task_status_summary": task_summary,
    }


def _list_orders(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT order_no, product_name, status, created_at FROM production_orders
               WHERE status = ? ORDER BY created_at DESC LIMIT ?""",
            (status, limit))
    else:
        cur = conn.execute(
            """SELECT order_no, product_name, status, created_at FROM production_orders
               ORDER BY created_at DESC LIMIT ?""",
            (limit,))
    return _fetch_dicts(cur)


def _material_inventory(conn: sqlite3.Connection, args: dict) -> dict:
    code = _require(args, "code")
    material = _fetch_one(conn.execute(
        "SELECT * FROM materials WHERE code = ?", (code,)))
    if material is None:
        raise ToolError(f"物料不存在: {code}")
    locations = _fetch_dicts(conn.execute(
        """SELECT l.code AS location_code, l.name AS location_name, i.quantity
           FROM inventory i JOIN locations l ON l.id = i.location_id
           WHERE i.material_id = ? ORDER BY l.code""",
        (material["id"],)))
    return {
        "material": material,
        "locations": locations,
        "location_count": len(locations),
    }


def _low_stock(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    return _fetch_dicts(conn.execute(
        """SELECT code, name, specification, unit, total_quantity, available_quantity
           FROM materials ORDER BY available_quantity ASC LIMIT ?""",
        (limit,)))


def _workspace_summary(conn: sqlite3.Connection, args: dict) -> dict:
    def _count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "pending_transfer_requests": _count(
            "SELECT COUNT(*) FROM transfer_requests WHERE status = 'PENDING_APPROVAL'"),
        "pending_handovers": _count(
            "SELECT COUNT(*) FROM material_handovers WHERE status = 'PENDING'"),
        "open_tasks": _count(
            "SELECT COUNT(*) FROM assembly_tasks WHERE status <> 'COMPLETED'"),
        "open_exceptions": _count(
            "SELECT COUNT(*) FROM exceptions WHERE status = 'PENDING'"),
    }


def _list_transfer_requests(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT id, type, document_no, status, created_at FROM transfer_requests
               WHERE status = ? ORDER BY created_at DESC LIMIT ?""",
            (status, limit))
    else:
        cur = conn.execute(
            """SELECT id, type, document_no, status, created_at FROM transfer_requests
               ORDER BY created_at DESC LIMIT ?""",
            (limit,))
    return _fetch_dicts(cur)


def _list_handovers(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT id, work_item_id, quantity, status, created_at FROM material_handovers
               WHERE status = ? ORDER BY created_at DESC LIMIT ?""",
            (status, limit))
    else:
        cur = conn.execute(
            """SELECT id, work_item_id, quantity, status, created_at FROM material_handovers
               ORDER BY created_at DESC LIMIT ?""",
            (limit,))
    return _fetch_dicts(cur)


def _list_exceptions(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    status = args.get("status")
    if status:
        cur = conn.execute(
            """SELECT id, type, difference, status, description, created_at FROM exceptions
               WHERE status = ? ORDER BY created_at DESC LIMIT ?""",
            (status, limit))
    else:
        cur = conn.execute(
            """SELECT id, type, difference, status, description, created_at FROM exceptions
               ORDER BY created_at DESC LIMIT ?""",
            (limit,))
    return _fetch_dicts(cur)


def _task_progress(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    order_no = args.get("order_no")
    if order_no:
        cur = conn.execute(
            """SELECT order_no, device_no, status, progress_stage, updated_at
               FROM assembly_tasks WHERE order_no = ?
               ORDER BY updated_at DESC LIMIT ?""",
            (order_no, limit))
    else:
        cur = conn.execute(
            """SELECT order_no, device_no, status, progress_stage, updated_at
               FROM assembly_tasks ORDER BY updated_at DESC LIMIT ?""",
            (limit,))
    return _fetch_dicts(cur)


def _labor_summary(conn: sqlite3.Connection, args: dict) -> list:
    limit = _clamp_limit(args)
    return _fetch_dicts(conn.execute(
        """SELECT worker_user_id, type,
                  SUM(COALESCE(duration_minutes, 0)) AS total_minutes,
                  COUNT(*) AS record_count
           FROM labor_records
           WHERE started_at >= date('now', '-30 days')
           GROUP BY worker_user_id, type
           ORDER BY total_minutes DESC LIMIT ?""",
        (limit,)))


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

ANALYSIS_TOOLS: list[Tool] = [
    Tool(
        name="order_detail",
        description=(
            "查询单个生产订单的详情。返回订单整行记录、关联的产品模型数量与明细、"
            "以及该订单下装配任务按状态的汇总。参数 order_no 为必填的订单号。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "生产订单号，例如 PO-2026-001"},
            },
            "required": ["order_no"],
        },
        run=_order_detail,
    ),
    Tool(
        name="list_orders",
        description=(
            "列出生产订单，按创建时间倒序列出。返回订单号、产品名称、状态、创建时间。"
            "可选按状态过滤（如 IN_PROGRESS / COMPLETED）；limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "订单状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_list_orders,
    ),
    Tool(
        name="material_inventory",
        description=(
            "查询单个物料的库存详情。返回物料主数据整行，以及该物料在各库位的"
            "分布数量（库位编码、库位名称、数量）。参数 code 为必填的物料编码。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "物料编码，例如 MTR-001"},
            },
            "required": ["code"],
        },
        run=_material_inventory,
    ),
    Tool(
        name="low_stock",
        description=(
            "查询低库存物料。按可用数量 available_quantity 升序排列，返回物料编码、"
            "名称、规格、单位、总数量、可用数量。limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_low_stock,
    ),
    Tool(
        name="workspace_summary",
        description=(
            "工作台待办汇总，一次性返回四个关键数字：待审批的流转申请数、"
            "待确认的交接数、未完成的装配任务数、未处理的异常数。无参数。"
        ),
        parameters={"type": "object", "properties": {}},
        run=_workspace_summary,
    ),
    Tool(
        name="list_transfer_requests",
        description=(
            "列出流转申请单，按创建时间倒序。返回 id、类型、单据号、状态、创建时间。"
            "可选按状态过滤（如 PENDING_APPROVAL / APPROVED / REJECTED / EXECUTED）；"
            "limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "单据状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_list_transfer_requests,
    ),
    Tool(
        name="list_handovers",
        description=(
            "列出物料交接记录，按创建时间倒序。返回 id、工单项 id、数量、状态、创建时间。"
            "可选按状态过滤（如 PENDING / CONFIRMED / REJECTED / CANCELLED）；"
            "limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "交接状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_list_handovers,
    ),
    Tool(
        name="list_exceptions",
        description=(
            "列出异常记录，按创建时间倒序。返回 id、异常类型、差异数量、状态、"
            "描述、创建时间。可选按状态过滤；limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "异常状态过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_list_exceptions,
    ),
    Tool(
        name="task_progress",
        description=(
            "查询装配任务进度，按更新时间倒序。返回订单号、机台号、状态、"
            "进度阶段（0-3）、更新时间。可选按订单号过滤；limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "order_no": {"type": "string", "description": "生产订单号过滤，可选"},
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_task_progress,
    ),
    Tool(
        name="labor_summary",
        description=(
            "工时汇总：按工人 + 工时类型（ASSEMBLY 装配 / TEMPORARY_TRANSFER 临调）"
            "聚合最近 30 天的工时，返回工人 id、类型、总分钟数、记录数，"
            "按总分钟数降序。limit 控制条数，默认 20，最大 50。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认 20，最大 50", "default": 20},
            },
        },
        run=_labor_summary,
    ),
]


def openai_tools_schema(tools: list[Tool]) -> list[dict]:
    """把工具列表转成 OpenAI function calling 格式的 tools 参数。"""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def get_tool(tools: list[Tool], name: str) -> Tool | None:
    """按名称查找工具，找不到返回 None。"""
    for t in tools:
        if t.name == name:
            return t
    return None
