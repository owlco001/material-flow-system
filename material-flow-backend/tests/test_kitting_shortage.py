"""需求 2：工单齐套情况 + 缺料清单 —— API / 导出 / 管理台。

覆盖验收要求：跨机台合并、双口径（采购缺口 / 备料缺口）、按两种缺口排序、分页、
缺口 0 不出现、无需求记录不出现、ASSEMBLER 越权 403、齐套率边界（全齐/全缺/部分）、
导出两个 sheet 与 [Content_Types].xml 结构、权限与需求 1 同口径（WAREHOUSE_ADMIN 可查），
以及审查回归（仓库搜索 403、已到货却报缺料、导出对不上账、概览/清单同屏两套口径、
装配工机台数泄露）。
"""
from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "Admin@2026")

from app import main as backend  # noqa: E402
from app.xlsx_parser import XlsxSheetReader  # noqa: E402

WORKER_PASSWORD = "Worker@2026"
PROJECT = "JSBY-26B-200"
OVERVIEW_HEADER = (
    "项目号", "项目名称", "范围", "机台号", "机台名称", "需求行数", "齐套行数",
    "缺料行数", "状态异常行数", "齐套率(%)", "缺料料号数", "采购缺口", "备料缺口",
)
SHORTAGE_HEADER = (
    "料号", "品名", "规格", "单位", "采购缺口", "备料缺口", "在库量", "影响机台数",
    "影响机台", "最不利状态", "关联订单号",
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
    # 状态「到货」但只到一部分：必须按缺料算，不能算齐套。
    # arrived=2 / in_stock=0 → 采购缺口 8、备料缺口 10（两个口径故意不同）
    add_requirement(PROJECT, devices["P4"], "M-ARRIVED", required=10, arrived=2, in_stock=0, status="ARRIVED")
    # 状态写着无在库，但货已到齐（arrived==required）：不进缺料清单，且算齐套
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
    # P1-2：已全量到货但 in_stock=0（不得进缺料清单、且算齐套）+ 部分到货（仍缺）
    arrived_project = add_project("JSBY-26B-206", "26B-206 已到货待入库", ["V1"])
    add_requirement("JSBY-26B-206", arrived_project["V1"], "A-ARRIVED-FULL",
                    required=5, arrived=5, in_stock=0, status="ARRIVED")
    add_requirement("JSBY-26B-206", arrived_project["V1"], "A-PARTIAL",
                    required=5, arrived=2, in_stock=0, status="ARRIVED")

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
    # 15 行需求：13 行采购缺口>0 + 2 行到货齐了（M-FULL + 状态未回写的 M-ZERO）
    assert summary["requirementLineCount"] == 15
    assert summary["kittedLineCount"] == 2
    assert summary["shortageLineCount"] == 13
    # 「到货齐了但状态还是无在库」只作为齐套行的子集披露，不参与恒等式
    assert summary["statusAnomalyLineCount"] == 1
    assert "zeroGapLineCount" not in summary
    assert summary["kittingRate"] == 13.3
    assert summary["shortageMaterialCount"] == 5
    # 双口径：采购缺口只算缺料行（60+10+9+9+8=96）；
    # 备料缺口按全部需求行（M-ARRIVED 备料还差 10），故 98
    assert summary["totalProcureGapQuantity"] == 96
    assert summary["totalPrepareGapQuantity"] == 98
    assert "totalShortageQuantity" not in summary, "含义含糊的单口径字段已移除"
    assert summary["statusCounts"] == {"outOfStock": 13, "arrived": 1, "inStock": 1, "other": 0}
    # 恒等式：需求行 = 齐套 + 缺料（双口径后不再需要零缺口兜底）
    assert summary["requirementLineCount"] == (
        summary["kittedLineCount"] + summary["shortageLineCount"]
    )

    devices = {d["deviceNo"]: d for d in body["devices"]}
    assert list(devices) == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert devices["P1"]["requirementLineCount"] == 3
    assert devices["P1"]["shortageLineCount"] == 3
    assert devices["P1"]["kittingRate"] == 0.0
    assert [t["materialCode"] for t in devices["P1"]["topShortages"]] == ["M-BIG", "M-MID", "M-T2"]
    assert devices["P5"]["kittingRate"] == 0.0
    assert devices["P7"]["kittingRate"] == 100.0
    assert devices["P7"]["topShortages"] == []
    # 状态仍是「无在库」但货已到齐：算齐套，同时计入状态异常行
    assert devices["P6"]["kittingRate"] == 50.0
    assert devices["P6"]["kittedLineCount"] == 1
    assert devices["P6"]["shortageLineCount"] == 1  # 只有 M-BIG；M-ZERO 采购缺口为 0
    assert devices["P6"]["statusAnomalyLineCount"] == 1
    assert devices["P6"]["procureGapQuantity"] == 10
    assert devices["P6"]["prepareGapQuantity"] == 10
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
        "totalProcureGapQuantity": 96,
        "totalPrepareGapQuantity": 98,
        "listPrepareGapQuantity": 98,
        "deviceCount": 7,
        "shortageLineCount": 13,
    }

    big = body["items"][0]
    assert (big["procureGapQuantity"], big["prepareGapQuantity"]) == (60, 60)
    assert big["affectedDeviceCount"] == 6
    assert big["affectedDeviceNos"] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert big["deviceNoText"] == "P1,P2,P3,P4,P5 等 6 台", "超过 5 台必须折叠"
    assert big["requiredQuantity"] == 60 and big["inStockQuantity"] == 0
    assert big["lineCount"] == 6
    assert big["statusCode"] == "OUT_OF_STOCK" and big["statusLabel"] == "缺货"
    assert big["orderNo"] == PROJECT
    assert big["unit"] == "件" and big["specification"] == "规格"

    mid = body["items"][1]
    assert (mid["procureGapQuantity"], mid["prepareGapQuantity"], mid["affectedDeviceCount"]) == (10, 10, 2)
    assert mid["deviceNoText"] == "P1,P2"
    # 同为 9 的缺口：3 台机台的 M-T2 在 1 台的 M-T1 之前
    tie2, tie1 = body["items"][2], body["items"][3]
    assert (tie2["materialCode"], tie2["procureGapQuantity"], tie2["affectedDeviceCount"]) == ("M-T2", 9, 3)
    assert (tie1["materialCode"], tie1["procureGapQuantity"], tie1["affectedDeviceCount"]) == ("M-T1", 9, 1)
    # 部分到货：最不利状态不是「缺货」而是「到货」，但采购缺口仍要算
    arrived = body["items"][4]
    assert arrived["statusCode"] == "ARRIVED" and arrived["statusLabel"] == "到货"
    # 两个口径同时给出：采购 10−2=8，备料 10−0=10
    assert (arrived["procureGapQuantity"], arrived["prepareGapQuantity"]) == (8, 10)
    assert (arrived["requiredQuantity"], arrived["arrivedQuantity"], arrived["inStockQuantity"]) == (10, 2, 0)
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
        assert p1["summary"]["totalProcureGapQuantity"] == 17
        assert p1["summary"]["totalPrepareGapQuantity"] == 17

        only_short = get_shortages(client, token, material_code="m-t").json()
        assert codes(only_short) == ["M-T2", "M-T1"]
        assert only_short["materialFilter"] == "m-t"
        # P2-2：料号过滤同时作用于概览，概览缺口合计与清单列求和必须相等
        # （改前概览是全项目 96、清单是过滤后 18，同一屏两套口径）
        assert only_short["summary"]["totalProcureGapQuantity"] == 18
        assert only_short["summary"]["totalPrepareGapQuantity"] == 18
        assert sum(i["procureGapQuantity"] for i in only_short["items"]) == 18
        # 不带过滤时仍是全项目口径
        assert get_shortages(client, token).json()["summary"]["totalProcureGapQuantity"] == 96

        # 同一屏（导出两个 sheet）也必须同口径：概览合计 = 清单列求和
        missing = get_shortages(client, token, device_no="NOPE-1")
        export = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                            params={"material_code": "m-t"}, headers=auth(token))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DEVICE_NOT_FOUND"
    overview = [r.values for r in XlsxSheetReader.read_rows(export.content, "齐套概览")[1:]]
    total = overview[0]
    assert total[5:9] == ("4", "0", "4", "0"), "过滤后的概览只统计命中料号的行"
    assert (total[11], total[12]) == ("18", "18")
    filtered_rows = [r.values for r in XlsxSheetReader.read_rows(export.content, "缺料清单")[1:]]
    assert [r[0] for r in filtered_rows] == ["M-T2", "M-T1"]
    assert sum(int(r[4]) for r in filtered_rows) == int(total[11])
    assert sum(int(r[5]) for r in filtered_rows) == int(total[12])


