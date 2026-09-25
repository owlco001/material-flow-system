"""条码生成测试：二维码 / Code128，订单 / 机台 / 物料三实体 + 打印页。"""

from __future__ import annotations

import os
import shutil
import uuid

TEST_ROOT = "/tmp/mf_barcodes"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
os.environ["MATERIAL_FLOW_DATA"] = f"{TEST_ROOT}/data"
os.environ["MATERIAL_FLOW_UPLOADS"] = f"{TEST_ROOT}/uploads"
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "TestAdmin@2026")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as backend  # noqa: E402 与 admin_web 共用同一模块实例
from app.barcodes import data_uri, render  # noqa: E402

_client_ctx = TestClient(backend.app)
client = _client_ctx.__enter__()

# 注意：conftest 的 autouse fixture 给每个测试分配隔离数据库，token 按 DB 路径缓存，
# 同一测试内多次调用复用，跨测试自动隔离。
_tokens: dict[str, str] = {}


def H() -> dict[str, str]:
    db_key = str(backend.DB_PATH)
    if db_key not in _tokens:
        r = client.post("/api/v1/auth/login", json={
            "username": "owlco", "password": "TestAdmin@2026",
            "deviceId": "barcode-test", "clientVersion": "0.4.0",
        })
        assert r.status_code == 200, r.text
        tmp = {"Authorization": f"Bearer {r.json()['accessToken']}"}
        r = client.post("/api/v1/auth/change-password", headers=tmp, json={
            "oldPassword": "TestAdmin@2026", "newPassword": "BarcodeAdmin@2026",
        })
        assert r.status_code == 200, r.text
        r = client.post("/api/v1/auth/login", json={
            "username": "owlco", "password": "BarcodeAdmin@2026",
            "deviceId": "barcode-test", "clientVersion": "0.4.0",
        })
        assert r.status_code == 200, r.text
        _tokens[db_key] = r.json()["accessToken"]
    return {"Authorization": f"Bearer {_tokens[db_key]}"}


# ---------- 模块级单元测试 ----------

def test_render_qr_png_is_valid_png():
    data, media = render("26B-013", "qr", "png")
    assert media == "image/png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_qr_svg_is_valid_svg():
    data, media = render("26B-013", "qr", "svg")
    assert media == "image/svg+xml"
    assert b"<svg" in data


def test_render_code128_png_and_svg():
    data, media = render("MTR-001", "code128", "png")
    assert media == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n"
    data, media = render("MTR-001", "code128", "svg")
    assert media == "image/svg+xml" and b"<svg" in data


def test_render_rejects_bad_params():
    with pytest.raises(ValueError):
        render("26B-013", "datamatrix", "png")
    with pytest.raises(ValueError):
        render("26B-013", "qr", "bmp")
    with pytest.raises(ValueError):
        render("物料一号", "code128", "png")  # Code128 不支持非 ASCII


def test_data_uri_shape():
    uri = data_uri("26B-013", "qr", "png")
    assert uri.startswith("data:image/png;base64,")


# ---------- API 测试 ----------

def test_barcode_requires_auth():
    r = client.get("/api/v1/barcodes/order/26B-013")
    assert r.status_code == 401


def test_barcode_rejects_unknown_entity():
    r = client.get("/api/v1/barcodes/rocket/26B-013", headers=H())
    assert r.status_code == 404


def test_barcode_rejects_missing_key():
    r = client.get("/api/v1/barcodes/order/NO-SUCH-ORDER", headers=H())
    assert r.status_code == 404


def test_barcode_order_qr_and_code128():
    for kind in ("qr", "code128"):
        r = client.get(f"/api/v1/barcodes/order/26B-013?kind={kind}&image=png", headers=H())
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_barcode_material_svg():
    r = client.get("/api/v1/barcodes/material/MTR-001?kind=qr&image=svg", headers=H())
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in r.content


