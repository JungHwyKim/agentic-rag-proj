"""Word 로더 테스트 — 본문과 표의 원본 순서 보존이 핵심."""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.document.docx import DocxLoader
from rag_pipeline.schemas.document import ElementType


# 제목·문단·표가 문서에 등장한 순서 그대로 나와야 한다.
# python-docx의 paragraphs/tables를 따로 읽으면 표가 맨 뒤로 밀린다
def test_preserves_body_order(docx_file: Path, offline_config: IngestConfig) -> None:
    document = DocxLoader(offline_config).load(docx_file)
    types = [element.type for element in document.elements]

    assert types == [
        ElementType.TITLE,
        ElementType.PARAGRAPH,
        ElementType.TABLE,
        ElementType.PARAGRAPH,
    ]


# 한국어 텍스트가 깨지지 않아야 한다
def test_korean_text_intact(docx_file: Path, offline_config: IngestConfig) -> None:
    document = DocxLoader(offline_config).load(docx_file)

    assert document.elements[0].text == "연간 보고서"
    assert document.elements[1].text == "첫 번째 문단입니다."
    assert document.elements[3].text == "표 다음에 오는 문단입니다."


# 표는 마크다운으로 직렬화되고 원본 배열이 metadata["rows"]에 남아야 한다
def test_table_markdown_and_rows(docx_file: Path, offline_config: IngestConfig) -> None:
    document = DocxLoader(offline_config).load(docx_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert "| 이름 | 점수 |" in table.text
    assert "| --- | --- |" in table.text
    assert "| 홍길동 | 95 |" in table.text
    assert table.metadata["rows"] == [["이름", "점수"], ["홍길동", "95"]]
    # docx는 네이티브 구조가 있으므로 LlamaParse를 타지 않는다
    assert table.metadata["table_source"] == "native"


# 제목 요소는 heading_level 을 함께 갖는다
def test_heading_level(docx_file: Path, offline_config: IngestConfig) -> None:
    document = DocxLoader(offline_config).load(docx_file)

    assert document.elements[0].metadata["heading_level"] == 1


# order 는 0부터 빈틈없이 증가해야 재조립이 안전하다
def test_order_is_sequential(docx_file: Path, offline_config: IngestConfig) -> None:
    document = DocxLoader(offline_config).load(docx_file)

    assert [e.order for e in document.elements] == [0, 1, 2, 3]


# 외부 API를 한 번도 부르지 않아야 한다
def test_no_external_calls(docx_file: Path, offline_config: IngestConfig) -> None:
    document = DocxLoader(offline_config).load(docx_file)

    assert document.metadata.vision_calls == 0
    assert document.metadata.llamaparse_calls == 0
