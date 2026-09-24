"""S5 装配模型管理契约测试（docs/admin-web-contract.md §8.4）。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main as backend


def seed_users():
    c = backend.db()
    c.execute("DELETE FROM web_sessions")
    c.execute("DELETE FROM sessions")
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_admin", "owlco", "管理员", "ADMIN", backend.hash_password("Admin@2026"), 0, 1, backend.now()),
    )
    c.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("u_sup", "supervisor1", "车间主管", "WORKSHOP_SUPERVISOR", backend.hash_password("Sup@2026x"), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def seed_models():
    c = backend.db()
    for version, status, sha in (
        (1, "ARCHIVED", "a" * 64),
        (2, "PUBLISHED", "b" * 64),
    ):
        c.execute(
            "INSERT INTO assembly_model_versions(id,model_code,model_name,version,format,"
            "byte_size,sha256,storage_key,status,created_by,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"mdl_{version}", "GearboxAssy", "齿轮箱装配", version, "GLB", 4958788,
             sha, f"assembly-models/mdl_{version}/v{version}.glb", status, "u_admin", backend.now()),
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


def test_models_admin_ok_supervisor_forbidden():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        assert client.get("/admin/models").status_code == 200
    with TestClient(backend.app) as client2:
        assert web_login(client2, "supervisor1", "Sup@2026x").status_code == 303
        assert client2.get("/admin/models").status_code == 403


def test_models_list_shows_versions_with_labels():
    seed_models()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/models")
        assert r.status_code == 200
        assert "GearboxAssy" in r.text and "v2" in r.text and "v1" in r.text
        assert "已发布" in r.text and "已归档" in r.text


def test_models_code_filter_shows_published_card():
    seed_models()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/models?code=GearboxAssy")
        assert r.status_code == 200
        assert "当前发布 · GearboxAssy" in r.text
        assert "版本 v2" in r.text and "bbbbbbbbbbbbbbbb" in r.text


def test_models_unknown_code_and_empty_db():
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        empty = client.get("/admin/models")
        assert empty.status_code == 200 and "暂无数据" in empty.text
        missing = client.get("/admin/models?code=NoSuchModel")
        assert missing.status_code == 200
        assert "资源不存在" in missing.text
