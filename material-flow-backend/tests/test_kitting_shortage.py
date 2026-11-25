"""需求 2：工单齐套情况 + 缺料清单 —— API / 导出 / 管理台。

覆盖验收要求：跨机台合并、排序（缺口降序 + 影响机台数降序）、分页、缺口 0 不出现、
无需求记录不出现、ASSEMBLER 越权 403、齐套率边界（全齐/全缺/部分）、导出两个 sheet、
以及权限与需求 1 同口径（WAREHOUSE_ADMIN 可查）。
"""
from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

from app import main as backend  # noqa: E402
from app.xlsx_parser import XlsxSheetReader  # noqa: E402

WORKER_PASSWORD = "Worker@2026"
PROJECT = "JSBY-26B-200"
OVERVIEW_HEADER = (
    "项目号", "项目名称", "范围", "机台号", "机台名称", "需求行数", "齐套行数",
    "缺料行数", "齐套率(%)", "缺料料号数", "缺口总量",
)
SHORTAGE_HEADER = (
    "料号", "品名", "规格", "单位", "总缺口量", "影响机台数", "影响机台",
    "最不利状态", "关联订单号",
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


def add_material(code: str, name: str | None = None) -> None:
    c = backend.db()
    c.execute(
        "INSERT OR IGNORE INTO materials(id,code,name,specification,unit) VALUES(?,?,?,?,?)",
        (f"mat-{code}", code, name or f"品名-{code}", "规格", "件"),
    )
    c.commit()
    c.close()


def add_requirement(order_no: str, device_id: str, material_code: str, *,
                    required: int, arrived: int = 0, in_stock: int = 0,
                    status: str = "OUT_OF_STOCK") -> None:
    add_material(material_code)
    c = backend.db()
    order_id = c.execute(
        "SELECT id FROM production_orders WHERE order_no=?", (order_no,)
    ).fetchone()["id"]
    ts = backend.now()
    c.execute(
        "INSERT INTO order_material_requirements(id,order_id,device_id,material_id,"
        "required_quantity,arrived_quantity,in_stock_quantity,status_code,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f"req-{order_no}-{device_id}-{material_code}", order_id, device_id,
         f"mat-{material_code}", required, arrived, in_stock, status, ts, ts),
    )
    c.commit()
    c.close()


def add_task(order_no: str, device_no: str, assembler_id: str | None, task_no: int) -> None:
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_summary(client: TestClient, token: str, project_no: str = PROJECT, **params):
    return client.get(f"/api/v1/projects/{project_no}/kitting-summary",
                      params=params, headers=auth(token))


def get_shortages(client: TestClient, token: str, project_no: str = PROJECT, **params):
    return client.get(f"/api/v1/projects/{project_no}/shortage-list",
                      params=params, headers=auth(token))


