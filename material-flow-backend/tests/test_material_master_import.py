"""物料主档（U9 ItemMaster.xlsx）导入：结构识别 → 预览比对 → 确认写入 → 增量。"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from xml.sax.saxutils import escape

from fastapi.testclient import TestClient

from app import main as backend
from app import material_master as mm
from tests.test_admin_web_nav import seed, web_login

# 与真实导出一致的 70 列表头（顺序相同）
HEADERS = [
    "主分类.分类编码", "主分类", "财务分类.分类编码", "成本分类", "成本分类.分类编码", "财务分类.分类名称", "料号",
    "参考料号1(U9图号)", "品名", "规格", "描述(品牌)", "实体扩展字段.全局段6(加工件分类)", "附件", "T6料号",
    "参考料号2(T6图号)", "库存单位.编码", "库存单位.名称", "重量单位.编码", "重量单位.名称", "库存单位重量", "设备编号",
    "项目号", "形态属性", "存储地点", "仓库业务员.编码", "仓库业务员.名称", "采购员.编码", "采购员.名称",
    "料品采购相关信息.最小叫货量", "采购预处理提前期(天)", "累计制造提前期(天)", "生产部门.编码", "生产部门.名称",
    "进货质检方案", "完工质检方案", "收货程序", "安全库存量", "库存上限", "生产分类.分类编码", "生产分类.分类名称",
    "库存主单位", "库存分类", "修改人", "修改时间", "创建时间", "创建人", "采购分类", "财务分类", "料品形态", "控制组织",
    "是否版本数量控制", "是否成分控制", "是否等级控制", "专用料", "段2(流水号)", "段1(编号分类)", "MRP分类",
    "料品库存相关信息", "料品生产相关信息.是否进行工程变更版本控制", "料品销售相关信息.可用量检查",
    "料品销售相关信息.可用量规则", "有效性.失效日期", "实体扩展字段.全局段4(打单分类)", "可销售", "可委外", "可生产",
    "可库存交易", "可采购", "参考成本", "最新成本",
]
assert len(HEADERS) == 70


def _row(code, name="轴承", spec="6205", unit="个", form="采购件", expiry="2958465", device="", cost="1.5", **kw):
    r = {h: "" for h in HEADERS}
    r.update({
        "主分类.分类编码": "30101001", "主分类": "轴承类", "财务分类.分类编码": "CW01", "财务分类.分类名称": "原材料",
        "料号": code, "品名": name, "规格": spec, "库存单位.编码": "EA", "库存单位.名称": unit, "库存主单位": unit,
        "附件": "0", "库存单位重量": "0", "形态属性": form, "存储地点": "外购件库", "设备编号": device,
        "修改时间": "2026.10.09 16:06:11", "创建时间": "2024.02.23 21:47:54", "修改人": "刘霞", "创建人": "刘霞",
        "段2(流水号)": "1", "段1(编号分类)": "轴承", "料品库存相关信息": "100" + code[-5:], "有效性.失效日期": expiry,
        "可采购": "√", "参考成本": cost, "最新成本": "0", "采购预处理提前期(天)": "3",
    })
    r.update(kw)
    return [r[h] for h in HEADERS]


def _col(i):
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def _xlsx(rows, title=True):
    lines = []
    all_rows = ([["物料主档"]] if title else []) + [HEADERS] + rows
    for ri, vals in enumerate(all_rows, 1):
        cells = "".join(
            f'<c r="{_col(ci)}{ri}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
            for ci, v in enumerate(vals) if v != ""
        )
        lines.append(f'<row r="{ri}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main"><sheetData>' + "".join(lines) + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="物料主档" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def _token(client, username="owlco", password="Admin@2026"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password, "deviceId": username})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def _preview(client, h, data, name="ItemMaster.xlsx"):
    return client.post("/api/v1/material-master/import/preview", headers=h,
                       files={"file": (name, io.BytesIO(data), "application/octet-stream")})


def _commit(client, h, preview_id, skip=False, op=None):
    op = op or str(uuid.uuid4())
    return client.post("/api/v1/material-master/import/commit", headers={**h, "Idempotency-Key": op},
                       json={"previewId": preview_id, "clientOperationId": op, "skipInvalid": skip})


def _seed_existing_material():
    c = backend.db()
    c.execute("INSERT OR REPLACE INTO materials(id,code,name,specification,unit,batch_no,expiry_date,total_quantity,"
              "available_quantity,version,category) VALUES('m_old','30101001-00001','旧名','旧规格','件','B1','2027-01-01',50,40,1,'手工分类')")
    c.commit()
    c.close()


def test_value_parsers():
    assert mm._parse_cell("serial_date", "2958465") == mm.FOREVER
    assert mm._parse_cell("serial_date", "46283") == "2026-09-18"
    assert mm._parse_cell("dot_datetime", "2026.10.09 16:06:11") == "2026-10-09 16:06:11"
    assert mm._parse_cell("flag", "√") == 1
    assert mm._parse_cell("text", "  ") is None
    assert mm._parse_cell("num", "1317.25715") == 1317.25715
    assert mm._parse_cell("num", "0") == 0


def test_preview_commit_keeps_quantities_and_is_incremental():
    seed()
    _seed_existing_material()
    rows = [
        _row("30101001-00001", name="深沟球轴承", unit="个"),
        _row("30101001-00002", name="滚珠丝杠", form="制造件", device="Z99", expiry="46283"),
        _row("30101001-00003", name="", unit="个"),            # 缺品名
        _row("30101001-00002", name="重复"),                    # 重复料号
    ]
    with TestClient(backend.app) as client:
        h = _token(client)
        r = _preview(client, h, _xlsx(rows))
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["headerRow"] == 2 and p["sheetName"] == "物料主档"
        assert p["preview"]["totalRows"] == 4
        assert p["preview"]["validRows"] == 2 and p["preview"]["invalidRows"] == 2
        assert p["preview"]["newRows"] == 2
        assert {e["code"] for e in p["errors"]} == {"REQUIRED", "DUPLICATE"}
        assert p["summary"]["recognizedColumns"] == len(mm.COLUMNS)
        assert "库存主单位" in p["summary"]["extraHeaders"]

        r = _commit(client, h, p["previewId"])
        assert r.status_code == 422, r.text   # 有错误行，未勾选跳过
        op = str(uuid.uuid4())
        r = _commit(client, h, p["previewId"], skip=True, op=op)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["masterInserted"] == 2 and res["materialsCreated"] == 1 and res["materialsUpdated"] == 1
        assert _commit(client, h, p["previewId"], skip=True, op=op).json()["idempotent"] is True

        c = backend.db()
        m = c.execute("SELECT * FROM materials WHERE code='30101001-00001'").fetchone()
        assert (m["name"], m["unit"], m["total_quantity"], m["available_quantity"], m["batch_no"], m["category"]) == \
            ("深沟球轴承", "个", 50, 40, "B1", "手工分类")
        m2 = c.execute("SELECT * FROM materials WHERE code='30101001-00002'").fetchone()
        assert m2["category"] == "轴承类" and m2["total_quantity"] == 0
        mrow = c.execute("SELECT * FROM material_master WHERE code='30101001-00002'").fetchone()
        assert mrow["device_no"] == "Z99" and mrow["effective_until"] == "2026-09-18"
        assert mrow["u9_modified_at"] == "2026-10-09 16:06:11" and mrow["can_purchase"] == 1
        c.close()

        # 同一文件再导入：全部未变化；改一行规格：1 条变更
        rows2 = [_row("30101001-00001", name="深沟球轴承", unit="个"),
                 _row("30101001-00002", name="滚珠丝杠", form="制造件", device="Z99", expiry="46283", spec="SFU1605")]
        p2 = _preview(client, h, _xlsx(rows2)).json()
        assert p2["preview"]["unchangedRows"] == 1 and p2["preview"]["changedRows"] == 1
        assert p2["changes"][0]["fields"] == [{"field": "规格", "before": "6205", "after": "SFU1605"}]
        assert p2["summary"]["fieldChangeCounts"] == {"规格": 1}
        assert _commit(client, h, p2["previewId"]).json()["masterUpdated"] == 1

        # 查询接口：成本字段只给 ADMIN
        d = client.get("/api/v1/materials/30101001-00002/master", headers=h).json()
        assert d["specification"] == "SFU1605" and d["refCost"] == 1.5 and d["effective"] in (True, False)
        s = client.get("/api/v1/material-master", headers=h, params={"q": "Z99"}).json()
        assert s["total"] == 1


def test_template_and_role_errors():
    seed()
    with TestClient(backend.app) as client:
        h = _token(client)
        bad = _xlsx([["x"]], title=False).replace(b"\xe6\x96\x99\xe5\x8f\xb7", b"code")  # 去掉「料号」表头
        r = _preview(client, h, bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "MATERIAL_MASTER_TEMPLATE_INVALID"
        r = _preview(client, h, b"not a zip")
        assert r.status_code == 422
        r = _preview(client, h, _xlsx([_row("A-1")]), name="x.csv")
        assert r.status_code == 422
        op = _token(client, "operator1", "Op@2026xx")
        assert _preview(client, op, _xlsx([_row("A-1")])).status_code == 403


def test_admin_web_upload_and_commit():
    seed()
    with TestClient(backend.app) as client:
        assert web_login(client, "owlco", "Admin@2026").status_code == 303
        page = client.get("/admin/material-master")
        assert page.status_code == 200 and "物料主档" in page.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        r = client.post("/admin/material-master/import", data={"csrf_token": csrf},
                        files={"file": ("ItemMaster.xlsx", io.BytesIO(_xlsx([_row("B-00001"), _row("B-00002")])),
                                        "application/octet-stream")}, follow_redirects=False)
        assert r.status_code == 303, r.text
        loc = r.headers["location"]
        assert loc.startswith("/admin/material-master/import/mmb_")
        prev = client.get(loc)
        assert prev.status_code == 200 and "确认导入" in prev.text
        op = re.search(r'name="op_id" value="([^"]+)"', prev.text).group(1)
        r = client.post(loc + "/commit", data={"csrf_token": csrf, "op_id": op}, follow_redirects=False)
        assert r.status_code == 303
        done = client.get(r.headers["location"])
        assert "导入结果" in done.text and "主档新增 2" in done.text
        lst = client.get("/admin/material-master?q=B-00002")
        assert "B-00002" in lst.text and "已导入" in lst.text
