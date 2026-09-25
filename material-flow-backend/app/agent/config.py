"""Agent 配置：全部来自环境变量。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    timeout_s: float
    max_iters: int

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            enabled=os.environ.get("AGENT_ENABLED", "1") == "1",
            base_url=os.environ.get("AGENT_LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=os.environ.get("AGENT_LLM_API_KEY", ""),
            model=os.environ.get("AGENT_LLM_MODEL", "deepseek-chat"),
            timeout_s=float(os.environ.get("AGENT_LLM_TIMEOUT_S", "60")),
            max_iters=int(os.environ.get("AGENT_MAX_ITERS", "8")),
        )
