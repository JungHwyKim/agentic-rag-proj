"""적재 파이프라인 2단계 — 로더가 뽑은 텍스트를 검색에 쓸 수 있게 정리한다.

`ParsedDocument` 를 받아 `ParsedDocument` 를 돌려준다. 이 형태를 지켜야 `ingest()` 안에서
단계를 떼었다 붙였다 할 수 있다. HTTP 를 모르므로 스크립트·노트북에서도 그대로 쓴다.

입력을 변형하지 않고 새 문서를 돌려주되, **바뀐 요소만 새로 만들고 나머지는 그대로 재사용**
한다. 깨끗한 문서(HWPX 는 제어문자가 0개다)에서 746개 요소를 통째로 복사하지 않기 위해서다.

요소를 지우지 않는다는 점이 중요하다. 지금 규칙은 요소 **안의 글자**만 고치므로 `order` 에
새 빈틈이 생기지 않는다. 나중에 요소를 지우는 규칙(머리말 제거 등)을 넣더라도 원본 `order` 를
유지한다 — `order` 의 계약상 용도는 정렬 하나뿐이라(schemas/document.py) 연속성이 필요 없고,
`elements` 에는 이미 빈틈이 있다(핸들러가 요소를 0개 만든 자리).
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.config import CleanerConfig
from rag_pipeline.ingestion.cleaner.characters import strip_control_chars
from rag_pipeline.schemas.document import Element, ParsedDocument

__all__ = ["clean"]


# 표 요소의 rows(2차원 셀 배열)를 정리한다. 바뀐 게 없으면 None 을 돌려준다.
# text 만 고치고 rows 를 두면 같은 표의 두 표현이 어긋난다
def _clean_rows(rows: Any) -> list[list[str]] | None:
    if not isinstance(rows, list):
        return None

    changed = False
    new_rows: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            return None
        new_row = []
        for cell in row:
            cleaned = strip_control_chars(cell) if isinstance(cell, str) else cell
            changed = changed or cleaned is not cell
            new_row.append(cleaned)
        new_rows.append(new_row)

    return new_rows if changed else None


# 요소 하나를 정리한다. 바뀐 게 없으면 원본 객체를 그대로 돌려준다
def _clean_element(element: Element) -> Element:
    text = strip_control_chars(element.text)
    rows = _clean_rows(element.metadata.get("rows"))

    if text is element.text and rows is None:
        return element

    update: dict[str, Any] = {"text": text}
    if rows is not None:
        update["metadata"] = {**element.metadata, "rows": rows}
    return element.model_copy(update=update)


# 문서를 정리해 새 ParsedDocument 로 돌려준다. 설정으로 규칙을 끌 수 있다
def clean(document: ParsedDocument, config: CleanerConfig | None = None) -> ParsedDocument:
    config = config or CleanerConfig()

    if not config.remove_control_chars:
        return document

    elements = [_clean_element(element) for element in document.elements]
    cleaned_count = sum(
        1 for before, after in zip(document.elements, elements, strict=True) if before is not after
    )
    if not cleaned_count:
        return document

    metadata = document.metadata.model_copy(update={"cleaned_elements": cleaned_count})
    return document.model_copy(update={"elements": elements, "metadata": metadata})
