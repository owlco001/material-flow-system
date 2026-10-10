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


# ---------- 流式 ----------

class _SSE:
    status = 200

    def __init__(self, chunks):
        self.lines = [("data: " + json.dumps(c) + "\n").encode() for c in chunks] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_chat_stream_assembles_text_and_tool_calls(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "共"}}]},
        {"choices": [{"delta": {"content": "3 个"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "bom_items", "arguments": "{\"co"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "de\":\"D\"}"}}]}}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    ]
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda req, timeout: _SSE(chunks))
    deltas = []
    r = llm.chat_stream(CFG, [{"role": "user", "content": "x"}], [{"type": "function"}], on_delta=deltas.append)
    assert deltas == ["共", "3 个"] and r.content == "共3 个"
    assert r.tool_calls[0].name == "bom_items" and json.loads(r.tool_calls[0].arguments_json) == {"code": "D"}
    assert r.prompt_tokens == 7 and r.completion_tokens == 3


def test_chat_stream_falls_back_when_unsupported(monkeypatch):
    def urlopen(req, timeout):
        if json.loads(req.data).get("stream"):
            raise urllib.error.HTTPError("u", 400, "no stream", {}, io.BytesIO(b"{}"))
        return _Resp(json.dumps({"choices": [{"message": {"content": "非流式"}}]}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    got = []
    assert llm.chat_stream(CFG, [{"role": "user", "content": "x"}], on_delta=got.append).content == "非流式"
    assert got == ["非流式"]


def test_run_chat_emits_events(monkeypatch, conn):
    script = iter([ChatResult(content="", tool_calls=[ChatToolCall("t1", "material_master_stats", "{}")]),
                   ChatResult(content="共 3 个")])

    def fake_stream(cfg, messages, tools=None, on_delta=None):
        r = next(script)
        if r.content and on_delta:
            on_delta(r.content)
        return r

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    events = []
    service.run_chat(conn, CFG, "u1", None, "q", on_event=events.append)
    types = [e["type"] for e in events]
    assert types == ["session", "tool_start", "tool_end", "delta"]
    assert events[1]["label"] == "统计物料主档"
    assert events[2]["ok"] and events[2]["summary"] == "共 3 条"


# ---------- 评测集 ----------

def test_eval_cases_reference_real_tools():
    from app.agent import eval as ev
    names = {t.name for t in T.ANALYSIS_TOOLS}
    cases = ev.load_cases()
    assert len(cases) >= 30 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert set(c.get("expect_tools") or []) <= names, c["id"]


def test_eval_judge():
    from app.agent.eval import judge
    assert judge({"expect_tools": ["a", "b"]}, ["b"])
    assert not judge({"expect_tools": ["a", "b"], "expect_all": True}, ["b"])
    assert judge({"expect_no_tools": True}, [])
    assert not judge({"expect_no_tools": True}, ["a"])


def test_eval_runner_with_stub(monkeypatch, conn):
    from app.agent import eval as ev

    def fake_stream(cfg, messages, tools=None, on_delta=None):
        if messages[-1]["role"] == "tool":
            return ChatResult(content="完成")
        return ChatResult(content="", tool_calls=[ChatToolCall("t", "material_master_stats", "{}")])

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    out = io.StringIO()
    s = ev.run_eval(conn, CFG, [{"id": "a", "q": "多少", "expect_tools": ["material_master_stats"]},
                                {"id": "b", "q": "x", "expect_tools": ["bom_items"]}], out=out)
    assert s["passed"] == 1 and s["total"] == 2 and "通过 1/2" in out.getvalue()


def test_templates_have_no_nested_script_tags():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "templates"
    for f in root.glob("*.html"):
        for block in re.findall(r"<script[^>]*>(.*?)</script>", f.read_text(encoding="utf-8"), re.S):
            assert "<script" not in block, f"{f.name} 内嵌了重复的 <script> 标签"


# ---------- 第一阶段收尾：可追溯、会话管理、反馈、用量 ----------

def _two_step(monkeypatch):
    script = iter([
        ChatResult(content="", tool_calls=[ChatToolCall("t1", "search_material_master", '{"keyword": "轴承"}')],
                   prompt_tokens=100, completion_tokens=10),
        ChatResult(content="有 2 个", prompt_tokens=200, completion_tokens=20),
    ])
    monkeypatch.setattr(llm, "chat", lambda cfg, messages, tools=None: next(script))


def test_reply_carries_sources_and_persists(monkeypatch, conn):
    _two_step(monkeypatch)
    r = service.run_chat(conn, CFG, "u1", None, "轴承有几个")
    assert r["message_id"] and r["sources"][0]["name"] == "search_material_master"
    assert r["sources"][0]["args"] == {"keyword": "轴承"} and r["sources"][0]["ok"] is True
    msgs = service.get_messages(conn, "u1", r["session_id"])
    final = [m for m in msgs if m["id"] == r["message_id"]][0]
    assert final["sources"][0]["label"] and final["rating"] is None


def test_session_manage_and_feedback(monkeypatch, conn):
    _two_step(monkeypatch)
    r = service.run_chat(conn, CFG, "u1", None, "轴承有几个")
    sid, mid = r["session_id"], r["message_id"]
    service.rename_session(conn, "u1", sid, "  轴承  统计 ")
    service.pin_session(conn, "u1", sid, True)
    s = service.list_sessions(conn, "u1")[0]
    assert s["title"] == "轴承 统计" and s["pinned"] is True
    with pytest.raises(ValueError):
        service.rename_session(conn, "u2", sid, "x")  # 不是本人会话
    service.set_feedback(conn, "u1", mid, -1, "数字不对")
    assert [m for m in service.get_messages(conn, "u1", sid) if m["id"] == mid][0]["rating"] == -1
    service.set_feedback(conn, "u1", mid, 0)
    assert [m for m in service.get_messages(conn, "u1", sid) if m["id"] == mid][0]["rating"] is None
    with pytest.raises(ValueError):
        service.set_feedback(conn, "u2", mid, 1)
    service.set_feedback(conn, "u1", mid, 1)
    service.delete_session(conn, "u1", sid)
    assert service.list_sessions(conn, "u1") == []
    assert conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_feedback").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_usage").fetchone()[0] == 1  # 用量保留


def test_usage_stats_and_daily_limit(monkeypatch, conn):
    from dataclasses import replace
    _two_step(monkeypatch)
    service.run_chat(conn, CFG, "u1", None, "轴承有几个")
    # 一条 40 天前的记录不应计入
    conn.execute("INSERT INTO agent_usage(session_id,user_id,model,prompt_tokens,completion_tokens,created_at)"
                 " VALUES(NULL,'u2','m2',5,5,datetime('now','-40 days'))")
    st = service.usage_stats(conn, 14)
    assert st["total_calls"] == 1 and st["total_tokens"] == 330
    assert st["by_user"][0] == {"user_id": "u1", "name": "u1", "calls": 1, "tokens": 330, "today_tokens": 330}
    assert st["by_model"][0]["model"] == "m" and st["feedback"] == {"up": 0, "down": 0}
    assert service.today_tokens(conn, "u1") == 330
    with pytest.raises(LLMError, match="上限"):
        service.run_chat(conn, replace(CFG, daily_token_limit=300), "u1", None, "再问")
    _two_step(monkeypatch)
    service.run_chat(conn, replace(CFG, daily_token_limit=300), "u3", None, "别人不受影响")


def test_usage_stats_empty_db():
    c = sqlite3.connect(":memory:")
    st = service.usage_stats(c, 7)
    assert st["total_calls"] == 0 and st["by_day"] == [] and st["by_user"] == []


# ---------- 第二阶段：缺料分析 ----------

@pytest.fixture
def sconn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
    CREATE TABLE materials(id TEXT PRIMARY KEY, code TEXT UNIQUE, name TEXT, unit TEXT, available_quantity INTEGER);
    INSERT INTO materials VALUES('m1','A1','螺栓','个',10),('m2','B2','垫片','个',100),('m3','C3','电机','台',0);
    CREATE TABLE production_orders(id TEXT PRIMARY KEY, order_no TEXT UNIQUE, product_name TEXT, status TEXT);
    INSERT INTO production_orders VALUES('o1','PO-1','产品一','IN_PROGRESS'),('o2','PO-2','产品二','IN_PROGRESS'),
                                         ('o3','PO-3','产品三','COMPLETED');
    CREATE TABLE order_material_requirements(id TEXT, order_id TEXT, device_id TEXT, material_id TEXT,
      required_quantity INT, arrived_quantity INT, in_stock_quantity INT);
    INSERT INTO order_material_requirements VALUES('r1','o1',NULL,'m1',8,8,8),('r2','o1',NULL,'m3',2,1,0),
                                                  ('r3','o3',NULL,'m3',9,0,0);
    CREATE TABLE production_order_models(id TEXT, order_id TEXT, model_code TEXT, planned_quantity INT, bom_version_id TEXT);
    INSERT INTO production_order_models VALUES('pm1','o2','MD-1',3,NULL);
    CREATE TABLE bom_versions(id TEXT, model_code TEXT, version_no INT, status TEXT);
    INSERT INTO bom_versions VALUES('v1','MD-1',1,'PUBLISHED'),('v0','MD-1',2,'DRAFT');
    CREATE TABLE bom_items(bom_version_id TEXT, material_code TEXT, material_name TEXT, unit TEXT, quantity REAL, scrap_rate REAL);
    INSERT INTO bom_items VALUES('v1','A1','螺栓','个',4,0),('v1','B2','垫片','个',8,0.2),('v1','Z9','新料','件',1,0);
    CREATE TABLE agg_bom_batches(id TEXT, project_name TEXT, created_at TEXT);
    CREATE TABLE agg_bom_items(batch_id TEXT, device_code TEXT, material_code TEXT, material_name TEXT, unit TEXT, quantity REAL);
    INSERT INTO agg_bom_batches VALUES('b1','项目','2026-10-01');
    INSERT INTO agg_bom_items VALUES('b1','D1','A1','螺栓','个',6),('b1','D2','A1','螺栓','个',6),('b1','D1','C3','电机','台',1);
    """)
    yield c
    c.close()


def _short(c, **a):
    return T.get_tool(T.ANALYSIS_TOOLS, "material_shortage").run(c, a)


def test_shortage_from_order_requirements(sconn):
    r = _short(sconn, order_no="PO-1")
    assert r["source"] == "订单物料需求" and r["short_types"] == 1
    assert r["items"] == [{"material_code": "C3", "material_name": "电机", "unit": "台", "required": 2, "arrived": 1,
                           "in_stock": 0, "shortage": 2, "not_arrived": 1}]
    assert len(_short(sconn, order_no="PO-1", only_short=False)["items"]) == 2


def test_shortage_from_order_models_bom(sconn):
    r = _short(sconn, order_no="PO-2")
    by = {i["material_code"]: i for i in r["items"]}
    assert by["A1"]["required"] == 12 and by["A1"]["shortage"] == 2
    assert "B2" not in by  # 8×3/(1-0.2)=30 ≤ 100
    assert by["Z9"]["no_stock_record"] is True and by["Z9"]["shortage"] == 3


def test_shortage_by_model_and_devices(sconn):
    r = _short(sconn, model_code="MD-1", quantity=20)
    by = {i["material_code"]: i for i in r["items"]}
    assert by["B2"]["required"] == 200 and by["B2"]["shortage"] == 100
    r = _short(sconn, device_codes=["D1", "D2", "DX"], quantity=1)
    by = {i["material_code"]: i for i in r["items"]}
    assert by["A1"]["shortage"] == 2 and by["C3"]["shortage"] == 1 and r["devices_not_found"] == ["DX"]
    with pytest.raises(T.ToolError):
        _short(sconn, model_code="NOPE")
    with pytest.raises(T.ToolError):
        _short(sconn, model_code="MD-1", quantity=-1)


def test_shortage_all_open_orders(sconn):
    r = _short(sconn)
    assert r["orders_affected"] == 1  # PO-3 已完成，不计
    assert r["items"][0]["material_code"] == "C3" and r["items"][0]["shortage"] == 2


# ---------- 第二阶段：趋势对比 ----------

def test_trend_compare_day_and_breakdown():
    from datetime import datetime, timedelta, timezone
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE exceptions(id TEXT, type TEXT, status TEXT, created_at TEXT)")
    now = datetime.now(timezone.utc)
    rows = [(now, "SHORT"), (now - timedelta(days=1), "SHORT"), (now - timedelta(days=1), "DAMAGE"),
            (now - timedelta(days=8), "SHORT")]
    for i, (t, ty) in enumerate(rows):
        c.execute("INSERT INTO exceptions VALUES(?,?,?,?)", (str(i), ty, "OPEN", t.isoformat()))
    run = T.get_tool(T.ANALYSIS_TOOLS, "trend_compare").run
    r = run(c, {"metric": "exceptions", "granularity": "day", "periods": 7, "group_by": "type"})
    assert len(r["series"]) == 7 and r["current_total"] == 3 and r["previous_total"] == 1
    assert r["change"] == 2 and r["change_pct"] == 200.0
    assert r["breakdown"][0] == {"key": "SHORT", "value": 2}
    assert sum(p["value"] for p in r["series"]) == 3
    with pytest.raises(T.ToolError):
        run(c, {"metric": "nope"})
    with pytest.raises(T.ToolError):
        run(c, {"metric": "exceptions", "group_by": "id; DROP TABLE x"})
    r = run(c, {"metric": "exceptions", "granularity": "month", "periods": 2})
    assert len(r["series"]) == 2 and r["current_total"] == 4
