"""적재 파이프라인 2단계 — 로더가 뽑은 텍스트를 검색에 쓸 수 있게 정리한다.

`ParsedDocument` 를 받아 `ParsedDocument` 를 돌려준다. 이 형태를 지켜야 `ingest()` 안에서
단계를 떼었다 붙였다 할 수 있다. HTTP 를 모르므로 스크립트·노트북에서도 그대로 쓴다.

규칙은 두 종류다.

  문자 수준 (characters, whitespace)  요소 안의 글자만 고친다. 요소별로 독립이다
  문서 수준 (headers)                 문서 전체를 봐야 판정할 수 있고 요소를 지운다

문자 수준을 먼저 돌린다. 공백만 다른 머리말을 같은 것으로 보려면 정규화가 앞서야 한다
(실측으로는 순서를 바꿔도 결과가 같았지만 논리적으로 이쪽이 맞다).

입력을 변형하지 않고 새 문서를 돌려주되, **바뀐 요소만 새로 만들고 나머지는 그대로 재사용**
한다. 깨끗한 문서(HWPX 는 제어문자가 0개다)에서 746개 요소를 통째로 복사하지 않기 위해서다.
이 규약은 세 층 모두에 걸린다 — 문자열 변환 함수·요소 정리·문서 정리가 각각 "안 바뀌었으면
인자로 받은 객체 그대로"를 지켜야 상위의 `is` 비교가 성립한다.

`order` 는 어떤 규칙도 재부여하지 않는다. 계약상 용도는 정렬 하나뿐이라(schemas/document.py)
연속성이 필요 없고, `elements` 에는 이미 빈틈이 있다(핸들러가 요소를 0개 만든 자리).
머리말을 지워 생기는 빈틈도 그대로 둔다 — 원본 위치를 남기는 쪽이 덜 훼손한다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rag_pipeline.config import CleanerConfig
from rag_pipeline.ingestion.cleaner.characters import strip_control_chars
from rag_pipeline.ingestion.cleaner.headers import remove_repeated_headers
from rag_pipeline.ingestion.cleaner.whitespace import normalize_whitespace
from rag_pipeline.schemas.document import Element, ParsedDocument

__all__ = ["clean"]

# 문자열 하나를 정리하는 규칙. 안 바뀌면 인자를 그대로 돌려주는 것이 규약이다
TextRule = Callable[[str], str]


# 표 요소의 rows(2차원 셀 배열)에 규칙들을 적용한다. 바뀐 게 없으면 None 을 돌려준다.
# text 만 고치고 rows 를 두면 같은 표의 두 표현이 어긋나므로 규칙마다 여기를 함께 태운다
def _clean_rows(rows: Any, rules: list[TextRule]) -> list[list[str]] | None:
    if not isinstance(rows, list):
        return None

    changed = False
    new_rows: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            return None
        new_row = []
        for cell in row:
            value = cell
            if isinstance(value, str):
                for rule in rules:
                    value = rule(value)
            changed = changed or value is not cell
            new_row.append(value)
        new_rows.append(new_row)

    return new_rows if changed else None


# 요소 하나에 문자 수준 규칙들을 적용한다. 바뀐 게 없으면 원본 객체를 그대로 돌려준다
def _clean_element(element: Element, rules: list[TextRule]) -> Element:
    text = element.text
    for rule in rules:
        text = rule(text)
    rows = _clean_rows(element.metadata.get("rows"), rules)

    if text is element.text and rows is None:
        return element

    update: dict[str, Any] = {"text": text}
    if rows is not None:
        update["metadata"] = {**element.metadata, "rows": rows}
    return element.model_copy(update=update)


# 설정을 보고 적용할 문자 수준 규칙 목록을 만든다
def _text_rules(config: CleanerConfig) -> list[TextRule]:
    rules: list[TextRule] = []
    if config.remove_control_chars:
        rules.append(strip_control_chars)
    if config.normalize_whitespace:
        rules.append(normalize_whitespace)
    return rules


# 문서를 정리해 새 ParsedDocument 로 돌려준다. 규칙은 설정으로 하나씩 끌 수 있다
def clean(document: ParsedDocument, config: CleanerConfig | None = None) -> ParsedDocument:
    config = config or CleanerConfig()

    rules = _text_rules(config)
    if rules:
        elements = [_clean_element(element, rules) for element in document.elements]
        cleaned = sum(
            1
            for before, after in zip(document.elements, elements, strict=True)
            if before is not after
        )
        if cleaned:
            metadata = document.metadata.model_copy(
                update={"cleaned_elements": document.metadata.cleaned_elements + cleaned}
            )
            document = document.model_copy(update={"elements": elements, "metadata": metadata})

    if config.remove_repeated_headers:
        document, _ = remove_repeated_headers(document, config)

    return document
