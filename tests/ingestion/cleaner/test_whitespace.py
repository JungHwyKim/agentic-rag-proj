"""공백 정규화 테스트. 다른 정리 규칙은 꺼 두고 이 규칙만 본다."""

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

# 공백 규칙만 켠 설정
SPACE_ONLY = CleanerConfig(remove_control_chars=False, remove_repeated_headers=False)


# 테스트용 최소 문서를 만든다
def _document(*elements: Element) -> ParsedDocument:
    return ParsedDocument(
        metadata=DocumentMetadata(
            source_name="샘플.hwpx", file_format=FileFormat.HWPX, size_bytes=1
        ),
        elements=list(elements),
    )


# 문단 요소 하나를 만든다
def _paragraph(text: str, order: int = 0) -> Element:
    return Element(type=ElementType.PARAGRAPH, text=text, order=order)


# 문장 한가운데 낀 두 칸은 BPE 토크나이저에서 한 칸과 다른 토큰이 된다.
# 실측: '...명시하고,  추가적 필요 사항은...' 형태로 HWPX 89요소에 있었다
def test_repeated_spaces_collapse() -> None:
    document = clean(_document(_paragraph("명시하고,  추가적   필요 사항은")), SPACE_ONLY)

    assert document.elements[0].text == "명시하고, 추가적 필요 사항은"


# 탭도 공백으로 본다. characters 규칙이 탭을 남기는 이유가 여기서 처리되기 때문이다
def test_tabs_collapse_to_single_space() -> None:
    document = clean(_document(_paragraph("가\t\t나")), SPACE_ONLY)

    assert document.elements[0].text == "가 나"


# 앞뒤 공백을 없앤다. 실측: PDF 에서 15요소가 앞뒤 공백을 달고 나왔다
def test_leading_and_trailing_space_removed() -> None:
    document = clean(_document(_paragraph("  가운데 문장  ")), SPACE_ONLY)

    assert document.elements[0].text == "가운데 문장"


# 개행은 표 마크다운의 행 구분자다. 공백으로 뭉개면 표가 통째로 깨진다
def test_newline_is_preserved_and_lines_trimmed() -> None:
    table = Element(
        type=ElementType.TABLE,
        text="|  구 분  |  값  |\n|  ---  |  ---  |",
        order=0,
    )
    document = clean(_document(table), SPACE_ONLY)

    assert document.elements[0].text == "| 구 분 | 값 |\n| --- | --- |"


# 표의 두 표현을 함께 정규화해야 어긋나지 않는다
def test_table_rows_normalized_together() -> None:
    table = Element(
        type=ElementType.TABLE,
        text="| 항목  A | 값 |\n| --- | --- |",
        order=0,
        metadata={"rows": [["항목  A", "값"], ["줄1\n줄2  뒤", "  설명  "]]},
    )
    document = clean(_document(table), SPACE_ONLY)
    cleaned = document.elements[0]

    assert cleaned.text == "| 항목 A | 값 |\n| --- | --- |"
    assert cleaned.metadata["rows"][0] == ["항목 A", "값"]
    # 셀 안 개행은 살리되 줄 단위로 공백을 정리한다
    assert cleaned.metadata["rows"][1] == ["줄1\n줄2 뒤", "설명"]


# 요소를 지우지 않는다. 개수와 order 가 그대로여야 한다
def test_elements_and_order_untouched() -> None:
    original = _document(_paragraph("깨끗함", order=0), _paragraph("두  칸", order=5))
    document = clean(original, SPACE_ONLY)

    assert len(document.elements) == 2
    assert [e.order for e in document.elements] == [0, 5]


# 설정으로 끌 수 있어야 한다
def test_disabled_leaves_text_alone() -> None:
    original = _document(_paragraph("두  칸"))
    document = clean(
        original,
        CleanerConfig(
            remove_control_chars=False, normalize_whitespace=False, remove_repeated_headers=False
        ),
    )

    assert document is original


# 이미 깔끔한 문서는 원본 객체 그대로 나와야 한다.
# 새 객체를 만들면 cleaned_elements 가 부풀고 큰 문서가 통째로 복사된다
def test_clean_document_passes_through_untouched() -> None:
    original = _document(_paragraph("이미 깔끔한 문장입니다."))
    document = clean(original, SPACE_ONLY)

    assert document is original
    assert document.metadata.cleaned_elements == 0