def test_barcode_device_end_to_end():
    device_no = f"DEV-{uuid.uuid4().hex[:8].upper()}"
    op_id = str(uuid.uuid4())
    r = client.post("/api/v1/devices", headers={
        **H(), "X-Request-Id": str(uuid.uuid4()), "Idempotency-Key": op_id,
    }, json={
        "clientOperationId": op_id,
        "deviceNo": device_no,
        "deviceName": "测试机台",
        "workshop": "一车间",
    })
    assert r.status_code in (200, 201), r.text
    r = client.get(f"/api/v1/barcodes/device/{device_no}?kind=code128", headers=H())
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_barcode_payload_is_scan_compatible():
    """条码内容即编号本身：扫码接口能直接识别。"""
    r = client.post("/api/v1/scan/resolve", headers=H(), json={
        "rawValue": "26B-013", "clientOperationId": str(uuid.uuid4()),
    })
    assert r.status_code == 200
    assert r.json()["type"] == "PRODUCTION_ORDER"
    r = client.post("/api/v1/scan/resolve", headers=H(), json={
        "rawValue": "MTR-001", "clientOperationId": str(uuid.uuid4()),
    })
    assert r.status_code == 200
    assert r.json()["type"] == "MATERIAL_CODE"


# ---------- 管理台打印页 ----------

def _admin_cookie():
    import re

    # 模块级 TestClient 的 cookie jar 跨测试残留会话，先清空保证每次都是匿名访问登录页。
    client.cookies.clear()
    # backend 即 app.main，与 admin_web 共用同一实例和同一隔离库；显式 seed 管理员
    #（must_change_password=0），避免依赖初始改密流程。
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "管理员", "ADMIN",
         backend.hash_password("TestAdmin@2026"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()
    page = client.get("/admin/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    jar = client.post(
        "/admin/login",
        data={"username": "owlco", "password": "TestAdmin@2026", "csrf_token": token},
        follow_redirects=False,
    )
    assert jar.status_code in (200, 303), jar.text
    return jar.cookies


def test_admin_barcodes_page_and_print():
    cookies = _admin_cookie()
    r = client.get("/admin/barcodes", cookies=cookies)
    assert r.status_code == 200
    assert "条码生成" in r.text
    r = client.get("/admin/barcodes?entity=order&key=26B-013&kind=qr&copies=2", cookies=cookies)
    assert r.status_code == 200
    assert "26B-013" in r.text
    r = client.get("/admin/barcodes/print?entity=material&key=MTR-001&kind=code128&copies=3",
                   cookies=cookies)
    assert r.status_code == 200
    assert r.text.count("data:image/svg+xml;base64,") == 3
    assert "window.print()" in r.text


def test_admin_barcodes_print_missing_key():
    cookies = _admin_cookie()
    r = client.get("/admin/barcodes/print?entity=order&kind=qr", cookies=cookies)
    assert r.status_code == 400


def test_admin_barcodes_image_download():
    cookies = _admin_cookie()
    r = client.get("/admin/barcodes/image?entity=order&key=26B-013&kind=qr&image=png",
                   cookies=cookies)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "attachment" in r.headers["content-disposition"]
    r = client.get("/admin/barcodes/image?entity=material&key=MTR-001&kind=code128&image=svg",
                   cookies=cookies)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    # 未登录匿名访问应被拒绝（重定向到登录页）
    client.cookies.clear()
    r = client.get("/admin/barcodes/image?entity=order&key=26B-013", follow_redirects=False)
    assert r.status_code in (302, 303)
    # 不存在的编号
    cookies = _admin_cookie()
    r = client.get("/admin/barcodes/image?entity=order&key=NO-SUCH-ORDER", cookies=cookies)
    assert r.status_code == 404


# ---------- 条码文字说明 ----------

def test_render_with_label_adds_caption():
    """带 label 时四种条码图下方都有 “标签：编号” 的文字说明。"""
    from PIL import Image
    import io

    data, _ = render("MC-01", "qr", "png", "机台")
    img = Image.open(io.BytesIO(data))
    plain = Image.open(io.BytesIO(render("MC-01", "qr", "png")[0]))
    assert img.height > plain.height  # 说明条增加了高度

    data, _ = render("MC-01", "qr", "svg", "机台")
    assert "机台：MC-01" in data.decode("utf-8")

    data, _ = render("MC-01", "code128", "png", "机台")
    img = Image.open(io.BytesIO(data))
    plain = Image.open(io.BytesIO(render("MC-01", "code128", "png")[0]))
    assert img.height > plain.height

    data, _ = render("MC-01", "code128", "svg", "机台")
    text = data.decode("utf-8")
    assert "机台：MC-01" in text
    assert text.count("<text") == 1  # 无重复的人读文字


def test_render_without_label_unchanged():
    """不带 label 时保持原样输出（无说明条）。"""
    data, _ = render("MC-01", "code128", "svg")
    assert "MC-01" in data.decode("utf-8")
    assert "机台" not in data.decode("utf-8")
