"""U9 客户端抽象。

U9Client 定义智慧工厂需要的全部 U9 数据操作；两种实现：
  - ClassicU9Client：经典 U9（局域网）→ UBF WebService(SOAP)。需实施商发布服务后，
    用 wsdl_url + 账套用户调用。SOAP 调用建议用 zeep（requirements 届时追加）。
  - U9CloudClient：U9cloud → REST（iuap 开放平台风格：appKey/appSecret 取 token）。

在拿到实施商文档前，两个实现的方法体均为占位，调用时抛 U9NotImplemented，
sync 层会把这种情况记为"待实施商文档"而不是失败重试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import U9Config


class U9Error(Exception):
    """U9 调用失败（网络/认证/业务校验）。"""


class U9NotImplemented(U9Error):
    """接口占位：待实施商提供 WSDL/字段文档后实现。"""


@dataclass
class U9Client:
    cfg: U9Config

    # ---- Phase 1：只读（U9 → 本地） ----
    def health_check(self) -> dict:
        """连通性检查：返回 {ok, latency_ms, server_version?}。"""
        raise U9NotImplemented("health_check 待实施商 WSDL/文档后实现")

    def fetch_items(self, updated_since: str | None = None) -> list[dict]:
        """料品档案。期望字段：code, name, spec, unit（字段名待实施商确认）。"""
        raise U9NotImplemented("fetch_items 待实施商 WSDL/文档后实现")

    def fetch_bom(self, product_code: str) -> dict:
        """取某产品/设备的多层 BOM。期望返回 {product_code, lines: [{level, parent_code,
        material_code, material_name, spec, unit, qty, material_form}]}。"""
        raise U9NotImplemented("fetch_bom 待实施商 WSDL/文档后实现")

    def fetch_inventory(self, warehouse: str | None = None) -> list[dict]:
        """库存现存量。期望字段：material_code, warehouse, location, qty。"""
        raise U9NotImplemented("fetch_inventory 待实施商 WSDL/文档后实现")

    def fetch_production_orders(self, status: str | None = None) -> list[dict]:
        """生产订单。期望字段：order_no, product_code, product_name, qty, status。"""
        raise U9NotImplemented("fetch_production_orders 待实施商 WSDL/文档后实现")

    # ---- Phase 2：回写（本地 → U9），一期不调用 ----
    def post_completion(self, order_no: str, device_code: str, qty: float) -> dict:
        """完工汇报。"""
        raise U9NotImplemented("post_completion 为 Phase 2，待实施商确认业务校验规则后实现")

    def post_material_issue(self, order_no: str, lines: list[dict]) -> dict:
        """领料出库。"""
        raise U9NotImplemented("post_material_issue 为 Phase 2，待实施商确认业务校验规则后实现")


@dataclass
class ClassicU9Client(U9Client):
    """经典 U9（UBF WebService / SOAP）。

    实施商发布服务后，实现方式（示例思路，具体按文档调整）：
        from zeep import Client
        client = Client(self.cfg.wsdl_url, transport=Transport(timeout=self.cfg.timeout_seconds))
        # 按 WSDL 定义的服务名/方法名调用，账套用户做认证
    """

    def _todo(self, name: str) -> U9NotImplemented:
        return U9NotImplemented(
            f"{name}: 经典 U9 SOAP 调用待实施商提供 WSDL 地址与方法定义后实现 "
            f"(当前 U9_WSDL_URL={'已配置' if self.cfg.wsdl_url else '未配置'})")


@dataclass
class U9CloudClient(U9Client):
    """U9cloud（REST，iuap 开放平台风格）。

    认证参考（YonBIP 产品线已验证，U9cloud 待确认是否同源）：
        1. appKey + appSecret 做 HmacSHA256 签名换 access_token（约 2 小时有效）
        2. 业务接口形如 {gateway}/<路径>?access_token=...
    """


def get_client(cfg: U9Config | None = None) -> U9Client:
    """按 U9_MODE 返回对应客户端。"""
    from .config import get_config
    cfg = cfg or get_config()
    if cfg.mode == "cloud":
        return U9CloudClient(cfg)
    return ClassicU9Client(cfg)
