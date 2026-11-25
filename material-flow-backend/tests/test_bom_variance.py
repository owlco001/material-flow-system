"""需求 4：项目结案「实际需求 vs 工程 BOM」差异报表 —— API / 导出 / 管理台。

覆盖验收要点：
  - 无工程 BOM 的机台 bomQty/deltaQty/各类计数必须是 null（不是 0），不进项目合计；
  - 三类差异边界（差 0.01 也算差异）、2 位小数（不 int() 截断）、五类 kind 判定；
  - summary 与明细同源（同一份内存结果求和，only_diff=0 时逐列对账）；
  - 只比 PUBLISHED 的最新版本（DRAFT / 旧版本不参与）；
  - ASSEMBLER 查非参与项目 → 403 PROJECT_FORBIDDEN；
  - 导出两个 sheet，数量落在正确列位（ElementTree 按列字母断言）；
  - 分页与 device_no / kind / only_diff 过滤；管理台页面与覆盖率提示。
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

WORKER_PASSWORD = "Worker@2026"
PROJECT = "JSBY-26B-301"
NO_BOM_PROJECT = "JSBY-26B-302"
OTHER_PROJECT = "JSBY-26B-303"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"s": SHEET_NS}


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


def add_material_in(c, code: str) -> None:
    c.execute(
        "INSERT OR IGNORE INTO materials(id,code,name,specification,unit) VALUES(?,?,?,?,?)",
        (f"mat-{code}", code, f"品名-{code}", "规格", "件"),
    )


def add_bom(device_no: str, version: int, items: list[tuple[str, float]], *,
            status: str = "PUBLISHED") -> None:
    """写一个 BOM 版本（model_code = 机台号）及其单层明细。"""
    c = backend.db()
    vid = f"bv-{device_no}-{version}"
    ts = backend.now()
    c.execute(
        "INSERT INTO bom_versions(id,model_code,version_no,status,source_file_sha256,"
        "row_count,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (vid, device_no, version, status, "sha-" + vid, len(items), "u_admin", ts),
    )
    for line_no, (code, qty) in enumerate(items, 1):
        add_material_in(c, code)
        c.execute(
            "INSERT INTO bom_items(id,bom_version_id,material_id,material_code,material_name,"
            "specification,unit,quantity,scrap_rate,line_no) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"bi-{vid}-{code}", vid, f"mat-{code}", code, f"品名-{code}", "规格", "件",
             qty, 0, line_no),
        )
    c.commit()
    c.close()


def add_requirement(order_no: str, device_id: str | None, material_code: str, *,
                    required: int) -> None:
    c = backend.db()
    add_material_in(c, material_code)
    order_id = c.execute(
        "SELECT id FROM production_orders WHERE order_no=?", (order_no,)
    ).fetchone()["id"]
    ts = backend.now()
    c.execute(
        "INSERT INTO order_material_requirements(id,order_id,device_id,material_id,"
        "required_quantity,arrived_quantity,in_stock_quantity,status_code,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (f"req-{order_no}-{device_id}-{material_code}", order_id, device_id,
         f"mat-{material_code}", required, 0, 0, "OUT_OF_STOCK", ts, ts),
    )
    c.commit()
    c.close()


def add_task(order_no: str, device_no: str, assembler_id: str, task_no: int) -> None:
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


@pytest.fixture()
def seeded():
    """主项目 3 台机台（D1 全类型 / D2 多版本 / D3 无 BOM）+ 全无 BOM 项目 + 他人项目。"""
    add_user("bv-admin", "ADMIN")
    add_user("bv-planner", "PLANNER")
    add_user("bv-wh", "WAREHOUSE_ADMIN")
    add_user("bv-asm", "ASSEMBLER")
    add_user("bv-asm-other", "ASSEMBLER")
    add_user("bv-op", "OPERATOR")

    devices = add_project(PROJECT, "26B-301 BOM 差异主项目", ["D1", "D2", "D3"])
    # D1：五类差异都在这一台上（M-DEC / M-INC 是 0.01 边界）
    add_bom("D1", 1, [
        ("M-SAME", 2.0),      # 需求 2 → SAME
        ("M-INC", 11.99),     # 需求 12 → INCREASED +0.01
        ("M-DEC", 8.01),      # 需求 8  → DECREASED -0.01
        ("M-MISS", 3.0),      # 无需求 → MISSING_IN_REQUIREMENT
        ("M-ROUND", 4.004),   # 需求 4 → 2 位小数后 4.0 = 4 → SAME（不是 0.004 差异）
    ])
    add_requirement(PROJECT, devices["D1"], "M-SAME", required=2)
    add_requirement(PROJECT, devices["D1"], "M-INC", required=12)
    add_requirement(PROJECT, devices["D1"], "M-DEC", required=8)
    add_requirement(PROJECT, devices["D1"], "M-ROUND", required=4)

    # D2：v1 已被 v2 取代，v9 还是 DRAFT —— 只允许 v2 参与比对
    add_bom("D2", 1, [("M-SAME", 99.0), ("M-OLD", 5.0)])
    add_bom("D2", 2, [("M-SAME", 1.0)])
    add_bom("D2", 9, [("M-DRAFT", 7.0)], status="DRAFT")
    add_requirement(PROJECT, devices["D2"], "M-SAME", required=1)
    add_requirement(PROJECT, devices["D2"], "M-NEW", required=3)   # BOM 无 → ADDED

    # D3：没有工程 BOM，只有需求 —— 必须表达为「无法比对」，不能是 0 差异
    add_requirement(PROJECT, devices["D3"], "M-ONLY", required=5)

    # 整个项目都没有工程 BOM（验收 2）
    no_bom = add_project(NO_BOM_PROJECT, "26B-302 无 BOM 项目", ["X1", "X2"])
    add_requirement(NO_BOM_PROJECT, no_bom["X1"], "N-1", required=10)
    add_requirement(NO_BOM_PROJECT, no_bom["X2"], "N-2", required=20)

    # 他人项目（装配工越权用），也带 BOM 以便证明 403 不是「本来就没数据」
    other = add_project(OTHER_PROJECT, "26B-303 他人项目", ["O1"])
    add_bom("O1", 1, [("SECRET", 1.0)])
    add_requirement(OTHER_PROJECT, other["O1"], "SECRET", required=1)

    add_task(PROJECT, "D1", "bv-asm", 1)
    add_task(OTHER_PROJECT, "O1", "bv-asm-other", 2)
    return devices


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": WORKER_PASSWORD, "deviceId": f"dev-{username}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["accessToken"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_variance(client: TestClient, token: str, project_no: str = PROJECT, **params):
    return client.get(f"/api/v1/projects/{project_no}/bom-variance",
                      params=params, headers=auth(token))


def get_export(client: TestClient, token: str, project_no: str = PROJECT, **params):
    return client.get(f"/api/v1/projects/{project_no}/bom-variance/export",
                      params=params, headers=auth(token))


def items_by_code(body: dict, device_no: str | None = None) -> dict[str, dict]:
    return {item["materialCode"]: item for item in body["items"]
            if device_no is None or item["deviceNo"] == device_no}


def device_by_no(body: dict) -> dict[str, dict]:
    return {row["deviceNo"]: row for row in body["devices"]}


def form_csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def web_login(client: TestClient, username: str, password: str = WORKER_PASSWORD):
    page = client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": form_csrf(page.text)},
        follow_redirects=False,
    )


def sheet_cells(content: bytes, sheet_name: str) -> tuple[dict[tuple[int, str], str], list[str]]:
    """按 (行号, 列字母) 读单元格原值，用来断言「数字没左移一列」。"""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [s.get("name") for s in workbook.findall("s:sheets/s:sheet", NS)]
        index = names.index(sheet_name) + 1
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
    cells: dict[tuple[int, str], str] = {}
    for cell in root.iter(f"{{{SHEET_NS}}}c"):
        ref = cell.get("r")
        row = int(re.sub(r"[A-Z]", "", ref))
        col = re.sub(r"[0-9]", "", ref)
        number = cell.find("s:v", NS)
        text = cell.find("s:is/s:t", NS)
        value = number.text if number is not None else (text.text if text is not None else None)
        cells[(row, col)] = value
    return cells, names


# ---------------------------------------------------------------- 无 BOM 的机台


def test_no_bom_device_is_null_not_zero(seeded):
    """无工程 BOM 的机台：bomQty/deltaQty/各 kind 计数为 null，requiredQty 仍是需求事实。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        body = get_variance(client, token, only_diff=0).json()

    summary = body["summary"]
    assert (summary["devices"], summary["devicesWithBom"],
            summary["devicesWithoutBom"]) == (3, 2, 1)
    assert summary["coverage"] == round(2 / 3, 4)

    d3 = device_by_no(body)["D3"]
    assert d3["hasBom"] is False
    assert d3["bomVersion"] is None
    assert d3["bomQty"] is None, "无 BOM 不许填 0 —— 那是「没比对」不是「没差异」"
    assert d3["deltaQty"] is None
    assert d3["requiredQty"] == 5, "需求件数是事实，仍要给出"
    for key in ("added", "increased", "decreased", "same", "missingInRequirement"):
        assert d3[key] is None, f"无 BOM 机台的 {key} 不能是 0"
    # 无 BOM 机台不产生明细行，也不进合计
    assert all(item["deviceNo"] != "D3" for item in body["items"])
    assert summary["materials"] == len(body["items"]) == 7


