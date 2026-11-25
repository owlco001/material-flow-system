"""需求 3：缺料物料状态（四档）+ U9 采购订单同步。

覆盖验收要求：四档边界（正好覆盖 / 差 1 件）、判定顺序、已关闭 PO 不计在途、
已到货未入库不计入在途、PO 数据为空时降级（poDataFresh=false 且页面提示）、
料号体系不一致 → UNKNOWN_MATERIAL、同步幂等（重复同步不产生重复行）、
U9 缺字段时必须报错而不是当 0；并保证需求 2 的缺口数字不变。
"""
from __future__ import annotations

import json
import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

from app import main as backend  # noqa: E402
from app.u9 import sync as u9_sync  # noqa: E402
from app.u9 import mappers as u9_mappers  # noqa: E402
from app.u9.client import U9CClient, U9Error  # noqa: E402
from app.u9.config import U9Config  # noqa: E402
from app.xlsx_parser import XlsxSheetReader  # noqa: E402

WORKER_PASSWORD = "Worker@2026"
PROJECT = "JSBY-26B-300"

SHORTAGE_HEADER = (
    "料号", "品名", "规格", "单位", "采购缺口", "备料缺口", "在库量", "影响机台数",
    "影响机台", "最不利状态", "关联订单号",
    "物料状态", "在库可用量", "已到货未入库", "在途量", "可覆盖量", "未结案PO", "供应商",
)


# ---------------------------------------------------------------- seeding


def add_user(uid: str, role: str) -> None:
    c = backend.db()
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, uid, uid, role, backend.hash_password(WORKER_PASSWORD), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def add_project(order_no: str, devices: list[str]) -> dict[str, str]:
    c = backend.db()
    ts = backend.now()
    order_id = f"po-{order_no}"
    c.execute(
        "INSERT INTO production_orders(id,order_no,product_name,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)", (order_id, order_no, f"{order_no} 项目", "IN_PROGRESS", ts, ts))
    ids = {}
    for index, device_no in enumerate(devices, 1):
        device_id = f"od-{order_no}-{index}"
        c.execute(
            "INSERT INTO order_devices(id,order_id,device_type,device_no,sequence_no,created_at)"
            " VALUES(?,?,?,?,?,?)", (device_id, order_id, "机台", device_no, index, ts))
        ids[device_no] = device_id
    c.commit()
    c.close()
    return ids


def add_requirement(order_no: str, device_id: str, material_code: str, *,
                    required: int, arrived: int = 0, in_stock: int = 0,
                    available: int = 0, status: str = "OUT_OF_STOCK") -> None:
    c = backend.db()
    c.execute("INSERT OR IGNORE INTO materials(id,code,name,specification,unit,available_quantity)"
              " VALUES(?,?,?,?,?,?)",
              (f"mat-{material_code}", material_code, f"品名-{material_code}", "规格", "件",
               available))
    c.execute("UPDATE materials SET available_quantity=? WHERE code=?", (available, material_code))
    order_id = c.execute("SELECT id FROM production_orders WHERE order_no=?",
                         (order_no,)).fetchone()["id"]
    ts = backend.now()
    c.execute(
        "INSERT INTO order_material_requirements(id,order_id,device_id,material_id,"
        "required_quantity,arrived_quantity,in_stock_quantity,status_code,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f"req-{order_no}-{device_id}-{material_code}", order_id, device_id,
         f"mat-{material_code}", required, arrived, in_stock, status, ts, ts))
    c.commit()
    c.close()


@pytest.fixture()
def seeded():
    add_user("ss-admin", "ADMIN")
    add_user("ss-sup", "WORKSHOP_SUPERVISOR")
    add_user("ss-wh", "WAREHOUSE_ADMIN")
    devices = add_project(PROJECT, ["D1", "D2", "D3", "D4", "D5", "D6"])
    # 1) 在库可满足：可用量正好等于采购缺口（10）
    add_requirement(PROJECT, devices["D1"], "S-STOCK", required=10, available=10)
    # 1 的边界：差 1 件（可覆盖 9 < 缺口 10）→ 不得判「在库可满足」
    add_requirement(PROJECT, devices["D1"], "S-STOCK-M1", required=10, available=9)
    # 2) 已到货待入库：未结案 PO 已到货 100 = 缺口 100
    add_requirement(PROJECT, devices["D2"], "S-ARRIVED", required=100)
    # 3) 在途采购：未到货 50 = 缺口 50
    add_requirement(PROJECT, devices["D3"], "S-TRANSIT", required=50)
    # 3 的部分覆盖：在途 15 < 缺口 40，仍算在途（可覆盖量 15）
    add_requirement(PROJECT, devices["D4"], "S-PARTIAL", required=40)
    # 4) 无采购计划
    add_requirement(PROJECT, devices["D5"], "S-NOPLAN", required=20)
    # 已关闭 PO 覆盖的料号：不得计入在途 → 仍是「无采购计划」
    add_requirement(PROJECT, devices["D6"], "S-CLOSED", required=30)
    # 料号体系不一致：U9 料品档案里没有
    add_requirement(PROJECT, devices["D6"], "S-UNKNOWN", required=5)
    return devices


# ---------------------------------------------------------------- U9 夹具


