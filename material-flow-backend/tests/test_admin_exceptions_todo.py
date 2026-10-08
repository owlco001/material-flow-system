"""异常处理页（管理台首页）顶部待办卡片。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as backend
from tests.test_admin_web_nav import seed, web_login


def test_exceptions_page_shows_todo_cards():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        r = client.get("/admin/exceptions")
        assert r.status_code == 200
        for label, href in (
            ("待审批流转", "/admin/flows?status=PENDING_APPROVAL"),
            ("待确认交接", "/admin/handovers"),
            ("待料任务", "/admin/tasks?status=WAITING_MATERIAL"),
            ("装配进行中", "/admin/tasks?status=IN_PROGRESS"),
        ):
            assert label in r.text
            assert f'href="{href}"' in r.text