def test_project_without_any_bom_reports_no_comparison(seeded):
    """验收 2：整个项目没有工程 BOM → 0 台可比、items 为空、合计不从 0 冒充一致。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        body = get_variance(client, token, NO_BOM_PROJECT).json()
        export = get_export(client, token, NO_BOM_PROJECT)

    summary = body["summary"]
    assert (summary["devices"], summary["devicesWithBom"],
            summary["devicesWithoutBom"]) == (2, 0, 2)
    assert summary["coverage"] == 0.0
    assert body["items"] == [] and body["total"] == 0
    assert summary["bomQty"] is None and summary["requiredQty"] is None
    assert summary["deltaQty"] is None, "全部无 BOM 时不许返回 deltaQty=0"
    assert all(row["hasBom"] is False for row in body["devices"])
    assert all(row["deltaQty"] is None for row in body["devices"])

    # 导出仍要能出：两个 sheet，机台汇总里数量列留空
    assert export.status_code == 200
    cells, names = sheet_cells(export.content, "机台汇总")
    assert names == ["机台汇总", "料号差异"]
    assert cells[(2, "A")].startswith("项目合计")
    for col in ("D", "E", "F"):
        assert cells.get((2, col)) is None, "无 BOM 项目合计不许把 0 写进数量列"
    assert cells[(3, "B")] == "否"


# ---------------------------------------------------------------- 分类边界


def test_kind_boundaries_two_decimals_and_no_int_truncation(seeded):
    """差 0.01 也算差异；4.004 按 2 位小数参与比较，BOM 量不能被 int() 截断。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        body = get_variance(client, token, only_diff=0).json()

    items = items_by_code(body, "D1")
    assert items["M-INC"]["kind"] == "INCREASED" and items["M-INC"]["deltaQty"] == 0.01
    assert items["M-DEC"]["kind"] == "DECREASED" and items["M-DEC"]["deltaQty"] == -0.01
    assert items["M-ROUND"]["bomQty"] == 4.0 and items["M-ROUND"]["kind"] == "SAME"
    assert items["M-SAME"]["kind"] == "SAME" and items["M-SAME"]["deltaQty"] == 0
    assert items["M-MISS"]["kind"] == "MISSING_IN_REQUIREMENT"
    assert items["M-MISS"]["requiredQty"] == 0 and items["M-MISS"]["bomQty"] == 3.0
    # BOM 无、需求有 → ADDED（D2 的 M-NEW）
    added = items_by_code(body, "D2")["M-NEW"]
    assert added["kind"] == "ADDED" and added["bomQty"] == 0 and added["deltaQty"] == 3.0
    # 小数不被截断：BOM 11.99 / 8.01 原样保留 2 位
    assert items["M-INC"]["bomQty"] == 11.99 and items["M-DEC"]["bomQty"] == 8.01
    # 品名/规格/单位来自物料主档
    assert items["M-SAME"]["materialName"] == "品名-M-SAME"
    assert items["M-SAME"]["unit"] == "件"


