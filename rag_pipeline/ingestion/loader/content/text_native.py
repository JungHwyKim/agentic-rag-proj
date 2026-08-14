"""텍스트 핸들러 — 포맷별 기본 로더가 이미 뽑아낸 텍스트를 그대로 Element로 만든다."""

from __future__ import annotations

from rag_pipeline.ingestion.loader.content.base import ContentRegion, LoadContext
from rag_pipeline.schemas.document import Element, ElementType


# 추출된 텍스트를 Element로 변환. 요소 종류는 로더가 metadata에 실어 보낸 값을 따른다
def handle(region: ContentRegion, ctx: LoadContext) -> list[Element]:
    text = (region.text or "").strip()
    if not text:
        return []

    element_type = region.metadata.pop("element_type", ElementType.PARAGRAPH)
    if isinstance(element_type, str):
        element_type = ElementType(element_type)

    return [
        Element(
            type=element_type,
            text=text,
            page=region.page,
            order=region.order,
            metadata=dict(region.metadata),
        )
    ]
