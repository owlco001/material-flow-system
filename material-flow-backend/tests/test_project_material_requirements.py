"""需求 1：项目物料需求（按设备展开到底）——API / 导出 / 管理台。

覆盖验收要求的：层级还原、多机台分组、无备料批次、无设备、权限（装配工只见
自己机台）、批次选择（最新）、matched_by 三种来源、导出列与合计行。
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

from app import main as backend  # noqa: E402
from app.xlsx_parser import XlsxSheetReader  # noqa: E402

WORKER_PASSWORD = "Worker@2026"
HEADER = (
    "项目号", "项目名称", "机台号", "机台名称", "层级", "父件料号", "料号", "品名",
    "规格", "单位", "单机用量", "机台合计需求", "已到货", "在库", "状态", "匹配来源",
)


# ---------------------------------------------------------------- seeding


def add_user(uid: str, role: str, password: str = WORKER_PASSWORD) -> None:
    c = backend.db()
    c.execute(
        "INSERT OR REPLACE INTO users(id,username,display_name,role,password_hash,"
        "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, uid, uid, role, backend.hash_password(password), 0, 1, backend.now()),
    )
    c.commit()
    c.close()


def add_project(order_no: str, project_name: str, devices: list[str]) -> dict[str, str]:
    c = backend.db()
    order_id = f"po-{order_no}"
    ts = backend.now()
    c.execute(
        "INSERT INTO production_orders(id,order_no,product_name,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (order_id, order_no, project_name, "IN_PROGRESS", ts, ts),
    )
    ids: dict[str, str] = {}
    for index, device_no in enumerate(devices, 1):
        device_id = f"od-{order_no}-{index}"
        c.execute(
            "INSERT INTO order_devices(id,order_id,device_type,device_no,sequence_no,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (device_id, order_id, "机台", device_no, index, ts),
        )
        ids[device_no] = device_id
    c.commit()
    c.close()
    return ids


def add_batch(batch_id: str, project_name: str, created_at: str, items: list[dict]) -> None:
    """items: device_code / level_no / parent_code / material_code / qty(可选) / name(可选)。"""
    c = backend.db()
    c.execute(
        "INSERT INTO agg_bom_batches(id,project_name,file_name,file_sha256,row_count,"
        "device_count,material_count,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (batch_id, project_name, f"{batch_id}.xlsx", f"sha-{batch_id}", len(items),
         len({i["device_code"] for i in items}), len({i["material_code"] for i in items}),
         "tester", created_at),
    )
    for line_no, item in enumerate(items, 1):
        c.execute(
            "INSERT INTO agg_bom_items(id,batch_id,device_code,device_name,level_no,parent_code,"
            "material_code,material_name,specification,unit,quantity,material_form,line_no)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{batch_id}-{line_no}", batch_id, item["device_code"], item.get("device_name", "机台"),
             item.get("level_no", 1), item.get("parent_code", ""), item["material_code"],
             item.get("name", f"品名-{item['material_code']}"), item.get("spec", "规格"),
             item.get("unit", "件"), item.get("qty", 1), item.get("form", "采购件"), line_no),
        )
    c.commit()
    c.close()


def add_requirement(order_no: str, device_id: str, material_code: str, *,
                    required: int, arrived: int, in_stock: int, status: str) -> None:
    c = backend.db()
    material_id = f"mat-{material_code}"
    c.execute(
        "INSERT OR IGNORE INTO materials(id,code,name,specification,unit) VALUES(?,?,?,?,?)",
        (material_id, material_code, f"品名-{material_code}", "规格", "件"),
    )
    order_id = c.execute(
        "SELECT id FROM production_orders WHERE order_no=?", (order_no,)
    ).fetchone()["id"]
    ts = backend.now()
    c.execute(
        "INSERT INTO order_material_requirements(id,order_id,device_id,material_id,"
        "required_quantity,arrived_quantity,in_stock_quantity,status_code,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f"req-{order_no}-{device_id}-{material_code}", order_id, device_id, material_id,
         required, arrived, in_stock, status, ts, ts),
    )
    c.commit()
    c.close()


def add_task(order_no: str, device_no: str, assembler_id: str | None, task_no: int = 1) -> None:
    c = backend.db()
    ts = backend.now()
    c.execute(
        "INSERT INTO assembly_tasks(id,order_no,device_id,device_no,status,progress_stage,"
        "task_version,assigned_assembler_id,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f"task-{order_no}-{task_no}", order_no, f"dev-{device_no}", device_no,
         "IN_PROGRESS", 1, 1, assembler_id, ts, ts),
    )
    c.commit()
    c.close()


def login(client: TestClient, username: str, password: str = WORKER_PASSWORD) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "deviceId": f"dev-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def get_requirements(client: TestClient, token: str, project_no: str, **params):
    return client.get(
        f"/api/v1/projects/{project_no}/material-requirements",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture()
def seeded():
    """两块项目：26B-012（两台机台、两版批次）、26B-099（无备料表）。"""
    add_user("pmr-admin", "ADMIN")
    add_user("pmr-sup", "WORKSHOP_SUPERVISOR")
    add_user("pmr-planner", "PLANNER")
    add_user("pmr-wh", "WAREHOUSE_ADMIN")
    add_user("pmr-asm-1", "ASSEMBLER")
    add_user("pmr-asm-2", "ASSEMBLER")

    devices = add_project("JSBY-26B-012", "26B-012 顶升移栽机项目",
                          ["26B-012-P5-12", "26B-012-P5-13"])
    add_batch("batch-old", "26B-012 顶升移栽机项目", "2026-10-01T00:00:00+00:00", [
        {"device_code": "26B-012-P5-12", "level_no": 1, "parent_code": "",
         "material_code": "26B-012-P5-12"},
        {"device_code": "26B-012-P5-12", "level_no": 2, "parent_code": "26B-012-P5-12",
         "material_code": "OLD-1", "qty": 99},
    ])
    add_batch("batch-new", "26B-012 顶升移栽机项目", "2026-10-20T02:11:03+00:00", [
        {"device_code": "26B-012-P5-12", "level_no": 1, "parent_code": "",
         "material_code": "26B-012-P5-12", "device_name": "顶升移栽机", "qty": 1},
        {"device_code": "26B-012-P5-12", "level_no": 2, "parent_code": "26B-012-P5-12",
         "material_code": "C1", "qty": 2},
        {"device_code": "26B-012-P5-12", "level_no": 3, "parent_code": "C1",
         "material_code": "L1", "qty": 6},
        {"device_code": "26B-012-P5-12", "level_no": 2, "parent_code": "26B-012-P5-12",
         "material_code": "C2", "qty": 3},
        {"device_code": "26B-012-P5-13", "level_no": 1, "parent_code": "",
         "material_code": "26B-012-P5-13", "device_name": "缓存输送机", "qty": 1},
        {"device_code": "26B-012-P5-13", "level_no": 2, "parent_code": "26B-012-P5-13",
         "material_code": "C3", "qty": 4},
    ])
    add_requirement("JSBY-26B-012", devices["26B-012-P5-12"], "C1",
                    required=10, arrived=4, in_stock=2, status="ARRIVED")

    add_project("JSBY-26B-099", "26B-099 无备料表项目", ["26B-099-A1"])
    add_project("JSBY-26B-100", "26B-100 无设备项目", [])

    add_task("JSBY-26B-012", "26B-012-P5-12", "pmr-asm-1", task_no=1)
    add_task("JSBY-26B-012", "26B-012-P5-13", "pmr-asm-2", task_no=2)
    return devices


# ---------------------------------------------------------------- tests


def test_batch_selection_tree_hierarchy_and_status_link(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        response = get_requirements(client, token, "JSBY-26B-012", expand="all")
        assert response.status_code == 200, response.text
        body = response.json()

    # 批次选择：两台机台都命中的最新批次（batch-new），旧批次不得混入
    assert body["matchedBy"] == "device_no"
    assert body["matchedValue"] in {"26B-012-P5-12", "26B-012-P5-13"}
    assert body["batch"]["batchId"] == "batch-new"
    assert body["batch"]["createdAt"] == "2026-10-20T02:11:03+00:00"
    assert "OLD-1" not in response.text

    # 多机台分组，按 sequence_no
    assert [d["deviceNo"] for d in body["devices"]] == ["26B-012-P5-12", "26B-012-P5-13"]
    assert body["devices"][0]["deviceName"] == "顶升移栽机"

    # 层级还原：根 → C1/C2，C1 → L1
    tree = body["devices"][0]["tree"]
    assert [n["materialCode"] for n in tree] == ["26B-012-P5-12"]
    root = tree[0]
    assert root["childCount"] == 2
    assert [c["materialCode"] for c in root["children"]] == ["C1", "C2"]
    assert [c["materialCode"] for c in root["children"][0]["children"]] == ["L1"]
    assert root["children"][0]["children"][0]["levelNo"] == 3
    assert body["devices"][1]["tree"][0]["children"][0]["materialCode"] == "C3"

    # 统计口径：共 6 项、3 层
    assert body["summary"]["itemCount"] == 6
    assert body["summary"]["levelCount"] == 3
    assert body["summary"]["deviceCount"] == 2
    assert body["summary"]["projectDeviceCount"] == 2

    # 状态联动：C1 有需求行，附带数量与状态；没有记录的节点 requirement 必须为空
    c1 = root["children"][0]
    assert c1["requirement"] == {
        "requiredQuantity": 10, "arrivedQuantity": 4, "inStockQuantity": 2,
        "statusCode": "ARRIVED", "statusLabel": "到货",
    }
    assert c1["deviceTotalQuantity"] == 2
    leaf = c1["children"][0]
    assert leaf["requirement"] is None
    assert root["requirement"] is None
    # 同料号多处出现时，机台合计用量按料号汇总
    assert root["deviceTotalQuantity"] == 1


def test_tree_uses_level_stack_when_parent_code_is_device_root(seeded):
    """现场 U9 聚合 BOM：母件料号整段都填设备根，3/4 层必须按层级栈挂回上一级。"""
    add_project("JSBY-26B-014", "26B-014 缓存线", ["P8-01"])
    add_batch("batch-u9", "26B-014 缓存线", "2026-10-23T00:00:00+00:00", [
        {"device_code": "P8-01", "level_no": 1, "parent_code": "P8-01", "material_code": "P8-01"},
        {"device_code": "P8-01", "level_no": 2, "parent_code": "P8-01", "material_code": "U2-A", "qty": 2},
        {"device_code": "P8-01", "level_no": 3, "parent_code": "P8-01", "material_code": "U3-A", "qty": 4},
        {"device_code": "P8-01", "level_no": 4, "parent_code": "P8-01", "material_code": "U4-A", "qty": 8},
        {"device_code": "P8-01", "level_no": 2, "parent_code": "P8-01", "material_code": "U2-B", "qty": 1},
        {"device_code": "P8-01", "level_no": 3, "parent_code": "P8-01", "material_code": "U3-B", "qty": 3},
    ])
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        body = get_requirements(client, token, "JSBY-26B-014", expand="all").json()
    tree = body["devices"][0]["tree"]
    assert len(tree) == 1 and tree[0]["materialCode"] == "P8-01"
    level_two = tree[0]["children"]
    assert [n["materialCode"] for n in level_two] == ["U2-A", "U2-B"]
    assert [n["materialCode"] for n in level_two[0]["children"]] == ["U3-A"]
    assert [n["materialCode"] for n in level_two[0]["children"][0]["children"]] == ["U4-A"]
    assert [n["materialCode"] for n in level_two[1]["children"]] == ["U3-B"]
    assert body["summary"]["levelCount"] == 4


def test_expand_top_only_returns_first_level(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        top = get_requirements(client, token, "JSBY-26B-012", expand="top").json()
        full = get_requirements(client, token, "JSBY-26B-012", expand="all").json()
    assert top["expand"] == "top"
    root = top["devices"][0]["tree"][0]
    assert root["children"] == []
    assert root["childCount"] == 2
    # 默认（不传 expand）即 top
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        default = get_requirements(client, token, "JSBY-26B-012").json()
    assert default["expand"] == "top"
    assert full["devices"][0]["tree"][0]["children"]


def test_device_filter_and_project_search(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        one = get_requirements(client, token, "JSBY-26B-012", expand="all",
                               device_no="26B-012-P5-13").json()
        assert [d["deviceNo"] for d in one["devices"]] == ["26B-012-P5-13"]
        assert one["deviceFilter"] == "26B-012-P5-13"
        assert one["summary"]["itemCount"] == 2

        missing = get_requirements(client, token, "JSBY-26B-012", device_no="NOPE-1")
        assert missing.status_code == 404

        search = client.get("/api/v1/projects", params={"q": "26B-012"},
                            headers={"Authorization": f"Bearer {token}"})
        assert search.status_code == 200
        assert [p["projectNo"] for p in search.json()["projects"]] == ["JSBY-26B-012"]
        assert search.json()["projects"][0]["deviceCount"] == 2

        # 项目号大小写不敏感；项目名模糊也可唯一命中
        lower = get_requirements(client, token, "jsby-26b-012").json()
        assert lower["projectNo"] == "JSBY-26B-012"
        assert lower["resolvedFrom"] == "order_no"
        fuzzy = get_requirements(client, token, "顶升移栽").json()
        assert fuzzy["projectNo"] == "JSBY-26B-012"
        assert fuzzy["resolvedFrom"] == "fuzzy"


def test_ambiguous_fuzzy_and_missing_project(seeded):
    add_project("JSBY-26B-012X", "26B-012 备用项目", [])
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        ambiguous = get_requirements(client, token, "26B-012")
        assert ambiguous.status_code == 409
        assert ambiguous.json()["error"]["code"] == "PROJECT_AMBIGUOUS"
        assert len(ambiguous.json()["error"]["details"]["candidates"]) == 2

        missing = get_requirements(client, token, "NOPE-999")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"
        assert "项目不存在" in missing.json()["error"]["message"]


def test_no_batch_and_no_devices_have_explicit_messages(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        no_batch = get_requirements(client, token, "JSBY-26B-099").json()
        assert no_batch["reason"] == "NO_BATCH"
        assert no_batch["batch"] is None
        assert no_batch["matchedBy"] == "none"
        assert "未匹配到备料表批次" in no_batch["message"]
        assert [d["deviceNo"] for d in no_batch["devices"]] == ["26B-099-A1"]
        assert no_batch["devices"][0]["itemCount"] == 0

        no_device = get_requirements(client, token, "JSBY-26B-100").json()
        assert no_device["reason"] == "NO_DEVICES"
        assert "暂无设备" in no_device["message"]
        assert no_device["devices"] == []

        # 无备料表时导出给出明确中文错误，而不是空文件
        export = client.get(
            "/api/v1/projects/JSBY-26B-099/material-requirements/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert export.status_code == 409
        assert export.json()["error"]["code"] == "NO_BATCH"


def test_assembler_only_sees_own_devices(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "pmr-asm-1")
        body = get_requirements(client, token, "JSBY-26B-012", expand="all").json()
        assert [d["deviceNo"] for d in body["devices"]] == ["26B-012-P5-12"]
        assert body["summary"]["deviceCount"] == 1
        assert body["summary"]["projectDeviceCount"] == 1
        # 别台机台的 BOM 明细绝不能出现在响应里
        assert "C3" not in response_text(body)

        other = get_requirements(client, token, "JSBY-26B-012", device_no="26B-012-P5-13")
        assert other.status_code == 403

        # 项目搜索对装配工也只返回本人参与的项目
        search = client.get("/api/v1/projects", params={"q": "26B-"},
                            headers={"Authorization": f"Bearer {token}"})
        assert search.status_code == 200
        assert [p["projectNo"] for p in search.json()["projects"]] == ["JSBY-26B-012"]


def test_roles_admin_supervisor_planner_allowed_warehouse_denied(seeded):
    with TestClient(backend.app) as client:
        for username in ("pmr-sup", "pmr-planner"):
            token = login(client, username)
            assert get_requirements(client, token, "JSBY-26B-012").status_code == 200
        wh = login(client, "pmr-wh")
        denied = get_requirements(client, wh, "JSBY-26B-012")
        assert denied.status_code == 403
        assert client.get("/api/v1/projects",
                          headers={"Authorization": f"Bearer {wh}"}).status_code == 403
        no_token = client.get("/api/v1/projects/JSBY-26B-012/material-requirements")
        assert no_token.status_code == 401


def test_matched_by_device_prefix(seeded):
    """项目短号前缀命中：订单机台号是 P7-01，批次设备编码是 26B-013-P7-01。"""
    add_project("JSBY-26B-013", "26B-013 分拣线", ["P7-01"])
    add_batch("batch-prefix", "分拣线", "2026-10-21T00:00:00+00:00", [
        {"device_code": "26B-013-P7-01", "level_no": 1, "parent_code": "",
         "material_code": "26B-013-P7-01"},
        {"device_code": "26B-013-P7-01", "level_no": 2, "parent_code": "26B-013-P7-01",
         "material_code": "PF-1", "qty": 5},
    ])
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        body = get_requirements(client, token, "JSBY-26B-013", expand="all").json()
    assert body["matchedBy"] == "device_prefix"
    assert body["matchedValue"] == "26B-013-"
    assert body["devices"][0]["deviceNo"] == "P7-01"
    assert body["devices"][0]["linked"] is True
    assert body["devices"][0]["matchedBy"] == "device_prefix"
    assert body["devices"][0]["tree"][0]["children"][0]["materialCode"] == "PF-1"


def test_matched_by_project_name_keeps_unlinked_group(seeded):
    """机台号与设备编码都对不上时退回项目名称，并标 linked=false，不冒充已匹配。"""
    add_project("JSBY-26C-001", "26C-001 分拣线", ["26C-001-A1"])
    add_batch("batch-name", "26C-001 分拣线", "2026-10-22T00:00:00+00:00", [
        {"device_code": "A1", "level_no": 1, "parent_code": "", "material_code": "A1"},
        {"device_code": "A1", "level_no": 2, "parent_code": "A1",
         "material_code": "PN-1", "qty": 2},
    ])
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        body = get_requirements(client, token, "JSBY-26C-001", expand="all").json()
    assert body["matchedBy"] == "project_name"
    assert body["devices"][0]["deviceNo"] == "A1"
    assert body["devices"][0]["linked"] is False
    assert body["devices"][0]["matchedBy"] == "project_name"
    assert body["devices"][0]["tree"][0]["children"][0]["materialCode"] == "PN-1"
    # 未匹配机台档案的组没有 order_devices.id，因此不挂需求状态
    assert body["devices"][0]["tree"][0]["children"][0]["requirement"] is None


def test_export_xlsx_columns_hierarchy_and_totals(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        response = client.get(
            "/api/v1/projects/JSBY-26B-012/material-requirements/export",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition and ".xlsx" in disposition
    assert "filename*=UTF-8''" in disposition

    rows = XlsxSheetReader.read_rows(response.content)
    assert rows, "导出的 xlsx 必须能被解析"
    assert rows[0].values == HEADER
    texts = [r.values for r in rows[1:]]

    # 层级保留：第 3 层料号前有两个全角缩进
    leaf_rows = [r for r in texts if r[6].strip() == "L1"]
    assert leaf_rows, "叶子件 L1 必须出现在导出中"
    assert leaf_rows[0][6].startswith("　　")
    assert leaf_rows[0][4] == "3"
    assert leaf_rows[0][5] == "C1"

    # 合计行：每台机台一行小计 + 一行项目合计
    subtotal = [r for r in texts if r[4] == "小计"]
    assert len(subtotal) == 2
    total = [r for r in texts if r[2] == "项目合计"]
    assert len(total) == 1
    assert "6 项 / 3 层" in total[0][7]

    # 需求状态列：C1 有记录，L1 留空（不得用 0 冒充）
    c1 = [r for r in texts if r[6].strip() == "C1"][0]
    assert c1[11] == "10" and c1[12] == "4" and c1[13] == "2" and c1[14] == "到货"
    l1 = [r for r in texts if r[6].strip() == "L1"][0]
    assert l1[12] == "" and l1[13] == "" and l1[14] == ""

    # 单机过滤导出
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        one = client.get(
            "/api/v1/projects/JSBY-26B-012/material-requirements/export",
            params={"device_no": "26B-012-P5-13"},
            headers={"Authorization": f"Bearer {token}"},
        )
    one_rows = [r.values for r in XlsxSheetReader.read_rows(one.content)[1:]]
    assert {r[2] for r in one_rows if r[2] != "项目合计"} == {"26B-012-P5-13"}


def test_orders_detail_unchanged(seeded):
    """新增路由不得影响既有 /api/v1/orders/* 契约。"""
    with TestClient(backend.app) as client:
        token = login(client, "pmr-admin")
        detail = client.get("/api/v1/orders/JSBY-26B-012/detail",
                            headers={"Authorization": f"Bearer {token}"})
    assert detail.status_code == 200, detail.text
    assert {"orderNo", "materials", "materialSummary", "assemblyTasks",
            "progress", "timeline"} <= set(detail.json())


# ---------------------------------------------------------------- 管理台


def web_login(client: TestClient, username: str, password: str = WORKER_PASSWORD):
    page = client.get("/admin/login")
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    token = match.group(1)
    return client.post("/admin/login",
                       data={"username": username, "password": password,
                             "csrf_token": token},
                       follow_redirects=False)


def test_admin_page_renders_tree_and_exports(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "pmr-sup").status_code == 303
        response = client.get("/admin/projects/material-requirements",
                              params={"project_no": "JSBY-26B-012"})
        assert response.status_code == 200, response.text
        html = response.text
        assert "项目物料需求" in html
        assert "26B-012-P5-12" in html and "26B-012-P5-13" in html
        assert "机台号精确匹配" in html
        assert "共 <b>6</b> 项、<b>3</b> 层" in html
        assert "导出 xlsx" in html
        assert "2026-10-20T02:11:03+00:00" in html
        # 默认（top）只渲染第一层：根行给出「按机台展开」链接，深层件不在 HTML 里
        assert "device_no=26B-012-P5-12&expand=all" in html
        assert ">L1</span>" not in html

        full = client.get("/admin/projects/material-requirements",
                          params={"project_no": "JSBY-26B-012", "expand": "all"})
        assert full.status_code == 200
        assert ">L1</span>" in full.text and ">C3</span>" in full.text
        assert "pmr-toggle" in full.text

        export = client.get("/admin/projects/material-requirements/export",
                            params={"project_no": "JSBY-26B-012"})
        assert export.status_code == 200
        assert export.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert XlsxSheetReader.read_rows(export.content)[0].values == HEADER


def test_admin_page_search_candidates_and_empty_states(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "pmr-sup").status_code == 303
        search = client.get("/admin/projects/material-requirements", params={"q": "26B-012"})
        assert search.status_code == 200
        assert "匹配到的项目" in search.text
        assert "/admin/projects/material-requirements?project_no=JSBY-26B-012" in search.text

        no_batch = client.get("/admin/projects/material-requirements",
                              params={"project_no": "JSBY-26B-099"})
        assert no_batch.status_code == 200
        assert "未匹配到备料表批次" in no_batch.text

        not_found = client.get("/admin/projects/material-requirements",
                               params={"project_no": "NOPE-999"})
        assert not_found.status_code == 200
        assert "项目不存在" in not_found.text

        blank = client.get("/admin/projects/material-requirements")
        assert blank.status_code == 200
        assert "请输入项目号查询" in blank.text


def test_admin_page_forbidden_for_warehouse(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "pmr-wh").status_code == 303
        assert client.get("/admin/projects/material-requirements").status_code == 403


def response_text(body: dict) -> str:
    import json

    return json.dumps(body, ensure_ascii=False)