def test_only_latest_published_version_is_compared(seeded):
    """只比 PUBLISHED 的最新版本：旧版本 v1 与新 DRAFT v9 都不能参与。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        body = get_variance(client, token, only_diff=0).json()

    codes = {item["materialCode"] for item in body["items"] if item["deviceNo"] == "D2"}
    assert codes == {"M-SAME", "M-NEW"}, "旧版本料号 M-OLD / DRAFT 料号 M-DRAFT 不许出现"
    d2 = device_by_no(body)["D2"]
    assert d2["bomVersion"] == 2
    assert d2["bomQty"] == 1.0 and d2["requiredQty"] == 4.0 and d2["deltaQty"] == 3.0


# ---------------------------------------------------------------- 汇总同源


def test_summary_equals_detail_sums(seeded):
    """summary 必须与明细求和一致（同一份内存结果聚合，不是第二条 SQL）。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        body = get_variance(client, token, only_diff=0).json()

    summary, items = body["summary"], body["items"]
    assert summary["materials"] == len(items) == 7
    assert summary["bomQty"] == round(sum(i["bomQty"] for i in items), 2) == 30.0
    assert summary["requiredQty"] == round(sum(i["requiredQty"] for i in items), 2) == 30.0
    assert summary["deltaQty"] == round(sum(i["deltaQty"] for i in items), 2) == 0.0
    for kind, key in (("ADDED", "added"), ("INCREASED", "increased"),
                      ("DECREASED", "decreased"), ("SAME", "same"),
                      ("MISSING_IN_REQUIREMENT", "missingInRequirement")):
        assert summary[key] == sum(1 for i in items if i["kind"] == kind), key
    assert sum(summary[key] for key in ("added", "increased", "decreased", "same",
                                        "missingInRequirement")) == len(items)
    # 每台机台的数量也等于该台明细求和
    for device in body["devices"]:
        if not device["hasBom"]:
            continue
        rows = [i for i in items if i["deviceNo"] == device["deviceNo"]]
        assert device["bomQty"] == round(sum(i["bomQty"] for i in rows), 2)
        assert device["requiredQty"] == round(sum(i["requiredQty"] for i in rows), 2)
        assert device["deltaQty"] == round(sum(i["deltaQty"] for i in rows), 2)