def po_row(doc_no: str, lines: list[tuple], *, supplier: str = "苏州供应商",
           status_value: str = "2", status_name: str = "Approved",
           business_date: str = "2026-11-01") -> dict:
    return {
        "DocNo": doc_no,
        "BusinessDate": business_date,
        "Status": {"Value": status_value, "Name": status_name},
        "Supplier_Code": f"SUP-{doc_no}",
        "Supplier_ShortName": supplier,
        "DeliveryDate": "2026-12-01",
        "POLines": [
            {"DocLineNo": line_no,
             "ItemInfo": {"ItemCode": code, "ItemName": f"品名-{code}"},
             "PurQtyTU": pur, "TotalRecievedQtyTU": recv,
             "DeliveryDate": "2026-12-01"}
            for (line_no, code, pur, recv) in lines
        ],
    }


def default_po_rows() -> list[dict]:
    return [
        po_row("PO-2601", [(1, "S-ARRIVED", 100, 100)], supplier="宁波到货供应商"),
        po_row("PO-2602", [(1, "S-TRANSIT", 50, 0)], supplier="上海在途供应商"),
        po_row("PO-2603", [(1, "S-PARTIAL", 15, 0)]),
        # 已关闭：即使本地存了这行，也不能算在途
        po_row("PO-2604", [(1, "S-CLOSED", 30, 0)], status_value="4", status_name="Closed"),
    ]


class FakePOClient:
    """假 U9 客户端：只实现采购订单与料品档案两条查询。

    料品档案默认「除了 unknown_codes 之外全认识」——这样本地其他项目/演示数据里的需求料号
    不会被误判成料号体系不一致，只有 S-UNKNOWN 命中 UNKNOWN_MATERIAL。
    """

    def __init__(self, rows: list[dict], unknown_codes=("S-UNKNOWN",)):
        self.rows = rows
        self.unknown_codes = set(unknown_codes)
        self.calls: list[tuple] = []

    def fetch_purchase_orders(self, since_days=180, only_open=True, errors=None):
        self.calls.append(("po", since_days, only_open))
        rows = list(self.rows)
        if only_open:
            from app.u9.mappers import po_status_closed, po_status_pair
            rows = [r for r in rows
                    if not po_status_closed(*po_status_pair(r))]
        return rows

    def fetch_items(self, codes, errors=None):
        self.calls.append(("items", tuple(codes)))
        return [{"m_code": c, "m_name": f"品名-{c}"}
                for c in codes if str(c) not in self.unknown_codes]


