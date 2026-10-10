"""Agent 对话服务：基于只读工具的 ReAct 对话循环（数据分析助手）。

- 会话与消息落库：agent_sessions / agent_messages
- token 用量落库：agent_usage（每次 run_chat 汇总一行）
- 工具执行异常统一转为 {"error": 中文信息} 交给模型处理
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.agent import llm
from app.agent.config import AgentConfig
from app.agent.db import ensure_agent_tables
from app.agent.llm import LLMError
from app.agent.tools import ANALYSIS_TOOLS, ToolError, get_tool, openai_tools_schema


ANALYST_SYSTEM_PROMPT = """你是智慧工厂（Smart Factory）的数据分析助手。

职责：
- 回答用户关于生产订单、物料库存、机台任务、流转申请、交接留痕、异常记录和工时等方面的数据问题。
- 只能使用提供的只读工具查询数据；所有数字必须来自工具返回的结果，严禁编造数字或凭空猜测。
- 回答使用简体中文，简洁直接；关键数字要注明数据来源（例如"来自订单 PO-2026-001 的详情查询"）。
- 不清楚用户指代的具体单号或编码时，先用列表类工具查找确认，不要臆测。
- 工具返回错误时如实转告用户，不要编造结果。
- 用户询问职责范围之外的问题（如写代码、闲聊、与工厂数据无关的事项）时，礼貌拒绝，并说明你只能做工厂数据分析。
- 列表类工具支持 limit/offset 分页：需要统计总数或查看全量时，用 offset 翻页取完（如先 limit=50 offset=0，再 offset=50），不要只看默认 20 条就下结论。
- 物料主档（U9 料号、品名、规格、图号、分类、存储地点等）用 search_material_master / material_master_detail / material_master_stats；
  只问总数或分布时用统计工具，不要翻页数数。BOM 用 bom_items（机型/设备的子件）和 bom_where_used（物料反查用在哪）。