def test_shortage_list_sort_switches_metric(seeded):
    """sort=procure / sort=prepare 各自按对应缺口降序，且顺序确实不同。"""
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        procure = get_shortages(client, token).json()
        prepare = get_shortages(client, token, sort="prepare").json()
        explicit = get_shortages(client, token, sort="procure").json()
        bad = get_shortages(client, token, sort="bogus")

    assert codes(procure) == ["M-BIG", "M-MID", "M-T2", "M-T1", "M-ARRIVED"]
    assert procure["sort"] == "procure"
    assert codes(explicit) == codes(procure)
    # 备料缺口：M-BIG 60 → M-MID 10(2台) → M-ARRIVED 10(1台) → M-T2 9(3台) → M-T1 9(1台)
    assert codes(prepare) == ["M-BIG", "M-MID", "M-ARRIVED", "M-T2", "M-T1"]
    assert prepare["sort"] == "prepare"
    assert [i["prepareGapQuantity"] for i in prepare["items"]] == [60, 10, 10, 9, 9]
    assert [i["procureGapQuantity"] for i in procure["items"]] == [60, 10, 9, 9, 8]
    assert codes(prepare) != codes(procure)
    # 两种排序的集合必须相同（只是顺序不同）
    assert sorted(codes(prepare)) == sorted(codes(procure))
    assert bad.status_code == 422


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
        # P2-3：项目总机台数对装配工隐藏（与 unmappedRequirementCount 同口径）
        assert summary["summary"]["projectDeviceCount"] is None
        assert summary["summary"]["unmappedRequirementCount"] is None
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
    # 需求 / 齐套 / 缺料 / 状态异常 / 齐套率
    assert data[0][5:10] == ("15", "2", "13", "1", "13.3")
    # 缺料料号数 / 采购缺口 / 备料缺口
    assert data[0][10:] == ("5", "96", "98")
    # P2-1：需求行 = 齐套 + 缺料（双口径后恒等式不再需要零缺口兜底）
    assert int(data[0][5]) == int(data[0][6]) + int(data[0][7])
    for row in data[1:]:
        assert int(row[5]) == int(row[6]) + int(row[7]), row[3]
    assert [r[3] for r in data[1:]] == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert data[7][9] == "100.0"  # P7 全齐
    assert data[6][9] == "50.0", "P6：M-BIG 缺料 + 已到货未回写的 M-ZERO"
    assert (data[6][11], data[6][12]) == ("10", "10")

    shortages = XlsxSheetReader.read_rows(response.content, sheet_name="缺料清单")
    assert shortages[0].values == SHORTAGE_HEADER
    rows = [r.values for r in shortages[1:]]
    assert [r[0] for r in rows] == ["M-BIG", "M-MID", "M-T2", "M-T1", "M-ARRIVED"]
    # 采购缺口 / 备料缺口 / 在库量 / 影响机台数
    assert rows[0][4:8] == ("60", "60", "0", "6")
    assert rows[0][8] == "P1,P2,P3,P4,P5 等 6 台"
    assert rows[0][9] == "缺货" and rows[0][10] == PROJECT
    # 双列可对账：M-MID 采购缺口 5×2=10、备料缺口 5×2=10、在库 5×2=10
    mid_row = rows[1]
    assert (mid_row[0], mid_row[4], mid_row[5], mid_row[6]) == ("M-MID", "10", "10", "10")
    # 两个口径不同的料号：采购 8 / 备料 10
    assert (rows[4][0], rows[4][4], rows[4][5]) == ("M-ARRIVED", "8", "10")

    # 导出不受分页影响：过滤机台 P1 后仍只含 P1
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        one = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                         params={"device_no": "P1"}, headers=auth(token))
    one_rows = [r.values for r in XlsxSheetReader.read_rows(one.content, "缺料清单")[1:]]
    assert [r[0] for r in one_rows] == ["M-BIG", "M-MID", "M-T2"]
    assert all(r[8] == "P1" for r in one_rows)