def sync_po(rows: list[dict] | None = None, *, only_open: bool | None = False,
            unknown_codes=("S-UNKNOWN",), dry_run: bool = False,
            check_items: bool = True) -> dict:
    """跑一次 po 同步。``only_open=None`` 表示不传该参数，走服务端默认口径（P0-1 回归用）。"""
    client = FakePOClient(rows if rows is not None else default_po_rows(), unknown_codes)
    kwargs = {"dry_run": dry_run, "client": client, "since_days": 180,
              "check_items": check_items}
    if only_open is not None:
        kwargs["only_open"] = only_open
    c = backend.db()
    try:
        return u9_sync.run_sync("po", c, U9Config(), **kwargs)
    finally:
        c.close()


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": WORKER_PASSWORD,
              "deviceId": f"dev-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def shortage(client: TestClient, token: str, **params) -> dict:
    response = client.get(f"/api/v1/projects/{PROJECT}/shortage-list",
                          params=params, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def by_code(body: dict) -> dict[str, dict]:
    return {item["materialCode"]: item for item in body["items"]}


# ---------------------------------------------------------------- 四档判定（纯函数）


def test_status_boundaries_exact_cover_and_off_by_one():
    """每档边界：正好覆盖 → 该档；差 1 件 → 落到下一档。"""
    f = backend._shortage_status_code
    # 在库可满足：可用量 = 缺口（正好）；差 1 件 → 落到已到货
    assert f(gap=10, available=10, arrived=0, in_transit=0) == "IN_STOCK_READY"
    assert f(gap=10, available=9, arrived=10, in_transit=0) == "ARRIVED_NOT_STOCKED"
    # 已到货待入库：已到货 = 缺口（正好）；差 1 件 → 落到在途
    assert f(gap=10, available=0, arrived=10, in_transit=0) == "ARRIVED_NOT_STOCKED"
    assert f(gap=10, available=0, arrived=9, in_transit=1) == "IN_TRANSIT"
    # 在途：有未到货量即算在途（部分覆盖也给在途，不误报「无采购计划」）
    assert f(gap=10, available=0, arrived=0, in_transit=10) == "IN_TRANSIT"
    assert f(gap=10, available=0, arrived=0, in_transit=1) == "IN_TRANSIT"
    # 无采购计划：三者都不能覆盖，且没有未结案 PO 未到货量
    assert f(gap=10, available=0, arrived=0, in_transit=0) == "NO_PLAN"
    assert f(gap=10, available=9, arrived=9, in_transit=0) == "NO_PLAN"


def test_status_order_stock_before_arrived_before_transit():
    """判定顺序严格 1→2→3→4：前档满足时不能被后档抢走。"""
    f = backend._shortage_status_code
    # 三档都够：必须判「在库可满足」
    assert f(gap=5, available=5, arrived=999, in_transit=999) == "IN_STOCK_READY"
    # 在库不够、到货与在途都够：必须判「已到货待入库」
    assert f(gap=5, available=4, arrived=5, in_transit=999) == "ARRIVED_NOT_STOCKED"
    # 在库/到货都不够、在途够：判「在途采购」
    assert f(gap=5, available=4, arrived=4, in_transit=5) == "IN_TRANSIT"


def test_display_quantities_never_round_up():
    """P2-5：展示圆整不得进位（9.6 显示 10 会与「无采购计划」自相矛盾）。"""
    snapshot = {"fresh": True, "unknownCodes": set()}
    item = {"procureGapQuantity": 10, "materialCode": "Q-1"}
    fields = backend._shortage_status_fields(item, available_stock=9.6,
                                             bucket={"arrived": 0.0, "inTransit": 0.0},
                                             snapshot=snapshot)
    assert fields["stockStatus"] == "NO_PLAN"
    assert fields["availableStockQty"] == 9.6

    fields = backend._shortage_status_fields(item, available_stock=0.0,
                                             bucket={"arrived": 0.0, "inTransit": 0.4},
                                             snapshot=snapshot)
    assert fields["stockStatus"] == "IN_TRANSIT"
    assert fields["inTransitQty"] == 0.4, "在途 0.4 不能显示成 0（否则「在途采购」显示在途 0）"
    assert fields["coverableQuantity"] == 0.4

    # 整数值仍是 int（导出/页面不出现 100.0）
    whole = backend._qty_down(100.0)
    assert whole == 100 and isinstance(whole, int)


# ---------------------------------------------------------------- U9 同步


def test_po_sync_is_idempotent(seeded):
    first = sync_po()
    assert first["status"] == "OK"
    stats = first["stats"]
    assert stats["total"] == 4 and stats["lines"] == 4
    assert stats["inserted"] == 8  # 4 张头 + 4 行
    c = backend.db()
    try:
        before = (c.execute("SELECT COUNT(*) FROM u9_po_headers").fetchone()[0],
                  c.execute("SELECT COUNT(*) FROM u9_po_lines").fetchone()[0])
    finally:
        c.close()

    second = sync_po()
    after = None
    c = backend.db()
    try:
        after = (c.execute("SELECT COUNT(*) FROM u9_po_headers").fetchone()[0],
                 c.execute("SELECT COUNT(*) FROM u9_po_lines").fetchone()[0])
        dupes = c.execute("SELECT doc_no, line_no, COUNT(*) FROM u9_po_lines"
                          " GROUP BY doc_no, line_no HAVING COUNT(*) > 1").fetchall()
    finally:
        c.close()
    assert before == after == (4, 4)
    assert dupes == []
    assert second["stats"]["inserted"] == 0
    assert second["stats"]["updated"] == 0
    assert second["stats"]["unchanged"] == 8

    # 到货量变化 → 只更新，不新增
    rows = default_po_rows()
    rows[1]["POLines"][0]["TotalRecievedQtyTU"] = 50
    third = sync_po(rows=rows)
    c = backend.db()
    try:
        count = c.execute("SELECT COUNT(*) FROM u9_po_lines").fetchone()[0]
        recv = c.execute("SELECT recieved_qty_tu FROM u9_po_lines WHERE doc_no='PO-2602'"
                         ).fetchone()[0]
    finally:
        c.close()
    assert count == 4
    assert recv == 50
    assert third["stats"]["updated"] == 1 and third["stats"]["inserted"] == 0


def test_po_sync_removes_lines_deleted_in_u9(seeded):
    """U9 删掉/改行号的行要跟着删，不能留下幽灵在途。"""
    sync_po()
    rows = default_po_rows()
    rows[2]["POLines"] = []  # PO-2603 的行被 U9 删掉
    sync_po(rows=rows)
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM u9_po_lines WHERE doc_no='PO-2603'"
                         ).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM u9_po_lines").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM u9_po_headers").fetchone()[0] == 4
    finally:
        c.close()
    with TestClient(backend.app) as client:
        items = by_code(shortage(client, login(client, "ss-admin")))
    assert items["S-PARTIAL"]["stockStatus"] == "NO_PLAN"
    assert items["S-PARTIAL"]["inTransitQty"] == 0


def test_po_sync_only_open_skips_closed(seeded):
    result = sync_po(only_open=True)
    c = backend.db()
    try:
        docs = [r[0] for r in c.execute("SELECT doc_no FROM u9_po_headers ORDER BY doc_no")]
    finally:
        c.close()
    assert docs == ["PO-2601", "PO-2602", "PO-2603"]
    assert result["stats"]["total"] == 3