def test_summary_follows_device_filter(seeded):
    """按机台过滤时汇总与明细一起收窄（不是只过滤明细、汇总仍报全项目）。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        body = get_variance(client, token, device_no="D1", only_diff=0).json()

    summary = body["summary"]
    assert (summary["devices"], summary["devicesWithBom"], summary["devicesWithoutBom"]) == (1, 1, 0)
    assert summary["coverage"] == 1.0
    assert (summary["materials"], summary["bomQty"], summary["requiredQty"],
            summary["deltaQty"]) == (5, 29.0, 26.0, -3.0)
    assert body["deviceFilter"] == "D1"


# ---------------------------------------------------------------- 过滤与分页


def test_only_diff_kind_filter_and_pagination(seeded):
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        default = get_variance(client, token).json()               # only_diff=1
        all_rows = get_variance(client, token, only_diff=0).json()
        same = get_variance(client, token, kind="SAME").json()     # kind 优先于 only_diff
        added = get_variance(client, token, kind="ADDED").json()
        page1 = get_variance(client, token, only_diff=0, page=1, page_size=2).json()
        page4 = get_variance(client, token, only_diff=0, page=4, page_size=2).json()
        over = get_variance(client, token, only_diff=0, page=9, page_size=2).json()

    assert default["total"] == 4, "默认 only_diff=1 应排除 3 个 SAME 行"
    assert {i["kind"] for i in default["items"]} == {
        "ADDED", "INCREASED", "DECREASED", "MISSING_IN_REQUIREMENT"}
    assert all_rows["total"] == 7
    assert same["total"] == 3 and {i["kind"] for i in same["items"]} == {"SAME"}
    assert added["total"] == 1 and added["items"][0]["deviceNo"] == "D2"

    assert (page1["page"], page1["pageSize"], page1["total"], page1["hasMore"]) == (1, 2, 7, True)
    assert len(page1["items"]) == 2
    assert (page4["page"], len(page4["items"]), page4["hasMore"]) == (4, 1, False)
    assert over["items"] == [] and over["hasMore"] is False
    # 分页不改变汇总口径
    assert page1["summary"] == all_rows["summary"]


# ---------------------------------------------------------------- 权限


def test_assembler_scope_and_forbidden(seeded):
    """装配工只能看本人参与机台；查非参与项目必须 403 PROJECT_FORBIDDEN。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-asm")
        own = get_variance(client, token, only_diff=0)
        forbidden = get_variance(client, token, OTHER_PROJECT)
        forbidden_export = get_export(client, token, OTHER_PROJECT)
        no_bom_forbidden = get_variance(client, token, NO_BOM_PROJECT)

    assert own.status_code == 200, own.text
    body = own.json()
    assert [d["deviceNo"] for d in body["devices"]] == ["D1"]
    assert body["summary"]["devices"] == 1

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PROJECT_FORBIDDEN"
    assert forbidden_export.status_code == 403
    assert no_bom_forbidden.status_code == 403


