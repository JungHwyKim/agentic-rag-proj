"""Excel 로더 (.xlsx / .xls).

시트 자체가 표이므로 시트당 표 리전 하나를 만든다. 수식은 계산된 값으로 읽고,
거대한 시트가 메모리를 잡아먹지 않도록 행 수에 상한을 둔다.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import openpyxl
import xlrd

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.schemas.document import FileFormat


# 셀 값을 사람이 읽는 문자열로 바꾼다. 날짜는 ISO, None은 빈 문자열
def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


# 오른쪽·아래쪽의 완전히 빈 행과 열을 잘라낸다
def _trim(rows: list[list[str]]) -> list[list[str]]:
    while rows and not any(cell for cell in rows[-1]):
        rows.pop()
    if not rows:
        return []

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    last_col = 0
    for row in rows:
        for index, cell in enumerate(row):
            if cell:
                last_col = max(last_col, index)
    return [row[: last_col + 1] for row in rows]


# .xlsx / .xls 를 읽는 로더
class ExcelLoader(BaseDocumentLoader):
    file_format = FileFormat.XLSX

    def __init__(self, config=None, *, file_format: FileFormat = FileFormat.XLSX) -> None:
        super().__init__(config)
        self.file_format = file_format

    # 시트마다 표 리전 하나씩 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        if self.file_format is FileFormat.XLS:
            sheets = self._read_xls(path, ctx)
        else:
            sheets = self._read_xlsx(path, ctx)

        regions: list[ContentRegion] = []
        for order, (name, rows) in enumerate(sheets):
            trimmed = _trim(rows)
            if not trimmed:
                continue
            regions.append(
                ContentRegion(
                    content_type=ContentType.TABLE,
                    order=order,
                    source_format=self.file_format.value,
                    page=order + 1,
                    rows=trimmed,
                    metadata={"sheet_name": name},
                )
            )
        return regions

    # openpyxl로 .xlsx를 읽는다. read_only로 메모리를, data_only로 수식 대신 값을 얻는다
    def _read_xlsx(self, path: Path, ctx: LoadContext) -> list[tuple[str, list[list[str]]]]:
        limit = self.config.max_excel_rows
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        try:
            sheets: list[tuple[str, list[list[str]]]] = []
            for worksheet in workbook.worksheets:
                rows: list[list[str]] = []
                for index, row in enumerate(worksheet.iter_rows(values_only=True)):
                    if index >= limit:
                        ctx.warn(
                            f"시트 '{worksheet.title}'가 {limit}행을 넘어 이후 행은 생략했습니다."
                        )
                        break
                    rows.append([_stringify(cell) for cell in row])
                sheets.append((worksheet.title, rows))
            return sheets
        finally:
            workbook.close()

    # xlrd로 구형 .xls를 읽는다
    def _read_xls(self, path: Path, ctx: LoadContext) -> list[tuple[str, list[list[str]]]]:
        limit = self.config.max_excel_rows
        book = xlrd.open_workbook(str(path))
        sheets: list[tuple[str, list[list[str]]]] = []
        for sheet in book.sheets():
            rows: list[list[str]] = []
            for index in range(min(sheet.nrows, limit)):
                rows.append([_stringify(v) for v in sheet.row_values(index)])
            if sheet.nrows > limit:
                ctx.warn(f"시트 '{sheet.name}'가 {limit}행을 넘어 이후 행은 생략했습니다.")
            sheets.append((sheet.name, rows))
        return sheets

    # 시트 개수를 페이지 수로 삼는다
    def extract_metadata(self, path: Path) -> dict[str, object]:
        if self.file_format is FileFormat.XLS:
            return {"page_count": xlrd.open_workbook(str(path)).nsheets}
        workbook = openpyxl.load_workbook(str(path), read_only=True)
        try:
            return {"page_count": len(workbook.worksheets)}
        finally:
            workbook.close()
