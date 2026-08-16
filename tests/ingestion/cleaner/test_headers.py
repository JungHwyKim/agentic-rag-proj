"""반복 머리말 제거 테스트.

이 규칙은 cleaner 에서 **요소를 실제로 지우는 첫 규칙**이라 오탐이 곧 데이터 손실이다.
그래서 "지운다"보다 "안 지운다"를 확인하는 테스트가 더 많다.

다른 정리 규칙은 꺼 두고 이 규칙만 본다.
"""

from __future__ import annotations

from rag_pipeline.config import CleanerConfig
from rag_pipeline.ingestion.cleaner import clean
from rag_pipeline.schemas.document import (
    DocumentMetadata,
    Element,
    ElementType,
    FileFormat,
    ParsedDocument,
)

# 머리말 규칙만 켠 설정
HEADERS_ONLY = CleanerConfig(remove_control_chars=False, normalize_whitespace=False)


# 테스트용 최소 문서를 만든다
def _document(*elements: Element) -> ParsedDocument:
    return ParsedDocument(
        metadata=DocumentMetadata(
            source_name="샘플.hwpx", file_format=FileFormat.HWPX, size_bytes=1
        ),
        elements=list(elements),
    )


# 쪽마다 [머리말, 본문] 두 문단이 있는 문서를 만든다. 머리말은 pages 목록의 쪽에만 넣는다
def _paged(total: int, header: str, header_pages: set[int]) -> ParsedDocument:
    elements: list[Element] = []
    order = 0
    for page in range(1, total + 1):
        if page in header_pages:
            elements.append(
                Element(type=ElementType.PARAGRAPH, text=header, page=page, order=order)
            )
            order += 1
        elements.append(
            Element(
                type=ElementType.PARAGRAPH, text=f"{page}쪽 본문입니다.", page=page, order=order
            )
        )
        order += 1
    return _document(*elements)


# 문서 전체에 걸쳐 쪽 첫머리에 되풀이되면 머리말이다.
# 실측: AI 가이드에서 25개 쪽의 첫 요소가 같은 문구였다
def test_repeated_header_removed() -> None:
    document = clean(_paged(10, "기관명 가이드 2019", {1, 4, 7, 10}), HEADERS_ONLY)

    assert all("기관명 가이드" not in e.text for e in document.elements)
    assert document.metadata.removed_elements == 4
    assert len(document.elements) == 10


# 두 쪽뿐이면 머리말로 보지 않는다. 우연한 반복과 가릴 수 없다
def test_two_pages_is_not_a_header() -> None:
    original = _paged(10, "우연히 같은 문장", {2, 9})
    document = clean(original, HEADERS_ONLY)

    assert document is original


# 이번 규칙의 핵심. 여러 쪽에 나와도 문서 앞부분에 뭉쳐 있으면 머리말이 아니라 내용이다.
# 등장 '횟수' 로만 보면 이걸 못 가린다 — 실측에서 'AI 활동' 같은 표 머리행이 여기 걸렸다
def test_clustered_repeats_are_not_headers() -> None:
    original = _paged(20, "표 머리행", {2, 3, 4, 5})
    document = clean(original, HEADERS_ONLY)

    assert document is original
    assert document.metadata.removed_elements == 0


# 표는 판정 대상이 아니다. 양식 문서에서 같은 표가 여러 쪽 반복돼도 지우면 안 된다
def test_repeated_table_is_never_removed() -> None:
    elements: list[Element] = []
    for order, page in enumerate((1, 4, 7, 10)):
        elements.append(
            Element(
                type=ElementType.TABLE,
                text="| 항목 | 값 |\n| --- | --- |",
                page=page,
                order=order,
            )
        )
    original = _document(*elements)
    document = clean(original, HEADERS_ONLY)

    assert document is original
    assert len([e for e in document.elements if e.type is ElementType.TABLE]) == 4


# 같은 문구가 본문 한가운데 있으면 남긴다. 위치가 머리말인 것만 지운다
def test_same_text_in_body_is_kept() -> None:
    header = "기관명 가이드 2019"
    elements: list[Element] = []
    order = 0
    for page in range(1, 11):
        elements.append(
            Element(type=ElementType.PARAGRAPH, text=header, page=page, order=order)
        )
        order += 1
        # 본문 한가운데 같은 문구를 한 번 더 넣는다
        elements.append(
            Element(type=ElementType.PARAGRAPH, text=header, page=page, order=order)
        )
        order += 1

    document = clean(_document(*elements), HEADERS_ONLY)

    # 쪽마다 첫 번째 것만 지워지고 두 번째는 남는다
    assert len(document.elements) == 10
    assert document.metadata.removed_elements == 10


# 쪽 정보가 없는 문서(배치 정보 없는 HWPX, 텍스트 파일)에서는 판정 자체가 불가능하다
def test_document_without_pages_is_untouched() -> None:
    original = _document(
        *(
            Element(type=ElementType.PARAGRAPH, text="같은 문장", order=i)
            for i in range(6)
        )
    )
    document = clean(original, HEADERS_ONLY)

    assert document is original


# 전부 머리말로 판정되는 문서에서 본문을 다 날리면 안 된다.
# 쪽마다 요소가 하나뿐인 짧은 문서에서 실제로 일어난다
def test_document_is_never_emptied() -> None:
    elements = [
        Element(type=ElementType.PARAGRAPH, text="같은 제목", page=p, order=p - 1)
        for p in range(1, 7)
    ]
    document = clean(_document(*elements), HEADERS_ONLY)

    assert len(document.elements) == 6
    assert document.metadata.removed_elements == 0
    assert any("제거를 취소" in w for w in document.metadata.warnings)


# order 를 다시 매기지 않는다. 앞뒤 단계가 참조하던 값이 조용히 달라지면 안 된다
def test_order_is_not_renumbered() -> None:
    document = clean(_paged(10, "기관명 가이드 2019", {1, 4, 7, 10}), HEADERS_ONLY)
    orders = [e.order for e in document.elements]

    # 10쪽 본문 + 머리말 4개 = 14개(order 0..13). 머리말 4개가 빠져 10개가 남는다
    assert orders == sorted(orders)
    assert len(orders) == 10
    # 번호를 다시 매겼다면 0..9 가 됐을 것이다. 원본 자리를 그대로 지킨다
    assert max(orders) == 13


# 무엇을 지웠는지 남겨야 오탐을 알아차릴 수 있다.
# warnings 가 아니라 removed_texts 에 넣는다 — 정상 제거는 '문제'가 아니다
def test_removed_text_is_reported_outside_warnings() -> None:
    document = clean(_paged(10, "기관명 가이드 2019", {1, 4, 7, 10}), HEADERS_ONLY)

    assert any("기관명 가이드 2019" in t for t in document.metadata.removed_texts)
    assert document.metadata.warnings == []


# 설정으로 끌 수 있어야 한다
def test_disabled_keeps_headers() -> None:
    original = _paged(10, "기관명 가이드 2019", {1, 4, 7, 10})
    document = clean(
        original,
        CleanerConfig(
            remove_control_chars=False, normalize_whitespace=False, remove_repeated_headers=False
        ),
    )

    assert document is original
