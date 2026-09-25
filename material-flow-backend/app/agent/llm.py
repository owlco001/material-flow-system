"""OpenAI 兼容的 LLM 聊天调用，仅用标准库 urllib。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from app.agent.config import AgentConfig


class LLMError(Exception):
    """LLM 调用失败：未配置、网络错误、非 200、JSON 解析失败。"""


@dataclass(frozen=True)
class ChatToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: list[ChatToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


def chat(cfg: AgentConfig, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
    if not cfg.api_key:
        raise LLMError("AGENT_LLM_API_KEY 未配置")

    body: dict = {
        "model": cfg.model,
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cfg.base_url.rstrip("/") + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + cfg.api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise LLMError(f"LLM 返回 HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM 请求失败: {e.reason}") from e
    except TimeoutError as e:
        raise LLMError(f"LLM 请求超时({cfg.timeout_s}s)") from e
    except OSError as e:
        raise LLMError(f"LLM 请求失败: {e}") from e

    if status != 200:
        raise LLMError(f"LLM 返回 HTTP {status}")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise LLMError("LLM 返回非 JSON") from e

    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError("LLM 返回结构异常") from e

    content = message.get("content") or ""
    tool_calls = [
        ChatToolCall(
            id=tc.get("id", ""),
            name=(tc.get("function") or {}).get("name", ""),
            arguments_json=(tc.get("function") or {}).get("arguments", ""),
        )
        for tc in (message.get("tool_calls") or [])
    ]
    usage = payload.get("usage") or {}
    return ChatResult(
        content=content,
        tool_calls=tool_calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )
