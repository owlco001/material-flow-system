"""管理台不再使用浏览器原生 confirm()，统一走 base.html 的 uiConfirm 弹窗。"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def test_templates_do_not_use_native_confirm():
    offenders = []
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        for m in re.finditer(r"(?<![\w.])confirm\(", text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.name}:{line}")
    assert offenders == [], f"请改用 data-confirm 或 uiConfirm()：{offenders}"


def test_base_provides_ui_confirm():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "window.uiConfirm" in base
    assert 'id="uiConfirmMask"' in base
