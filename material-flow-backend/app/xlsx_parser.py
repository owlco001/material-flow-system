from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import IO, Iterable, Iterator


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


# 解压后单个 XML 部件的上限：防 zip 炸弹。ItemMaster 3.7 万行约 90 MB，留足余量。
MAX_PART_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


class XlsxSheetReader:
    @staticmethod
    def read_rows(file_bytes: bytes, sheet_name: str | None = None, max_rows: int | None = None) -> list[XlsxRow]:
        return list(XlsxSheetReader.iter_rows(file_bytes, sheet_name=sheet_name, max_rows=max_rows))

    @staticmethod
    def iter_rows(
        source: bytes | str | "os.PathLike[str]" | IO[bytes],
        sheet_name: str | None = None,
        max_rows: int | None = None,
    ) -> Iterator[XlsxRow]:
        """流式逐行读取工作表。

        旧实现用 ``ET.fromstring`` 把整张 sheet XML 读进内存，3.7 万行 × 70 列的
        物料主档（sheet1.xml ≈ 90 MB）会吃掉 1 GB 以上内存；这里改为 iterparse，
        每处理完一行就释放节点，内存只与共享字符串表大小相关。
        ``source`` 可以是 bytes、文件路径或已打开的二进制文件对象。
        """
        if max_rows is not None and max_rows < 0:
            raise ValueError("max_rows must be non-negative")
        try:
            handle = _BytesReader(source) if isinstance(source, (bytes, bytearray)) else source
            with zipfile.ZipFile(handle) as workbook:
                _guard_part_sizes(workbook)
                shared_strings = _read_shared_strings(workbook)
                sheet_path = _select_sheet_path(workbook, sheet_name)
                yield from _iter_sheet_rows(workbook, sheet_path, shared_strings, max_rows)
        except zipfile.BadZipFile as exc:
            raise XlsxParseError("invalid .xlsx zip archive") from exc
        except ET.ParseError as exc:
            raise XlsxParseError("invalid .xlsx XML") from exc
        except KeyError as exc:
            raise XlsxParseError(f"missing .xlsx part: {exc.args[0]}") from exc

    @staticmethod
    def sheet_names(source: bytes | str | "os.PathLike[str]" | IO[bytes]) -> list[str]:
        try:
            handle = _BytesReader(source) if isinstance(source, (bytes, bytearray)) else source
            with zipfile.ZipFile(handle) as workbook:
                root = ET.fromstring(workbook.read("xl/workbook.xml"))
                return [s.attrib.get("name", "") for s in root.findall("s:sheets/s:sheet", NS)]
        except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
            raise XlsxParseError("invalid .xlsx file") from exc


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


def _guard_part_sizes(workbook: zipfile.ZipFile) -> None:
    for info in workbook.infolist():
        if info.file_size > MAX_PART_UNCOMPRESSED_BYTES:
            raise XlsxParseError(f"xlsx part too large: {info.filename}")


_SI = f"{{{SHEET_NS}}}si"
_T = f"{{{SHEET_NS}}}t"
_RPH = f"{{{SHEET_NS}}}rPh"
_ROW = f"{{{SHEET_NS}}}row"
_C = f"{{{SHEET_NS}}}c"
_V = f"{{{SHEET_NS}}}v"
_IS = f"{{{SHEET_NS}}}is"


def _si_text(item: ET.Element) -> str:
    # 跳过 <rPh>（日文注音）里的 <t>，只取正文 run。
    parts: list[str] = []
    for child in item:
        if child.tag == _T:
            parts.append(child.text or "")
        elif child.tag != _RPH:
            parts.extend(t.text or "" for t in child.iter(_T))
    return "".join(parts)


def _read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    strings: list[str] = []
    with workbook.open("xl/sharedStrings.xml") as fh:
        for _event, element in ET.iterparse(fh, events=("end",)):
            if element.tag == _SI:
                strings.append(_si_text(element))
                element.clear()
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


def _iter_sheet_rows(
    workbook: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
    max_rows: int | None,
) -> Iterator[XlsxRow]:
    count = 0
    with workbook.open(sheet_path) as fh:
        for _event, row_element in ET.iterparse(fh, events=("end",)):
            if row_element.tag != _ROW:
                continue
            if max_rows is not None and count >= max_rows:
                raise XlsxMaxRowsExceededError(f"sheet row count exceeds max_rows={max_rows}")
            count += 1
            row_index = int(row_element.attrib.get("r") or count)
            values_by_column: dict[int, str] = {}
            max_column = -1
            for cell in row_element.iter(_C):
                ref = cell.attrib.get("r", "")
                column = _column_index(ref) if ref else max_column + 1
                values_by_column[column] = _cell_text(cell, shared_strings)
                max_column = max(max_column, column)
            row_element.clear()
            values = tuple(values_by_column.get(column, "") for column in range(max_column + 1))
            yield XlsxRow(index=row_index, values=values)


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = cell.find(_IS)
        return "" if inline is None else "".join(text.text or "" for text in inline.iter(_T))
    value = cell.find(_V)
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
