"""S13 BOM 导入契约测试（docs/admin-web-contract.md §8.11）。"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app import main as backend

CSV_HEADER = "modelCode,materialCode,materialName,specification,unit,quantity,scrapRate,substituteMaterialCodes"
CSV_BODY = f"{CSV_HEADER}\nM-TEST,MAT-1,零件A,规格甲,个,2,0,\nM-TEST,MAT-2,零件B,规格乙,个,3,0.05,"


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    for uid, uname, role, pwd in (
        ("u_sup", "supervisor1", "WORKSHOP_SUPERVISOR", "Sup@2026x"),
        ("u_wh", "wh-admin", "WAREHOUSE_ADMIN", "Wh@2026x"),
    ):
        c.execute(
            "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (uid, uname, uname, role, backend.hash_password(pwd), 0, 1, backend.now()),
        )
    c.execute(
        "INSERT OR REPLACE INTO materials(id,code,name,specification,unit,batch_no,expiry_date,"
        "total_quantity,available_quantity,version) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("mat_1", "MAT-1", "零件A", "规格甲", "个", None, None, 0, 0, 1),
    )
    c.execute(
        "INSERT OR REPLACE INTO materials(id,code,name,specification,unit,batch_no,expiry_date,"
        "total_quantity,available_quantity,version) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("mat_2", "MAT-2", "零件B", "规格乙", "个", None, None, 0, 0, 1),
    )
    c.execute(
        "INSERT OR REPLACE INTO production_order_models(id,model_code,model_name,created_at)"
        " VALUES(?,?,?,?)",
        ("pom_test", "M-TEST", "测试机型", backend.now()),
    )
    c.commit()
    c.close()


def seed_bom_version():
    c = backend.db()
    c.execute(
        "INSERT INTO bom_versions(id,model_code,version_no,status,source_file_sha256,row_count,"
        "created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("bv_1", "GearboxAssy", 1, "DRAFT", "a" * 64, 42, "u_sup", backend.now()),
    )
    c.commit()
    c.close()


@pytest.fixture(autouse=True)
def _seed(isolated_backend_database):
    seed_users()
    yield


def form_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "表单必须携带 csrf_token"
    return match.group(1)


def web_login(client, username, password):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def test_boms_supervisor_ok_warehouse_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        assert client.get("/admin/boms").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "wh-admin", "Wh@2026x").status_code == 303
        assert client2.get("/admin/boms").status_code == 403


def test_boms_list_shows_seeded_version():
    seed_bom_version()
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.get("/admin/boms")
        assert r.status_code == 200
        assert "GearboxAssy" in r.text and "v1" in r.text and "DRAFT" in r.text and "42" in r.text


def test_bom_upload_preview_then_commit():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/boms")
        preview = client.post(
            "/admin/boms/import",
            data={"csrf_token": form_csrf(page.text), "modelCode": "M-TEST"},
            files={"file": ("bom.csv", CSV_BODY.encode("utf-8"), "text/csv")},
        )
        assert preview.status_code == 200
        assert "BOM 导入预览" in preview.text and "有效 2" in preview.text
        assert "MAT-1" in preview.text and "MAT-2" in preview.text
        op = str(uuid.uuid4())
        pid_match = re.search(r'name="previewId" value="([^"]+)"', preview.text)
        assert pid_match
        commit = client.post(
            "/admin/boms/import/commit",
            data={"csrf_token": form_csrf(preview.text), "clientOperationId": op,
                  "previewId": pid_match.group(1)},
            follow_redirects=False,
        )
        assert commit.status_code == 303
        assert commit.headers["location"] == "/admin/boms?notice=bom_imported"
        c = backend.db()
        version = c.execute("SELECT * FROM bom_versions WHERE model_code='M-TEST'").fetchone()
        item_count = c.execute("SELECT COUNT(*) FROM bom_items WHERE bom_version_id=?", (version["id"],)).fetchone()[0]
        c.close()
        assert version is not None and item_count == 2


def test_bom_invalid_file_shows_inline_error():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        page = client.get("/admin/boms")
        r = client.post(
            "/admin/boms/import",
            data={"csrf_token": form_csrf(page.text), "modelCode": "M-TEST"},
            files={"file": ("bom.csv", b"\x00\x01\x02 not a bom", "text/csv")},
        )
        assert r.status_code == 200 and "error" in r.text


def test_bom_upload_without_csrf_rejected():
    with TestClient(backend.app) as client:
        assert web_login(client, "supervisor1", "Sup@2026x").status_code == 303
        r = client.post(
            "/admin/boms/import",
            data={"csrf_token": "", "modelCode": "M-TEST"},
            files={"file": ("bom.csv", CSV_BODY.encode("utf-8"), "text/csv")},
        )
        assert r.status_code == 403
        c = backend.db()
        count = c.execute("SELECT COUNT(*) FROM bom_versions WHERE model_code='M-TEST'").fetchone()[0]
        c.close()
        assert count == 0