def test_closed_po_learned_by_default_sync_lifecycle(seeded):
    """P0-1 生命周期：开单(未到货) → U9 关单 → 默认参数再同步 → 不再计在途。

    改前默认 ``only_open=true``：关闭单在 client 就被丢掉，本地头永远停在 Approved，
    ``inTransitQty`` 永远是 100、料号永远不落 NO_PLAN（本用例会红）。
    """
    approved = [po_row("PO-CYCLE", [(1, "S-NOPLAN", 100, 0)])]
    first = sync_po(rows=approved, only_open=None)
    assert first["stats"]["total"] == 1
    assert first["stats"]["only_open"] is False, "同步默认必须包含已结案订单（否则学不到关闭）"
    with TestClient(backend.app) as client:
        before = shortage(client, login(client, "ss-admin"))
    assert by_code(before)["S-NOPLAN"]["stockStatus"] == "IN_TRANSIT"
    assert by_code(before)["S-NOPLAN"]["inTransitQty"] == 100
    open_before = before["poData"]["openOrderCount"]
    assert open_before == 1

    # U9 侧采购把单关了（到货仍为 0）
    closed = [po_row("PO-CYCLE", [(1, "S-NOPLAN", 100, 0)],
                     status_value="4", status_name="Closed")]
    second = sync_po(rows=closed, only_open=None)
    assert second["stats"]["total"] == 1, "关闭单必须同步回来（否则无法识别关闭）"

    c = backend.db()
    try:
        status_name = c.execute("SELECT status_name FROM u9_po_headers WHERE doc_no='PO-CYCLE'"
                                ).fetchone()[0]
    finally:
        c.close()
    assert status_name == "Closed"

    with TestClient(backend.app) as client:
        after = shortage(client, login(client, "ss-admin"))
    item = by_code(after)["S-NOPLAN"]
    assert item["stockStatus"] == "NO_PLAN"
    assert item["inTransitQty"] == 0
    assert item["arrivedNotStockedQty"] == 0
    assert item["openPoNos"] == []
    assert after["poData"]["openOrderCount"] == open_before - 1


def test_unknown_material_check_has_no_code_cap(seeded):
    """P1-1：料号核对不得因为「超过 2000 个」整段跳过（生产库需求料号去重 2,762）。"""
    class _CountingClient:
        def __init__(self):
            self.calls: list[int] = []

        def fetch_items(self, codes, errors=None):
            self.calls.append(len(codes))
            return [{"m_code": c, "m_name": c} for c in codes if c != "M-MISSING"]

    client = _CountingClient()
    codes = [f"M-{i:05d}" for i in range(2742)] + ["M-MISSING"]
    errors: list[dict] = []
    unknown, status = u9_sync._po_unknown_materials(None, client, codes, errors, batch_size=500)
    assert (unknown, status) == (["M-MISSING"], "ok")
    assert errors == [], "不得再写「超过 2000 跳过核对」并返回空结果"
    # 确实分块跑完了全部料号（500×5 + 243）
    assert client.calls == [500, 500, 500, 500, 500, 243]
    assert sum(client.calls) == len(codes)


