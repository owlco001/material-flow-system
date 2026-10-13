"""U9 集成配置：全部来自环境变量，默认值 = 未启用。

U9C（U9 Cloud，REST /webapi，本项目当前对接模式，默认）：
    U9_ENABLED=1
    U9_MODE=u9c
    U9_BASE_URL=http://<U9C服务器>/U9C          # 末尾不要带 /webapi
    U9_CLIENT_ID=...  U9_CLIENT_SECRET=...      # U9C「第三方应用授权」里登记的应用
    U9_ENT_CODE=...   U9_ORG_CODE=...   U9_USER_CODE=...
    U9_DEFAULT_WH=...                           # 可选：库存同步默认存储地点编码
    U9_TIMEOUT_SECONDS=30  U9_VERIFY_SSL=1

经典 U9（UBF WebService/SOAP）：U9_MODE=classic，仍为占位，调用一律记为「待实施商文档」。

凭证只从环境变量读取；safe_dict() 只给出脱敏摘要，绝不含密钥。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, lo: int = 1, hi: int = 1_000_000) -> int:
    try:
        v = int(_env(name) or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def _mode() -> str:
    m = _env("U9_MODE", "u9c").lower() or "u9c"
    # 旧骨架里的 cloud 即 U9cloud，统一并入 u9c
    return "u9c" if m in ("u9c", "cloud", "u9cloud", "rest") else m


def mask(value: str, keep: int = 4) -> str:
    """脱敏：只保留前 keep 位，其余用 * 代替（空串返回空串）。"""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * min(8, len(value) - keep)


# U9C 连接必填项：环境变量名 → 中文说明
U9C_REQUIRED = (
    ("U9_BASE_URL", "base_url", "U9C 服务地址"),
    ("U9_CLIENT_ID", "client_id", "第三方应用 clientid"),
    ("U9_CLIENT_SECRET", "client_secret", "第三方应用 clientsecret"),
    ("U9_ENT_CODE", "ent_code", "企业编码"),
    ("U9_ORG_CODE", "org_code", "组织编码"),
    ("U9_USER_CODE", "user_code", "U9 用户编码"),
)


@dataclass
class U9Config:
    enabled: bool = field(default_factory=lambda: _flag("U9_ENABLED", False))
    mode: str = field(default_factory=_mode)
    # ---- U9C REST ----
    base_url: str = field(default_factory=lambda: _env("U9_BASE_URL").rstrip("/"))
    client_id: str = field(default_factory=lambda: _env("U9_CLIENT_ID"))
    client_secret: str = field(default_factory=lambda: _env("U9_CLIENT_SECRET"))
    ent_code: str = field(default_factory=lambda: _env("U9_ENT_CODE"))
    org_code: str = field(default_factory=lambda: _env("U9_ORG_CODE"))
    user_code: str = field(default_factory=lambda: _env("U9_USER_CODE"))
    default_wh: str = field(default_factory=lambda: _env("U9_DEFAULT_WH"))
    timeout_seconds: int = field(default_factory=lambda: _int("U9_TIMEOUT_SECONDS", 30, 1, 600))
    verify_ssl: bool = field(default_factory=lambda: _flag("U9_VERIFY_SSL", True))
    # 文档写的是 x-www-form-urlencoded，但实际请求体是 JSON；默认按 JSON 发，可改
    content_type: str = field(default_factory=lambda: _env("U9_CONTENT_TYPE", "application/json")
                              or "application/json")
    # 《接口授权》文档待用户重发：先按 U9C 标准路径，必要时可改
    auth_path: str = field(default_factory=lambda: _env("U9_AUTH_PATH", "/webapi/OAuth2/AuthLogin")
                           or "/webapi/OAuth2/AuthLogin")
    # 现场实测 AuthLogin 返回「token有效时间为5分钟」：默认缓存 4 分钟，失效时还会自动重登一次
    token_ttl_seconds: int = field(default_factory=lambda: _int("U9_TOKEN_TTL_SECONDS", 240, 30, 86400))
    batch_size: int = field(default_factory=lambda: _int("U9_BATCH_SIZE", 50, 1, 500))
    # BOM 多层展开
    bom_recursive: bool = field(default_factory=lambda: _flag("U9_BOM_RECURSIVE", True))
    bom_max_depth: int = field(default_factory=lambda: _int("U9_BOM_MAX_DEPTH", 8, 1, 30))
    bom_max_lines: int = field(default_factory=lambda: _int("U9_BOM_MAX_LINES", 20000, 10, 200000))
    bom_enrich: bool = field(default_factory=lambda: _flag("U9_BOM_ENRICH", True))
    # 库存：本地数量取 U9 哪个数（store=现存量 m_storeQty / available=可用量 m_canUseQty）
    inventory_qty_field: str = field(default_factory=lambda: (_env("U9_INVENTORY_QTY_FIELD", "store")
                                                             or "store").lower())
    # 库存按料号逐个查询时的并发数（库存接口请求体是单个对象，不能一次查多个料号）
    concurrency: int = field(default_factory=lambda: _int("U9_CONCURRENCY", 4, 1, 16))
    # ---- 经典 U9（占位） ----
    wsdl_url: str = field(default_factory=lambda: _env("U9_WSDL_URL"))
    username: str = field(default_factory=lambda: _env("U9_USERNAME"))
    password: str = field(default_factory=lambda: _env("U9_PASSWORD"))
    org: str = field(default_factory=lambda: _env("U9_ORG"))
    sync_interval_minutes: int = field(default_factory=lambda: _int("U9_SYNC_INTERVAL_MINUTES", 60))

    def missing_fields(self) -> list[str]:
        """未配齐的环境变量名列表。"""
        if self.mode == "u9c":
            return [env for env, attr, _ in U9C_REQUIRED if not getattr(self, attr)]
        if self.mode == "classic":
            return [n for n, v in (("U9_WSDL_URL", self.wsdl_url), ("U9_USERNAME", self.username),
                                   ("U9_PASSWORD", self.password)) if not v]
        return ["U9_MODE"]

    @property
    def configured(self) -> bool:
        """凭证是否配齐（不含 enabled 开关）。"""
        return not self.missing_fields()

    @property
    def ready(self) -> bool:
        return self.enabled and self.configured

    def safe_dict(self) -> dict:
        """给 /status 与管理页用的脱敏配置摘要（绝不含密码/密钥）。"""
        d = {
            "enabled": self.enabled,
            "mode": self.mode,
            "configured": self.configured,
            "ready": self.ready,
            "missing": self.missing_fields(),
            "timeout_seconds": self.timeout_seconds,
        }
        if self.mode == "u9c":
            d.update({
                "base_url": self.base_url,
                "client_id": mask(self.client_id),
                "client_secret_set": bool(self.client_secret),
                "ent_code": self.ent_code,
                "org_code": self.org_code,
                "user_code": self.user_code,
                "default_wh": self.default_wh,
                "verify_ssl": self.verify_ssl,
                "content_type": self.content_type,
                "token_ttl_seconds": self.token_ttl_seconds,
                "batch_size": self.batch_size,
                "bom_recursive": self.bom_recursive,
                "bom_max_depth": self.bom_max_depth,
                "inventory_qty_field": self.inventory_qty_field,
                "concurrency": self.concurrency,
            })
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
