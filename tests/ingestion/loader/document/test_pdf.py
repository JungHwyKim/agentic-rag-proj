"""PDF 로더 테스트."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pymupdf

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.loader.content import table_llamaparse
from rag_pipeline.ingestion.loader.document.pdf import PdfLoader, _overlaps
from rag_pipeline.schemas.document import ElementType


# 페이지별 텍스트가 추출되고 페이지 번호가 1부터 붙어야 한다
def test_extracts_text_with_page_numbers(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)
    pages = {e.page for e in document.elements}

    assert document.metadata.page_count == 2
    assert pages == {1, 2}
    assert all(e.type is ElementType.PARAGRAPH for e in document.elements)
    # 텍스트가 제대로 추출됐다면 스캔본으로 오인하지 않는다
    assert document.metadata.warnings == []


# 한국어 텍스트가 깨지지 않아야 한다
def test_korean_text_intact(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)

    assert "첫째 쪽 내용입니다." in document.elements[0].text


# 괘선이 있는 표를 찾아내고 셀 구조를 복원해야 한다
def test_detects_table(pdf_with_table_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_with_table_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    rows = table.metadata["rows"]
    flat = [cell for row in rows for cell in row]
    assert "홍길동" in flat
    assert "95" in flat


# 표 영역 안의 글자가 본문 문단으로 중복 추출되면 안 된다
def test_table_text_not_duplicated(pdf_with_table_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_with_table_file)
    paragraphs = " ".join(e.text for e in document.elements if e.type is ElementType.PARAGRAPH)

    assert "홍길동" not in paragraphs
    assert "아래는 성적표입니다." in paragraphs


# PDF 표는 LlamaParse로 라우팅되어야 한다 (docx와 달리 네이티브 구조가 없다)
def test_pdf_table_routed_to_llamaparse(mocker, pdf_with_table_file: Path) -> None:
    fake_result = SimpleNamespace(markdown="| 이름 | 점수 |\n| --- | --- |\n| 홍길동 | 95 |")
    parse = mocker.Mock(return_value=fake_result)
    mocker.patch.object(
        table_llamaparse,
        "_make_client",
        return_value=SimpleNamespace(parsing=SimpleNamespace(parse=parse)),
    )

    config = IngestConfig()
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            llama_cloud_api_key="test-key",
            openai_api_key=None,
            table_pdf_handler="llamaparse",
            image_handler="skip",
            cache_enabled=False,
        ),
    )

    document = PdfLoader(config).load(pdf_with_table_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert table.metadata["table_source"] == "llamaparse"
    assert document.metadata.llamaparse_calls == 1
    # 문서 전체가 아니라 표 영역만 잘라서 보내야 과금 페이지가 줄어든다
    sent = parse.call_args.kwargs["upload_file"]
    assert sent[0].endswith(".pdf")
    assert len(sent[1]) < pdf_with_table_file.stat().st_size * 3


# 텍스트가 거의 없는 페이지는 스캔본으로 보고 이미지 경로로 보내야 한다
def test_blank_page_treated_as_scan(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(str(path))
    document.close()

    parsed = PdfLoader(offline_config).load(path)

    assert any("이미지로 처리" in w for w in parsed.metadata.warnings)


# order 는 문서 전체에 걸쳐 빈틈없이 증가해야 한다
def test_order_is_sequential(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)
    orders = [e.order for e in document.elements]

    assert orders == sorted(orders)
    assert orders == list(range(len(orders)))


# 겹침 판정: 표 영역 안에 완전히 들어간 텍스트 블록은 걸러져야 한다
def test_overlap_detection() -> None:
    table = (0.0, 0.0, 100.0, 100.0)

    assert _overlaps((10.0, 10.0, 50.0, 50.0), table) is True
    assert _overlaps((200.0, 200.0, 250.0, 250.0), table) is False
    # 살짝만 걸치는 블록은 표에 속하지 않은 것으로 본다
    assert _overlaps((90.0, 90.0, 190.0, 190.0), table) is False


# 외부 API 키가 없으면 LlamaParse를 부르지 않아야 한다
def test_no_llamaparse_without_key(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)

    assert document.metadata.llamaparse_calls == 0