- 能并行的查询在同一轮一次性发出多个工具调用，减少往返。
- 回答以结论开头；多条数据用 Markdown 表格或列表，不要大段堆砌原始 JSON。
"""

# 单个工具结果写回模型上下文的最大字符数，防止大结果撑爆上下文、拖慢响应
_TOOL_RESULT_MAX_CHARS = 12000
_HISTORY_LIMIT = 20


def _system_prompt() -> str:
    from datetime import timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    weekday = "一二三四五六日"[now.weekday()]
    return ANALYST_SYSTEM_PROMPT + f"\n当前时间：{now:%Y-%m-%d %H:%M}（北京时间，星期{weekday}）。理解“今天/本周/上月”等相对时间以此为准。\n"


TOOL_LABELS = {
    "order_detail": "查询订单详情", "list_orders": "查询订单列表", "material_inventory": "查询物料库存",
    "low_stock": "查询低库存", "workspace_summary": "查询工作台概览", "list_transfer_requests": "查询流转申请",
    "list_handovers": "查询交接记录", "list_exceptions": "查询异常记录", "task_progress": "查询任务进度",
    "labor_summary": "统计工时", "search_material_master": "搜索物料主档", "material_master_detail": "查询物料主档详情",
    "material_master_stats": "统计物料主档", "bom_items": "查询 BOM 明细", "bom_where_used": "物料反查 BOM",
}


def _summarize_result(result_json: str) -> dict:
    """给前端步骤条的一句话摘要（不含原始数据）。"""
    try:
        data = json.loads(result_json)
    except ValueError:
        return {"ok": True, "summary": "结果已截断"}
    if isinstance(data, dict) and "error" in data and len(data) == 1:
        return {"ok": False, "summary": str(data["error"])[:120]}
    if isinstance(data, list):
        return {"ok": True, "summary": f"{len(data)} 条"}
    if isinstance(data, dict):
        if "total" in data:
            return {"ok": True, "summary": f"共 {data['total']} 条"}
        for k in ("items", "groups", "models"):
            if isinstance(data.get(k), list):
                return {"ok": True, "summary": f"{len(data[k])} 条"}
    return {"ok": True, "summary": "已返回"}


def _truncate_result(result_json: str) -> str:
    if len(result_json) <= _TOOL_RESULT_MAX_CHARS:
        return result_json
    return (result_json[:_TOOL_RESULT_MAX_CHARS]
            + f"…（结果过长已截断，原长 {len(result_json)} 字符。请缩小 limit 或加过滤条件分页查询）")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _save_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
    tool_calls_json: str | None,
    sources: list | None = None,
) -> str:
    mid = _new_id()
    conn.execute(
        """INSERT INTO agent_messages(id, session_id, role, content, tool_calls_json, created_at, sources_json)
           VALUES(?,?,?,?,?,?,?)""",
        (mid, session_id, role, content or "", tool_calls_json, _now(),
         json.dumps(sources, ensure_ascii=False) if sources else None),
    )
    return mid


def _check_session_owner(conn: sqlite3.Connection, user_id: str, session_id: str) -> None:
    row = conn.execute(
        "SELECT user_id FROM agent_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError("会话不存在")
    if row[0] != user_id:
        raise ValueError("会话不属于当前用户")


def _run_tool(conn: sqlite3.Connection, tools: list, name: str, arguments_json: str):
    """执行单个工具调用，任何异常都转为 {"error": 中文信息}。"""
    tool = get_tool(tools, name)
    if tool is None:
        return {"error": f"未知工具: {name}"}
    try:
        args = json.loads(arguments_json or "{}")
    except (ValueError, TypeError):
        return {"error": f"工具 {name} 的参数不是合法 JSON"}
    try:
        return tool.run(conn, args)
    except ToolError as e:
        return {"error": str(e)}
    except Exception as e:  # 兜底：工具内部未预期的异常
        return {"error": f"工具 {name} 执行失败: {e}"}


def run_chat(
    conn: sqlite3.Connection,
    cfg: AgentConfig,
    user_id: str,
    session_id: str | None,
    message: str,
    tools: list = ANALYSIS_TOOLS,
    on_event=None,
) -> dict:
    """on_event(dict) 不为空时走流式：
    {"type":"session","session_id"} / {"type":"tool_start","name","args"} /
    {"type":"tool_end","name","ok","summary"} / {"type":"delta","text"}"""
    """ReAct 对话循环：模型推理 -> 工具调用 -> 模型汇总，直到无 tool_calls 或达到最大轮次。"""
    if not cfg.enabled:
        raise LLMError("Agent 功能未启用")
    ensure_agent_tables(conn)
    if cfg.daily_token_limit > 0:
        used = today_tokens(conn, user_id)
        if used >= cfg.daily_token_limit:
            raise LLMError(f"今日 Agent 用量已达上限（{used}/{cfg.daily_token_limit} tokens），明天再试或联系管理员调高")

    # 1. 会话：新建或校验归属
    if session_id:
        _check_session_owner(conn, user_id, session_id)
    else:
        session_id = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO agent_sessions(id, user_id, title, created_at, updated_at)
               VALUES(?,?,?,?,?)""",
            (session_id, user_id, " ".join(message.split())[:30], now, now),
        )

    def emit(ev: dict) -> None:
        if on_event is not None:
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001  前端断开不影响对话落库
                pass

    def call_llm(msgs: list[dict], schemas):
        if on_event is None:
            return llm.chat(cfg, msgs, schemas)
        return llm.chat_stream(cfg, msgs, schemas,
                               on_delta=lambda t: emit({"type": "delta", "text": t}))

    emit({"type": "session", "session_id": session_id})

    # 2. 落库用户消息
    user_msg_id = _new_id()
    conn.execute(
        """INSERT INTO agent_messages(id, session_id, role, content, tool_calls_json, created_at)
           VALUES(?,?,?,?,?,?)""",
        (user_msg_id, session_id, "user", message, None, _now()),
    )

    # 3. 取最近 20 条历史（不含刚写入的本条），拼 messages
    # 只带 user 与最终 assistant 回复：中间的 tool 消息与带 tool_calls 的 assistant
    # 若单独回放，会出现「tool 消息前没有对应 tool_calls」，OpenAI/DeepSeek 直接 HTTP 400。
    hist_rows = conn.execute(
        """SELECT role, content FROM agent_messages
           WHERE session_id = ? AND id != ?
             AND (role = 'user' OR (role = 'assistant' AND tool_calls_json IS NULL AND content != ''))
           ORDER BY rowid DESC LIMIT ?""",
        (session_id, user_msg_id, _HISTORY_LIMIT),
    ).fetchall()
    hist_rows = list(hist_rows)
    # 历史以 assistant 开头没有意义（被截断的问答），丢掉
    while hist_rows and hist_rows[-1][0] != "user":
        hist_rows.pop()
    messages: list[dict] = [{"role": "system", "content": _system_prompt()}]
    messages.extend({"role": r[0], "content": r[1]} for r in reversed(hist_rows))
    messages.append({"role": "user", "content": message})

    # 4. ReAct 循环
    tool_schemas = openai_tools_schema(tools)
    prompt_tokens = 0
    completion_tokens = 0
    iterations = 0
    reply: str | None = None
    tool_cache: dict[tuple[str, str], str] = {}
    sources: list[dict] = []  # 可追溯：本轮回答用到的工具、参数与结果摘要
    reply_id: str | None = None

    for _ in range(cfg.max_iters):
        iterations += 1
        result = call_llm(messages, tool_schemas)
        prompt_tokens += result.prompt_tokens
        completion_tokens += result.completion_tokens

        if not result.tool_calls:
            reply = result.content
            reply_id = _save_message(conn, session_id, "assistant", result.content, None, sources)
            break

        # assistant（含 tool_calls）追加进 messages 与 agent_messages
        tool_calls_payload = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments_json}
            for tc in result.tool_calls
        ]
        messages.append(
            {
                "role": "assistant",
                "content": result.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments_json},
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        _save_message(
            conn,
            session_id,
            "assistant",
            result.content,
            json.dumps(tool_calls_payload, ensure_ascii=False),
        )

        # 逐个执行工具调用（同一轮对话内相同调用直接复用结果）
        for tc in result.tool_calls:
            cache_key = (tc.name, tc.arguments_json or "{}")
            emit({"type": "tool_start", "name": tc.name, "label": TOOL_LABELS.get(tc.name, tc.name),
                  "args": tc.arguments_json or "{}"})
            if cache_key in tool_cache:
                result_json = tool_cache[cache_key]
            else:
                tool_result = _run_tool(conn, tools, tc.name, tc.arguments_json)
                try:
                    result_json = json.dumps(tool_result, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    result_json = json.dumps({"error": "工具返回了无法序列化的数据"}, ensure_ascii=False)
                result_json = _truncate_result(result_json)
                tool_cache[cache_key] = result_json
            summary = _summarize_result(result_json)
            emit({"type": "tool_end", "name": tc.name, **summary})
            sources.append({"name": tc.name, "label": TOOL_LABELS.get(tc.name, tc.name),
                            "args": _safe_args(tc.arguments_json), **summary})
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result_json}
            )
            _save_message(conn, session_id, "tool", result_json, None)

    if reply is None:
        # 轮次用尽：不再给工具，让模型基于已查到的数据给出结论
        messages.append({"role": "user", "content": "已达到工具调用上限。请只根据上面已经查到的数据直接回答；数据不足的部分明确说明。"})
        try:
            result = call_llm(messages, None)
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            reply = result.content or None
        except LLMError:
            reply = None
        if not reply:
            reply = "抱歉，这个问题比较复杂，已达到最大推理轮次，请换一种问法重试。"
        reply_id = _save_message(conn, session_id, "assistant", reply, None, sources)

    # 5. 用量落库 + 更新会话时间
    now = _now()
    conn.execute(
        """INSERT INTO agent_usage(session_id, user_id, model, prompt_tokens,
                                   completion_tokens, created_at)
           VALUES(?,?,?,?,?,?)""",
        (session_id, user_id, cfg.model, prompt_tokens, completion_tokens, now),
    )
    conn.execute(
        "UPDATE agent_sessions SET updated_at = ? WHERE id = ?", (now, session_id)
    )
    conn.commit()

    return {
        "session_id": session_id,
        "message_id": reply_id,
        "reply": reply,
        "sources": sources,
        "iterations": iterations,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def list_sessions(conn: sqlite3.Connection, user_id: str, limit: int = 20) -> list[dict]:
    """列出某用户的会话，按更新时间倒序。"""
    rows = conn.execute(
        """SELECT id, title, created_at, updated_at, pinned FROM agent_sessions
           WHERE user_id = ? ORDER BY pinned DESC, updated_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3], "pinned": bool(r[4])}
        for r in rows
    ]


def get_messages(
    conn: sqlite3.Connection, user_id: str, session_id: str, limit: int = 50
) -> list[dict]:
    """取某会话最近 limit 条消息（时间正序），先校验会话归属。"""
    _check_session_owner(conn, user_id, session_id)
    rows = conn.execute(
        """SELECT m.id, m.role, m.content, m.tool_calls_json, m.created_at, m.sources_json, f.rating
           FROM agent_messages m
           LEFT JOIN agent_feedback f ON f.message_id = m.id AND f.user_id = ?
           WHERE m.session_id = ? ORDER BY m.rowid DESC LIMIT ?""",
        (user_id, session_id, limit),
    ).fetchall()
    return [
        {
            "id": r[0],
            "role": r[1],
            "content": r[2],
            "tool_calls_json": r[3],
            "created_at": r[4],
            "sources": json.loads(r[5]) if r[5] else [],
            "rating": r[6],
        }
        for r in reversed(rows)
    ]


def _safe_args(arguments_json: str | None) -> dict:
    try:
        v = json.loads(arguments_json or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


# ---------- 会话管理 ----------

def rename_session(conn: sqlite3.Connection, user_id: str, session_id: str, title: str) -> None:
    title = " ".join((title or "").split())[:60]
    if not title:
        raise ValueError("标题不能为空")
    _check_session_owner(conn, user_id, session_id)
    conn.execute("UPDATE agent_sessions SET title = ? WHERE id = ?", (title, session_id))
    conn.commit()


def pin_session(conn: sqlite3.Connection, user_id: str, session_id: str, pinned: bool) -> None:
    _check_session_owner(conn, user_id, session_id)
    conn.execute("UPDATE agent_sessions SET pinned = ? WHERE id = ?", (1 if pinned else 0, session_id))
    conn.commit()


def delete_session(conn: sqlite3.Connection, user_id: str, session_id: str) -> None:
    """删除会话及其消息、反馈；用量记录保留（统计口径不变）。"""
    _check_session_owner(conn, user_id, session_id)
    conn.execute("DELETE FROM agent_feedback WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
    conn.commit()


def set_feedback(conn: sqlite3.Connection, user_id: str, message_id: str, rating: int,
                 comment: str | None = None) -> None:
    """点赞 1 / 点踩 -1 / 取消 0。只能评价自己会话里的助手回答。"""
    if rating not in (1, -1, 0):
        raise ValueError("rating 只能是 1、-1 或 0")
    ensure_agent_tables(conn)
    row = conn.execute(
        """SELECT m.session_id, m.role, s.user_id FROM agent_messages m
           JOIN agent_sessions s ON s.id = m.session_id WHERE m.id = ?""", (message_id,)).fetchone()
    if row is None or row[2] != user_id:
        raise ValueError("消息不存在")
    if row[1] != "assistant":
        raise ValueError("只能评价助手回答")
    if rating == 0:
        conn.execute("DELETE FROM agent_feedback WHERE message_id = ? AND user_id = ?", (message_id, user_id))
    else:
        conn.execute(
            """INSERT INTO agent_feedback(message_id, user_id, session_id, rating, comment, created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(message_id, user_id) DO UPDATE SET
                 rating = excluded.rating, comment = excluded.comment, created_at = excluded.created_at""",
            (message_id, user_id, row[0], rating, (comment or "")[:500] or None, _now()))
    conn.commit()


# ---------- 用量 ----------
# created_at 存 UTC ISO；统计按北京时间切日

_BJ = "'+8 hours'"


def today_tokens(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        f"""SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM agent_usage
            WHERE user_id = ? AND date(created_at, {_BJ}) = date('now', {_BJ})""",
        (user_id,),
    ).fetchone()
    return int(row[0] or 0)


def usage_stats(conn: sqlite3.Connection, days: int = 14) -> dict:
    """用量看板：最近 N 天按天、按人、按模型汇总，以及回答反馈。"""
    ensure_agent_tables(conn)
    days = max(1, min(int(days), 90))
    since = f"date('now', {_BJ}, '-{days - 1} days')"
    in_range = f"date(a.created_at, {_BJ}) >= {since}"
    by_day = conn.execute(
        f"""SELECT date(a.created_at, {_BJ}) AS d, COUNT(*),
                   COALESCE(SUM(a.prompt_tokens), 0), COALESCE(SUM(a.completion_tokens), 0)
            FROM agent_usage a WHERE {in_range} GROUP BY d ORDER BY d DESC""").fetchall()
    has_users = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone() is not None
    name_sel = "COALESCE(u.display_name, u.username, a.user_id)" if has_users else "a.user_id"
    join = "LEFT JOIN users u ON u.id = a.user_id" if has_users else ""
    by_user = conn.execute(
        f"""SELECT a.user_id, {name_sel}, COUNT(*),
                   COALESCE(SUM(a.prompt_tokens + a.completion_tokens), 0),
                   COALESCE(SUM(CASE WHEN date(a.created_at, {_BJ}) = date('now', {_BJ})
                                     THEN a.prompt_tokens + a.completion_tokens ELSE 0 END), 0)
            FROM agent_usage a {join} WHERE {in_range}
            GROUP BY a.user_id ORDER BY 4 DESC LIMIT 100""").fetchall()
    by_model = conn.execute(
        f"""SELECT a.model, COUNT(*), COALESCE(SUM(a.prompt_tokens + a.completion_tokens), 0)
            FROM agent_usage a WHERE {in_range} GROUP BY a.model ORDER BY 3 DESC""").fetchall()
    fb = conn.execute(
        f"""SELECT COALESCE(SUM(rating = 1), 0), COALESCE(SUM(rating = -1), 0) FROM agent_feedback
            WHERE date(created_at, {_BJ}) >= {since}""").fetchone()
    return {
        "days": days,
        "total_calls": sum(r[1] for r in by_day),
        "total_tokens": sum(r[2] + r[3] for r in by_day),
        "by_day": [{"date": r[0], "calls": r[1], "prompt_tokens": r[2], "completion_tokens": r[3],
                    "tokens": r[2] + r[3]} for r in by_day],
        "by_user": [{"user_id": r[0], "name": r[1], "calls": r[2], "tokens": r[3], "today_tokens": r[4]}
                    for r in by_user],
        "by_model": [{"model": r[0], "calls": r[1], "tokens": r[2]} for r in by_model],
        "feedback": {"up": int(fb[0]), "down": int(fb[1])},
    }
