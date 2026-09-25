"""条码生成：订单 / 机台 / 物料的二维码（QR）与一维码（Code128）。

设计约定：
- 条码**内容直接编码业务编号本身**（订单号 / 机台号 / 料号），不另设编码
  方案，因此与 ``POST /api/v1/scan/resolve`` 的扫码识别规则完全兼容：
  扫出的码能被现有扫码接口直接解析回订单 / 物料 / 库位 / 机台。
- 输出两种载体：PNG（位图，贴标签/扫码枪通用）与 SVG（矢量，打印不糊）。
"""

from __future__ import annotations

import base64
import io

import qrcode
from barcode import Code128
from barcode.writer import ImageWriter, SVGWriter
from qrcode.image.svg import SvgPathImage

# 实体类型 -> (表名, 编号列, 展示名)
ENTITIES: dict[str, tuple[str, str, str]] = {
    "order": ("production_orders", "order_no", "生产订单"),
    "device": ("devices", "device_no", "机台"),
    "material": ("materials", "code", "物料"),
}

KINDS = ("qr", "code128")
IMAGES = ("png", "svg")


def resolve_payload(entity: str, key: str, conn) -> str | None:
    """按实体类型与编号查出应编码的规范编号；不存在返回 None。"""
    table, column, _ = ENTITIES[entity]
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column}=? COLLATE NOCASE",
        (key.strip(),),
    ).fetchone()
    return row[column] if row else None


def _check_code128_text(text: str) -> None:
    if not text or not text.isascii():
        raise ValueError("Code128 仅支持 ASCII 编号")


def render_qr_png(text: str) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_qr_svg(text: str) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(image_factory=SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def render_code128_png(text: str) -> bytes:
    _check_code128_text(text)
    writer = ImageWriter()
    writer.set_options(
        {
            "module_width": 0.35,
            "module_height": 14.0,
            "font_size": 11,
            "text_distance": 5.0,
            "quiet_zone": 6.0,
            "dpi": 300,
        }
    )
    buf = io.BytesIO()
    Code128(text, writer=writer).write(buf)
    return buf.getvalue()


def render_code128_svg(text: str) -> bytes:
    _check_code128_text(text)
    writer = SVGWriter()
    buf = io.BytesIO()
    Code128(text, writer=writer).write(buf)
    return buf.getvalue()


def render(entity_payload: str, kind: str, image: str) -> tuple[bytes, str]:
    """生成条码，返回 (字节, media_type)。参数非法抛 ValueError。"""
    if kind not in KINDS:
        raise ValueError(f"不支持的条码类型: {kind}")
    if image not in IMAGES:
        raise ValueError(f"不支持的图片格式: {image}")
    if kind == "qr":
        data = render_qr_png(entity_payload) if image == "png" else render_qr_svg(entity_payload)
        media = "image/png" if image == "png" else "image/svg+xml"
    else:
        data = render_code128_png(entity_payload) if image == "png" else render_code128_svg(entity_payload)
        media = "image/png" if image == "png" else "image/svg+xml"
    return data, media


def data_uri(payload: str, kind: str, image: str) -> str:
    """供打印页内嵌的 data URI。"""
    data, media = render(payload, kind, image)
    return f"data:{media};base64," + base64.b64encode(data).decode("ascii")
