"""Agent 优化回归：历史回放不含孤立工具消息、物料主档/BOM 工具、结果截断、轮次用尽兜底、LLM 重试。"""
import io
import json
import sqlite3
import urllib.error

import pytest

from app.agent import llm, service, tools as T
from app.agent.config import AgentConfig
from app.agent.llm import ChatResult, ChatToolCall, LLMError

CFG = AgentConfig(enabled=True, base_url="http://x", api_key="k", model="m", timeout_s=5, max_iters=3)



@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
    CREATE TABLE material_master(code TEXT PRIMARY KEY, name TEXT, specification TEXT, drawing_no TEXT,
      t6_code TEXT, unit_name TEXT, item_form TEXT, main_category_name TEXT, storage_location TEXT,
      device_no TEXT, project_no TEXT, buyer_name TEXT, extra_json TEXT, row_hash TEXT, source_batch_id TEXT);
    INSERT INTO material_master VALUES('M001','深沟球轴承','6204-2RS','DW-1','', '个','采购件','标准件','A区','D1','P1','张三','{}','h',NULL);
    INSERT INTO material_master VALUES('M002','支架','Q235','DW-2','', '件','自制件','结构件','B区','D1','P1','','{}','h',NULL);
    INSERT INTO material_master VALUES('M003','轴承座','HT200','DW-3','', '件','自制件','结构件','B区','D2','P2','','{}','h',NULL);
    CREATE TABLE agg_bom_batches(id TEXT PRIMARY KEY, project_name TEXT, file_name TEXT, created_at TEXT);
    CREATE TABLE agg_bom_items(id TEXT, batch_id TEXT, device_code TEXT, device_name TEXT, level_no INT,
      parent_code TEXT, material_code TEXT, material_name TEXT, specification TEXT, unit TEXT, quantity REAL,
      material_form TEXT, line_no INT);
    INSERT INTO agg_bom_batches VALUES('b1','项目一','a.xlsx','2026-10-10');
    INSERT INTO agg_bom_items VALUES('i1','b1','DEV-1','机台1',1,'DEV-1','M001','深沟球轴承','6204','个',4,'采购件',1);
    INSERT INTO agg_bom_items VALUES('i2','b1','DEV-1','机台1',1,'DEV-1','M002','支架','Q235','件',2,'自制件',2);
    """)
    yield c
    c.close()


def run(conn, name, **args):
    return T.get_tool(T.ANALYSIS_TOOLS, name).run(conn, args)


def test_material_master_tools(conn):
    r = run(conn, "search_material_master", keyword="轴承")
    assert r["total"] == 2 and {i["code"] for i in r["items"]} == {"M001", "M003"}
    r = run(conn, "search_material_master", keyword="轴承", item_form="自制")
    assert r["total"] == 1
    d = run(conn, "material_master_detail", code="M001")
    assert d["material"]["品名"] == "深沟球轴承"
    st = run(conn, "material_master_stats", group_by="item_form")
    assert st["total"] == 3 and st["groups"][0] == {"value": "自制件", "count": 2}
    with pytest.raises(T.ToolError):
        run(conn, "material_master_detail", code="NOPE")


def test_bom_tools(conn):
    b = run(conn, "bom_items", code="DEV-1")
    assert b["total"] == 2 and b["items"][0]["material_code"] == "M001"
    w = run(conn, "bom_where_used", material_code="M002")
    assert w["agg_boms"][0]["device_code"] == "DEV-1"
    with pytest.raises(T.ToolError):
        run(conn, "bom_items", code="NONE")


def test_tools_report_missing_tables():
    c = sqlite3.connect(":memory:")
    with pytest.raises(T.ToolError, match="尚未导入"):
        run(c, "search_material_master", keyword="x")



def test_history_excludes_tool_messages(monkeypatch, conn):
    seen = []
    script = iter([
        ChatResult(content="", tool_calls=[ChatToolCall("t1", "material_master_stats", "{}")]),
        ChatResult(content="共 3 个料号"),
        ChatResult(content="第二问答案"),
    ])

    def fake(cfg, messages, tools=None):
        seen.append(json.loads(json.dumps(messages)))
        return next(script)

    monkeypatch.setattr(llm, "chat", fake)
    r1 = service.run_chat(conn, CFG, "u1", None, "多少料号")
    service.run_chat(conn, CFG, "u1", r1["session_id"], "再问一个")
    second = seen[-1]
    roles = [m["role"] for m in second]
    assert "tool" not in roles
    assert roles == ["system", "user", "assistant", "user"]
    assert "当前时间" in second[0]["content"]


def test_tool_result_truncated_and_cached(monkeypatch, conn):
    monkeypatch.setattr(service, "_TOOL_RESULT_MAX_CHARS", 50)
    tc = ChatToolCall("t1", "search_material_master", '{"keyword":"轴承"}')
    script = iter([ChatResult(content="", tool_calls=[tc, ChatToolCall("t2", tc.name, tc.arguments_json)]),
                   ChatResult(content="ok")])
    captured = []

    def fake(cfg, messages, tools=None):
        captured.append(messages)
        return next(script)

    monkeypatch.setattr(llm, "chat", fake)
    service.run_chat(conn, CFG, "u1", None, "q")
    tool_msgs = [m for m in captured[-1] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "已截断" in tool_msgs[0]["content"]
    assert tool_msgs[0]["content"] == tool_msgs[1]["content"]


def test_max_iters_falls_back_to_summary(monkeypatch, conn):
    def fake(cfg, messages, tools=None):
        if tools is None:
            return ChatResult(content="根据已查数据：3 个料号")
        return ChatResult(content="", tool_calls=[ChatToolCall("t", "material_master_stats", "{}")])

    monkeypatch.setattr(llm, "chat", fake)
    r = service.run_chat(conn, CFG, "u1", None, "q")
    assert r["reply"] == "根据已查数据：3 个料号"


class _Resp:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_llm_retries_on_429(monkeypatch):
    calls = {"n": 0}
    ok = json.dumps({"choices": [{"message": {"content": "hi"}}], "usage": {}}).encode()

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b'{"error":{"message":"busy"}}'))
        return _Resp(ok)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    assert llm.chat(CFG, [{"role": "user", "content": "x"}]).content == "hi"
    assert calls["n"] == 2


def test_llm_400_not_retried_and_shows_message(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":{"message":"bad tool msg"}}'))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(LLMError, match="bad tool msg"):
        llm.chat(CFG, [{"role": "user", "content": "x"}])
    assert calls["n"] == 1
