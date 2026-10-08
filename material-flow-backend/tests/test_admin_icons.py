"""管理台导航图标使用 SVG sprite：每个引用的图标都必须有对应 symbol，且不再使用字符图标。"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def test_every_icon_reference_has_symbol():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    symbols = set(re.findall(r'<symbol id="(i-[\w-]+)"', base))
    used = set()
    for path in TEMPLATES.glob("*.html"):
        used |= set(re.findall(r'<use href="#(i-[\w-]+)"', path.read_text(encoding="utf-8")))
    assert used, "导航应使用 SVG 图标"
    assert used <= symbols, f"缺少图标定义：{sorted(used - symbols)}"


def test_side_links_use_svg_icons():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    links = re.findall(r'<a class="side-link[^>]*>(<i>.*?</i>)', base)
    assert links
    assert all('<svg class="ico"' in i for i in links)
