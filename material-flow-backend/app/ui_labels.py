"""管理台展示层统一字典与 Jinja 过滤器（v0.7.0）。

只影响 HTML 展示：状态 / 事件 / 异常等英文码 → 中文，UTC ISO 时间 → 北京时间，
用户 ID → 姓名。API 返回值与权限逻辑不经过这里。

模板用法：
    {{ row.created_at|local_dt }}              → 2026-10-12 19:34
    {{ e.type|label("exception_type") }}       → 缺料
    {{ item.event_type|label("event") }}       → 流转执行
    {{ item.actor_user_id|user_name }}         → 张三（查不到时回退原 ID）
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2 import pass_context

BEIJING = timezone(timedelta(hours=8))

# ---- 分类字典：同一个码在不同语境下含义不同时（如 PENDING），按 kind 取 ----
LABELS: dict[str, dict[str, str]] = {
    "order_status": {
        "DRAFT": "草稿", "RELEASED": "已下达", "IN_PROGRESS": "生产中",
        "COMPLETED": "已完成", "CANCELLED": "已取消", "PAUSED": "已暂停",
    },
    "task_status": {
        "WAITING_MATERIAL": "待领料", "MATERIAL_ACCEPTED": "已领料", "IN_PROGRESS": "生产中",
        "PAUSED_FOR_TEMPORARY_TRANSFER": "暂停（临调）", "COMPLETED": "已完成",
    },
    "stage_status": {
        "NOT_STARTED": "未开始", "IN_PROGRESS": "进行中", "COMPLETED": "已完成",
        "REWORK_REQUIRED": "需返工",
    },
    "bom_status": {"DRAFT": "草稿", "PUBLISHED": "已发布", "ARCHIVED": "已归档", "PREVIEW": "预览"},
    "model_status": {"DRAFT": "草稿", "PUBLISHED": "已发布", "ARCHIVED": "已归档"},
    "transfer_status": {
        "PENDING_APPROVAL": "待审批", "APPROVED": "已审批", "REJECTED": "已驳回", "EXECUTED": "已执行",
    },
    "transfer_type": {"INBOUND": "入库", "OUTBOUND": "出库", "TRANSFER": "调拨", "STOCKTAKE": "盘点"},
    "handover_status": {"PENDING": "待确认", "CONFIRMED": "已确认", "REJECTED": "已驳回", "CANCELLED": "已取消"},
    "exception_status": {
        "PENDING": "待处理", "OPEN": "待处理", "IN_PROGRESS": "处理中",
        "APPROVED": "已通过", "REJECTED": "已驳回", "RESOLVED": "已解决",
    },
    "exception_type": {
        "SHORTAGE": "缺料", "DAMAGE": "破损", "SURPLUS": "盘盈", "LOSS": "盘亏",
        "QUALITY": "质量问题", "MISMATCH": "账实不符", "EXPIRED": "过期", "WRONG_MATERIAL": "错料",
        "OVERAGE": "多发", "OTHER": "其他",
    },
    "stocktake_status": {"PENDING_CONFIRM": "待确认", "CONFIRMED": "已确认"},
    "workspace_status": {
        "RELEASED": "已下达", "WAITING_MATERIAL": "待领料", "MATERIAL_ACCEPTED": "已领料",
        "IN_PROGRESS": "生产中", "PAUSED_FOR_TEMPORARY_TRANSFER": "暂停（临调）", "COMPLETED": "已完成",
        "OUT_OF_STOCK": "缺货", "ARRIVED": "到货", "IN_STOCK": "在库",
        "OUTBOUND_PENDING": "待出库", "OUTBOUND_APPROVED": "已审批待出库", "OUTBOUND_CONFIRMED": "已出库",
        "PENDING": "待交接", "PICKED_UP": "已领取", "AT_STATION": "已到机台",
        "REJECTED": "已驳回", "CANCELLED": "已取消", "CONFIRMED": "已确认",
    },
    "event": {
        # audit_events
        "ORDER_CREATED": "订单创建", "ORDER_STATUS_CHANGED": "订单状态变更",
        "HANDOVER_CREATED": "交接创建", "HANDOVER_CONFIRMED": "交接确认",
        "HANDOVER_REJECTED": "交接驳回", "HANDOVER_CANCELLED": "交接取消",
        "OUTBOUND_APPROVED": "出库审批通过", "OUTBOUND_CONFIRMED": "出库确认",
        "TRANSFER_REJECTED": "流转驳回", "TRANSFER_HANDOVER": "流转交接",
        "TRANSFER_HANDOVER_CONFIRMED": "流转交接确认", "TEMPORARY_TRANSFER": "临时借调",
        "FLOW_TRANSFER_EXECUTED": "流转执行", "FLOW_TRANSFER_APPROVED": "流转审批",
        "FLOW_TRANSFER_CREATED": "流转申请",
        "MATERIAL_PICKED_UP": "物料领取", "MATERIAL_AT_STATION": "物料到机台",
        "MATERIAL_ACCEPTED_FOR_ASSEMBLY": "装配领料",
        "ASSEMBLY_TASK_CREATED": "任务创建", "ASSEMBLY_STARTED": "装配开始",
        "ASSEMBLY_PROGRESS_UPDATED": "装配进度更新", "ASSEMBLY_COMPLETED": "装配完成",
        "BOM_IMPORT": "BOM 导入", "AGG_BOM_IMPORT": "聚合BOM导入", "AGENT_IMPORT": "智能导入",
        "ADMIN_PROVISIONED_CLI": "管理员CLI开通", "ADMIN_ROLE_PREVIEW": "角色预览",
        "ADMIN_USER_EDIT": "编辑用户", "ADMIN_PASSWORD_RESET": "重置密码",
        "ADMIN_USER_HARD_DELETE": "彻底删除用户", "SESSIONS_REVOKED": "强制下线",
        "ACCOUNT_LOCKED": "账号锁定", "CHANGE_PASSWORD": "修改密码",
        # audit_logs.action
        "LOGIN": "登录", "LOGOUT": "退出登录", "LOGOUT_ALL": "全部下线", "LOCKED": "登录锁定",
        "REFRESH": "会话续期", "CREATE": "创建", "UPDATE": "更新", "DELETE": "删除",
        "HARD_DELETE": "彻底删除", "APPROVE": "审批", "REVIEW": "审核", "EXECUTE": "执行",
        "CONFIRM": "确认", "ASSIGN": "分配", "BIND": "绑定", "SETUP": "初始化",
        "INITIALIZE_ADMIN": "初始化管理员", "CREATE_ORDER": "创建订单", "SET_ORDER_STATUS": "变更订单状态",
        "DRAWING_BIND": "绑定图纸", "DRAWING_UNBIND": "解绑图纸", "DRAWING_DELETE": "删除图纸",
        "USER": "用户操作",
    },
    "entity": {
        "PRODUCTION_ORDER": "生产订单", "MATERIAL_HANDOVER": "物料交接", "HANDOVER": "物料交接",
        "TRANSFER_REQUEST": "流转申请", "MATERIAL_FLOW": "物料流转", "AGG_BOM_BATCH": "聚合BOM批次",
        "AGENT_IMPORT_JOB": "智能导入任务", "USER": "用户", "SESSION": "会话", "STOCKTAKE": "盘点",
        "EXCEPTION": "异常单", "DEVICE": "机台", "LOCATION": "库位", "DRAWING": "图纸",
        "ASSEMBLY_TASK": "装配任务", "BOM_VERSION": "BOM 版本", "DEVICE_MODEL_3D": "机台模型",
        "MATERIAL": "物料",
    },
    "result": {"SUCCESS": "成功", "FAIL": "失败", "FAILURE": "失败", "DENIED": "拒绝"},
    "role": {
        "ADMIN": "管理员", "WAREHOUSE_ADMIN": "仓库管理员", "WORKSHOP_SUPERVISOR": "车间主管",
        "PLANNER": "计划员", "OPERATOR": "操作员", "MATERIAL": "物料员", "ASSEMBLER": "装配工",
    },
    "import_status": {"PENDING": "待处理", "HANDLED": "已处理"},
    "u9_status": {"SUCCESS": "成功", "FAILED": "失败", "RUNNING": "进行中", "SKIPPED": "已跳过"},
}

# 未指定 kind 时的兜底：按下列顺序合并（越靠前优先级越高）
_GENERIC_ORDER = (
    "event", "entity", "exception_type", "transfer_status", "task_status", "order_status",
    "workspace_status", "exception_status", "transfer_type", "stocktake_status", "bom_status",
    "result", "role",
)
GENERIC: dict[str, str] = {}
for _k in reversed(_GENERIC_ORDER):
    GENERIC.update(LABELS[_k])


def label(code: Any, kind: str | None = None, default: str | None = None) -> str:
    """英文码 → 中文；未知码原样返回（空值返回 default 或 “—”）。"""
    if code is None or code == "":
        return default if default is not None else "—"
    s = str(code)
    if kind:
        hit = LABELS.get(kind, {}).get(s)
        if hit:
            return hit
    return GENERIC.get(s, s)


def label_options(kind: str) -> list[tuple[str, str]]:
    """下拉框选项 [(code, 中文)]。"""
    return list(LABELS.get(kind, {}).items())


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_dt(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """UTC/带时区 ISO 时间 → 北京时间 “YYYY-MM-DD HH:mm”。空值 “—”；无法解析原样返回。

    只有日期（YYYY-MM-DD，如效期）的值不做时区换算，原样返回。
    """
    if value is None or value == "":
        return "—"
    s = str(value).strip()
    if len(s) == 10 and s[4:5] == "-" and s[7:8] == "-":
        return s
    dt = _parse_dt(value)
    if dt is None:
        return s
    return dt.astimezone(BEIJING).strftime(fmt)


def local_date(value: Any) -> str:
    return local_dt(value, "%Y-%m-%d")


def beijing_day_bounds(date_str: str | None, end: bool = False) -> str | None:
    """日期输入（YYYY-MM-DD，北京时间）→ UTC ISO 边界，供审计等按时间过滤。

    起始取当天 00:00，截止取当天 23:59:59.999999。已是完整 ISO 时间的原样返回。
    """
    s = (date_str or "").strip()
    if not s:
        return None
    if len(s) == 10:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=BEIJING)
        except ValueError:
            return s
        if end:
            d = d + timedelta(days=1) - timedelta(microseconds=1)
        return d.astimezone(timezone.utc).isoformat()
    return s


def _all_user_names() -> dict[str, str]:
    """一次查询全部用户 id → 姓名（users 表规模为百级），供同一请求内反复使用。"""
    from app import main as backend  # 延迟导入，避免循环

    c = backend.db()  # 管理台 GET 页面复用请求级共享连接；其 close() 不会真正关闭
    try:
        return {r[0]: r[1] for r in c.execute("SELECT id, display_name FROM users").fetchall() if r[1]}
    except sqlite3.Error:
        return {}
    finally:
        c.close()


@pass_context
def user_name(ctx, uid: Any, default: str = "—") -> str:
    """用户 ID → 姓名；查不到回退原 ID。每个请求只查一次 users（缓存在 request.state）。"""
    if uid is None or uid == "":
        return default
    s = str(uid)
    request = ctx.get("request") if ctx is not None else None
    state = getattr(request, "state", None)
    names = getattr(state, "ui_user_names", None) if state is not None else None
    if names is None:
        names = _all_user_names()
        if state is not None:
            state.ui_user_names = names
    return names.get(s, s)


def register(env) -> None:
    env.filters["local_dt"] = local_dt
    env.filters["local_date"] = local_date
    env.filters["label"] = label
    env.filters["user_name"] = user_name
    env.globals["label_options"] = label_options
    env.globals["LABELS"] = LABELS
