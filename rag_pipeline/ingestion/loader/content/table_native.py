"""표 핸들러(네이티브) — 이미 셀 구조를 갖고 있는 표를 마크다운으로 직렬화한다.

docx/pptx/xlsx/hwpx 는 파일 안에 셀 구조가 명시되어 있고, PDF도 PyMuPDF의
find_tables() 가 행·열을 뽑아준다. 둘 다 여기로 온다 — 외부 API 호출이 없다.
"""

from __future__ import annotations

from rag_pipeline.ingestion.loader.content.base import (
    ContentRegion,
    LoadContext,
    rows_to_markdown,
)
from rag_pipeline.schemas.document import Element, ElementType


# 2차원 셀 배열을 마크다운 표 Element로 변환. 원본 배열은 metadata["rows"]에 보존
def handle(region: ContentRegion, ctx: LoadContext) -> list[Element]:
    rows = region.rows or []
    markdown = rows_to_markdown(rows)
    if not markdown:
        return []

    metadata = dict(region.metadata)
    metadata["rows"] = rows
    metadata.setdefault("table_source", "native")
    if region.bbox is not None:
        metadata["bbox"] = list(region.bbox)

    return [
        Element(
            type=ElementType.TABLE,
            text=markdown,
            page=region.page,
            order=region.order,
            metadata=metadata,
        )
    ]
