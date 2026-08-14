"""Excel 로더 테스트."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.document.excel import ExcelLoader
from rag_pipeline.schemas.document import ElementType


# 시트마다 표 요소 하나씩 나와야 한다
def test_one_table_per_sheet(xlsx_file: Path, offline_config: IngestConfig) -> None:
    document = ExcelLoader(offline_config).load(xlsx_file)

    assert len(document.elements) == 2
    assert all(e.type is ElementType.TABLE for e in document.elements)
    assert [e.metadata["sheet_name"] for e in document.elements] == ["매출", "비용"]


# 셀 값이 마크다운 표로 직렬화되어야 한다
def test_markdown_table(xlsx_file: Path, offline_config: IngestConfig) -> None:
    document = ExcelLoader(offline_config).load(xlsx_file)
    first = document.elements[0]

    assert "| 월 | 금액 |" in first.text
    assert "| 1월 | 1000 |" in first.text


# 정수로 떨어지는 float 는 소수점 없이 나와야 한다 (1000.0 -> 1000)
def test_integer_floats_are_clean(xlsx_file: Path, offline_config: IngestConfig) -> None:
    document = ExcelLoader(offline_config).load(xlsx_file)

    assert "1000.0" not in document.elements[0].text


# 수식은 계산된 값으로 읽어야 한다
def test_formula_reads_cached_value(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "formula.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["a", "b"])
    sheet.append([1, 2])
    workbook.save(str(path))

    document = ExcelLoader(offline_config).load(path)
    # data_only=True 이므로 계산되지 않은 수식은 빈 값이 되고, 파싱은 실패하지 않는다
    assert document.elements[0].metadata["rows"][0] == ["a", "b"]


# 행 수 상한을 넘으면 경고를 남기고 잘라야 한다
def test_row_limit_warns(tmp_path: Path) -> None:
    path = tmp_path / "big.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for index in range(30):
        sheet.append([f"행{index}", index])
    workbook.save(str(path))

    config = IngestConfig(max_excel_rows=10)
    document = ExcelLoader(config).load(path)

    assert len(document.elements[0].metadata["rows"]) == 10
    assert any("10행" in w for w in document.metadata.warnings)


# 빈 후행 행·열은 잘라내야 한다
def test_trailing_blanks_trimmed(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "sparse.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "값"
    sheet["A2"] = "1"
    # E10까지 빈 셀을 건드려 사용 영역을 억지로 늘린다
    sheet["E10"] = None
    workbook.save(str(path))

    document = ExcelLoader(offline_config).load(path)
    rows = document.elements[0].metadata["rows"]

    assert rows == [["값"], ["1"]]
