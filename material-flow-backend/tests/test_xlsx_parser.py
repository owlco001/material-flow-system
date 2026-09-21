from pathlib import Path

import pytest

from app.xlsx_parser import (
    HeaderDetectService,
    HeaderMissingFieldsError,
    XlsxMaxRowsExceededError,
    XlsxRow,
    XlsxSheetReader,
)


SAMPLE_DIR = Path("/root/.hermes/profiles/ai_arch/cache/documents")
BOM_FILE = SAMPLE_DIR / "doc_ba5f84d68162_bom-26b-012.xlsx"
STOCK_FILE = SAMPLE_DIR / "doc_85e53a2dba97_库存数量_(19).xlsx"
MO_FILE = SAMPLE_DIR / "doc_e6a94206891c_MO20260921.xlsx"


def read_sample_rows(path: Path):
    return XlsxSheetReader.read_rows(path.read_bytes())


def test_detects_real_bom_header_on_first_row_and_key_fields():
    rows = read_sample_rows(BOM_FILE)

    result = HeaderDetectService.detect_header(rows, ["母件料号", "料品编码", "料品名称", "单位名称", "实际用量"])

    assert result.header_row_index == 1
    assert result.headers[result.column_map["母件料号"]] == "母件料号"
    assert result.headers[result.column_map["料品编码"]] == "料品编码"
    assert rows[1].values[result.column_map["料品编码"]] == "69926012-00001"


def test_detects_real_stock_header_on_third_row_and_key_fields():
    rows = read_sample_rows(STOCK_FILE)

    result = HeaderDetectService.detect_header(rows, ["存储地点名称", "料号", "品名", "库存单位名称", "现存量(库存单位)"])

    assert result.header_row_index == 3
    assert result.headers[result.column_map["料号"]] == "料号"
    assert result.headers[result.column_map["现存量(库存单位)"]] == "现存量(库存单位)"
    assert rows[3].values[result.column_map["料号"]] == "30102001-00021"


def test_detects_real_mo_header_on_second_row_and_key_fields():
    rows = read_sample_rows(MO_FILE)

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
        XlsxSheetReader.read_rows(BOM_FILE.read_bytes(), max_rows=1)

    assert "max_rows=1" in str(exc_info.value)
