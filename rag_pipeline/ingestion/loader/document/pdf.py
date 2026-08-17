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

import pymupdf

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.ingestion.loader.document.headings import FONT_SIZES_KEY
from rag_pipeline.schemas.document import FileFormat

# 표 영역을 잘라낼 때 테두리가 잘리지 않도록 주는 여백(pt)
_CROP_PADDING = 4.0


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

        for table in tables:
            bbox = tuple(float(v) for v in table.bbox)
            try:
                rows = [[("" if c is None else str(c)) for c in row] for row in table.extract()]
            except Exception:  # noqa: BLE001
                rows = []
            if not any(any(cell.strip() for cell in row) for row in rows):
                continue

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
