"""제어·보이지 않는 문자 정리 테스트.

파일을 만들지 않고 ParsedDocument 를 직접 조립한다. 이 단계는 파일과 무관하게
요소 텍스트만 다루므로 로더를 태울 이유가 없다.
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


# 테스트용 최소 문서를 만든다
def _document(*elements: Element) -> ParsedDocument:
    return ParsedDocument(
        metadata=DocumentMetadata(
            source_name="샘플.pdf", file_format=FileFormat.PDF, size_bytes=1
        ),
        elements=list(elements),
    )


# 문단 요소 하나를 만든다
def _paragraph(text: str, order: int = 0) -> Element:
    return Element(type=ElementType.PARAGRAPH, text=text, order=order)


# PDF 글머리 기호가 떨어진 제어문자를 걷어내야 한다.
# 실측: 신뢰가능 AI 가이드 PDF 에 '작성 \x07김동현 수석' 형태로 박혀 있었다
def test_control_chars_removed() -> None:
    document = clean(_document(_paragraph("작성 \x07김동현 수석(정책본부)")))

    assert document.elements[0].text == "작성 김동현 수석(정책본부)"


# 개행은 표 마크다운의 행 구분자다. 지우면 표가 한 줄로 뭉개져 통째로 깨진다
def test_newline_is_kept() -> None:
    table = Element(
        type=ElementType.TABLE,
        text="| 구 분 | 추진내용 |\n| --- | --- |\n| 기관 | 처리과 |",
        order=0,
    )
    document = clean(_document(table))

    assert document.elements[0].text.count("\n") == 2
    assert document.elements[0].text.startswith("| 구 분 | 추진내용 |\n")


# 탭은 남기고, 캐리지리턴은 지우지 말고 개행으로 바꾼다.
# 그냥 지우면 앞뒤 줄이 서로 붙어 문장이 뒤엉킨다
def test_tab_kept_and_carriage_return_normalized() -> None:
    document = clean(_document(_paragraph("가\t나\r\n다\r라")))

    assert document.elements[0].text == "가\t나\n다\n라"


# 보이지 않는 서식문자는 사람이 못 잡는다. 토큰이 쪼개져 검색이 조용히 나빠진다
def test_invisible_format_chars_removed() -> None:
    # zero-width space, BOM, soft hyphen
    document = clean(_document(_paragraph("주식​회사﻿한국­지사")))

    assert document.elements[0].text == "주식회사한국지사"


# 표는 text 와 metadata["rows"] 를 둘 다 들고 있다. 한쪽만 고치면 두 표현이 어긋난다
def test_table_rows_cleaned_together_with_text() -> None:
    table = Element(
        type=ElementType.TABLE,
        text="| 항목\x01 | 값 |\n| --- | --- |",
        order=0,
        metadata={"rows": [["항목\x01", "값"], ["1\n실용가이드", "설명\x07"]]},
    )
    document = clean(_document(table))
    cleaned = document.elements[0]

    assert "\x01" not in cleaned.text
    assert cleaned.metadata["rows"][0] == ["항목", "값"]
    # 셀 안의 개행은 행 구분자가 아니라 셀 내부 줄바꿈이므로 그대로 남는다
    assert cleaned.metadata["rows"][1] == ["1\n실용가이드", "설명"]


# 이 단계는 요소를 지우지 않는다. 개수와 order 가 그대로여야 한다
def test_elements_and_order_are_untouched() -> None:
    original = _document(
        _paragraph("깨끗한 문단", order=0),
        _paragraph("제어문자 \x01 있음", order=7),
    )
    document = clean(original)

    assert len(document.elements) == 2
    assert [e.order for e in document.elements] == [0, 7]


# 설정으로 끌 수 있어야 한다
def test_disabled_leaves_text_alone() -> None:
    original = _document(_paragraph("작성 \x07김동현"))
    document = clean(original, CleanerConfig(remove_control_chars=False))

    assert document is original


# 깨끗한 문서는 손대지 않는다. HWPX 는 제어문자가 0개라 이 경로를 매번 탄다
def test_clean_document_passes_through_untouched() -> None:
    original = _document(_paragraph("이미 깨끗한 문단입니다."))
    document = clean(original)

    assert document is original
    assert document.metadata.cleaned_elements == 0


# 무엇을 얼마나 손댔는지 보여야 정리 단계를 의심할 근거가 생긴다
def test_cleaned_element_count_is_reported() -> None:
    document = clean(
        _document(
            _paragraph("깨끗함", order=0),
            _paragraph("더러움 \x01", order=1),
            _paragraph("더러움 \x07", order=2),
        )
    )

    assert document.metadata.cleaned_elements == 2