def test_items_check_disabled_is_visible_in_response_and_page(seeded):
    """P1-1/P2-10：未核对与「核对通过（0 个未识别）」必须可区分。"""
    result = sync_po(check_items=False)
    assert result["stats"]["itemsCheck"] == "disabled"
    assert result["stats"]["itemsChecked"] is False
    assert result["stats"]["unknownMaterials"] == []

    with TestClient(backend.app) as client:
        token = login(client, "ss-admin")
        body = shortage(client, token)
        client.get("/admin/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text)
        assert match
        client.post("/admin/login", data={"username": "ss-admin", "password": WORKER_PASSWORD,
                                         "csrf_token": match.group(1)})
        page = client.get("/admin/projects/kitting", params={"project_no": PROJECT})

    assert body["poData"]["itemsChecked"] is False
    assert page.status_code == 200
    assert "料号核对未执行" in page.text


def flat_po_record(doc_no: str, line_no: int, code: str, pur: float, recv: float,
                   *, status_value: str = "2", status_name: str = "Approved") -> dict:
    """扁平形态：一条记录就是一行（行字段与单头同级，没有 POLines 列表）。"""
    return {
        "DocNo": doc_no,
        "BusinessDate": "2026-11-01",
        "Status": {"Value": status_value, "Name": status_name},
        "Supplier_Code": f"SUP-{doc_no}",
        "Supplier_ShortName": "扁平供应商",
        "DeliveryDate": "2026-12-01",
        "DocLineNo": line_no,
        "ItemInfo": {"ItemCode": code, "ItemName": f"品名-{code}"},
        "PurQtyTU": pur,
        "TotalRecievedQtyTU": recv,
    }


def test_flat_po_line_shape_is_parsed(seeded):
    """P1-2 防御：若查询按「一条记录一行」返回，必须能还原成 2 张单 3 行，而不是 0 行。"""
    rows = [
        flat_po_record("PO-F1", 1, "S-TRANSIT", 30, 0),
        flat_po_record("PO-F1", 2, "S-PARTIAL", 5, 0),
        flat_po_record("PO-F2", 1, "S-ARRIVED", 60, 60),
    ]
    result = sync_po(rows=rows)
    stats = result["stats"]
    assert stats["total"] == 2, f"扁平形态应还原出 2 张单，实际 {stats}"
    assert stats["lines"] == 3, "扁平形态应还原出 3 行（按 DocNo+DocLineNo），不能是 0 行"
    c = backend.db()
    try:
        lines = [(r[0], int(r[1]), r[2]) for r in c.execute(
            "SELECT doc_no, line_no, item_code FROM u9_po_lines ORDER BY doc_no, line_no")]
    finally:
        c.close()
    assert lines == [("PO-F1", 1, "S-TRANSIT"), ("PO-F1", 2, "S-PARTIAL"),
                     ("PO-F2", 1, "S-ARRIVED")]
    with TestClient(backend.app) as client:
        items = by_code(shortage(client, login(client, "ss-admin")))
    assert items["S-TRANSIT"]["inTransitQty"] == 30
    assert items["S-PARTIAL"]["inTransitQty"] == 5
    assert items["S-ARRIVED"]["arrivedNotStockedQty"] == 60


def test_validate_po_rows_rejects_zero_lines_with_flat_point_keys():
    """P1-2：有单、0 行、响应里又有 POLines.* 扁平键 → 必须报错（不能当没有行放过）。"""
    rows = [{
        "DocNo": "PO-X", "BusinessDate": "2026-11-01",
        "Status.Value": "2", "Status.Name": "Approved",
        "Supplier_Code": "S1", "DeliveryDate": "2026-12-01",
        "POLines.DocLineNo": 1, "POLines.ItemInfo.ItemCode": "M1",
        "POLines.PurQtyTU": 5, "POLines.TotalRecievedQtyTU": 0,
    }]
    with pytest.raises(ValueError) as excinfo:
        u9_mappers.validate_po_rows(rows)
    assert "行" in str(excinfo.value)


def test_client_paging_dedupes_by_doc_and_line_and_sends_order_by():
    """P1-2：扁平形态下行号才唯一；分页去重键必须是 (DocNo, DocLineNo)，且请求带 OrderBy。"""
    pages = {
        1: [flat_po_record("PO-A", 1, "M1", 10, 0), flat_po_record("PO-A", 2, "M2", 10, 0)],
        # 第 2 页重复第 1 页的 PO-A#1（网关排序不稳时会这样），同时带来 PO-B#1
        2: [flat_po_record("PO-A", 1, "M1", 10, 0), flat_po_record("PO-B", 1, "M3", 10, 0)],
        3: [],
    }
    seen: list[dict] = []

    def transport(method, url, headers, body, timeout, verify):
        if "AuthLogin" in url:
            return 200, json.dumps({"ResCode": 0, "Data": "tok"}).encode()
        payload = json.loads(body.decode())
        seen.append(payload)
        return 200, json.dumps({"ResCode": 0,
                                "Data": pages.get(payload.get("PageIndex", 1), [])}).encode()

    cfg = U9Config(enabled=True, base_url="http://u9.test/u9c", client_id="cid",
                   client_secret="sec", ent_code="001", org_code="001", user_code="demo")
    rows = U9CClient(cfg, transport).fetch_purchase_orders(page_size=2, max_pages=5)
    got = [(u9_mappers.po_doc_no(r), u9_mappers.po_line_key(r)) for r in rows]
    # 改前按 DocNo 去重：第 1 页只剩 PO-A#1，第 2 页全是已见单号 → novel=0 提前停 → 只 1 行
    assert got == [("PO-A", "1"), ("PO-A", "2"), ("PO-B", "1")]
    assert all(p.get("OrderBy") == "DocNo" for p in seen)


def test_po_sync_missing_field_raises_instead_of_zero(seeded):
    """U9 对不存在的字段静默忽略：缺 TotalRecievedQtyTU 必须报错，不得当 0。"""
    rows = default_po_rows()
    for row in rows:
        for line in row["POLines"]:
            line.pop("TotalRecievedQtyTU")
    with pytest.raises(U9Error) as excinfo:
        sync_po(rows=rows)
    assert "TotalRecievedQtyTU" in str(excinfo.value)
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM u9_po_headers").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM u9_po_lines").fetchone()[0] == 0
    finally:
        c.close()
    # 降级仍在：没写入过采购数据 → 页面提示不可信
    with TestClient(backend.app) as client:
        body = shortage(client, login(client, "ss-admin"))
    assert body["poDataFresh"] is False


def test_client_query_payload_and_field_validation():
    """真实客户端：请求体字段齐全；缺字段抛 U9Error；关闭单与超窗单被过滤。"""
    seen: list[dict] = []

    def transport(method, url, headers, body, timeout, verify):
        if "AuthLogin" in url:
            return 200, json.dumps({"ResCode": 0, "Data": "tok"}).encode()
        payload = json.loads(body.decode())
        seen.append(payload)
        if payload.get("PageIndex", 1) > 1:
            return 200, json.dumps({"ResCode": 0, "Data": []}).encode()
        return 200, json.dumps({"ResCode": 0, "Data": [
            po_row("PO-OK", [(1, "M1", 10, 0)]),
            po_row("PO-CLOSED", [(1, "M2", 10, 0)], status_name="Closed", status_value="4"),
            po_row("PO-OLD", [(1, "M3", 10, 0)], business_date="2000-01-01"),
        ]}).encode()

    cfg = U9Config(enabled=True, base_url="http://u9.test/u9c", client_id="cid",
                   client_secret="sec", ent_code="001", org_code="001",
                   user_code="demo")
    rows = U9CClient(cfg, transport).fetch_purchase_orders(since_days=180, only_open=True)
    assert [r["DocNo"] for r in rows] == ["PO-OK"]
    assert seen and seen[0]["EntityFullName"] == "UFIDA.U9.PM.PO.PurchaseOrder"
    fields = seen[0]["ReturnFields"]
    for required in ("DocNo", "BusinessDate", "Status.Value", "Status.Name",
                     "Supplier_Code", "DeliveryDate", "POLines.DocLineNo",
                     "POLines.ItemInfo.ItemCode", "POLines.PurQtyTU",
                     "POLines.TotalRecievedQtyTU"):
        assert required in fields

    def bad_transport(method, url, headers, body, timeout, verify):
        if "AuthLogin" in url:
            return 200, json.dumps({"ResCode": 0, "Data": "tok"}).encode()
        row = po_row("PO-BAD", [(1, "M1", 10, 0)])
        row["POLines"][0].pop("TotalRecievedQtyTU")
        return 200, json.dumps({"ResCode": 0, "Data": [row]}).encode()

    with pytest.raises(U9Error) as excinfo:
        U9CClient(cfg, bad_transport).fetch_purchase_orders()
    assert "TotalRecievedQtyTU" in str(excinfo.value)


