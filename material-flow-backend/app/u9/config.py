"""U9 集成配置：全部来自环境变量，默认值 = 未启用。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class U9Config:
    """U9 连接配置。

    经典 U9（局域网本地部署）：
        U9_MODE=classic
        U9_WSDL_URL=http://<U9服务器>/u9/services/....?wsdl   # 实施商发布后提供
        U9_USERNAME=... / U9_PASSWORD=...                      # U9 账套用户
        U9_ORG=...                                             # 组织编码（待实施商确认字段名）

    U9cloud（如以后迁移）：
        U9_MODE=cloud
        U9_BASE_URL=https://<网关>
        U9_APP_KEY=... / U9_APP_SECRET=...
        U9_TENANT_ID=...
    """
    enabled: bool = field(default_factory=lambda: _flag("U9_ENABLED", False))
    mode: str = field(default_factory=lambda: os.environ.get("U9_MODE", "classic").strip().lower())
    # classic
    wsdl_url: str = field(default_factory=lambda: os.environ.get("U9_WSDL_URL", ""))
    username: str = field(default_factory=lambda: os.environ.get("U9_USERNAME", ""))
    password: str = field(default_factory=lambda: os.environ.get("U9_PASSWORD", ""))
    org: str = field(default_factory=lambda: os.environ.get("U9_ORG", ""))
    # cloud
    base_url: str = field(default_factory=lambda: os.environ.get("U9_BASE_URL", "").rstrip("/"))
    app_key: str = field(default_factory=lambda: os.environ.get("U9_APP_KEY", ""))
    app_secret: str = field(default_factory=lambda: os.environ.get("U9_APP_SECRET", ""))
    tenant_id: str = field(default_factory=lambda: os.environ.get("U9_TENANT_ID", ""))
    # 同步行为
    sync_interval_minutes: int = field(
        default_factory=lambda: int(os.environ.get("U9_SYNC_INTERVAL_MINUTES", "60") or 60))
    timeout_seconds: int = field(
        default_factory=lambda: int(os.environ.get("U9_TIMEOUT_SECONDS", "30") or 30))

    @property
    def configured(self) -> bool:
        """凭证是否配齐（不含 enabled 开关）。"""
        if self.mode == "cloud":
            return bool(self.base_url and self.app_key and self.app_secret)
        return bool(self.wsdl_url and self.username and self.password)

    def safe_dict(self) -> dict:
        """给 /status 接口用的脱敏配置摘要（绝不含密码/密钥）。"""
        d = {
            "enabled": self.enabled,
            "mode": self.mode,
            "configured": self.configured,
            "sync_interval_minutes": self.sync_interval_minutes,
        }
        if self.mode == "cloud":
            d.update({"base_url": self.base_url, "app_key_set": bool(self.app_key),
                      "tenant_id": self.tenant_id})
        else:
            d.update({"wsdl_url": self.wsdl_url, "username": self.username, "org": self.org,
                      "password_set": bool(self.password)})
        return d


_config: U9Config | None = None


def get_config() -> U9Config:
    global _config
    if _config is None:
        _config = U9Config()
    return _config


def reload_config() -> U9Config:
    global _config
    _config = U9Config()
    return _config