@pytest.fixture()
def seeded():
    """主项目 6 台机台（覆盖缺口排序/合并/折叠）+ 三个齐套率边界项目 + 他人项目。"""
    add_user("kt-admin", "ADMIN")
    add_user("kt-sup", "WORKSHOP_SUPERVISOR")
    add_user("kt-planner", "PLANNER")
    add_user("kt-wh", "WAREHOUSE_ADMIN")
    add_user("kt-asm-1", "ASSEMBLER")
    add_user("kt-asm-2", "ASSEMBLER")

    devices = add_project(PROJECT, "26B-200 齐套主项目", [f"P{i}" for i in range(1, 8)])
    # M-BIG：6 台机台各缺 10 → 合并成一行、缺口 60、影响 6 台（>5 折叠）
    for i in range(1, 7):
        add_requirement(PROJECT, devices[f"P{i}"], "M-BIG", required=10, in_stock=0, status="OUT_OF_STOCK")
    # M-MID：2 台各缺 5 → 缺口 10、影响 2 台
    for i in (1, 2):
        add_requirement(PROJECT, devices[f"P{i}"], "M-MID", required=10, arrived=5, in_stock=5, status="OUT_OF_STOCK")
    # M-T1 / M-T2 缺口同为 9，M-T2 影响 3 台必须排在前面（排序第二关键字）
    add_requirement(PROJECT, devices["P3"], "M-T1", required=9, in_stock=0, status="OUT_OF_STOCK")
    add_requirement(PROJECT, devices["P1"], "M-T2", required=4, arrived=2, in_stock=2, status="OUT_OF_STOCK")
    add_requirement(PROJECT, devices["P2"], "M-T2", required=4, arrived=1, in_stock=1, status="OUT_OF_STOCK")
    add_requirement(PROJECT, devices["P3"], "M-T2", required=4, arrived=0, in_stock=0, status="OUT_OF_STOCK")
    # 状态「到货」但只到一部分：必须按缺料算，不能算齐套
    add_requirement(PROJECT, devices["P4"], "M-ARRIVED", required=10, arrived=4, in_stock=2, status="ARRIVED")
    # 状态写着无在库，但缺口为 0（已足量到货/在库）：不进缺料清单
    add_requirement(PROJECT, devices["P6"], "M-ZERO", required=5, arrived=5, in_stock=5, status="OUT_OF_STOCK")
    # 真正齐套
    add_requirement(PROJECT, devices["P7"], "M-FULL", required=4, arrived=4, in_stock=4, status="IN_STOCK")
    # 物料主档存在但没有需求记录：绝不能进缺料清单
    add_material("M-NOREC")

    # 齐套率边界：50.0 / 100.0 / 0.0 / 无需求行
    half = add_project("JSBY-26B-201", "26B-201 半齐套", ["R1"])
    add_requirement("JSBY-26B-201", half["R1"], "K-FULL", required=5, arrived=5, in_stock=5, status="IN_STOCK")
    add_requirement("JSBY-26B-201", half["R1"], "K-SHORT", required=5, arrived=3, status="ARRIVED")
    all_done = add_project("JSBY-26B-202", "26B-202 全齐套", ["S1"])
    add_requirement("JSBY-26B-202", all_done["S1"], "DONE-1", required=3, arrived=3, in_stock=3, status="IN_STOCK")
    all_short = add_project("JSBY-26B-203", "26B-203 全缺料", ["T1"])
    add_requirement("JSBY-26B-203", all_short["T1"], "SHORT-1", required=7, status="OUT_OF_STOCK")
    add_project("JSBY-26B-204", "26B-204 无需求记录", ["U1"])

    # 他人项目：装配工越权用
    other = add_project("JSBY-26B-205", "26B-205 他人项目", ["X1"])
    add_requirement("JSBY-26B-205", other["X1"], "SECRET-1", required=9, status="OUT_OF_STOCK")

    add_task(PROJECT, "P1", "kt-asm-1", 1)
    add_task(PROJECT, "P2", "kt-asm-2", 2)
    add_task("JSBY-26B-205", "X1", "kt-asm-2", 3)
    return devices


def codes(body: dict) -> list[str]:
    return [item["materialCode"] for item in body["items"]]


# ---------------------------------------------------------------- 概览


