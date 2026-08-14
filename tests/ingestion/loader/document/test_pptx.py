"""PowerPoint 로더 테스트."""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.document.pptx import PptxLoader
from rag_pipeline.schemas.document import ElementType


# 제목·본문·표·노트가 모두 추출되어야 한다
def test_extracts_all_shape_kinds(pptx_file: Path, offline_config: IngestConfig) -> None:
    document = PptxLoader(offline_config).load(pptx_file)
    texts = [e.text for e in document.elements]

    assert "분기 실적" in texts
    assert "매출이 증가했습니다." in texts
    assert any("1분기" in t for t in texts)
    assert "발표자 노트입니다." in texts


# 발표자 노트는 본문과 구분되는 별도 요소 종류여야 한다
def test_notes_are_separate_type(pptx_file: Path, offline_config: IngestConfig) -> None:
    document = PptxLoader(offline_config).load(pptx_file)
    note = next(e for e in document.elements if e.type is ElementType.SLIDE_NOTE)

    assert note.text == "발표자 노트입니다."


# 슬라이드 제목은 TITLE 로 잡혀야 한다
def test_title_detected(pptx_file: Path, offline_config: IngestConfig) -> None:
    document = PptxLoader(offline_config).load(pptx_file)
    title = next(e for e in document.elements if e.type is ElementType.TITLE)

    assert title.text == "분기 실적"


# 모든 요소에 슬라이드 번호가 붙어야 한다
def test_slide_number(pptx_file: Path, offline_config: IngestConfig) -> None:
    document = PptxLoader(offline_config).load(pptx_file)

    assert all(e.page == 1 for e in document.elements)
    assert document.metadata.page_count == 1


# 표는 네이티브 구조로 처리되어야 한다
def test_table_is_native(pptx_file: Path, offline_config: IngestConfig) -> None:
    document = PptxLoader(offline_config).load(pptx_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert table.metadata["table_source"] == "native"
    assert table.metadata["rows"][0] == ["분기", "매출"]