def test_operator_and_warehouse_roles(seeded):
    """仓库只读全量；OPERATOR 不在报表口径内（403 FORBIDDEN）。"""
    with TestClient(backend.app) as client:
        wh_token = login(client, "bv-wh")
        op_token = login(client, "bv-op")
        wh = get_variance(client, wh_token, only_diff=0)
        op = get_variance(client, op_token)
        missing_token = client.get(f"/api/v1/projects/{PROJECT}/bom-variance")

    assert wh.status_code == 200 and wh.json()["summary"]["devices"] == 3
    assert op.status_code == 403 and op.json()["error"]["code"] == "FORBIDDEN"
    assert missing_token.status_code == 401


# ---------------------------------------------------------------- 导出列位


def test_export_sheets_and_column_positions(seeded):
    """导出行数正确；数量必须落在对应列（小计行也不许左移一列）。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        export = get_export(client, token, only_diff=0)

    assert export.status_code == 200
    assert export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    summary_cells, names = sheet_cells(export.content, "机台汇总")
    assert names == ["机台汇总", "料号差异"]
    # 表头 11 列，顺序固定
    assert [summary_cells[(1, col)] for col in "ABCDEFGHIJK"] == [
        "机台号", "是否有工程BOM", "BOM版本", "BOM件数", "需求件数", "差异件数",
        "新增料号数", "增加数", "减少数", "一致数", "漏备料数"]
    # 第 2 行 = 项目合计：D..K 必须是 BOM件数/需求件数/差异件数/五类计数
    assert summary_cells[(2, "A")] == "项目合计（1 台无工程BOM未纳入）"
    assert summary_cells[(2, "D")] == "30.0"
    assert summary_cells[(2, "E")] == "30.0"
    assert summary_cells[(2, "F")] == "0.0"
    assert [summary_cells[(2, col)] for col in "GHIJK"] == ["1", "1", "1", "3", "1"]
    # D1 / D2 / D3 三行，顺序 = 机台顺序
    assert [summary_cells[(row, "A")] for row in (3, 4, 5)] == ["D1", "D2", "D3"]
    # D1 全类型：版本 1 / BOM 29.00 / 需求 26.00 / 差异 -3.00 / 新增 0 / 增加 1 / 减少 1 / 一致 2 / 漏备料 1
    assert [summary_cells[(3, col)] for col in ("B", "C", "D", "E", "F")] == [
        "是", "1", "29.0", "26.0", "-3.0"]
    assert [summary_cells[(3, col)] for col in ("G", "H", "I", "J", "K")] == [
        "0", "1", "1", "2", "1"]
    # D2 用的是最新 PUBLISHED 版本 2（不是 1、也不是 DRAFT 9）
    assert [summary_cells[(4, col)] for col in ("B", "C", "D", "E", "F")] == [
        "是", "2", "1.0", "4.0", "3.0"]
    assert [summary_cells[(4, col)] for col in ("G", "H", "I", "J", "K")] == [
        "1", "0", "0", "1", "0"]
    # 无 BOM 机台：B=否，BOM版本/BOM件数/差异件数留空，需求件数在 E 列
    assert [summary_cells.get((5, col)) for col in ("B", "C", "D", "F")] == [
        "否", None, None, None]
    assert summary_cells[(5, "E")] == "5.0"

    item_cells, _ = sheet_cells(export.content, "料号差异")
    assert [item_cells[(1, col)] for col in "ABCDEFGHI"] == [
        "机台号", "料号", "品名", "规格", "单位", "BOM量", "需求量", "差异量", "类型"]
    # 第一行 = D1/M-DEC（按机台号+料号排序）
    assert [item_cells[(2, col)] for col in "ABCDEFGHI"] == [
        "D1", "M-DEC", "品名-M-DEC", "规格", "件", "8.01", "8.0", "-0.01", "减少"]
    # 共 7 行明细，第 8 行是 D2/M-SAME
    assert item_cells[(8, "A")] == "D2" and item_cells[(8, "B")] == "M-SAME"
    assert item_cells.get((9, "A")) is None


def test_export_respects_filters(seeded):
    """导出与列表同过滤：kind=INCREASED 只出一行且仍是 9 列。"""
    with TestClient(backend.app) as client:
        token = login(client, "bv-admin")
        export = get_export(client, token, kind="INCREASED")

    cells, names = sheet_cells(export.content, "料号差异")
    assert names == ["机台汇总", "料号差异"]
    assert [cells[(2, col)] for col in "ABFGHI"] == [
        "D1", "M-INC", "11.99", "12.0", "0.01", "增加"]
    assert cells.get((3, "A")) is None


# ---------------------------------------------------------------- 管理台页面


def test_admin_page_shows_coverage_warning_and_link(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "bv-planner").status_code == 303
        page = client.get("/admin/projects/bom-variance", params={"project_no": PROJECT})
        all_rows = client.get("/admin/projects/bom-variance",
                              params={"project_no": PROJECT, "only_diff": "0"})
        no_bom = client.get("/admin/projects/bom-variance",
                            params={"project_no": NO_BOM_PROJECT})

    assert page.status_code == 200
    html = page.text
    assert "BOM 差异" in html and 'href="/admin/projects/bom-variance"' in html
    assert "1 台机台无工程 BOM，未纳入比对（先在 U9 同步 BOM）" in html
    assert "66.7%" in html, "覆盖率 = 2/3 必须出现在页面上"
    assert "BOM 差异" in client.get("/admin/projects/kitting").text  # 侧栏并列入口

    # 默认只显示差异行（3 行 SAME 不在表里），only_diff=0 时一致行出现
    assert html.count("<td>M-SAME</td>") == 0
    assert all_rows.text.count("<td>M-SAME</td>") == 2
    # 无 BOM 机台显示「无」且差异量为 —，不是 0.00
    assert '<span class="badge b-warn" title="无工程 BOM，无法比对">无</span>' in html
    assert "该项目没有可参与比对的机台（无工程 BOM）" in no_bom.text


def test_admin_page_role_guard(seeded):
    with TestClient(backend.app) as client:
        assert web_login(client, "bv-wh").status_code == 303
        assert client.get("/admin/projects/bom-variance").status_code == 200
        assert client.get("/admin/projects/bom-variance/export").status_code == 400
    with TestClient(backend.app) as client:
        assert web_login(client, "bv-op").status_code == 303
        denied = client.get("/admin/projects/bom-variance")
        assert denied.status_code == 403


def test_admin_page_search_does_not_403_warehouse(seeded):
    """仓库搜索项目必须给候选项（不能调带 _pmr_authorize 的 API 函数吃 403 JSON）。"""
    with TestClient(backend.app) as client:
        assert web_login(client, "bv-wh").status_code == 303
        page = client.get("/admin/projects/bom-variance", params={"q": "26B-301"})

    assert page.status_code == 200
    assert "匹配到的项目" in page.text
    assert 'project_no=JSBY-26B-301' in page.text
    assert "无权查看项目物料需求" not in page.text