# ---------------------------------------------------------------- 端到端四档


def test_four_tiers_end_to_end(seeded):
    first = sync_po()
    assert first["status"] == "OK"
    with TestClient(backend.app) as client:
        token = login(client, "ss-admin")
        body = shortage(client, token)

    assert body["poDataFresh"] is True
    assert body["poData"]["fresh"] is True
    assert body["poData"]["openOrderCount"] == 3
    assert body["poData"]["stale"] is False
    assert body["poData"]["itemsChecked"] is True

    items = by_code(body)
    assert items["S-STOCK"]["stockStatus"] == "IN_STOCK_READY"
    assert items["S-STOCK"]["stockStatusLabel"] == "在库可满足"
    assert items["S-STOCK"]["availableStockQty"] == 10
    assert items["S-STOCK"]["arrivedNotStockedQty"] == 0
    assert items["S-STOCK"]["inTransitQty"] == 0

    # 差 1 件：9 < 10，不能判「在库可满足」，又没有任何 PO → 无采购计划
    assert items["S-STOCK-M1"]["stockStatus"] == "NO_PLAN"
    assert items["S-STOCK-M1"]["availableStockQty"] == 9

    # 已到货待入库：到货 100 = 缺口 100，且在途 0（已到货未入库不计入在途）
    arrived = items["S-ARRIVED"]
    assert arrived["stockStatus"] == "ARRIVED_NOT_STOCKED"
    assert arrived["arrivedNotStockedQty"] == 100
    assert arrived["inTransitQty"] == 0
    assert arrived["openPoNos"] == ["PO-2601"]
    assert arrived["suppliers"] == ["宁波到货供应商"]

    # 在途采购：未到货 50 = 缺口 50
    transit = items["S-TRANSIT"]
    assert transit["stockStatus"] == "IN_TRANSIT"
    assert transit["inTransitQty"] == 50
    assert transit["arrivedNotStockedQty"] == 0
    assert transit["coverableQuantity"] == 50
    assert transit["partialCoverage"] is False
    assert transit["openPoNos"] == ["PO-2602"]
    assert transit["suppliers"] == ["上海在途供应商"]

    # 部分覆盖：在途 15 < 缺口 40，仍算在途，但标出可覆盖量与部分覆盖
    partial = items["S-PARTIAL"]
    assert partial["stockStatus"] == "IN_TRANSIT"
    assert partial["inTransitQty"] == 15
    assert partial["coverableQuantity"] == 15
    assert partial["partialCoverage"] is True

    # 无采购计划 / 已关闭 PO 不算在途
    assert items["S-CLOSED"]["stockStatus"] == "NO_PLAN"
    assert items["S-CLOSED"]["inTransitQty"] == 0
    assert items["S-CLOSED"]["openPoNos"] == []
    assert items["S-NOPLAN"]["stockStatus"] == "NO_PLAN"

    # 料号体系不一致 → UNKNOWN_MATERIAL，不塞进 NO_PLAN
    assert items["S-UNKNOWN"]["stockStatus"] == "UNKNOWN_MATERIAL"
    assert items["S-UNKNOWN"]["stockStatusLabel"] == "料号未识别"
    assert body["poData"]["unknownMaterialCount"] == 1

    # 需求 2 的缺口数字不受状态层影响：这些行都没到货，采购缺口 = 需求
    for code, required in (("S-STOCK", 10), ("S-STOCK-M1", 10), ("S-ARRIVED", 100),
                           ("S-TRANSIT", 50), ("S-PARTIAL", 40), ("S-NOPLAN", 20),
                           ("S-CLOSED", 30), ("S-UNKNOWN", 5)):
        assert items[code]["procureGapQuantity"] == required, code
        assert items[code]["prepareGapQuantity"] == required, code

    assert body["statusBreakdown"] == {
        "IN_STOCK_READY": 1, "ARRIVED_NOT_STOCKED": 1, "IN_TRANSIT": 2,
        "NO_PLAN": 3, "UNKNOWN_MATERIAL": 1,
    }


def test_closed_po_not_counted_as_in_transit(seeded):
    """已关闭 PO 即使本地有行，也不得计入在途（只统计未结案）。"""
    sync_po(only_open=False)  # 关闭单也写进快照，验证读取侧同样过滤
    c = backend.db()
    try:
        assert c.execute("SELECT COUNT(*) FROM u9_po_headers").fetchone()[0] == 4
    finally:
        c.close()
    with TestClient(backend.app) as client:
        body = shortage(client, login(client, "ss-admin"))
    item = by_code(body)["S-CLOSED"]
    assert item["stockStatus"] == "NO_PLAN"
    assert item["inTransitQty"] == 0
    assert item["arrivedNotStockedQty"] == 0
    assert item["openPoNos"] == []
    # 关闭单不计入「未结案订单数」
    assert body["poData"]["openOrderCount"] == 3