def test_summary_counts_rate_and_device_grouping(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        response = get_summary(client, token)
        assert response.status_code == 200, response.text
        body = response.json()

    summary = body["summary"]
    assert body["projectNo"] == PROJECT
    assert body["projectName"] == "26B-200 齐套主项目"
    assert summary["deviceCount"] == 7
    assert summary["projectDeviceCount"] == 7
    # 15 行需求：13 缺口>0 + 1 齐套 + 1 缺口为 0 的「无在库」异常行
    assert summary["requirementLineCount"] == 15
    assert summary["kittedLineCount"] == 1
    assert summary["shortageLineCount"] == 13
    assert summary["zeroGapLineCount"] == 1
    assert summary["kittingRate"] == 6.7
    assert summary["shortageMaterialCount"] == 5
    assert summary["totalShortageQuantity"] == 96
    assert summary["statusCounts"] == {"outOfStock": 13, "arrived": 1, "inStock": 1, "other": 0}

    devices = {d["deviceNo"]: d for d in body["devices"]}
    assert list(devices) == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert devices["P1"]["requirementLineCount"] == 3
    assert devices["P1"]["shortageLineCount"] == 3
    assert devices["P1"]["kittingRate"] == 0.0
    assert [t["materialCode"] for t in devices["P1"]["topShortages"]] == ["M-BIG", "M-MID", "M-T2"]
    assert devices["P5"]["kittingRate"] == 0.0
    assert devices["P7"]["kittingRate"] == 100.0
    assert devices["P7"]["topShortages"] == []
    # 状态无在库但缺口为 0：不算齐套、也不算缺料
    assert devices["P6"]["kittingRate"] == 0.0
    assert devices["P6"]["shortageLineCount"] == 1  # 只有 M-BIG；M-ZERO 缺口为 0
    assert devices["P6"]["zeroGapLineCount"] == 1
    assert "items" not in body, "概览接口不返回缺料明细"


def test_kitting_rate_boundaries(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        partial = get_summary(client, token, "JSBY-26B-201").json()["summary"]
        full = get_summary(client, token, "JSBY-26B-202").json()["summary"]
        none_kitted = get_summary(client, token, "JSBY-26B-203").json()["summary"]
        empty = get_summary(client, token, "JSBY-26B-204")
        assert empty.status_code == 200
        empty_body = empty.json()

    assert (partial["requirementLineCount"], partial["kittedLineCount"]) == (2, 1)
    assert partial["kittingRate"] == 50.0
    assert (full["requirementLineCount"], full["kittedLineCount"]) == (1, 1)
    assert full["kittingRate"] == 100.0
    assert none_kitted["kittingRate"] == 0.0
    assert none_kitted["shortageLineCount"] == 1
    # 没有任何需求行时返回 null（不能用 0% 冒充）
    assert empty_body["summary"]["requirementLineCount"] == 0
    assert empty_body["summary"]["kittingRate"] is None
    assert empty_body["devices"][0]["kittingRate"] is None
    assert get_shortages(client, token, "JSBY-26B-204").json()["items"] == []


# ---------------------------------------------------------------- 缺料清单


def test_shortage_list_merges_across_devices_and_sorts(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        response = get_shortages(client, token)
        assert response.status_code == 200, response.text
        body = response.json()

    # 缺口降序，缺口相同按影响机台数降序；M-ZERO / M-NOREC / M-FULL 不得出现
    assert codes(body) == ["M-BIG", "M-MID", "M-T2", "M-T1", "M-ARRIVED"]
    assert body["total"] == 5
    assert body["page"] == 1 and body["pageSize"] == 50 and body["hasMore"] is False
    assert body["summary"] == {
        "mergedMaterialCount": 5,
        "totalShortageQuantity": 96,
        "deviceCount": 7,
        "shortageLineCount": 13,
    }

    big = body["items"][0]
    assert big["shortageQuantity"] == 60
    assert big["affectedDeviceCount"] == 6
    assert big["affectedDeviceNos"] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert big["deviceNoText"] == "P1,P2,P3,P4,P5 等 6 台", "超过 5 台必须折叠"
    assert big["requiredQuantity"] == 60 and big["inStockQuantity"] == 0
    assert big["lineCount"] == 6
    assert big["statusCode"] == "OUT_OF_STOCK" and big["statusLabel"] == "缺货"
    assert big["orderNo"] == PROJECT
    assert big["unit"] == "件" and big["specification"] == "规格"

    mid = body["items"][1]
    assert (mid["shortageQuantity"], mid["affectedDeviceCount"]) == (10, 2)
    assert mid["deviceNoText"] == "P1,P2"
    # 同为 9 的缺口：3 台机台的 M-T2 在 1 台的 M-T1 之前
    tie2, tie1 = body["items"][2], body["items"][3]
    assert (tie2["materialCode"], tie2["shortageQuantity"], tie2["affectedDeviceCount"]) == ("M-T2", 9, 3)
    assert (tie1["materialCode"], tie1["shortageQuantity"], tie1["affectedDeviceCount"]) == ("M-T1", 9, 1)
    # 部分到货：最不利状态不是「缺货」而是「到货」，但缺口仍要算
    arrived = body["items"][4]
    assert arrived["statusCode"] == "ARRIVED" and arrived["statusLabel"] == "到货"
    assert arrived["shortageQuantity"] == 8
    assert "M-ZERO" not in response.text
    assert "M-NOREC" not in response.text
    assert "M-FULL" not in response.text


def test_shortage_list_filters_by_device_and_material(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        p1 = get_shortages(client, token, device_no="P1").json()
        assert codes(p1) == ["M-BIG", "M-MID", "M-T2"]
        assert p1["summary"]["deviceCount"] == 1
        assert all(i["affectedDeviceNos"] == ["P1"] for i in p1["items"])
        assert p1["summary"]["totalShortageQuantity"] == 17

        only_short = get_shortages(client, token, material_code="m-t").json()
        assert codes(only_short) == ["M-T2", "M-T1"]
        assert only_short["materialFilter"] == "m-t"

        missing = get_shortages(client, token, device_no="NOPE-1")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "DEVICE_NOT_FOUND"


def test_shortage_list_pagination_is_stable(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        pages = [get_shortages(client, token, page=page, pageSize=2).json() for page in (1, 2, 3)]
        too_big = get_shortages(client, token, pageSize=201)
        bad_page = get_shortages(client, token, page=0)

    flattened = [code for page in pages for code in codes(page)]
    assert flattened == ["M-BIG", "M-MID", "M-T2", "M-T1", "M-ARRIVED"]
    assert len(set(flattened)) == 5, "翻页不能重复"
    assert [len(codes(p)) for p in pages] == [2, 2, 1]
    assert [p["hasMore"] for p in pages] == [True, True, False]
    assert all(p["total"] == 5 for p in pages)
    assert all(p["pageSize"] == 2 for p in pages)
    # 越界页返回空列表但 total 不变
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        beyond = get_shortages(client, token, page=9, pageSize=2).json()
    assert beyond["items"] == [] and beyond["total"] == 5 and beyond["hasMore"] is False
    assert too_big.status_code == 422
    assert bad_page.status_code == 422


# ---------------------------------------------------------------- 权限


def test_assembler_scope_and_foreign_project_403(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-asm-1")
        body = get_shortages(client, token).json()
        summary = get_summary(client, token).json()
        export = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export", headers=auth(token))

        # 只看本人机台 P1：合并后的影响机台只剩 P1，缺口也只算 P1
        assert [i["materialCode"] for i in body["items"]] == ["M-BIG", "M-MID", "M-T2"]
        assert all(i["affectedDeviceNos"] == ["P1"] for i in body["items"])
        assert body["summary"]["deviceCount"] == 1
        assert summary["summary"]["deviceCount"] == 1
        assert summary["summary"]["projectDeviceCount"] == 7
        assert [d["deviceNo"] for d in summary["devices"]] == ["P1"]
        # 装配工不暴露「未挂机台的需求行」统计
        assert summary["summary"]["unmappedRequirementCount"] is None
        assert "P2" not in body["items"][0]["deviceNoText"]

        own_export = XlsxSheetReader.sheet_names(export.content)
        assert own_export == ["齐套概览", "缺料清单"]

        # 越权机台 / 他人项目：403 而不是空数据
        denied = get_shortages(client, token, device_no="P2")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "FORBIDDEN"
        foreign = get_summary(client, token, "JSBY-26B-205")
        assert foreign.status_code == 403
        assert foreign.json()["error"]["code"] == "PROJECT_FORBIDDEN"
        assert "SECRET-1" not in foreign.text
        foreign_list = get_shortages(client, token, "JSBY-26B-205")
        assert foreign_list.status_code == 403
        foreign_export = client.get("/api/v1/projects/JSBY-26B-205/shortage-list/export",
                                    headers=auth(token))
        assert foreign_export.status_code == 403


def test_role_matrix_matches_requirement_one_plus_warehouse(seeded):
    with TestClient(backend.app) as client:
        for username in ("kt-sup", "kt-planner", "kt-wh"):
            token = login(client, username)
            assert get_summary(client, token).status_code == 200, username
            assert get_shortages(client, token).status_code == 200, username
        wh = login(client, "kt-wh")
        # 仓库可查齐套，但仍不能查需求 1 的项目物料需求（口径未变）
        assert get_summary(client, wh).status_code == 200
        assert client.get(f"/api/v1/projects/{PROJECT}/material-requirements",
                          headers=auth(wh)).status_code == 403
        assert client.get(f"/api/v1/projects/{PROJECT}/kitting-summary").status_code == 401
        missing = get_summary(client, login(client, "kt-admin"), "NOPE-999")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------- 导出


def test_export_two_sheets_parseable(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        response = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert "attachment" in response.headers["content-disposition"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]

    assert XlsxSheetReader.sheet_names(response.content) == ["齐套概览", "缺料清单"]

    overview = XlsxSheetReader.read_rows(response.content, sheet_name="齐套概览")
    assert overview[0].values == OVERVIEW_HEADER
    data = [r.values for r in overview[1:]]
    assert len(data) == 8, "1 行项目合计 + 7 台机台"
    assert data[0][2] == "项目合计"
    assert data[0][5:9] == ("15", "1", "13", "6.7")
    assert data[0][9] == "5" and data[0][10] == "96"
    assert [r[3] for r in data[1:]] == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert data[7][8] == "100.0"  # P7 全齐

    shortages = XlsxSheetReader.read_rows(response.content, sheet_name="缺料清单")
    assert shortages[0].values == SHORTAGE_HEADER
    rows = [r.values for r in shortages[1:]]
    assert [r[0] for r in rows] == ["M-BIG", "M-MID", "M-T2", "M-T1", "M-ARRIVED"]
    assert rows[0][4] == "60" and rows[0][5] == "6"
    assert rows[0][6] == "P1,P2,P3,P4,P5 等 6 台"
    assert rows[0][7] == "缺货" and rows[0][8] == PROJECT

    # 导出不受分页影响：过滤机台 P1 后仍只含 P1
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        one = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                         params={"device_no": "P1"}, headers=auth(token))
    one_rows = [r.values for r in XlsxSheetReader.read_rows(one.content, "缺料清单")[1:]]
    assert [r[0] for r in one_rows] == ["M-BIG", "M-MID", "M-T2"]
    assert all(r[6] == "P1" for r in one_rows)


def test_existing_material_status_endpoint_unchanged(seeded):
    """新增路由不得改变既有 /api/v1/orders/material-status 的返回字段。"""
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        response = client.post(
            "/api/v1/orders/material-status",
            json={"documentNo": PROJECT, "documentType": "PRODUCTION_ORDER", "pageSize": 2},
            headers=auth(token),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"items", "materialSummary", "shortageLineCount", "total", "hasMore"} <= set(body)
    assert body["total"] == 15
    assert body["shortageLineCount"] == 13


# ---------------------------------------------------------------- 管理台


def web_login(client: TestClient, username: str, password: str = WORKER_PASSWORD):
    page = client.get("/admin/login")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return client.post("/admin/login",
                       data={"username": username, "password": password,
                             "csrf_token": match.group(1)},
                       follow_redirects=False)


def test_admin_kitting_page_and_export(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "kt-sup").status_code == 303
        blank = client.get("/admin/projects/kitting")
        assert blank.status_code == 200
        assert "请输入项目号查询" in blank.text
        assert "齐套与缺料" in blank.text

        page = client.get("/admin/projects/kitting", params={"project_no": PROJECT})
        assert page.status_code == 200, page.text
        html = page.text
        assert "26B-200 齐套主项目" in html
        assert "缺料清单" in html and "导出 xlsx" in html
        assert "6.7%" in html
        assert "P1,P2,P3,P4,P5 等 6 台" in html
        assert "M-BIG" in html and "M-ZERO" not in html

        search = client.get("/admin/projects/kitting", params={"q": "26B-201"})
        assert search.status_code == 200
        assert "匹配到的项目" in search.text
        assert "project_no=JSBY-26B-201" in search.text

        not_found = client.get("/admin/projects/kitting", params={"project_no": "NOPE-999"})
        assert not_found.status_code == 200
        assert "项目不存在" in not_found.text

        export = client.get("/admin/projects/kitting/export", params={"project_no": PROJECT})
        assert export.status_code == 200
        assert XlsxSheetReader.sheet_names(export.content) == ["齐套概览", "缺料清单"]


def test_admin_kitting_page_roles(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "kt-wh").status_code == 303
        assert client.get("/admin/projects/kitting",
                          params={"project_no": PROJECT}).status_code == 200
    with TestClient(backend.app) as client:
        assert web_login(client, "kt-asm-1").status_code == 303
        denied = client.get("/admin/projects/kitting")
        assert denied.status_code == 403
        assert "齐套与缺料清单" in denied.text
