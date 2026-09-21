from __future__ import annotations

from io import BytesIO
import zipfile

import pytest

from app.xlsx_parser import (
    HeaderDetectService,
    HeaderMissingFieldsError,
    XlsxMaxRowsExceededError,
    XlsxRow,
    XlsxSheetReader,
)


def make_xlsx_bytes(rows: list[list[str]]) -> bytes:
    def cell_ref(row_index: int, column_index: int) -> str:
        column = ""
        value = column_index + 1
        while value:
            value, remainder = divmod(value - 1, 26)
            column = chr(ord("A") + remainder) + column
        return f"{column}{row_index}"

    def inline_cell(row_index: int, column_index: int, value: str) -> str:
        escaped = (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        return (
            f'<c r="{cell_ref(row_index, column_index)}" t="inlineStr">'
            f"<is><t>{escaped}</t></is>"
            "</c>"
        )

    sheet_rows = []
    for row_index, row_values in enumerate(rows, start=1):
        cells = "".join(
            inline_cell(row_index, column_index, value)
            for column_index, value in enumerate(row_values)
        )
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')

    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>""",
    }

    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return stream.getvalue()


def read_sample_rows(rows: list[list[str]]):
    return XlsxSheetReader.read_rows(make_xlsx_bytes(rows))


def test_detects_bom_header_on_first_row_and_key_fields():
    rows = read_sample_rows([
        ["母件料号", "料品编码", "料品名称", "单位名称", "实际用量"],
        ["BOM-ROOT", "69926012-00001", "测试料品", "PCS", "2"],
    ])

    result = HeaderDetectService.detect_header(rows, ["母件料号", "料品编码", "料品名称", "单位名称", "实际用量"])

    assert result.header_row_index == 1
    assert result.headers[result.column_map["母件料号"]] == "母件料号"
    assert result.headers[result.column_map["料品编码"]] == "料品编码"
    assert rows[1].values[result.column_map["料品编码"]] == "69926012-00001"


def test_detects_stock_header_on_third_row_and_key_fields():
    rows = read_sample_rows([
        ["库存汇总"],
        ["导出时间", "2026-09-21"],
        ["存储地点名称", "料号", "品名", "库存单位名称", "现存量(库存单位)"],
        ["成品仓", "30102001-00021", "测试库存料", "PCS", "10"],
    ])

    result = HeaderDetectService.detect_header(rows, ["存储地点名称", "料号", "品名", "库存单位名称", "现存量(库存单位)"])

    assert result.header_row_index == 3
    assert result.headers[result.column_map["料号"]] == "料号"
    assert result.headers[result.column_map["现存量(库存单位)"]] == "现存量(库存单位)"
    assert rows[3].values[result.column_map["料号"]] == "30102001-00021"


def test_detects_mo_header_on_second_row_and_key_fields():
    rows = read_sample_rows([
        ["生产订单列表"],
        ["单据编号", "料品料号", "料品品名", "生产数量", "博阳项目"],
        ["MO-2609170007", "ITEM-001", "测试生产料", "5", "项目A"],
    ])

    result = HeaderDetectService.detect_header(rows, ["单据编号", "料品料号", "料品品名", "生产数量", "博阳项目"])

    assert result.header_row_index == 2
    assert result.headers[result.column_map["单据编号"]] == "单据编号"
    assert result.headers[result.column_map["生产数量"]] == "生产数量"
    assert rows[2].values[result.column_map["单据编号"]] == "MO-2609170007"


def test_missing_required_header_fields_raises_clear_error():
    rows = [XlsxRow(index=1, values=("料号", "品名"))]

    with pytest.raises(HeaderMissingFieldsError) as exc_info:
        HeaderDetectService.detect_header(rows, ["料号", "现存量(库存单位)"])

    assert exc_info.value.missing_fields == ("现存量(库存单位)",)
    assert "missing required header fields" in str(exc_info.value)


def test_read_rows_max_rows_raises_clear_error_when_exceeded():
    with pytest.raises(XlsxMaxRowsExceededError) as exc_info:
        XlsxSheetReader.read_rows(
            make_xlsx_bytes([
                ["母件料号", "料品编码", "料品名称", "单位名称", "实际用量"],
                ["BOM-ROOT", "69926012-00001", "测试料品", "PCS", "2"],
            ]),
            max_rows=1,
        )

    assert "max_rows=1" in str(exc_info.value)
