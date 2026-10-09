"""机台/物料图纸：上传 PDF → 拆页 + 缩略图 → 绑定 → 终端按页获取。"""
from __future__ import annotations

import io
import re

from fastapi.testclient import TestClient
from PIL import Image

from app import main as backend
from tests.test_admin_web_nav import seed, web_login


def _pdf(pages: int = 3) -> bytes:
    imgs = [Image.new("RGB", (842, 595), (255, 255, 255 - i * 40)) for i in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _seed_targets():
    seed()
    c = backend.db()
    ts = backend.now()
    c.execute("INSERT OR REPLACE INTO devices(id, device_no, device_name, workshop, status, created_by, created_at, updated_at)"
              " VALUES('dev_t1','JT-001','一号机','总装','ACTIVE','u_admin',?,?)", (ts, ts))
    c.execute("INSERT OR REPLACE INTO materials(id, code, name, unit) VALUES('m_t1','MAT-001','轴承','个')")
    c.commit()
    c.close()


def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _upload(client, data: bytes, name="总装图.pdf", devices="JT-001", materials="MAT-001, NOPE-9"):
    page = client.get("/admin/drawings")
    assert page.status_code == 200
    return client.post(
        "/admin/drawings/upload",
        data={"csrf_token": _csrf(page.text), "title": "总装图", "drawing_no": "DWG-01",
              "revision": "A", "devices": devices, "materials": materials},
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        follow_redirects=False,
    )


def _token(client, username="owlco", password="Admin@2026"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password, "deviceId": username})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def test_upload_splits_pages_binds_and_serves():
    _seed_targets()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = _upload(client, _pdf(3))
        assert r.status_code == 303
        assert "%E5%85%B1%203%20%E9%A1%B5" in r.headers["location"]  # 共 3 页
        assert "NOPE-9" in r.headers["location"]  # 未找到的编码会提示

        listing = client.get("/admin/drawings")
        assert "总装图" in listing.text and "JT-001" in listing.text and "MAT-001" in listing.text

        client.cookies.clear()
        h = _token(client)
        dev = client.get("/api/v1/devices/JT-001/drawings", headers=h).json()
        assert len(dev["drawings"]) == 1
        d = dev["drawings"][0]
        assert d["pageCount"] == 3 and d["drawingNo"] == "DWG-01" and d["revision"] == "A"
        assert [p["page"] for p in d["pages"]] == [1, 2, 3]
        assert d["pages"][0]["widthPt"] > d["pages"][0]["heightPt"]  # 横向保留

        mat = client.get("/api/v1/materials/MAT-001/drawings", headers=h).json()
        assert [x["drawingId"] for x in mat["drawings"]] == [d["drawingId"]]

        page = client.get(d["pages"][1]["url"], headers=h)
        assert page.status_code == 200 and page.content.startswith(b"%PDF")
        assert page.headers["content-type"] == "application/pdf"
        assert "immutable" in page.headers["cache-control"]
        import pypdfium2 as pdfium
        assert len(pdfium.PdfDocument(page.content)) == 1  # 单页

        thumb = client.get(d["pages"][0]["thumbUrl"], headers=h)
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
        assert max(Image.open(io.BytesIO(thumb.content)).size) == 480

        assert client.get(f"/api/v1/drawings/{d['drawingId']}/pages/9", headers=h).status_code == 404
        assert client.get(d["pages"][0]["url"]).status_code == 401  # 需要登录


def test_rejects_non_pdf_and_marks_cad_unsupported(monkeypatch):
    monkeypatch.delenv("DRAWING_CAD_TO_PDF", raising=False)
    _seed_targets()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = _upload(client, b"hello", name="x.pdf")
        assert "%E4%B8%8D%E6%98%AF%E6%9C%89%E6%95%88" in r.headers["location"]  # 不是有效
        r = _upload(client, b"AC1032 fake dwg", name="y.dwg", materials="")
        assert r.status_code == 303
        c = backend.db()
        row = c.execute("SELECT status FROM drawings WHERE original_name='y.dwg'").fetchone()
        c.close()
        assert row["status"] == "UNSUPPORTED"
        client.cookies.clear()
        h = _token(client)
        # 未转换的图纸不出现在终端列表
        assert client.get("/api/v1/devices/JT-001/drawings", headers=h).json()["drawings"] == []


def test_unbind_and_delete():
    _seed_targets()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        _upload(client, _pdf(1))
        c = backend.db()
        did = c.execute("SELECT id FROM drawings").fetchone()["id"]
        bid = c.execute("SELECT id FROM drawing_bindings WHERE target_type='DEVICE'").fetchone()["id"]
        c.close()
        csrf = _csrf(client.get("/admin/drawings").text)
        assert client.post(f"/admin/drawings/{did}/unbind", data={"csrf_token": csrf, "binding_id": bid},
                           follow_redirects=False).status_code == 303
        h = _token(client)
        assert client.get("/api/v1/devices/JT-001/drawings", headers=h).json()["drawings"] == []
        assert len(client.get("/api/v1/materials/MAT-001/drawings", headers=h).json()["drawings"]) == 1
        assert client.post(f"/admin/drawings/{did}/delete", data={"csrf_token": csrf},
                           follow_redirects=False).status_code == 303
        assert client.get("/api/v1/materials/MAT-001/drawings", headers=h).json()["drawings"] == []
        assert not (backend.UPLOAD_DIR / "drawings" / did).exists()


def test_non_admin_forbidden():
    _seed_targets()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/drawings").status_code == 403