def test_snapshot_narrows_by_page_codes_but_keeps_global_open_count(seeded):
    """P2-1：快照按本页料号收窄（不整表扫 3 万行），但「共 N 张未结案订单」仍是全表口径。"""
    sync_po()
    c = backend.db()
    try:
        narrowed = backend._po_status_snapshot(c, ["S-TRANSIT"])
        full = backend._po_status_snapshot(c)
    finally:
        c.close()
    assert set(narrowed["byMaterial"]) == {"S-TRANSIT"}
    assert narrowed["byMaterial"]["S-TRANSIT"]["inTransit"] == 50
    assert narrowed["openOrderCount"] == 3 == full["openOrderCount"]
    assert set(full["byMaterial"]) == {"S-ARRIVED", "S-TRANSIT", "S-PARTIAL"}
    assert narrowed["fresh"] is True and narrowed["itemsChecked"] is True


def test_arrived_quantity_not_double_counted_as_in_transit(seeded):
    """已到货未入库不计入在途：pur=100 recv=100 → 在途 0，已到货 100。"""
    sync_po()
    with TestClient(backend.app) as client:
        item = by_code(shortage(client, login(client, "ss-admin")))["S-ARRIVED"]
    assert (item["arrivedNotStockedQty"], item["inTransitQty"]) == (100, 0)
    assert item["stockStatus"] == "ARRIVED_NOT_STOCKED"


# ---------------------------------------------------------------- 降级与过滤


def test_degraded_when_po_never_synced(seeded):
    """PO 表为空：状态一律 NO_PLAN + poDataFresh=false，页面必须提示不可信。"""
    with TestClient(backend.app) as client:
        token = login(client, "ss-admin")
        body = shortage(client, token)
        overview = client.get(f"/api/v1/projects/{PROJECT}/kitting-summary",
                              headers=auth(token)).json()
        assert client.get("/admin/login").status_code == 200
        web = client
        match = re.search(r'name="csrf_token" value="([^"]+)"', web.get("/admin/login").text)
        assert match
        web.post("/admin/login", data={"username": "ss-admin", "password": WORKER_PASSWORD,
                                       "csrf_token": match.group(1)})
        page = web.get("/admin/projects/kitting", params={"project_no": PROJECT})

    assert body["poDataFresh"] is False
    assert body["poData"] == {"fresh": False, "syncedAt": None, "stale": False,
                              "openOrderCount": 0, "unknownMaterialCount": 0,
                              "itemsChecked": False}
    assert {i["stockStatus"] for i in body["items"]} == {"NO_PLAN"}
    assert body["statusBreakdown"]["NO_PLAN"] == len(body["items"])
    assert overview["summary"]["statusBreakdown"]["NO_PLAN"] == len(body["items"])
    assert overview["poDataFresh"] is False
    assert page.status_code == 200
    assert "采购数据未同步，状态判断不可信" in page.text
    assert "无采购计划" in page.text


def test_status_filter_and_breakdown(seeded):
    sync_po()
    with TestClient(backend.app) as client:
        token = login(client, "ss-admin")
        full = shortage(client, token)
        only_transit = shortage(client, token, status="IN_TRANSIT")
        only_unknown = shortage(client, token, status="UNKNOWN_MATERIAL")
        empty = shortage(client, token, status="IN_STOCK_READY", material_code="S-NOPLAN")
        bad = client.get(f"/api/v1/projects/{PROJECT}/shortage-list",
                         params={"status": "BOGUS"}, headers=auth(token))

    assert {i["materialCode"] for i in only_transit["items"]} == {"S-TRANSIT", "S-PARTIAL"}
    assert only_transit["total"] == 2
    assert only_transit["statusFilter"] == "IN_TRANSIT"
    # 状态分布仍是全范围口径（不受 status 过滤影响），才能当过滤入口用
    assert only_transit["statusBreakdown"] == full["statusBreakdown"]
    assert [i["materialCode"] for i in only_unknown["items"]] == ["S-UNKNOWN"]
    # 料号过滤会同时收窄概览作用域（需求 2 的既有口径）：这里只剩 S-NOPLAN 一个料号
    assert empty["items"] == [] and empty["total"] == 0
    assert empty["statusBreakdown"]["NO_PLAN"] == 1
    assert sum(empty["statusBreakdown"].values()) == 1
    assert bad.status_code == 422


# ---------------------------------------------------------------- 导出 / 管理台


