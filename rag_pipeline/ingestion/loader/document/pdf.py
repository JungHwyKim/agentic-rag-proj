"""PDF 로더 (.pdf).

PDF에는 '표'라는 구조가 없다 — 선과 글자의 좌표만 있다. 그래서 여기서만 2차 라우팅이
진짜 의미를 갖는다:
  - 텍스트 블록 -> 기본 추출
  - 표 영역     -> 잘라서 LlamaParse (실패하면 PyMuPDF가 뽑은 행으로 폴백)
  - 그림 영역   -> classify 후 OCR 또는 Vision
  - 글자가 거의 없는 페이지 -> 스캔본으로 보고 페이지 전체를 이미지로 처리
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.ingestion.loader.document.headings import FONT_SIZES_KEY
from rag_pipeline.schemas.document import FileFormat

# 표 영역을 잘라낼 때 테두리가 잘리지 않도록 주는 여백(pt)
_CROP_PADDING = 4.0

# 조각난 표를 되붙일 때 쓰는 두 값. 근거는 PDF 5개 · 표 리전 103개 · 같은 쪽 인접쌍 36개
# 실측이다(2026-08-19, plan_log/표-경로.md 2회차).
#
# 세로 간격: 실제로 조각난 표는 6.8 이었고, 별개 표·별개 상자는 전부 18.8 이상이었다.
# 2.8배 틈이 있어 이 임계값은 표본 하나에 기대지 않는다. 12 는 그 사이다
_MERGE_MAX_GAP = 12.0
# 좌우 끝: 같은 표에서 잘린 조각은 x 가 소수점까지 같다. 반올림 오차만 흡수한다
_MERGE_X_TOLERANCE = 2.0

# 격자 칸의 중심점이 어느 칸에 덮였는지 볼 때 쓰는 여유. 테두리에 정확히 걸치는 경우만
# 흡수하면 되므로 작다 — 크게 잡으면 이웃 칸까지 덮은 것으로 오인한다
_GRID_EPS = 0.5

# (bbox, rows) — 표 하나. 되붙이기 전후로 같은 모양을 쓴다
_Table = tuple[tuple[float, ...], list[list[str]]]


# 블록 안 span 들의 글자 크기. 제목 승격 판정에만 쓰이고 headings.py 가 걷어낸다.
# 공백뿐인 span 은 뺀다 — 줄바꿈용 빈 span 이 엉뚱한 크기를 갖고 있어 최빈값을 흔든다.
# 소수점 첫째 자리로 반올림하는 이유는 같은 본문이 9.5 와 9.499996 으로 갈려 들어오기 때문
def _span_sizes(spans: list[dict]) -> list[float]:
    return [
        round(float(span["size"]), 1)
        for span in spans
        if span.get("text", "").strip() and span.get("size")
    ]


# 두 사각형이 의미 있게 겹치는지 판단. 표 영역 안의 텍스트 블록을 걸러내는 데 쓴다
def _overlaps(inner: tuple[float, ...], outer: tuple[float, ...], threshold: float = 0.5) -> bool:
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer

    overlap_w = max(0.0, min(ix1, ox1) - max(ix0, ox0))
    overlap_h = max(0.0, min(iy1, oy1) - max(iy0, oy0))
    overlap_area = overlap_w * overlap_h

    inner_area = max((ix1 - ix0) * (iy1 - iy0), 1e-6)
    return overlap_area / inner_area >= threshold


# 병합에 덮인 칸을 값으로 채운다.
#
# PyMuPDF 는 병합에 덮인 자리를 `row.cells[j] is None` 으로 내놓고 `extract()` 는 같은 자리에
# 빈 문자열을 준다. 그래서 **빈 칸이 "병합에 덮인 자리"인지 "그냥 빈 칸"인지 가를 수 있다** —
# 이 구분이 없으면 되풀이 자체가 불가능하다.
#
# 규칙은 HWPX 와 같다(`hwpx.py:_table_rows`). **세로로 이어진 칸에는 값을 되풀이하고 가로로
# 이어진 칸은 비운다** — splitter 가 표를 행 단위로만 쪼개므로 아래로 이어진 행은 다른 청크로
# 갈 수 있지만, 옆으로 이어진 칸은 언제나 같은 행에 남는다.
#
# **둘을 가르는 것이 이 함수의 어려운 부분이다.** 실측(PDF 5개·칸 3,503개, 2026-08-19)으로
# None 칸이 897개(25.6%)이고 그중 위에 앵커가 있는 것이 661개인데, **그 661개 중 226개(34.2%)는
# 왼쪽 칸이 이 열까지 뻗은 가로 병합이기도 하다.** 위만 보고 되풀이하면 그 226개에 엉뚱한 값이
# 들어간다. 그래서 가로를 먼저 보고, 가로가 아닐 때만 세로로 채운다.
def _fill_merged_cells(table: Any) -> list[list[str]]:
    values = [[("" if cell is None else str(cell)) for cell in row] for row in table.extract()]
    rows = getattr(table, "rows", None)
    if not rows or len(rows) != len(values):
        return values

    anchors = [
        (r, c, cell)
        for r, row in enumerate(rows)
        for c, cell in enumerate(row.cells)
        if cell is not None
    ]
    if not anchors:
        return values

    # 격자 경계는 **모든 칸 모서리의 합집합**에서 얻는다. 그 행에 있는 칸들만 보면, 여러 격자
    # 행에 걸친 칸 때문에 행의 세로 범위가 부풀어 중심점이 엉뚱한 행으로 간다(실측으로 그렇게
    # 재다가 멀쩡한 채움 80칸을 오류로 오판했다)
    xs = sorted({round(float(v), 1) for _, _, cell in anchors for v in (cell[0], cell[2])})
    ys = sorted({round(float(v), 1) for _, _, cell in anchors for v in (cell[1], cell[3])})
    if len(rows) != len(ys) - 1:
        return values

    for r, row in enumerate(rows):
        if len(row.cells) != len(xs) - 1:
            continue
        cy = (ys[r] + ys[r + 1]) / 2
        for c, cell in enumerate(row.cells):
            if cell is not None or c >= len(values[r]) or values[r][c]:
                continue

            cx = (xs[c] + xs[c + 1]) / 2
            for anchor_row, anchor_col, rect in anchors:
                if not (rect[0] - _GRID_EPS <= cx <= rect[2] + _GRID_EPS):
                    continue
                if not (rect[1] - _GRID_EPS <= cy <= rect[3] + _GRID_EPS):
                    continue
                # 덮은 칸의 **왼쪽 열**이고 그 칸이 위에 있을 때만 세로 연장이다.
                # 열이 다르면 가로 연장이므로 비워 둔다
                if anchor_col == c and anchor_row < r and anchor_col < len(values[anchor_row]):
                    values[r][c] = values[anchor_row][anchor_col]
                break

    return values


# 두 조각이 원래 한 표였나. 셋을 **모두** 만족해야 한다
def _is_same_table(a: _Table, b: _Table) -> bool:
    (ax0, _, ax1, ay1), a_rows = a
    (bx0, by0, bx1, _), b_rows = b

    if not 0 <= by0 - ay1 < _MERGE_MAX_GAP:
        return False
    if abs(ax0 - bx0) > _MERGE_X_TOLERANCE or abs(ax1 - bx1) > _MERGE_X_TOLERANCE:
        return False
    return bool(a_rows) and bool(b_rows) and len(a_rows[0]) == len(b_rows[0])


# 같은 표가 여러 조각으로 잡힌 것을 되붙인다.
#
# PyMuPDF 의 `find_tables()` 가 한 표를 여러 개로 나눠 내놓는 경우가 있다. 그러면 splitter 가
# **각 조각의 첫 행을 헤더로 되풀이**하므로 실제 열 이름은 첫 조각에만 남고 나머지 조각은
# 데이터 행이 헤더 자리에 온다 — 시행령 안내서 4쪽의 표 하나가 5조각으로 잡혀 열 이름
# `단계|실행항목|세부내용 및 방법|담당` 이 첫 조각에만 있었다(실측 2026-08-18).
#
# **여기서 붙여야 한다.** LlamaParse 로 올려도 안 고쳐진다 — 핸들러는 리전 하나의 blob 만
# 보내므로 외부 API 는 애초에 조각 하나밖에 못 본다(실측: 유료 경로에서 같은 표가 10조각).
def _merge_split_tables(tables: list[_Table]) -> list[_Table]:
    merged: list[_Table] = []

    for bbox, rows in sorted(tables, key=lambda item: item[0][1]):
        if merged and _is_same_table(merged[-1], (bbox, rows)):
            (px0, py0, px1, py1), prev_rows = merged[-1]
            merged[-1] = (
                (min(px0, bbox[0]), py0, max(px1, bbox[2]), max(py1, bbox[3])),
                prev_rows + rows,
            )
            continue
        merged.append((bbox, rows))

    return merged


# .pdf 를 읽는 로더
class PdfLoader(BaseDocumentLoader):
    file_format = FileFormat.PDF

    # 페이지를 훑어 텍스트·표·이미지 리전을 위치 순서대로 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        regions: list[ContentRegion] = []
        order = 0

        with pymupdf.open(str(path)) as document:
            for page_no in range(len(document)):
                page = document[page_no]

                # 글자가 거의 없으면 스캔본 -> 페이지 전체를 이미지로 넘긴다
                if len(page.get_text().strip()) < self.config.content.pdf_scan_text_threshold:
                    region = self._page_as_image(page, page_no, order)
                    if region is not None:
                        regions.append(region)
                        order += 1
                        ctx.warn(f"{page_no + 1}쪽은 텍스트가 없어 이미지로 처리했습니다.")
                    continue

                items = self._page_items(document, page, page_no, ctx)
                # 세로 위치 -> 가로 위치 순으로 정렬해야 사람이 읽는 순서가 된다
                for _, _, region in sorted(items, key=lambda it: (round(it[0], 1), it[1])):
                    region.order = order
                    regions.append(region)
                    order += 1

        return regions

    # 페이지 하나에서 표·텍스트·이미지 리전을 좌표와 함께 수집
    def _page_items(
        self, document: pymupdf.Document, page: pymupdf.Page, page_no: int, ctx: LoadContext
    ) -> list[tuple[float, float, ContentRegion]]:
        items: list[tuple[float, float, ContentRegion]] = []
        table_bboxes: list[tuple[float, ...]] = []

        # --- 표 ---
        try:
            found = page.find_tables()
            tables = list(found.tables)
        except Exception as exc:  # noqa: BLE001 - 표 탐지 실패가 페이지 전체를 막으면 안 된다
            ctx.warn(f"{page_no + 1}쪽 표 탐지에 실패했습니다: {type(exc).__name__}")
            tables = []

        extracted: list[_Table] = []
        for table in tables:
            bbox = tuple(float(v) for v in table.bbox)
            try:
                rows = _fill_merged_cells(table)
            except Exception:  # noqa: BLE001
                rows = []
            if not any(any(cell.strip() for cell in row) for row in rows):
                continue
            extracted.append((bbox, rows))

        for bbox, rows in _merge_split_tables(extracted):
            table_bboxes.append(bbox)
            items.append(
                (
                    bbox[1],
                    bbox[0],
                    ContentRegion(
                        content_type=ContentType.TABLE,
                        order=0,
                        source_format=self.file_format.value,
                        page=page_no + 1,
                        bbox=bbox,  # type: ignore[arg-type]
                        rows=rows,
                        # LlamaParse에 보낼 표 영역만 잘라낸 단일 페이지 PDF
                        blob=self._crop_pdf(document, page_no, bbox),
                        blob_kind="pdf",
                    ),
                )
            )

        # --- 텍스트 (표 영역과 겹치는 블록은 중복이므로 제외) ---
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            bbox = tuple(float(v) for v in block["bbox"])
            if any(_overlaps(bbox, tb) for tb in table_bboxes):
                continue

            spans = [
                span for line in block.get("lines", []) for span in line.get("spans", [])
            ]
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue

            items.append(
                (
                    bbox[1],
                    bbox[0],
                    ContentRegion(
                        content_type=ContentType.TEXT,
                        order=0,
                        source_format=self.file_format.value,
                        page=page_no + 1,
                        bbox=bbox,  # type: ignore[arg-type]
                        text=text,
                        metadata={FONT_SIZES_KEY: _span_sizes(spans)},
                    ),
                )
            )

        # --- 이미지 ---
        items.extend(self._image_items(document, page, page_no))
        return items

    # 페이지에 삽입된 그림들을 위치와 함께 수집
    def _image_items(
        self, document: pymupdf.Document, page: pymupdf.Page, page_no: int
    ) -> list[tuple[float, float, ContentRegion]]:
        items: list[tuple[float, float, ContentRegion]] = []

        for image_info in page.get_images(full=True):
            xref = image_info[0]
            try:
                rects = page.get_image_rects(xref)
                extracted = document.extract_image(xref)
            except Exception:  # noqa: BLE001 - 깨진 이미지 하나가 문서 전체를 막으면 안 된다
                continue

            blob = extracted.get("image")
            if not blob:
                continue

            rect = rects[0] if rects else page.rect
            bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

            items.append(
                (
                    bbox[1],
                    bbox[0],
                    ContentRegion(
                        content_type=ContentType.IMAGE,
                        order=0,
                        source_format=self.file_format.value,
                        page=page_no + 1,
                        bbox=bbox,
                        blob=blob,
                        blob_kind="png",
                        metadata={"xref": xref},
                    ),
                )
            )
        return items

    # 텍스트가 없는 페이지를 통째로 렌더링해 이미지 리전으로 만든다
    def _page_as_image(self, page: pymupdf.Page, page_no: int, order: int) -> ContentRegion | None:
        try:
            pixmap = page.get_pixmap(dpi=self.config.content.pdf_render_dpi)
            blob = pixmap.tobytes("png")
        except Exception:  # noqa: BLE001
            return None

        return ContentRegion(
            content_type=ContentType.IMAGE,
            order=order,
            source_format=self.file_format.value,
            page=page_no + 1,
            blob=blob,
            blob_kind="png",
            metadata={"scanned_page": True},
        )

    # 표 영역만 남긴 단일 페이지 PDF를 만들어 바이트로 돌려준다
    def _crop_pdf(
        self, document: pymupdf.Document, page_no: int, bbox: tuple[float, ...]
    ) -> bytes | None:
        try:
            cropped = pymupdf.open()
            cropped.insert_pdf(document, from_page=page_no, to_page=page_no)
            page = cropped[0]

            rect = (
                pymupdf.Rect(
                    bbox[0] - _CROP_PADDING,
                    bbox[1] - _CROP_PADDING,
                    bbox[2] + _CROP_PADDING,
                    bbox[3] + _CROP_PADDING,
                )
                & page.mediabox
            )

            if rect.is_empty or rect.width < 1 or rect.height < 1:
                cropped.close()
                return None

            page.set_cropbox(rect)
            data = cropped.tobytes()
            cropped.close()
            return data
        except Exception:  # noqa: BLE001 - 크롭 실패 시 LlamaParse를 건너뛰고 로컬 추출만 쓴다
            return None

    # 페이지 수와 문서 속성을 읽는다
    def extract_metadata(self, path: Path) -> dict[str, object]:
        with pymupdf.open(str(path)) as document:
            info = document.metadata or {}
            return {
                "page_count": len(document),
                "title": info.get("title") or None,
                "author": info.get("author") or None,
            }
