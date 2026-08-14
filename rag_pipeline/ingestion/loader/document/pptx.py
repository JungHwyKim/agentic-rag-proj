"""PowerPoint 로더 (.pptx).

슬라이드마다 도형을 순회하며 제목·본문·표·그림을 뽑는다. 그룹 도형은 안에 도형이
중첩돼 있으므로 재귀적으로 들어간다. 발표자 노트는 본문과 성격이 달라 별도 요소로 둔다.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.schemas.document import ElementType, FileFormat


# 표 도형을 2차원 셀 배열로 변환
def _table_to_rows(shape) -> list[list[str]]:
    return [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]


# .pptx 를 읽는 로더
class PptxLoader(BaseDocumentLoader):
    file_format = FileFormat.PPTX

    # 슬라이드별로 도형을 순회해 리전을 만든다
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]:
        presentation = Presentation(str(path))
        regions: list[ContentRegion] = []
        order = 0

        for index, slide in enumerate(presentation.slides, start=1):
            title_shape = slide.shapes.title

            for shape in slide.shapes:
                new_regions, order = self._shape_regions(shape, index, order, title_shape)
                regions.extend(new_regions)

            # 발표자 노트
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "").strip()
                if notes:
                    regions.append(
                        ContentRegion(
                            content_type=ContentType.TEXT,
                            order=order,
                            source_format=self.file_format.value,
                            page=index,
                            text=notes,
                            metadata={"element_type": ElementType.SLIDE_NOTE},
                        )
                    )
                    order += 1

        return regions

    # 도형 하나를 리전으로 변환. 그룹 도형이면 재귀적으로 내려간다
    def _shape_regions(
        self, shape, slide_no: int, order: int, title_shape
    ) -> tuple[list[ContentRegion], int]:
        regions: list[ContentRegion] = []

        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for child in shape.shapes:
                child_regions, order = self._shape_regions(child, slide_no, order, title_shape)
                regions.extend(child_regions)
            return regions, order

        if getattr(shape, "has_table", False):
            rows = _table_to_rows(shape)
            if any(any(cell for cell in row) for row in rows):
                regions.append(
                    ContentRegion(
                        content_type=ContentType.TABLE,
                        order=order,
                        source_format=self.file_format.value,
                        page=slide_no,
                        rows=rows,
                    )
                )
                order += 1
            return regions, order

        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                blob = shape.image.blob
            except (AttributeError, ValueError):
                return regions, order
            regions.append(
                ContentRegion(
                    content_type=ContentType.IMAGE,
                    order=order,
                    source_format=self.file_format.value,
                    page=slide_no,
                    blob=blob,
                    blob_kind="png",
                )
            )
            return regions, order + 1

        if getattr(shape, "has_text_frame", False):
            text = (shape.text_frame.text or "").strip()
            if text:
                is_title = title_shape is not None and shape == title_shape
                regions.append(
                    ContentRegion(
                        content_type=ContentType.TEXT,
                        order=order,
                        source_format=self.file_format.value,
                        page=slide_no,
                        text=text,
                        metadata={
                            "element_type": ElementType.TITLE if is_title else ElementType.PARAGRAPH
                        },
                    )
                )
                order += 1

        return regions, order

    # 슬라이드 수와 문서 속성을 읽는다
    def extract_metadata(self, path: Path) -> dict[str, object]:
        presentation = Presentation(str(path))
        props = presentation.core_properties
        return {
            "page_count": len(presentation.slides),
            "title": props.title or None,
            "author": props.author or None,
        }
