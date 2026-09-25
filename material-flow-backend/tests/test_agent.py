"""Agent 接口测试：对话 + 智能导入。"""

import csv
import io
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main as backend  # noqa: E402
from app.agent import llm as agent_llm  # noqa: E402
from app.agent.llm import ChatResult  # noqa: E402


def rid():
    return str(uuid.uuid4())


def login(client, username="owlco", password="Admin@2026"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password, "deviceId": rid()})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["accessToken"]}


def setup_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend, "DB_PATH", tmp_path / "data" / "material_flow.db")
    backend.init_db()
    c = backend.db()
    c.execute("UPDATE users SET must_change_password=0")
    c.commit(); c.close()


def csv_bytes(fieldnames, rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def preview(client, auth, target, payload_bytes, filename="import.csv"):
    return client.post(
        "/api/v1/agent/import/preview",
        files={"file": (filename, payload_bytes, "text/csv")},
        data={"target": target},
        headers=auth,
    )


# ---------- 对话 ----------

def test_chat_requires_auth(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        r = client.post("/api/v1/agent/chat", json={"message": "hi"})
        assert r.status_code == 401


def test_chat_no_api_key_returns_503(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    with TestClient(backend.app) as client:
        auth = login(client)
        r = client.post("/api/v1/agent/chat", json={"message": "库存最低的物料是什么？"}, headers=auth)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "AGENT_LLM_ERROR"


def test_chat_with_stubbed_llm(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    calls = []

    def fake_chat(cfg, messages, tools=None):
        calls.append((messages, tools))
        if tools and not any(m.get("role") == "tool" for m in messages):
            return ChatResult(
                content="", prompt_tokens=10, completion_tokens=5,
                tool_calls=[type("TC", (), {"id": "call_1", "name": "low_stock",
                                           "arguments_json": '{"limit": 3}'})()],
            )
        return ChatResult(content="库存最低的是 MTR-001，共 986 件。", prompt_tokens=20,
                          completion_tokens=15, tool_calls=[])

    monkeypatch.setattr(agent_llm, "chat", fake_chat)
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-key")
    with TestClient(backend.app) as client:
        auth = login(client)
        r = client.post("/api/v1/agent/chat", json={"message": "库存最低的物料是什么？"}, headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["reply"] == "库存最低的是 MTR-001，共 986 件。"
        assert body["iterations"] == 2
        assert body["usage"]["prompt_tokens"] == 30
        sid = body["session_id"]
        # 会话落库 + 可查询
        r2 = client.get("/api/v1/agent/sessions", headers=auth)
        assert r2.status_code == 200 and len(r2.json()["sessions"]) == 1
        r3 = client.get(f"/api/v1/agent/sessions/{sid}/messages", headers=auth)
        assert r3.status_code == 200
        roles = [m["role"] for m in r3.json()["messages"]]
        assert roles[0] == "user" and roles[-1] == "assistant" and "tool" in roles
        # 用量落库
        c = backend.db()
        usage = c.execute("SELECT SUM(prompt_tokens), SUM(completion_tokens) FROM agent_usage").fetchone()
        c.close()
        assert tuple(usage) == (30, 20)
        # 同一会话继续对话
        r4 = client.post("/api/v1/agent/chat", json={"session_id": sid, "message": "还有吗"}, headers=auth)
        assert r4.status_code == 200 and r4.json()["session_id"] == sid


def test_chat_unknown_session_404(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-key")
    with TestClient(backend.app) as client:
        auth = login(client)
        r = client.get("/api/v1/agent/sessions/nope/messages", headers=auth)
        assert r.status_code == 404


# ---------- 智能导入 ----------

MATERIAL_FIELDS = ["物料编码", "名称", "单位", "总数量"]


def test_import_materials_preview_commit_idempotent(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)  # 走 fallback 列映射
    rows = [
        {"物料编码": "MTR-101", "名称": "螺栓", "单位": "件", "总数量": "100"},
        {"物料编码": "MTR-102", "名称": "螺母", "单位": "件", "总数量": "200"},
    ]
    with TestClient(backend.app) as client:
        auth = login(client)
        r = preview(client, auth, "materials", csv_bytes(MATERIAL_FIELDS, rows))
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["canCommit"] and p["validRows"] == 2 and p["invalidRows"] == 0
        assert p["columnMap"]["code"] == "物料编码"
        job_id = p["jobId"]
        # 任务可查询
        g = client.get(f"/api/v1/agent/import/jobs/{job_id}", headers=auth)
        assert g.status_code == 200 and g.json()["status"] == "PENDING_CONFIRM"
        # 提交
        key = rid()
        c1 = client.post("/api/v1/agent/import/commit",
                         json={"job_id": job_id, "client_operation_id": key}, headers=auth)
        assert c1.status_code == 200, c1.text
        assert c1.json()["committed_rows"] == 2 and c1.json()["idempotent"] is False
        c = backend.db()
        assert c.execute("SELECT name FROM materials WHERE code='MTR-101'").fetchone()[0] == "螺栓"
        assert c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='AGENT_IMPORT'").fetchone()[0] == 1
        c.close()
        # 幂等重放
        c2 = client.post("/api/v1/agent/import/commit",
                         json={"job_id": job_id, "client_operation_id": key}, headers=auth)
        assert c2.status_code == 200 and c2.json()["idempotent"] is True
        # 异键冲突
        c3 = client.post("/api/v1/agent/import/commit",
                         json={"job_id": job_id, "client_operation_id": rid()}, headers=auth)
        assert c3.status_code == 409


def test_import_preview_validation_errors_block_commit(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    rows = [
        {"物料编码": "", "名称": "缺编码", "单位": "件", "总数量": "10"},      # code 为空
        {"物料编码": "MTR-201", "名称": "坏数量", "单位": "件", "总数量": "abc"},  # 数量非法
        {"物料编码": "MTR-202", "名称": "好件", "单位": "件", "总数量": "5"},    # 有效
        {"物料编码": "MTR-202", "名称": "重复", "单位": "件", "总数量": "6"},    # 文件内重复
    ]
    with TestClient(backend.app) as client:
        auth = login(client)
        r = preview(client, auth, "materials", csv_bytes(MATERIAL_FIELDS, rows))
        assert r.status_code == 200
        p = r.json()
        assert not p["canCommit"] and p["invalidRows"] == 3 and p["validRows"] == 1
        assert all("row" in e and "message" in e for e in p["errors"])
        c1 = client.post("/api/v1/agent/import/commit",
                         json={"job_id": p["jobId"], "client_operation_id": rid()}, headers=auth)
        assert c1.status_code == 422


def test_import_orders_and_inventory(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    with TestClient(backend.app) as client:
        auth = login(client)
        # 订单
        r = preview(client, auth, "orders",
                    csv_bytes(["订单号", "产品名称"], [{"订单号": "PO-9001", "产品名称": "测试产品"}]))
        assert r.status_code == 200 and r.json()["canCommit"]
        c1 = client.post("/api/v1/agent/import/commit",
                         json={"job_id": r.json()["jobId"], "client_operation_id": rid()}, headers=auth)
        assert c1.status_code == 200
        c = backend.db()
        assert c.execute("SELECT product_name FROM production_orders WHERE order_no='PO-9001'").fetchone()[0] == "测试产品"
        # 重复订单号被拦
        r2 = preview(client, auth, "orders",
                     csv_bytes(["订单号"], [{"订单号": "PO-9001"}]))
        assert r2.status_code == 200 and not r2.json()["canCommit"]
        # 库存：物料/库位不存在被拦
        r3 = preview(client, auth, "inventory",
                     csv_bytes(["物料编码", "库位编码", "数量"],
                               [{"物料编码": "NOPE", "库位编码": "A-01-03", "数量": "5"}]))
        assert r3.status_code == 200 and not r3.json()["canCommit"]
        c.close()


def test_import_bad_target_and_file_and_auth(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client)
        r = preview(client, auth, "nope", b"a,b\n1,2")
        assert r.status_code == 400
        r2 = client.post("/api/v1/agent/import/preview",
                         files={"file": ("x.txt", b"hello", "text/plain")},
                         data={"target": "materials"}, headers=auth)
        assert r2.status_code == 400
        r3 = client.post("/api/v1/agent/import/preview",
                         files={"file": ("x.csv", b"a\n1", "text/csv")},
                         data={"target": "materials"})
        assert r3.status_code == 401
        r4 = client.get("/api/v1/agent/import/jobs/nope", headers=auth)
        assert r4.status_code == 404


def test_import_forbidden_role(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    with TestClient(backend.app) as client:
        auth = login(client)
        # 建一个 ASSEMBLER 用户（不在导入角色内）
        r = client.post("/api/v1/admin/users", json={
            "employeeNo": "asm001", "displayName": "装配工", "role": "ASSEMBLER",
            "password": "Asm@2026",
        }, headers=auth)
        assert r.status_code in (200, 201), r.text
        c = backend.db()
        c.execute("UPDATE users SET must_change_password=0 WHERE username='asm001'")
        c.commit(); c.close()
        asm_auth = login(client, username="asm001", password="Asm@2026")
        p = preview(client, asm_auth, "materials", csv_bytes(MATERIAL_FIELDS, [
            {"物料编码": "MTR-301", "名称": "x", "单位": "件", "总数量": "1"}]))
        assert p.status_code == 403
        # 但 ASSEMBLER 仍可用分析对话（只读）
        monkeypatch.setenv("AGENT_LLM_API_KEY", "test-key")
        def fake_chat(cfg, messages, tools=None):
            return ChatResult(content="ok", prompt_tokens=1, completion_tokens=1, tool_calls=[])
        monkeypatch.setattr(agent_llm, "chat", fake_chat)
        ch = client.post("/api/v1/agent/chat", json={"message": "hi"}, headers=asm_auth)
        assert ch.status_code == 200


def test_agent_disabled(tmp_path, monkeypatch):
    setup_backend(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_ENABLED", "0")
    with TestClient(backend.app) as client:
        auth = login(client)
        r = client.post("/api/v1/agent/chat", json={"message": "hi"}, headers=auth)
        assert r.status_code == 503 and r.json()["error"]["code"] == "AGENT_DISABLED"
