"""极小 XLSX 写出器（仅用标准库 zipfile + XML）。

仓库运行时不带 openpyxl / xlsxwriter（requirements.txt 只读解析），但管理台需要
xlsx 导出。这里生成最小可用的 SpreadsheetML 包：单 sheet 或多 sheet、内联字符串
（inlineStr，不需要 sharedStrings）、数字单元格保留数值类型、表头加粗。

只支持本仓库导出场景需要的特性，不做公式、合并单元格。
读回验证见 tests/test_project_material_requirements.py（用 app.xlsx_parser 反向解析）。
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape

# XML 1.0 不允许的字符（除 \t \n \r 外的 C0 控制符）：留在文本里会让 sheet1.xml
# 非良构，Excel 打开报“文件已损坏”。Excel 复制粘贴的品名/规格里常见 \x0b。
_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(value: str) -> str:
    return _ILLEGAL_XML_CHARS.sub("", value)


def _text(value: str) -> str:
    """转义并剔除 XML 1.0 非法字符，供所有 <t> 文本节点使用。"""
    return escape(_clean(value))


_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _content_types(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '<Default Extension="xml" ContentType="application/xml"/>\n'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
        f"{overrides}\n"
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>\n'
        "</Types>"
    )


def _workbook_rels(sheet_count: int) -> str:
    """worksheet 用 rId1..rIdN，样式放最后一个 rId（单 sheet 时仍是 rId2，与 0.23.0 一致）。"""
    rels = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        f"{rels}\n"
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>\n'
        "</Relationships>"
    )

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA。"""
    if index < 0:
        raise ValueError("column index must be non-negative")
    letters = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _sheet_name(name: str) -> str:
    # Excel 限制：31 字符、不含 : \ / ? * [ ]
    cleaned = "".join(ch for ch in _clean(str(name or "Sheet1")) if ch not in ':\\/?*[]').strip()
    return (cleaned or "Sheet1")[:31]


def _cell(ref: str, value: Any, style: int) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{_text(str(value))}</t></is></c>'
    if isinstance(value, (int, float)):
        number = repr(value) if isinstance(value, float) else str(value)
        return f'<c r="{ref}"{style_attr}><v>{number}</v></c>'
    text = value.isoformat(sep=" ") if isinstance(value, datetime) else str(value)
    # 保留换行；xml:space 防止 Excel 吃掉首尾空格
    return (f'<c r="{ref}"{style_attr} t="inlineStr"><is>'
            f'<t xml:space="preserve">{_text(text)}</t></is></c>')


def _row_xml(
    row_index: int,
    values: Sequence[Any],
    *,
    style: int = 0,
    height: float | None = None,
) -> str:
    cells = "".join(
        _cell(f"{column_letter(col)}{row_index}", value, style)
        for col, value in enumerate(values)
    )
    height_attr = f' ht="{height}" customHeight="1"' if height else ""
    return f'<row r="{row_index}"{height_attr}>{cells}</row>'


def _sheet_xml(
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    column_widths: Sequence[float] | None = None,
    bold_rows: Iterable[int] | None = None,
) -> tuple[str, int]:
    """生成一张工作表的 XML，返回 (xml, 最后一行行号)。"""
    bold = set(bold_rows or ())
    parts: list[str] = []
    row_no = 1
    parts.append(_row_xml(row_no, list(header), style=1, height=20))
    row_no += 1
    for row_index, values in enumerate(rows):
        parts.append(_row_xml(row_no, list(values), style=1 if row_index in bold else 0))
        row_no += 1
    last_col = column_letter(max(0, len(header) - 1))
    cols = ""
    if column_widths:
        cols = "<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{width}" customWidth="1"/>'
            for i, width in enumerate(column_widths)
        ) + "</cols>"
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_col}{max(1, row_no - 1)}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"{cols}<sheetData>{''.join(parts)}</sheetData></worksheet>"
    )
    return sheet, max(1, row_no - 1)


def _unique_sheet_names(names: Sequence[str]) -> list[str]:
    """Excel 要求 sheet 名在当前工作簿内唯一（大小写不敏感）。"""
    used: set[str] = set()
    result: list[str] = []
    for index, raw in enumerate(names, 1):
        base = _sheet_name(raw or f"Sheet{index}")
        candidate = base
        suffix = 2
        while candidate.lower() in used:
            tail = f"({suffix})"
            candidate = (base[: 31 - len(tail)] + tail) if len(base) + len(tail) > 31 else base + tail
            suffix += 1
        used.add(candidate.lower())
        result.append(candidate)
    return result


def build_xlsx_workbook(sheets: Sequence[dict[str, Any]]) -> bytes:
    """生成多 sheet 的 .xlsx 字节串（仅标准库）。

    每个 sheet 是 dict：``header`` / ``rows`` / 可选 ``name`` / ``column_widths`` /
    ``bold_rows``（数据行下标，0 开始，用于合计行加粗）。
    """
    if not sheets:
        raise ValueError("at least one sheet is required")
    names = _unique_sheet_names([str(s.get("name") or s.get("title") or "") for s in sheets])
    sheet_parts: list[str] = []
    sheet_entries: list[str] = []
    for index, (sheet, name) in enumerate(zip(sheets, names), 1):
        xml, _last_row = _sheet_xml(
            sheet["header"],
            sheet.get("rows") or [],
            column_widths=sheet.get("column_widths"),
            bold_rows=sheet.get("bold_rows"),
        )
        sheet_parts.append(xml)
        sheet_entries.append(
            f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{index}" r:id="rId{index}"/>'
        )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_entries)}</sheets></workbook>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheets)))
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        archive.writestr("xl/styles.xml", _STYLES)
        for index, xml in enumerate(sheet_parts, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
    return buffer.getvalue()


def build_xlsx(
    sheet_title: str,
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    column_widths: Sequence[float] | None = None,
    bold_rows: Iterable[int] | None = None,
    sheet_name: str | None = None,
) -> bytes:
    """生成单 sheet 的 .xlsx 字节串（多 sheet 见 :func:`build_xlsx_workbook`）。

    ``rows`` 为数据行（不含表头）。``bold_rows`` 是数据行下标（0 开始），用于
    合计行加粗。``column_widths`` 为 Excel 列宽（字符数）。
    """
    return build_xlsx_workbook([{
        "name": sheet_name or sheet_title,
        "header": header,
        "rows": rows,
        "column_widths": column_widths,
        "bold_rows": bold_rows,
    }])
