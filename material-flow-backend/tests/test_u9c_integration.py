"""U9C REST 对接（0.8.0）：授权/token 复用与刷新、ResCode 错误、四类查询映射、同步预览/写入、路由与管理页。

HTTP 一律由 FakeU9 假服务注入（client 的 transport 参数 / monkeypatch _urllib_transport），不访问网络。
"""
from __future__ import annotations

import json
import re
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from app import main as backend
from app.u9 import client as u9client
from app.u9 import config as u9config
from app.u9 import mappers
from app.u9.client import U9AuthError, U9CClient, U9Error
from app.u9.config import U9Config
from app.u9.sync import run_sync

ADMIN = ("u_admin", "owlco", "系统管理员", "ADMIN", "Admin@2026")
PLANNER = ("u_plan", "planner1", "计划员甲", "PLANNER", "Plan@2026x")
SECRET = "test-secret-not-real"


def ref(code, name=""):
    return {"m_iD": 1, "m_code": code, "m_name": name, "sysState": 0}


def item(code, name, spec="", uom="个", form=9):
    return {"m_iD": hash(code) % 10000, "m_code": code, "m_name": name, "m_sPECS": spec,
            "m_inventoryUOM": ref("U" + uom, uom), "m_itemFormAttribute": form,
            "m_mainItemCategory": ref("C01", "原材料")}


def comp(code, name, qty, seq, parent_qty=1, uom="个", **kw):
    return {"m_sequence": seq, "m_itemMaster": ref(code, name), "m_usageQty": qty,
            "m_parentQty": parent_qty, "m_issueUOM": ref("U" + uom, uom), **kw}


