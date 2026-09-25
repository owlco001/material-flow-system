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
"""


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
) -> None:
    conn.execute(
        """INSERT INTO agent_messages(id, session_id, role, content, tool_calls_json, created_at)
           VALUES(?,?,?,?,?,?)""",
        (_new_id(), session_id, role, content or "", tool_calls_json, _now()),
    )


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
) -> dict:
    """ReAct 对话循环：模型推理 -> 工具调用 -> 模型汇总，直到无 tool_calls 或达到最大轮次。"""
    if not cfg.enabled:
        raise LLMError("Agent 功能未启用")
    ensure_agent_tables(conn)

    # 1. 会话：新建或校验归属
    if session_id:
        _check_session_owner(conn, user_id, session_id)
    else:
        session_id = _new_id()
        now = _now()
        conn.execute(
            """INSERT INTO agent_sessions(id, user_id, title, created_at, updated_at)
               VALUES(?,?,?,?,?)""",
            (session_id, user_id, message[:20], now, now),
        )

    # 2. 落库用户消息
    user_msg_id = _new_id()
    conn.execute(
        """INSERT INTO agent_messages(id, session_id, role, content, tool_calls_json, created_at)
           VALUES(?,?,?,?,?,?)""",
        (user_msg_id, session_id, "user", message, None, _now()),
    )

    # 3. 取最近 20 条历史（不含刚写入的本条），拼 messages
    hist_rows = conn.execute(
        """SELECT role, content FROM agent_messages
           WHERE session_id = ? AND id != ?
           ORDER BY rowid DESC LIMIT 20""",
        (session_id, user_msg_id),
    ).fetchall()
    messages: list[dict] = [{"role": "system", "content": ANALYST_SYSTEM_PROMPT}]
    messages.extend({"role": r[0], "content": r[1]} for r in reversed(hist_rows))
    messages.append({"role": "user", "content": message})

    # 4. ReAct 循环
    tool_schemas = openai_tools_schema(tools)
    prompt_tokens = 0
    completion_tokens = 0
    iterations = 0
    reply: str | None = None

    for _ in range(cfg.max_iters):
        iterations += 1
        result = llm.chat(cfg, messages, tool_schemas)
        prompt_tokens += result.prompt_tokens
        completion_tokens += result.completion_tokens

        if not result.tool_calls:
            reply = result.content
            _save_message(conn, session_id, "assistant", result.content, None)
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

        # 逐个执行工具调用
        for tc in result.tool_calls:
            tool_result = _run_tool(conn, tools, tc.name, tc.arguments_json)
            try:
                result_json = json.dumps(tool_result, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                result_json = json.dumps({"error": "工具返回了无法序列化的数据"}, ensure_ascii=False)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result_json}
            )
            _save_message(conn, session_id, "tool", result_json, None)

    if reply is None:
        reply = "抱歉，这个问题比较复杂，已达到最大推理轮次，请换一种问法重试。"
        _save_message(conn, session_id, "assistant", reply, None)

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
        "reply": reply,
        "iterations": iterations,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def list_sessions(conn: sqlite3.Connection, user_id: str, limit: int = 20) -> list[dict]:
    """列出某用户的会话，按更新时间倒序。"""
    rows = conn.execute(
        """SELECT id, title, created_at, updated_at FROM agent_sessions
           WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in rows
    ]


def get_messages(
    conn: sqlite3.Connection, user_id: str, session_id: str, limit: int = 50
) -> list[dict]:
    """取某会话最近 limit 条消息（时间正序），先校验会话归属。"""
    _check_session_owner(conn, user_id, session_id)
    rows = conn.execute(
        """SELECT id, role, content, tool_calls_json, created_at FROM agent_messages
           WHERE session_id = ? ORDER BY rowid DESC LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    return [
        {
            "id": r[0],
            "role": r[1],
            "content": r[2],
            "tool_calls_json": r[3],
            "created_at": r[4],
        }
        for r in reversed(rows)
    ]
