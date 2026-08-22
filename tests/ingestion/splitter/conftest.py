"""splitter 테스트용 문서 조립 헬퍼.

로더를 태우지 않고 `ParsedDocument` 를 직접 만든다. splitter 는 앞 단계 결과의 **형태**에만
의존하므로, 실제 파일을 파싱하면 테스트가 로더 변경에 함께 흔들린다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.schemas.document import (
    DocumentMetadata,
    Element,
    ElementType,
    FileFormat,
    ParsedDocument,
)


# 제목 요소 하나. heading_level 을 주면 계층이 생긴다
def title(text: str, *, level: int | None = None, order: int = 0, page: int | None = None):
    metadata = {} if level is None else {"heading_level": level}
    return Element(
        type=ElementType.TITLE, text=text, order=order, page=page, metadata=metadata
    )


# 본문 문단 하나
def para(text: str, *, order: int = 0, page: int | None = None):
    return Element(type=ElementType.PARAGRAPH, text=text, order=order, page=page)


# 표 요소 하나. rows 를 주면 행 그룹 분할이 가능해진다
def table(
    markdown: str,
    rows: list[list[str]] | None = None,
    *,
    order: int = 0,
    page: int | None = None,
    page_end: int | None = None,
):
    metadata: dict = {}
    if rows is not None:
        metadata["rows"] = rows
    if page_end is not None:
        metadata["page_end"] = page_end
    return Element(
        type=ElementType.TABLE, text=markdown, order=order, page=page, metadata=metadata
    )


# 요소 목록을 ParsedDocument 로 감싼다. order 를 안 준 요소에는 순서대로 매겨준다
@pytest.fixture
def make_document():
    def _make(elements: list[Element], *, source_name: str = "샘플.hwpx") -> ParsedDocument:
        numbered = [
            element if element.order else element.model_copy(update={"order": index})
            for index, element in enumerate(elements, start=1)
        ]
        return ParsedDocument(
            metadata=DocumentMetadata(
                source_name=source_name,
                file_format=FileFormat.HWPX,
                size_bytes=1234,
            ),
            elements=numbered,
        )

    return _make