class FakeU9:
    """假 U9C：记录请求，按路径返回数据。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.tokens_issued = 0
        self.valid_tokens: set[str] = set()
        self.auth_rescode = 0
        self.items = {
            "M001": item("M001", "螺栓", "M8x20", "个", 9),
            "M002": item("M002", "支架组件", "ZJ-1", "套", 10),
            "M003": item("M003", "钢板", "Q235 2mm", "张", 9),
            "SB01": item("SB01", "一号设备", "SB-1", "台", 10),
        }
        self.boms = {
            "SB01": [{"m_itemMaster": ref("SB01", "一号设备"), "m_bOMVersionCode": "A1", "m_alternateType": 0,
                      "m_bOMType": 0, "m_productUOM": ref("T", "台"),
                      "m_bOMComponents": [comp("M002", "支架组件", 2, 20), comp("M001", "螺栓", 8, 10),
                                          comp("MX", "已删除件", 1, 30, m_isDelete=True)]},
                     {"m_itemMaster": ref("SB01", "一号设备"), "m_bOMVersionCode": "A0", "m_alternateType": 0,
                      "m_bOMType": 0, "m_bOMComponents": [comp("OLD", "旧版件", 1, 10)]}],
            "M002": [{"m_itemMaster": ref("M002", "支架组件"), "m_bOMVersionCode": "1",
                      "m_bOMComponents": [comp("M003", "钢板", 3, 10, parent_qty=2),
                                          comp("SB01", "环路", 1, 20)]}],
        }
        self.inventory = [
            {"m_itemCode": "M001", "m_itemName": "螺栓", "m_whCode": "W01", "m_whName": "原料库",
             "m_binCode": "A1", "m_binName": "A1架", "m_storeQty": 100, "m_canUseQty": 80, "m_lotCode": "L1"},
            {"m_itemCode": "M001", "m_itemName": "螺栓", "m_whCode": "W01", "m_whName": "原料库",
             "m_binCode": "A1", "m_binName": "A1架", "m_storeQty": 20.5, "m_canUseQty": 20, "m_lotCode": "L2"},
            {"m_itemCode": "M003", "m_itemName": "钢板", "m_whCode": "W01", "m_whName": "原料库",
             "m_binCode": "", "m_storeQty": 5, "m_canUseQty": 5},
            {"m_itemCode": "ZZZ", "m_itemName": "本地没有", "m_whCode": "W01", "m_storeQty": 1, "m_canUseQty": 1},
        ]
        self.orders = {
            "MO001": {"m_docNo": "MO001", "m_itemMaster": ref("SB01", "一号设备"), "m_productQty": 2,
                      "m_productUOM": ref("T", "台"), "m_startDate": "2026-10-01 00:00:00",
                      "m_completeDate": "2026-10-20 00:00:00",
                      "m_mOPickListDTOs": [
                          {"m_itemMaster": ref("M001", "螺栓"), "m_actualReqQty": 14.0, "m_issuedQty": 14.0,
                           "m_issueUOM": ref("F040", "个")},
                          {"m_itemMaster": ref("M001", "螺栓"), "m_actualReqQty": 2.0},
                          {"m_itemMaster": ref("NEW9", "新料"), "m_actualReqQty": 0.5,
                           "m_issueUOM": ref("KG", "千克")},
                          {"m_itemMaster": ref("OFF1", "停用"), "m_actualReqQty": 3, "m_isDisable": True},
                      ]},
            "MO002": {"m_docNo": "MO002", "m_itemMaster": ref("SB02", "二号设备"), "m_productQty": 1,
                      "m_docState": 3},
        }
        self.fail_next_post_with: int | None = None
        self.mo_states: dict[str, int] = {}
        self.sqls: list[str] = []

    def __call__(self, method, url, headers, body, timeout, verify):
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        payload = json.loads(body) if body else None
        self.calls.append({"method": method, "path": u.path, "query": q, "headers": dict(headers),
                           "payload": payload})
        if u.path.endswith("/webapi/OAuth2/AuthLogin"):
            if self.auth_rescode:
                return 200, json.dumps({"ResCode": self.auth_rescode, "ResMsg": "x", "Data": None}).encode()
            assert q["clientsecret"] == SECRET and q["clientid"] == "wuliao"
            self.tokens_issued += 1
            tok = f"tok{self.tokens_issued}"
            self.valid_tokens.add(tok)
            return 200, json.dumps({"ResCode": 0, "Success": True, "Data": tok}).encode()
        if headers.get("token") not in self.valid_tokens:
            return 200, json.dumps({"ResCode": 405, "ResMsg": "授权码已过期"}).encode()
        if self.fail_next_post_with is not None:
            code, self.fail_next_post_with = self.fail_next_post_with, None
            return 200, json.dumps({"ResCode": code, "Success": False, "ResMsg": "料号不存在"}).encode(
                "utf-8")
        data: list = []
        if u.path.endswith("/webapi/ItemMaster/Query"):
            data = [self.items[p["ItemMaster"]["Code"]] for p in payload if p["ItemMaster"]["Code"] in self.items]
        elif u.path.endswith("/webapi/BOM/Query"):
            for p in payload:
                data.extend(self.boms.get(p["ItemMaster"]["Code"], []))
        elif u.path.endswith("/webapi/Invtrans/QueryQohAndAvailable"):
            assert isinstance(payload, dict)
            data = [r for r in self.inventory
                    if (not payload.get("ItemCode") or r["m_itemCode"] == payload["ItemCode"])
                    and (not payload.get("Wh") or r["m_whCode"] == payload["Wh"])]
        elif u.path.endswith("/webapi/MODoc/Query"):
            data = [self.orders[p["DocNo"]] for p in payload if p["DocNo"] in self.orders]
        elif u.path.endswith("/webapi/QueryCommon/QueryInfoBySql"):
            sql = payload["SqlString"]
            self.sqls.append(sql)
            if "from CBO_Category c" in sql:
                data = [{"Code": c, "Name": n} for c, n in [("3", "原材物料类"), ("304", "电气元器件类"),
                        ("30404", "电线"), ("30404001", "通用电线"), ("303", "机械标准零部件类")]]
                return 200, json.dumps({"ResCode": 0, "Success": True, "Data": data},
                                       ensure_ascii=False).encode("utf-8")
            if "from CBO_ItemMaster i" in sql:
                data = [{"ItemCode": "M001", "CatCode": "30404001"}, {"ItemCode": "M003", "CatCode": "303"},
                        {"ItemCode": "ZZZ", "CatCode": "303"}]
                return 200, json.dumps({"ResCode": 0, "Success": True, "Data": data}).encode("utf-8")
            if "InvTrans_WhQoh" in sql:
                codes = re.findall(r"'([^']+)'", sql.split("ItemInfo_ItemCode in (", 1)[1]) \
                    if "ItemInfo_ItemCode in (" in sql else None
                data = [{"ItemCode": r["m_itemCode"], "ItemName": r["m_itemName"], "WhCode": r["m_whCode"],
                         "WhName": r.get("m_whName"), "BinCode": r.get("m_binCode", ""),
                         "BinName": r.get("m_binName", ""), "LotCode": r.get("m_lotCode", ""),
                         "StoreQty": r["m_storeQty"], "CanUseQty": r["m_canUseQty"]}
                        for r in self.inventory if codes is None or r["m_itemCode"] in codes]
                return 200, json.dumps({"ResCode": 0, "Success": True, "Data": data},
                                       ensure_ascii=False).encode("utf-8")
            nos = re.findall(r"'([^']+)'", sql.split(" in (", 1)[1])
            data = [{"docno": n, "docstate": st, "productqty": 1.0, "totalcompleteqty": 1.0 if st == 3 else 0.0}
                    for n, st in self.mo_states.items() if n in nos]
        return 200, json.dumps({"ResCode": 0, "Success": True, "ResMsg": "", "Data": data},
                               ensure_ascii=False).encode("utf-8")


def make_cfg(**kw) -> U9Config:
    cfg = U9Config()
    base = dict(enabled=True, mode="u9c", base_url="http://u9.test/u9c", client_id="wuliao",
                client_secret=SECRET, ent_code="03", org_code="01", user_code="1064", batch_size=2,
                inventory_via_sql=False)
    base.update(kw)
    for k, v in base.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture(autouse=True)
def _clean_tokens():
    u9client.clear_token_cache()
    yield
    u9client.clear_token_cache()


@pytest.fixture
def fake():
    return FakeU9()


# ------------------------------------------------------------------ 配置
def test_config_defaults_disabled_and_u9c(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("U9_"):
            monkeypatch.delenv(k, raising=False)
    cfg = U9Config()
    assert cfg.enabled is False and cfg.mode == "u9c" and not cfg.configured and not cfg.ready
    assert "U9_BASE_URL" in cfg.missing_fields() and "U9_CLIENT_SECRET" in cfg.missing_fields()
    monkeypatch.setenv("U9_MODE", "cloud")
    assert U9Config().mode == "u9c"


def test_safe_dict_never_exposes_secret():
    cfg = make_cfg(client_secret="SuperSecretValue123")
    d = cfg.safe_dict()
    assert "SuperSecretValue123" not in json.dumps(d)
    assert d["client_secret_set"] is True and d["client_id"].startswith("wuli") and "*" in d["client_id"]
    assert d["ready"] is True and d["missing"] == []


# ------------------------------------------------------------------ 授权
def test_auth_login_params_and_token_reuse(fake):
    c = U9CClient(make_cfg(), fake)
    c.fetch_items(["M001"])
    c.fetch_items(["M003"])
    logins = [x for x in fake.calls if x["path"].endswith("AuthLogin")]
    assert len(logins) == 1 and fake.tokens_issued == 1
    q = logins[0]["query"]
    assert q == {"clientid": "wuliao", "clientsecret": SECRET, "userCode": "1064", "entcode": "03", "orgcode": "01"}
    assert logins[0]["path"] == "/u9c/webapi/OAuth2/AuthLogin"
    posts = [x for x in fake.calls if x["method"] == "POST"]
    assert all(p["headers"]["token"] == "tok1" for p in posts)
    assert all(p["headers"]["Content-Type"] == "application/json" for p in posts)


def test_token_refresh_once_on_expired(fake):
    c = U9CClient(make_cfg(), fake)
    c.fetch_items(["M001"])
    fake.valid_tokens.clear()  # 服务端让 token 失效
    rows = c.fetch_items(["M001"])
    assert rows and rows[0]["m_code"] == "M001" and fake.tokens_issued == 2


def test_token_ttl_expiry_triggers_relogin(fake, monkeypatch):
    c = U9CClient(make_cfg(token_ttl_seconds=30), fake)
    c.fetch_items(["M001"])
    real = u9client.time.time
    monkeypatch.setattr(u9client.time, "time", lambda: real() + 31)
    c.fetch_items(["M001"])
    assert fake.tokens_issued == 2


@pytest.mark.parametrize("code,hint", [(401, "未授权"), (406, "用户名不存在"), (408, "组织编码不存在"),
                                       (601, "IP 未授权"), (405, "已过期")])
def test_auth_error_codes_chinese(fake, code, hint):
    fake.auth_rescode = code
    with pytest.raises(U9AuthError) as ei:
        U9CClient(make_cfg(), fake).health_check()
    assert hint in str(ei.value) and SECRET not in str(ei.value)


def test_health_check_ok(fake):
    r = U9CClient(make_cfg(), fake).health_check()
    assert r["ok"] is True and isinstance(r["latency_ms"], int) and "tok" not in json.dumps(r)


def test_rescode_error_raises_with_resmsg(fake):
    c = U9CClient(make_cfg(), fake)
    fake.fail_next_post_with = 500
    with pytest.raises(U9Error) as ei:
        c.fetch_inventory()
    assert "ResCode=500" in str(ei.value) and "料号不存在" in str(ei.value)
    # 批量查询里单条业务错误不中断，记入 errors
    fake.fail_next_post_with = 500
    errors: list = []
    assert c.fetch_production_orders(["MO001"], errors=errors) == []
    assert errors[0]["key"] == "MO001" and "料号不存在" in errors[0]["error"]


def test_batch_business_error_falls_back_to_single(fake):
    c = U9CClient(make_cfg(batch_size=2), fake)
    fake.fail_next_post_with = 500  # 整批失败 → 逐条重试
    errors: list = []
    rows = c.fetch_items(["M001", "M003"], errors=errors)
    assert {r["m_code"] for r in rows} == {"M001", "M003"} and errors == []


def test_network_timeout_and_http_errors(fake):
    def boom(*a, **k):
        raise TimeoutError("timed out")
    with pytest.raises(U9Error) as ei:
        U9CClient(make_cfg(), boom).health_check()
    assert "超时" in str(ei.value) and SECRET not in str(ei.value)

    def refused(*a, **k):
        raise u9client.urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    with pytest.raises(U9Error) as ei:
        U9CClient(make_cfg(), refused).health_check()
    assert "无法连接" in str(ei.value) and SECRET not in str(ei.value)

    with pytest.raises(U9Error) as ei:
        U9CClient(make_cfg(), lambda *a, **k: (502, b"<html>Bad Gateway</html>")).health_check()
    assert "HTTP 502" in str(ei.value)


def test_content_type_configurable(fake):
    U9CClient(make_cfg(content_type="application/x-www-form-urlencoded"), fake).fetch_items(["M001"])
    post = [x for x in fake.calls if x["method"] == "POST"][0]
    assert post["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


# ------------------------------------------------------------------ 查询 + 映射
def test_fetch_items_batched_and_mapped(fake):
    c = U9CClient(make_cfg(batch_size=2), fake)
    rows = c.fetch_items(["M001", "M002", "M003", "M001", ""])
    posts = [x for x in fake.calls if x["method"] == "POST"]
    assert len(posts) == 2 and posts[0]["payload"][0] == {"ItemMaster": {"Code": "M001"}, "OtherID": "M001"}
    m = mappers.map_item(rows[0])
    assert m == {"code": "M001", "name": "螺栓", "specification": "M8x20", "unit": "个", "item_form": "采购件",
                 "category": "原材料", "category_code": "C01", "status": "", "u9_id": rows[0]["m_iD"]}
    assert mappers.map_item({"m_code": "X"})["unit"] == "个"
    with pytest.raises(ValueError):
        mappers.map_item({"m_name": "无编码"})


def test_fetch_bom_multilevel_with_cycle_guard(fake):
    c = U9CClient(make_cfg(), fake)
    bom = c.fetch_bom("SB01")
    assert bom["found"] and bom["bom_version"] == "A1" and bom["product_name"] == "一号设备"
    flat = [(ln["level"], ln["parent_code"], ln["material_code"], ln["qty"]) for ln in bom["lines"]]
    assert flat == [(2, "SB01", "M001", 8.0), (2, "SB01", "M002", 2.0),
                    (3, "M002", "M003", 1.5), (3, "M002", "SB01", 1.0)]
    m003 = bom["lines"][2]
    assert m003["spec"] == "Q235 2mm" and m003["unit"] == "个" and m003["material_form"] == "采购件"
    assert any("环路" in e["error"] for e in bom["errors"])
    first = [x for x in fake.calls if x["path"].endswith("/BOM/Query")][0]
    assert first["payload"] == [{"Org": {"Code": "01"}, "ItemMaster": {"Code": "SB01"}, "OtherID": "SB01"}]
    # 采购件 M001 不再查 BOM
    bom_codes = [p["ItemMaster"]["Code"] for x in fake.calls if x["path"].endswith("/BOM/Query") for p in x["payload"]]
    assert "M001" not in bom_codes and "M002" in bom_codes


def test_fetch_bom_depth_limit_and_single_level(fake):
    c = U9CClient(make_cfg(), fake)
    one = c.fetch_bom("SB01", recursive=False)
    assert [ln["level"] for ln in one["lines"]] == [2, 2]
    d1 = c.fetch_bom("SB01", max_depth=1)
    assert [ln["level"] for ln in d1["lines"]] == [2, 2] and d1["truncated"] is True
    assert c.fetch_bom("NOPE")["found"] is False


def test_fetch_inventory_object_body_and_map(fake):
    c = U9CClient(make_cfg(default_wh="W01"), fake)
    rows = c.fetch_inventory()
    post = [x for x in fake.calls if x["method"] == "POST"][0]
    assert post["payload"] == {"Org": "01", "Wh": "W01"}
    assert len(rows) == 4
    rows = c.fetch_inventory(["M003"], wh="")
    assert [x for x in fake.calls if x["method"] == "POST"][-1]["payload"] == {"Org": "01", "ItemCode": "M003"}
    m = mappers.map_inventory_row(fake.inventory[0])
    assert m["location_code"] == "W01-A1" and m["location_name"] == "原料库 / A1架" and m["qty"] == 100
    assert mappers.map_inventory_row(fake.inventory[0], "available")["qty"] == 80
    assert mappers.map_inventory_row(fake.inventory[2])["location_code"] == "W01"


def test_fetch_orders_and_status_mapping(fake):
    rows = U9CClient(make_cfg(), fake).fetch_production_orders(["MO001", "MO002", "MO404"])
    a, b = (mappers.map_production_order(r) for r in rows)
    assert a["order_no"] == "MO001" and a["product_code"] == "SB01" and a["qty"] == 2 and a["status"] is None
    assert a["start_date"] == "2026-10-01"
    assert b["status"] == "COMPLETED" and b["u9_state"] == "完工"
    assert mappers.map_production_order({"m_docNo": "X", "m_docState": 2})["status"] == "IN_PROGRESS"
    assert mappers.map_production_order({"m_docNo": "X", "m_docState": 0})["status"] == "DRAFT"


# ------------------------------------------------------------------ 同步（需要后端库）
def _seed_users():
    c = backend.db()
    for uid, uname, name, role, pwd in (ADMIN, PLANNER):
        c.execute("INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
                  "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (uid, uname, name, role, backend.hash_password(pwd), 0, 1, backend.now()))
    c.commit()
    c.close()


@pytest.fixture
def db(isolated_backend_database):
    _seed_users()
    c = backend.db()
    c.execute("PRAGMA foreign_keys=OFF")
    for tbl in ("inventory", "materials", "production_orders"):
        c.execute(f"DELETE FROM {tbl}")
    c.commit()
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("INSERT INTO materials(id, code, name, specification, unit) VALUES('mat1','M001','旧螺栓','','个')")
    c.execute("INSERT INTO materials(id, code, name, specification, unit) VALUES('mat3','M003','钢板','Q235 2mm','张')")
    c.execute("INSERT INTO production_orders(id, order_no, product_name, status, created_at) "
              "VALUES('po1','MO002','旧名','IN_PROGRESS','2026-10-01T00:00:00')")
    c.commit()
    yield c
    c.close()


def _patch_transport(monkeypatch, fake):
    monkeypatch.setattr(u9client, "_urllib_transport", fake)
    orig = u9client.U9Client.__init__

    def init(self, cfg, transport=None):
        orig(self, cfg, transport or fake)
    monkeypatch.setattr(u9client.U9Client, "__init__", init)


def test_sync_items_dry_run_then_real(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg()
    r = run_sync("items", db, cfg, dry_run=True, codes=["M002"])
    s = r["stats"]
    assert r["status"] == "OK" and s["requested"] == 3 and s["inserted"] == 1 and s["updated"] == 2
    assert s["unchanged"] == 0  # 0.12.0 起分类也参与比较，M003 补分类算更新
    assert db.execute("SELECT name FROM materials WHERE code='M001'").fetchone()[0] == "旧螺栓"
    assert not db.execute("SELECT 1 FROM materials WHERE code='M002'").fetchone()
    assert any(x["动作"] == "更新" and x["原品名"] == "旧螺栓" for x in s["sample"])
    r = run_sync("items", db, cfg, dry_run=False, codes=["M002"])
    assert db.execute("SELECT name, specification FROM materials WHERE code='M001'").fetchone()[:] == ("螺栓", "M8x20")
    assert db.execute("SELECT name, unit, total_quantity FROM materials WHERE code='M002'").fetchone()[:] == ("支架组件", "套", 0)
    again = run_sync("items", db, cfg, dry_run=False, codes=["M002"])["stats"]
    assert again["inserted"] == 0 and again["updated"] == 0 and again["unchanged"] == 3
    log = db.execute("SELECT status, dry_run, detail_json FROM u9_sync_logs WHERE id=?", (r["log_id"],)).fetchone()
    assert log[0] == "OK" and log[1] == 0 and "sample" in json.loads(log[2])


def test_sync_boms_dry_run_and_idempotent_real(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg()
    n0 = db.execute("SELECT COUNT(*) FROM agg_bom_batches").fetchone()[0]
    r = run_sync("boms", db, cfg, dry_run=True, product_codes=["SB01", "NOPE"], user_id="u_admin")
    s = r["stats"]
    assert s["total_lines"] == 5 and s["skipped"] == 1 and s["sample"][0]["层级"] == 1
    assert db.execute("SELECT COUNT(*) FROM agg_bom_batches").fetchone()[0] == n0
    r = run_sync("boms", db, cfg, dry_run=False, product_codes=["SB01"], user_id="u_admin")
    assert r["stats"]["inserted_batches"] == 1
    bid = r["stats"]["results"][0]["batch_id"]
    rows = db.execute("SELECT level_no, parent_code, material_code, quantity, material_form, device_code"
                      " FROM agg_bom_items WHERE batch_id=? ORDER BY line_no", (bid,)).fetchall()
    assert [tuple(x) for x in rows][:3] == [(1, "", "SB01", 1.0, "制造件", "SB01"),
                                           (2, "SB01", "M001", 8.0, "采购件", "SB01"),
                                           (2, "SB01", "M002", 2.0, "制造件", "SB01")]
    b = db.execute("SELECT project_name, file_name, created_by FROM agg_bom_batches WHERE id=?", (bid,)).fetchone()
    assert b[:] == ("U9:一号设备", "U9C BOM SB01 vA1", "u_admin")
    again = run_sync("boms", db, cfg, dry_run=False, product_codes=["SB01"])["stats"]
    assert again["inserted_batches"] == 0 and again["unchanged"] == 1


def test_sync_inventory_dry_run_then_real(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg(default_wh="W01")
    s = run_sync("inventory", db, cfg, dry_run=True, scope="all")["stats"]
    assert s["total"] == 4 and s["inserted"] == 2 and s["unknown_materials"] == ["ZZZ"]
    assert s["fractional_rounded"] == 1
    assert db.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0
    run_sync("inventory", db, cfg, dry_run=False, scope="all")
    inv = dict(db.execute("SELECT l.code, i.quantity FROM inventory i JOIN locations l ON l.id=i.location_id"
                          " JOIN materials m ON m.id=i.material_id WHERE m.code='M001'").fetchall())
    assert inv == {"W01-A1": 120}
    tot = db.execute("SELECT total_quantity, available_quantity FROM materials WHERE code='M001'").fetchone()
    assert tot[:] == (120, 100)
    again = run_sync("inventory", db, cfg, dry_run=False, scope="all")["stats"]
    assert again["inserted"] == 0 and again["updated"] == 0 and again["unchanged"] == 2


def test_sync_inventory_defaults_to_local_codes_per_item(db, fake, monkeypatch):
    """0.8.1：默认按本地料号逐个查询（现场整体查询会报 SQL CLR 错误）。"""
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg(default_wh="W01", concurrency=3)
    s = run_sync("inventory", db, cfg, dry_run=True)["stats"]
    assert s["scope"] == "local" and s["requested"] == 2 and s["total"] == 3 and s["unknown_count"] == 0
    bodies = [x["payload"] for x in fake.calls if x["method"] == "POST"]
    assert sorted(b["ItemCode"] for b in bodies) == ["M001", "M003"]
    assert all(b["Org"] == "01" and b["Wh"] == "W01" for b in bodies)
    assert fake.tokens_issued == 1


def test_clr_error_gets_chinese_hint(fake):
    c = U9CClient(make_cfg(), fake)
    orig = fake.__call__

    def clr(method, url, headers, body, timeout, verify):
        if url.endswith("/QueryQohAndAvailable"):
            orig(method, url, headers, body, timeout, verify)
            msg = ("在尝试加载程序集 ID 65536 时 Microsoft .NET Framework 出错。有关此错误的详细信息: "
                   "System.IO.FileLoadException: 未能加载文件或程序集“ufida.u9.sql.clrlib”")
            return 200, json.dumps({"ResCode": 500, "ResMsg": msg}).encode()
        return orig(method, url, headers, body, timeout, verify)
    c.transport = clr
    with pytest.raises(U9Error) as ei:
        c.fetch_inventory()
    assert "SQL CLR" in str(ei.value) and "FileLoadException" not in str(ei.value)
    errors: list = []
    assert c.fetch_inventory(["M001"], errors=errors) == [] and "SQL CLR" in errors[0]["error"]


def test_default_token_ttl_is_under_five_minutes(monkeypatch):
    monkeypatch.delenv("U9_TOKEN_TTL_SECONDS", raising=False)
    assert U9Config().token_ttl_seconds < 300


def test_sync_orders_dry_run_then_real(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg()
    s = run_sync("orders", db, cfg, dry_run=True, doc_nos=["MO001", "MO404"])["stats"]
    assert s["requested"] == 3 and s["inserted"] == 1 and s["updated"] == 1 and s["not_found"] == ["MO404"]
    assert not db.execute("SELECT 1 FROM production_orders WHERE order_no='MO001'").fetchone()
    run_sync("orders", db, cfg, dry_run=False, doc_nos=["MO001"])
    assert db.execute("SELECT product_name, status FROM production_orders WHERE order_no='MO001'").fetchone()[:] \
        == ("一号设备", "RELEASED")
    created = db.execute("SELECT created_at FROM production_orders WHERE order_no='MO001'").fetchone()[0]
    assert created.endswith("+00:00")  # 存 UTC，显示层再转北京时间
    assert db.execute("SELECT product_name, status FROM production_orders WHERE order_no='MO002'").fetchone()[:] \
        == ("二号设备", "COMPLETED")
    # 状态只前进：U9 回到「开立」不会把已完成拉回
    fake.orders["MO002"]["m_docState"] = 0
    run_sync("orders", db, cfg, dry_run=False)
    assert db.execute("SELECT status FROM production_orders WHERE order_no='MO002'").fetchone()[0] == "COMPLETED"


def test_sync_failure_logged(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    fake.auth_rescode = 601
    with pytest.raises(U9AuthError):
        run_sync("items", db, make_cfg(), dry_run=True)
    row = db.execute("SELECT status, error FROM u9_sync_logs ORDER BY started_at DESC LIMIT 1").fetchone()
    assert row[0] == "FAILED" and "IP" in row[1] and SECRET not in row[1]


def test_classic_mode_still_pending_doc(db):
    cfg = make_cfg(mode="classic", wsdl_url="http://x?wsdl", username="u", password="p")
    r = run_sync("items", db, cfg, dry_run=True)
    assert r["status"] == "PENDING_DOC"


# ------------------------------------------------------------------ 路由 / 管理页
def _api_token(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password,
                                                "deviceId": "pytest-u9"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("accessToken") or body.get("token") or body["data"]["accessToken"]


def _csrf(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def _web_login(client, username, password):
    page = client.get("/admin/login")
    r = client.post("/admin/login", data={"username": username, "password": password,
                                          "csrf_token": _csrf(page.text)}, follow_redirects=False)
    assert r.status_code == 303


def test_routes_503_when_disabled(db, monkeypatch):
    monkeypatch.setattr(u9config, "_config", make_cfg(enabled=False))
    with TestClient(backend.app) as client:
        tok = _api_token(client, ADMIN[1], ADMIN[4])
        h = {"Authorization": f"Bearer {tok}"}
        r = client.post("/api/v1/u9/sync/items", headers=h)
        assert r.status_code == 503 and "未启用" in r.text
        assert client.post("/api/v1/u9/health", headers=h).status_code == 503
        st = client.get("/api/v1/u9/status", headers=h)
        assert st.status_code == 200 and SECRET not in st.text and st.json()["config"]["enabled"] is False
    monkeypatch.setattr(u9config, "_config", make_cfg(client_secret=""))
    with TestClient(backend.app) as client:
        tok = _api_token(client, ADMIN[1], ADMIN[4])
        r = client.post("/api/v1/u9/sync/items", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 503 and "U9_CLIENT_SECRET" in r.text


def test_routes_admin_only_and_sync_ok(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    monkeypatch.setattr(u9config, "_config", make_cfg(default_wh="W01"))
    with TestClient(backend.app) as client:
        ptok = _api_token(client, PLANNER[1], PLANNER[4])
        assert client.post("/api/v1/u9/sync/items",
                           headers={"Authorization": f"Bearer {ptok}"}).status_code == 403
        h = {"Authorization": f"Bearer {_api_token(client, ADMIN[1], ADMIN[4])}"}
        assert client.post("/api/v1/u9/health", headers=h).json()["ok"] is True
        r = client.post("/api/v1/u9/sync/items?codes=M002", headers=h)
        assert r.status_code == 200 and r.json()["dry_run"] is True and r.json()["stats"]["inserted"] == 1
        assert client.post("/api/v1/u9/sync/boms", headers=h).status_code == 400
        r = client.post("/api/v1/u9/sync/boms?product_codes=SB01", headers=h)
        assert r.status_code == 200 and r.json()["stats"]["total_lines"] == 5
        r = client.post("/api/v1/u9/sync/orders?doc_nos=MO001&include_local=false", headers=h)
        assert r.json()["stats"]["requested"] == 1
        det = client.get(f"/api/v1/u9/logs/{r.json()['log_id']}", headers=h)
        assert det.status_code == 200 and det.json()["detail"]["sample"][0]["单号"] == "MO001"
        fake.auth_rescode = 408
        u9client.clear_token_cache()
        r = client.post("/api/v1/u9/sync/inventory", headers=h)
        assert r.status_code == 502 and "组织编码不存在" in r.text


def test_admin_page_disabled_buttons_and_hint(db, monkeypatch):
    monkeypatch.setattr(u9config, "_config", make_cfg(enabled=False, client_secret=""))
    with TestClient(backend.app) as client:
        _web_login(client, ADMIN[1], ADMIN[4])
        html = client.get("/admin/u9").text
        assert "测试连接" in html and "启用前待办" in html and "U9_CLIENT_SECRET" in html
        assert re.search(r'<button[^>]*>测试连接</button>', html) and \
            "disabled" in re.search(r'<button[^>]*>测试连接', html).group(0)
        assert all("disabled" in b for b in re.findall(r'<button[^>]*name="dry_run"[^>]*>', html))
        assert SECRET not in html
        r = client.post("/admin/u9/health", data={"csrf_token": _csrf(html)}, follow_redirects=False)
        assert r.status_code == 303 and "msg=" in r.headers["location"]


def test_admin_page_preview_flow(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    monkeypatch.setattr(u9config, "_config", make_cfg())
    with TestClient(backend.app) as client:
        _web_login(client, ADMIN[1], ADMIN[4])
        html = client.get("/admin/u9").text
        assert SECRET not in html and "已设置（不显示）" in html
        assert not re.search(r'<button[^>]*disabled[^>]*>测试连接', html)
        csrf = _csrf(html)
        r = client.post("/admin/u9/health", data={"csrf_token": csrf}, follow_redirects=False)
        assert "ok=1" in r.headers["location"]
        r = client.post("/admin/u9/sync/items", data={"csrf_token": csrf, "dry_run": "1", "codes": "M002",
                                                      "include_local": "1"}, follow_redirects=False)
        assert r.status_code == 303 and "run=" in r.headers["location"]
        page = client.get(r.headers["location"]).text
        assert "本次预览结果" in page and "支架组件" in page and "预览，未写入" in page
        r = client.post("/admin/u9/sync/boms", data={"csrf_token": csrf, "dry_run": "1"}, follow_redirects=False)
        assert "ok=0" in r.headers["location"]
        c2 = backend.db()
        assert not c2.execute("SELECT 1 FROM materials WHERE code='M002'").fetchone()
        c2.close()


# ------------------------------------------------------------------ 0.8.2：同步不阻塞服务
def test_sync_single_flight_rejects_concurrent(db, fake, monkeypatch):
    from app.u9 import sync as u9sync
    _patch_transport(monkeypatch, fake)
    assert u9sync._SYNC_LOCK.acquire(blocking=False)
    try:
        u9sync._RUNNING.update(entity="orders", started_at="2026-10-13T19:31:00")
        with pytest.raises(u9sync.U9SyncBusy, match="已有 U9 同步在运行"):
            run_sync("items", db, make_cfg(), dry_run=True)
    finally:
        u9sync._RUNNING.clear()
        u9sync._SYNC_LOCK.release()
    assert run_sync("items", db, make_cfg(), dry_run=True)["status"] == "OK"
    assert u9sync.running_sync() is None and not u9sync._SYNC_LOCK.locked()


def test_sync_lock_released_after_failure(db, fake, monkeypatch):
    from app.u9 import sync as u9sync
    _patch_transport(monkeypatch, fake)
    fake.auth_rescode = 408
    with pytest.raises(U9Error):
        run_sync("items", db, make_cfg(), dry_run=True)
    assert not u9sync._SYNC_LOCK.locked()


def test_sync_deadline_aborts(db, fake, monkeypatch):
    from app.u9 import sync as u9sync
    _patch_transport(monkeypatch, fake)
    clock = iter([0.0] + [1000.0] * 1000)
    monkeypatch.setattr(u9sync.time, "monotonic", lambda: next(clock))
    with pytest.raises(u9client.U9DeadlineExceeded, match="超过时限"):
        run_sync("items", db, make_cfg(sync_max_seconds=10), dry_run=True, codes=["M002"])
    row = db.execute("SELECT status, error FROM u9_sync_logs ORDER BY started_at DESC LIMIT 1").fetchone()
    assert row[0] == "FAILED" and "超过时限" in row[1]


def test_sync_key_and_bom_limits(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    with pytest.raises(U9Error, match="U9_SYNC_MAX_KEYS"):
        run_sync("orders", db, make_cfg(sync_max_keys=2), dry_run=True, doc_nos=["A", "B", "C"])
    with pytest.raises(U9Error, match="U9_BOM_MAX_PRODUCTS"):
        run_sync("boms", db, make_cfg(bom_max_products=1), dry_run=True, product_codes=["SB01", "SB02"])


def test_admin_sync_busy_message_and_api_409(db, fake, monkeypatch):
    from app.u9 import sync as u9sync
    _patch_transport(monkeypatch, fake)
    monkeypatch.setattr(u9config, "_config", make_cfg())
    with TestClient(backend.app) as client:
        _web_login(client, ADMIN[1], ADMIN[4])
        csrf = _csrf(client.get("/admin/u9").text)
        h = {"Authorization": f"Bearer {_api_token(client, ADMIN[1], ADMIN[4])}"}
        assert u9sync._SYNC_LOCK.acquire(blocking=False)
        try:
            r = client.post("/admin/u9/sync/orders", data={"csrf_token": csrf, "dry_run": "1"},
                            follow_redirects=False)
            loc = urllib.parse.unquote(r.headers["location"])
            assert r.status_code == 303 and "ok=0" in loc and "已有 U9 同步在运行" in loc
            assert client.post("/api/v1/u9/sync/items", headers=h).status_code == 409
            # 同步占用期间其他请求照常响应
            assert client.get("/healthz").status_code == 200
        finally:
            u9sync._SYNC_LOCK.release()


def test_admin_sync_does_not_block_event_loop(db, fake, monkeypatch):
    """同步在线程池执行：一个慢同步进行中，/healthz 仍能立即返回。"""
    import threading
    import time as _t
    from app.u9 import sync as u9sync
    _patch_transport(monkeypatch, fake)
    monkeypatch.setattr(u9config, "_config", make_cfg())
    gate = threading.Event()
    orig = u9sync._SYNC_FNS["orders"]

    def slow(*a, **k):
        gate.wait(5)
        return orig(*a, **k)
    monkeypatch.setitem(u9sync._SYNC_FNS, "orders", slow)
    import httpx
    import asyncio

    async def scenario():
        transport = httpx.ASGITransport(app=backend.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            page = await ac.get("/admin/login")
            await ac.post("/admin/login", data={"username": ADMIN[1], "password": ADMIN[4],
                                                "csrf_token": _csrf(page.text)})
            csrf = _csrf((await ac.get("/admin/u9")).text)
            task = asyncio.create_task(ac.post("/admin/u9/sync/orders",
                                               data={"csrf_token": csrf, "dry_run": "1"}))
            await asyncio.sleep(0.2)
            t0 = _t.monotonic()
            hz = await ac.get("/healthz")
            dt = _t.monotonic() - t0
            gate.set()
            r = await task
            return hz.status_code, dt, r.status_code
    code, dt, sync_code = asyncio.run(scenario())
    assert code == 200 and dt < 2 and sync_code == 303


def test_migrate_u9_naive_times(db):
    db.execute("INSERT INTO production_orders(id, order_no, product_name, status, created_at, updated_at) "
               "VALUES('u9mo-MOX','MOX','x','IN_PROGRESS','2026-10-14T16:10:05','2026-10-14T16:10:05')")
    db.execute("UPDATE production_orders SET created_at='2026-10-14T16:10:05' WHERE id='po1'")
    db.commit()
    backend._migrate_u9_naive_times(db)
    backend._migrate_u9_naive_times(db)  # 可重复执行
    assert db.execute("SELECT created_at, updated_at FROM production_orders WHERE id='u9mo-MOX'").fetchone()[:] \
        == ("2026-10-14T08:10:05+00:00", "2026-10-14T08:10:05+00:00")
    # 非 U9 来源的订单不动
    assert db.execute("SELECT created_at FROM production_orders WHERE id='po1'").fetchone()[0] == "2026-10-14T16:10:05"


def test_sync_orders_writes_picklist_requirements(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg()
    s = run_sync("orders", db, cfg, dry_run=True, doc_nos=["MO001"])["stats"]
    assert s["requirements_inserted"] == 0 and s["sample"][0]["备料行"] == 2
    assert not db.execute("SELECT 1 FROM order_material_requirements WHERE order_id='u9mo-MO001'").fetchone()
    s = run_sync("orders", db, cfg, dry_run=False, doc_nos=["MO001"])["stats"]
    assert s["requirements_inserted"] == 2 and s["materials_created"] == 1
    rows = dict(db.execute("SELECT m.code, r.required_quantity FROM order_material_requirements r"
                           " JOIN materials m ON m.id=r.material_id WHERE r.order_id='u9mo-MO001'"
                           " AND r.device_id IS NULL").fetchall())
    assert rows == {"M001": 16, "NEW9": 1}
    assert db.execute("SELECT unit FROM materials WHERE code='NEW9'").fetchone()[0] == "千克"
    # 再同步：不重复；U9 需求量变化则更新
    fake.orders["MO001"]["m_mOPickListDTOs"][1]["m_actualReqQty"] = 6.0
    s = run_sync("orders", db, cfg, dry_run=False, doc_nos=["MO001"])["stats"]
    assert s["requirements_inserted"] == 0 and s["requirements_updated"] == 1
    assert db.execute("SELECT COUNT(*) FROM order_material_requirements WHERE order_id='u9mo-MO001'").fetchone()[0] == 2


def test_picklist_requirements_visible_in_order_detail(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    run_sync("orders", db, make_cfg(), dry_run=False, doc_nos=["MO001"])
    db.commit()
    admin = db.execute("SELECT * FROM users WHERE id=?", (ADMIN[0],)).fetchone()
    d = backend.order_detail(order_no="MO001", page=1, pageSize=500, user=admin)
    text = json.dumps(d, ensure_ascii=False)
    assert "M001" in text and "NEW9" in text


def test_sync_orders_reads_doc_state_via_sql(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg()
    fake.mo_states = {"MO001": 2}
    run_sync("orders", db, cfg, dry_run=False, doc_nos=["MO001"])
    assert db.execute("SELECT status FROM production_orders WHERE order_no='MO001'").fetchone()[0] == "IN_PROGRESS"
    assert "o.Code='01'" in fake.sqls[-1] and "'MO001'" in fake.sqls[-1]
    fake.mo_states = {"MO001": 3}
    s = run_sync("orders", db, cfg, dry_run=False, doc_nos=["MO001"])["stats"]
    assert db.execute("SELECT status FROM production_orders WHERE order_no='MO001'").fetchone()[0] == "COMPLETED"
    assert any(x["U9状态"] == "完工" for x in s["sample"])


def test_mo_state_sql_rejects_unsafe_doc_no(fake):
    errors: list = []
    out = U9CClient(make_cfg(), fake).fetch_mo_states(["MO001", "X'; drop table MO_MO;--"], errors=errors)
    assert out == {} and len(fake.sqls) == 1 and "drop" not in fake.sqls[0]
    assert errors and "非法字符" in errors[0]["error"]


def test_mo_state_sql_failure_does_not_break_sync(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    def boom(self, *a, **k):
        raise u9client.U9Error("no sql", res_code=500)
    monkeypatch.setattr(u9client.U9CClient, "fetch_mo_states", boom)
    s = run_sync("orders", db, make_cfg(), dry_run=False, doc_nos=["MO001"])["stats"]
    assert s["inserted"] == 1 and any("订单状态" in e["key"] for e in s["errors"])


def test_inventory_via_sql_bypasses_invtrans(fake):
    c = U9CClient(make_cfg(inventory_via_sql=True), fake)
    rows = c.fetch_inventory(["M001", "M003", "bad'code"], wh="", errors=(errs := []))
    assert {r["m_itemCode"] for r in rows} == {"M001", "M003"}
    assert not any(x["path"].endswith("/webapi/Invtrans/QueryQohAndAvailable") for x in fake.calls)
    sql = fake.sqls[-1]
    assert "o.Code='01'" in sql and "bad" not in sql and errs and "非法字符" in errs[0]["error"]
    m = mappers.map_inventory_row(rows[0])
    assert m["location_code"] == "W01-A1" and m["qty"] == 100
    c.fetch_inventory(["M001"], wh="W01")
    assert "w.Code='W01'" in fake.sqls[-1]
    with pytest.raises(u9client.U9Error):
        c.fetch_inventory(["M001"], wh="W01' or 1=1--")


def test_sync_inventory_via_sql(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    s = run_sync("inventory", db, make_cfg(inventory_via_sql=True), dry_run=True)["stats"]
    assert s["total"] >= 2 and not s.get("errors")


def test_build_category_tree():
    t = {x["code"]: x for x in mappers.build_category_tree(
        [{"code": "3", "name": "原材"}, {"code": "30404001", "name": "通用电线"},
         {"code": "304", "name": "电气"}, {"code": "9", "name": None}])}
    assert t["3"]["parent_code"] is None and t["3"]["level"] == 1
    assert t["304"]["parent_code"] == "3" and t["304"]["level"] == 2
    assert t["30404001"]["parent_code"] == "304" and t["30404001"]["level"] == 3
    assert t["9"]["name"] == "9"


def test_sync_categories_and_warehouse_tree(db, fake, monkeypatch):
    _patch_transport(monkeypatch, fake)
    cfg = make_cfg()
    dry = run_sync("categories", db, cfg, dry_run=True)["stats"]
    assert dry["categories"] == 5 and dry["materials_updated"] == 2
    assert db.execute("SELECT COUNT(*) FROM material_categories").fetchone()[0] == 0
    run_sync("categories", db, cfg, dry_run=False)
    db.commit()
    assert db.execute("SELECT category, category_code FROM materials WHERE code='M001'").fetchone()[:] == \
        ("通用电线", "30404001")
    assert db.execute("SELECT parent_code FROM material_categories WHERE code='30404001'").fetchone()[0] == "30404"
    again = run_sync("categories", db, cfg, dry_run=True)["stats"]
    assert again["inserted"] == 0 and again["materials_updated"] == 0
    from app.admin_web import _category_tree_view, _inventory_list
    v = _category_tree_view(db, "c:304")
    assert [p["code"] for p in v["tree_path"]] == ["3", "304"]
    assert [(o["code"], o["count"]) for o in v["tree_options"]] == [("3", 2), ("303", 1), ("304", 1)]
    assert v["tree_children"][0]["code"] == "30404"
    items, paging, _ = _inventory_list(db, "", "c:304", 1)
    assert [i["code"] for i in items] == ["M001"]
    items, _, _ = _inventory_list(db, "", "c:3", 1)
    assert {i["code"] for i in items} == {"M001", "M003"}
    items, _, _ = _inventory_list(db, "", "c:3%", 1)
    assert {i["code"] for i in items} == {"M001", "M003"}


def test_sql_code_whitelist_allows_plus_and_cjk(fake):
    from app.u9.client import _code_ok
    assert _code_ok("30201001-00001+A") and _code_ok("AL-型材-002") and _code_ok("MO-2608130010")
    for bad in ("a'b", 'a"b', "a;b", "a--b", "a/*b", "a\\b", "a\nb", ""):
        assert not _code_ok(bad)
    c = U9CClient(make_cfg(inventory_via_sql=True), fake)
    errs = []
    c.fetch_inventory(["30201001-00001+A", "AL-型材-002", "x'--"], wh="", errors=errs)
    sql = fake.sqls[-1]
    assert "'30201001-00001+A'" in sql and "'AL-型材-002'" in sql and "x'" not in sql
    assert len(errs) == 1