def test_export_has_status_columns(seeded):
    sync_po()
    with TestClient(backend.app) as client:
        token = login(client, "ss-admin")
        response = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                              headers=auth(token))
        filtered = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                              params={"status": "ARRIVED_NOT_STOCKED"}, headers=auth(token))
    assert response.status_code == 200
    rows = XlsxSheetReader.read_rows(response.content, "缺料清单")
    assert rows[0].values == SHORTAGE_HEADER
    data = {r.values[0]: r.values for r in rows[1:]}
    assert data["S-ARRIVED"][11] == "已到货待入库"
    assert (data["S-ARRIVED"][13], data["S-ARRIVED"][14]) == ("100", "0")
    assert data["S-ARRIVED"][16] == "PO-2601"
    assert data["S-ARRIVED"][17] == "宁波到货供应商"
    assert data["S-TRANSIT"][11] == "在途采购"
    assert data["S-TRANSIT"][14] == "50"
    assert data["S-STOCK"][11] == "在库可满足"
    assert data["S-UNKNOWN"][11] == "料号未识别"
    # 既有列顺序不变（需求 2 的对账列位置不动）
    assert data["S-STOCK"][4:8] == ("10", "10", "0", "1")

    only = [r.values for r in XlsxSheetReader.read_rows(filtered.content, "缺料清单")[1:]]
    assert [r[0] for r in only] == ["S-ARRIVED"]


def test_admin_page_status_column_filter_and_freshness(seeded):
    with TestClient(backend.app) as client:
        client.get("/admin/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text)
        assert match
        client.post("/admin/login", data={"username": "ss-sup", "password": WORKER_PASSWORD,
                                          "csrf_token": match.group(1)})
        # 未同步：页面必须提示不可信
        before = client.get("/admin/projects/kitting", params={"project_no": PROJECT})
        assert "采购数据未同步，状态判断不可信" in before.text

        sync_po()

        page = client.get("/admin/projects/kitting", params={"project_no": PROJECT})
        filtered = client.get("/admin/projects/kitting",
                              params={"project_no": PROJECT, "status": "IN_TRANSIT"})
        closed_only = client.get("/admin/projects/kitting",
                                 params={"project_no": PROJECT, "status": "NO_PLAN"})

    assert page.status_code == 200
    html = page.text
    assert "物料状态" in html
    assert "采购数据同步于" in html and "共 3 张未结案订单" in html
    assert "采购数据未同步" not in html
    # 四色标签文案都在（五档）
    for label in ("在库可满足", "已到货待入库", "在途采购", "无采购计划", "料号未识别"):
        assert label in html
    # 过滤下拉的选项
    assert 'value="IN_TRANSIT"' in html and 'value="UNKNOWN_MATERIAL"' in html
    # 状态过滤只保留对应料号
    assert filtered.status_code == 200
    assert "S-TRANSIT" in filtered.text and "S-PARTIAL" in filtered.text
    assert "S-ARRIVED</td>" not in filtered.text and "S-NOPLAN</td>" not in filtered.text
    assert closed_only.status_code == 200
    assert "S-CLOSED" in closed_only.text and "S-NOPLAN" in closed_only.text
    assert "S-ARRIVED</td>" not in closed_only.text


def test_u9_admin_page_lists_po_entity(seeded):
    with TestClient(backend.app) as client:
        client.get("/admin/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text)
        client.post("/admin/login", data={"username": "ss-admin", "password": WORKER_PASSWORD,
                                          "csrf_token": match.group(1)})
        page = client.get("/admin/u9")
    assert page.status_code == 200
    assert "采购订单（在途/到货）" in page.text
    assert 'action="/admin/u9/sync/po"' in page.text
    assert 'name="since_days"' in page.text
    assert "u9_po_headers / u9_po_lines" in page.text
    # P0-1：默认勾选「同时同步已结案订单」（否则学不到 U9 上的关单）
    assert 'name="sync_closed" value="1" checked' in page.text
    assert "同时同步已结案订单（用于识别关闭，推荐）" in page.text


def test_overview_status_breakdown_matches_list(seeded):
    sync_po()
    with TestClient(backend.app) as client:
        token = login(client, "ss-admin")
        overview = client.get(f"/api/v1/projects/{PROJECT}/kitting-summary",
                              headers=auth(token)).json()
        body = shortage(client, token)
    assert overview["summary"]["statusBreakdown"] == body["statusBreakdown"]
    assert sum(overview["summary"]["statusBreakdown"].values()) == len(body["items"])
    # 概览既有数字不变
    assert overview["summary"]["requirementLineCount"] == 8
    assert overview["summary"]["shortageLineCount"] == 8
    assert overview["summary"]["totalProcureGapQuantity"] == 265


def test_sync_entry_wiring_for_po(seeded):
    """po 实体接进既有同步入口：参数映射 + 管理台表单被接受（未启用 U9 时 303 提示）。"""
    from app.u9.routes import build_sync_kwargs

    kwargs = build_sync_kwargs("po", codes="M1, M2", since_days=90,
                               only_open=False, check_items=False)
    assert kwargs == {"since_days": 90, "only_open": False, "codes": ["M1", "M2"],
                      "check_items": False}
    # P0-1：不传时（API 默认）必须含已结案，否则关闭单永远同步不回来
    assert build_sync_kwargs("po")["only_open"] is False

    with TestClient(backend.app) as client:
        client.get("/admin/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/admin/login").text)
        client.post("/admin/login", data={"username": "ss-admin", "password": WORKER_PASSWORD,
                                          "csrf_token": match.group(1)})
        page = client.get("/admin/u9")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = client.post("/admin/u9/sync/po",
                               data={"csrf_token": token, "dry_run": "1", "since_days": "180"},
                               follow_redirects=False)
    assert response.status_code == 303
    assert "sync%2Fpo" not in response.headers["location"]  # 未知实体才会被 route 拒
    assert "/admin/u9?msg=" in response.headers["location"]

