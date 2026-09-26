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
import re
from xml.sax.saxutils import escape

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

# 机台条码的查找顺序：机台主数据（devices）优先，订单机台（order_devices）兜底。
# 批量机台码页列的是订单装配任务的机台号，只存在于后者。
_DEVICE_TABLES: tuple[tuple[str, str], ...] = (
    ("devices", "device_no"),
    ("order_devices", "device_no"),
)

KINDS = ("qr", "code128")
IMAGES = ("png", "svg")

_CJK_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
)

_SVG_WH_RE = re.compile(r'width="([\d.]+)mm"\s+height="([\d.]+)mm"')
_SVG_TEXT_RE = re.compile(r"<text[^>]*>.*?</text>", re.S)


class _NoTextImageWriter(ImageWriter):
    """python-barcode 0.16.1 的 ImageWriter 没有 write_text 选项，
    用子类关闭自带人读文字，由我们统一拼中文说明。"""

    def _paint_text(self, xpos, ypos):
        pass


def resolve_payload(entity: str, key: str, conn) -> str | None:
    """按实体类型与编号查出应编码的规范编号；不存在返回 None。"""
    table, column, _ = ENTITIES[entity]
    tables = _DEVICE_TABLES if entity == "device" else ((table, column),)
    key = key.strip()
    for t, col in tables:
        row = conn.execute(
            f"SELECT {col} FROM {t} WHERE {col}=? COLLATE NOCASE",
            (key,),
        ).fetchone()
        if row:
            return row[col]
    return None


def _check_code128_text(text: str) -> None:
    if not text or not text.isascii():
        raise ValueError("Code128 仅支持 ASCII 编号")


def _caption(label: str | None, payload: str) -> str | None:
    """条码图下方的文字说明，如 “机台：MC-01”。无 label 时不加说明。"""
    if not label:
        return None
    return f"{label}：{payload}"


def _cjk_font(size: int):
    from PIL import ImageFont

    for path in _CJK_FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _png_with_caption(img, caption: str) -> bytes:
    """在条码图下方拼接一行文字说明（白底黑字，中文友好）。"""
    from PIL import Image, ImageDraw

    img = img.convert("RGB")
    font = _cjk_font(max(18, img.width // 11))
    probe = ImageDraw.Draw(img)
    bbox = probe.textbbox((0, 0), caption, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 16, 14
    new_w = max(img.width, tw + pad_x * 2)
    new_h = img.height + th + pad_y * 2
    canvas = Image.new("RGB", (new_w, new_h), "white")
    canvas.paste(img, ((new_w - img.width) // 2, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        ((new_w - tw) // 2 - bbox[0], img.height + pad_y - bbox[1]),
        caption, font=font, fill="black",
    )
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _svg_with_caption(svg_bytes: bytes, caption: str) -> bytes:
    """在 SVG 条码下方加一行文字说明；去掉 python-barcode 自带的人读文字避免重复。"""
    s = svg_bytes.decode("utf-8")
    m = _SVG_WH_RE.search(s)
    if not m:
        return svg_bytes
    w, h = float(m.group(1)), float(m.group(2))
    band = max(h * 0.16, 4.0)
    inner = re.sub(r"^.*?<svg[^>]*>", "", s, count=1, flags=re.S)
    inner = re.sub(r"</svg>\s*$", "", inner).strip()
    inner = _SVG_TEXT_RE.sub("", inner)
    out = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.3f}mm" height="{h + band:.3f}mm" '
        f'viewBox="0 0 {w:.3f} {h + band:.3f}">'
        f'<rect x="0" y="0" width="{w:.3f}" height="{h + band:.3f}" fill="white"/>'
        f"<g>{inner}</g>"
        f'<text x="{w / 2:.3f}" y="{h + band * 0.72:.3f}" text-anchor="middle" '
        f'font-family="Noto Sans CJK SC, Noto Sans SC, PingFang SC, Microsoft YaHei, sans-serif" '
        f'font-size="{band * 0.55:.3f}">{escape(caption)}</text></svg>'
    )
    return out.encode("utf-8")


def _pil_image(img):
    """qrcode 返回的是包装对象，取出真正的 PIL Image。"""
    get = getattr(img, "get_image", None)
    if callable(get):
        return get()
    inner = getattr(img, "_img", None)
    return inner if inner is not None else img


def render_qr_png(text: str, label: str | None = None) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = _pil_image(qr.make_image(fill_color="black", back_color="white"))
    caption = _caption(label, text)
    if caption:
        return _png_with_caption(img, caption)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_qr_svg(text: str, label: str | None = None) -> bytes:
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
    data = buf.getvalue()
    caption = _caption(label, text)
    return _svg_with_caption(data, caption) if caption else data


def render_code128_png(text: str, label: str | None = None) -> bytes:
    _check_code128_text(text)
    caption = _caption(label, text)
    # 有中文说明时用无文字 writer，避免编号出现两次
    writer = _NoTextImageWriter() if caption else ImageWriter()
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
    if not caption:
        return buf.getvalue()
    from PIL import Image

    return _png_with_caption(Image.open(buf), caption)


def render_code128_svg(text: str, label: str | None = None) -> bytes:
    _check_code128_text(text)
    writer = SVGWriter()
    buf = io.BytesIO()
    Code128(text, writer=writer).write(buf)
    data = buf.getvalue()
    caption = _caption(label, text)
    # SVGWriter 无视 write_text=False，自带的人读文字在 _svg_with_caption 里统一剥离
    return _svg_with_caption(data, caption) if caption else data


def render(entity_payload: str, kind: str, image: str, label: str | None = None) -> tuple[bytes, str]:
    """生成条码，返回 (字节, media_type)。参数非法抛 ValueError。

    label 为实体展示名（如 “机台”）时，图下方会拼一行 “机台：MC-01” 的
    文字说明；不传则保持原样输出。
    """
    if kind not in KINDS:
        raise ValueError(f"不支持的条码类型: {kind}")
    if image not in IMAGES:
        raise ValueError(f"不支持的图片格式: {image}")
    if kind == "qr":
        data = render_qr_png(entity_payload, label) if image == "png" else render_qr_svg(entity_payload, label)
        media = "image/png" if image == "png" else "image/svg+xml"
    else:
        data = render_code128_png(entity_payload, label) if image == "png" else render_code128_svg(entity_payload, label)
        media = "image/png" if image == "png" else "image/svg+xml"
    return data, media


def data_uri(payload: str, kind: str, image: str, label: str | None = None) -> str:
    """供打印页内嵌的 data URI。"""
    data, media = render(payload, kind, image, label)
    return f"data:{media};base64," + base64.b64encode(data).decode("ascii")
