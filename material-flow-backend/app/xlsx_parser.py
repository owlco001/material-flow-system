from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Iterable


SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"s": SHEET_NS, "r": REL_NS, "pr": PKG_REL_NS}


class XlsxParseError(ValueError):
    """Raised when an .xlsx workbook cannot be read safely."""


class XlsxMaxRowsExceededError(XlsxParseError):
    """Raised when a sheet contains more rows than the configured limit."""


class HeaderMissingFieldsError(ValueError):
    """Raised when no row contains all required header fields."""

    def __init__(self, missing_fields: Iterable[str]) -> None:
        self.missing_fields = tuple(missing_fields)
        super().__init__(f"missing required header fields: {', '.join(self.missing_fields)}")


@dataclass(frozen=True)
class XlsxRow:
    index: int
    values: tuple[str, ...]


@dataclass(frozen=True)
class HeaderDetectResult:
    header_row_index: int
    headers: tuple[str, ...]
    column_map: dict[str, int]


class XlsxSheetReader:
    @staticmethod
    def read_rows(file_bytes: bytes, sheet_name: str | None = None, max_rows: int | None = None) -> list[XlsxRow]:
        if max_rows is not None and max_rows < 0:
            raise ValueError("max_rows must be non-negative")
        try:
            with zipfile.ZipFile(_BytesReader(file_bytes)) as workbook:
                shared_strings = _read_shared_strings(workbook)
                sheet_path = _select_sheet_path(workbook, sheet_name)
                return _read_sheet_rows(workbook, sheet_path, shared_strings, max_rows)
        except zipfile.BadZipFile as exc:
            raise XlsxParseError("invalid .xlsx zip archive") from exc
        except ET.ParseError as exc:
            raise XlsxParseError("invalid .xlsx XML") from exc
        except KeyError as exc:
            raise XlsxParseError(f"missing .xlsx part: {exc.args[0]}") from exc


class HeaderDetectService:
    @staticmethod
    def detect_header(rows: Iterable[XlsxRow], required_fields: Iterable[str]) -> HeaderDetectResult:
        required = tuple(_normalize_header(field) for field in required_fields)
        if not required:
            raise ValueError("required_fields must not be empty")
        if any(not field for field in required):
            raise ValueError("required_fields must not contain blank values")

        best_missing = set(required)
        for row in rows:
            headers = tuple(_normalize_header(value) for value in row.values)
            column_map: dict[str, int] = {}
            for index, header in enumerate(headers):
                if header and header not in column_map:
                    column_map[header] = index
            missing = [field for field in required if field not in column_map]
            if not missing:
                return HeaderDetectResult(
                    header_row_index=row.index,
                    headers=headers,
                    column_map={field: column_map[field] for field in required},
                )
            if len(missing) < len(best_missing):
                best_missing = set(missing)
        raise HeaderMissingFieldsError(sorted(best_missing))


def _read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("s:si", NS):
        strings.append("".join(text.text or "" for text in item.findall(".//s:t", NS)))
    return strings


def _select_sheet_path(workbook: zipfile.ZipFile, sheet_name: str | None) -> str:
    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    rel_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rel_root}
    sheets = workbook_root.findall("s:sheets/s:sheet", NS)
    if not sheets:
        raise XlsxParseError("workbook contains no sheets")
    selected = None
    for sheet in sheets:
        if sheet_name is None or sheet.attrib.get("name") == sheet_name:
            selected = sheet
            break
    if selected is None:
        raise XlsxParseError(f"sheet not found: {sheet_name}")
    rel_id = selected.attrib[f"{{{REL_NS}}}id"]
    target = rel_targets[rel_id]
    if target.startswith("/"):
        path = target.lstrip("/")
    else:
        path = posixpath.normpath(posixpath.join("xl", target))
    return str(PurePosixPath(path))


def _read_sheet_rows(
    workbook: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
    max_rows: int | None,
) -> list[XlsxRow]:
    root = ET.fromstring(workbook.read(sheet_path))
    rows: list[XlsxRow] = []
    for row_element in root.findall("s:sheetData/s:row", NS):
        if max_rows is not None and len(rows) >= max_rows:
            raise XlsxMaxRowsExceededError(f"sheet row count exceeds max_rows={max_rows}")
        row_index = int(row_element.attrib.get("r") or len(rows) + 1)
        values_by_column: dict[int, str] = {}
        max_column = -1
        for cell in row_element.findall("s:c", NS):
            ref = cell.attrib.get("r", "")
            column = _column_index(ref) if ref else max_column + 1
            values_by_column[column] = _cell_text(cell, shared_strings)
            max_column = max(max_column, column)
        values = tuple(values_by_column.get(column, "") for column in range(max_column + 1))
        rows.append(XlsxRow(index=row_index, values=values))
    return rows


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find("s:is", NS)
        return "" if inline is None else "".join(text.text or "" for text in inline.findall(".//s:t", NS))
    value = cell.find("s:v", NS)
    raw = "" if value is None or value.text is None else value.text
    if cell_type == "s" and raw:
        try:
            return shared_strings[int(raw)]
        except (IndexError, ValueError) as exc:
            raise XlsxParseError("invalid shared string reference") from exc
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE" if raw == "0" else raw
    return raw


def _column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", cell_reference)
    if not match:
        return 0
    value = 0
    for char in match.group(1).upper():
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def _normalize_header(value: object) -> str:
    return "".join(str(value or "").replace("\u3000", " ").split()).strip()


class _BytesReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._offset = offset
        elif whence == 1:
            self._offset += offset
        elif whence == 2:
            self._offset = len(self._data) + offset
        else:
            raise ValueError("invalid whence")
        return self._offset

    def tell(self) -> int:
        return self._offset

    def seekable(self) -> bool:
        return True