def test_export_sort_prepare_reorders_shortage_sheet(seeded):
    """导出与列表同源：sort=prepare 时缺料 sheet 顺序按备料缺口。"""
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        content = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                             params={"sort": "prepare"}, headers=auth(token)).content
    rows = [r.values for r in XlsxSheetReader.read_rows(content, "缺料清单")[1:]]
    assert [r[0] for r in rows] == ["M-BIG", "M-MID", "M-ARRIVED", "M-T2", "M-T1"]
    assert [r[5] for r in rows] == ["60", "10", "10", "9", "9"]


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
        assert "13.3%" in html
        assert "P1,P2,P3,P4,P5 等 6 台" in html
        assert "M-BIG" in html and "M-ZERO" not in html
        # 双口径两列 + 图例必须在页面上
        assert "<th>采购缺口</th><th>备料缺口</th>" in html
        assert "图例：" in html and "需求 − 已到货" in html and "需求 − 在库" in html
        assert "采购缺口合计 <b>96</b>" in html

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


# ---------------------------------------------------------------- 审查回归（P1-1/P1-2/P2-3/P2-9）


def test_warehouse_admin_keyword_search_lists_candidates(seeded):
    """P1-1：仓库管理员按关键字搜索必须 200 + 候选列表（改前走 api_list_projects → JSON 403）。"""
    with TestClient(backend.app) as client:
        assert web_login(client, "kt-wh").status_code == 303
        response = client.get("/admin/projects/kitting", params={"q": "26B-201"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "匹配到的项目" in response.text
    assert "project_no=JSBY-26B-201" in response.text
    assert "26B-201 半齐套" in response.text
    assert "无权查看项目物料需求" not in response.text

    # 搜索只放开「搜索」这一步：仓库仍不能查需求 1 的接口
    with TestClient(backend.app) as client:
        wh = login(client, "kt-wh")
        assert client.get(f"/api/v1/projects/{PROJECT}/material-requirements",
                          headers=auth(wh)).status_code == 403
        # 但不带关键字直接进入页面仍是正常空态
        assert client.get("/admin/projects/kitting").status_code == 200


def test_arrived_full_line_is_kitted_and_not_short(seeded):
    """P1-2：arrived == required 且 in_stock == 0 → 采购缺口 0、不进清单、算齐套，
    但备料缺口 5 仍要计入概览（双口径的核心用例）。

    改前按 required - in_stock 计算，A-ARRIVED-FULL 会被报缺 5 件、齐套率 0%。
    """
    project_no = "JSBY-26B-206"
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        summary = get_summary(client, token, project_no).json()["summary"]
        listing = get_shortages(client, token, project_no).json()
        export = client.get(f"/api/v1/projects/{project_no}/shortage-list/export",
                            headers=auth(token))

    assert (summary["requirementLineCount"], summary["kittedLineCount"],
            summary["shortageLineCount"], summary["statusAnomalyLineCount"]) == (2, 1, 1, 0)
    assert summary["kittingRate"] == 50.0
    # 采购缺口只算 A-PARTIAL(5−2=3)；备料缺口含 A-ARRIVED-FULL(5−0=5) → 5+5=... 见下
    assert summary["totalProcureGapQuantity"] == 3
    # 备料口径含「已到货未入库」那 5 件：5(A-ARRIVED-FULL) + 5(A-PARTIAL) = 10
    assert summary["totalPrepareGapQuantity"] == 10

    # 已全量到货的行不出现；部分到货的行仍缺 required - arrived = 3
    assert [i["materialCode"] for i in listing["items"]] == ["A-PARTIAL"]
    assert listing["items"][0]["procureGapQuantity"] == 3
    assert listing["items"][0]["prepareGapQuantity"] == 5
    assert listing["items"][0]["inStockQuantity"] == 0
    assert "A-ARRIVED-FULL" not in str(listing)
    assert listing["summary"]["totalProcureGapQuantity"] == 3
    assert listing["summary"]["listPrepareGapQuantity"] == 5, "清单内备料缺口只算缺料行"

    shortage_rows = [r.values for r in XlsxSheetReader.read_rows(export.content, "缺料清单")[1:]]
    assert [r[0] for r in shortage_rows] == ["A-PARTIAL"]
    assert (shortage_rows[0][4], shortage_rows[0][5], shortage_rows[0][6]) == ("3", "5", "0")
    overview = [r.values for r in XlsxSheetReader.read_rows(export.content, "齐套概览")[1:]]
    assert overview[0][5:9] == ("2", "1", "1", "0")
    assert (overview[0][11], overview[0][12]) == ("3", "10")


def test_empty_project_export_blank_rate_and_total_row(seeded):
    """P2-9：空项目（无需求行）导出写 "" 而不是 0，合计行仍成立。"""
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        response = client.get("/api/v1/projects/JSBY-26B-204/shortage-list/export",
                              headers=auth(token))
    assert response.status_code == 200, response.text

    overview = XlsxSheetReader.read_rows(response.content, sheet_name="齐套概览")
    assert overview[0].values == OVERVIEW_HEADER
    data = [r.values for r in overview[1:]]
    assert len(data) == 2, "1 行项目合计 + 1 台机台"
    total, device = data
    assert total[2] == "项目合计"
    # 需求/齐套/缺料/异常 全 0，齐套率空串（不能写 0.0），两个缺口合计 0
    assert total[5:13] == ("0", "0", "0", "0", "", "0", "0", "0")
    assert device[3] == "U1"
    assert device[5:10] == ("0", "0", "0", "0", "")

    shortages = XlsxSheetReader.read_rows(response.content, sheet_name="缺料清单")
    assert shortages[0].values == SHORTAGE_HEADER
    assert [r.values for r in shortages[1:]] == []


def test_export_content_types_matches_zip_parts(seeded):
    """P2-9：[Content_Types].xml 的每个 Override 都要指向 zip 里真实存在的 part。"""
    with TestClient(backend.app) as client:
        token = login(client, "kt-admin")
        content = client.get(f"/api/v1/projects/{PROJECT}/shortage-list/export",
                             headers=auth(token)).content

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        types = ET.fromstring(archive.read("[Content_Types].xml"))
        ct_ns = {"ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
        part_names = [str(o.get("PartName") or "") for o in types.findall("ct:Override", ct_ns)]
        assert part_names, "缺少 Override"
        assert all(p.startswith("/") for p in part_names), part_names
        missing = [p for p in part_names if p.lstrip("/") not in names]
        assert missing == [], f"Override 指向不存在的 part: {missing}"

        worksheets = [p for p in part_names if p.startswith("/xl/worksheets/sheet")]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        wb_ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rel_ns = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
        sheets = workbook.findall("m:sheets/m:sheet", wb_ns)
        assert len(sheets) == len(worksheets) == 2, "sheet 数必须与 worksheet Override 数一致"
        assert [p.lstrip("/") for p in worksheets] == sorted(
            f"xl/worksheets/sheet{i}.xml" for i in (1, 2))

        # workbook 的 rId 必须在 workbook.xml.rels 里解析得到真实 part
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target")
                   for rel in rels.findall("r:Relationship", {
                       "r": "http://schemas.openxmlformats.org/package/2006/relationships"})}
        for sheet in sheets:
            rid = sheet.get(f"{{{rel_ns['r']}}}id")
            assert rid in targets, f"sheet {sheet.get('name')} 的 {rid} 不在 rels 里"
            part = "xl/" + targets[rid].lstrip("/")
            assert part in names, f"rels 目标不存在: {part}"


def test_admin_device_filter_hint_is_not_permission_limit(seeded):
    """P2-3：按机台过滤时不能再显示「其余不在你的权限内」（改前用 deviceCount 推断）。"""
    with TestClient(backend.app) as client:
        assert web_login(client, "kt-sup").status_code == 303
        filtered = client.get("/admin/projects/kitting",
                              params={"project_no": PROJECT, "device_no": "P1"})
        unfiltered = client.get("/admin/projects/kitting", params={"project_no": PROJECT})

    assert filtered.status_code == 200
    assert "可见机台 1 台" in filtered.text
    assert "已按机台 P1 过滤" in filtered.text
    assert "其余不在你的权限内" not in filtered.text
    assert "可见机台 7 台" in unfiltered.text
    assert "其余不在你的权限内" not in unfiltered.text
