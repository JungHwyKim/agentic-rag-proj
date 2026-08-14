"""실제 문서로 정리 단계를 검증하는 회귀 테스트.

합성 문서로는 "이 규칙이 실제로 필요한가"를 못 보여준다. PDF 에서만 제어문자가 나오고
HWPX 에서는 0개라는 사실이 이 규칙의 존재 이유이자 안전 근거이므로, 둘 다 잠근다.

tests/fixtures/ 에 실제 파일이 있을 때만 돌고 없으면 조용히 건너뛴다.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.cleaner import clean
from rag_pipeline.ingestion.cleaner.characters import strip_control_chars
from rag_pipeline.ingestion.pipeline import ingest
from rag_pipeline.schemas.document import ParsedDocument

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# 사람이 직접 세어 확인한 값. 이 문서의 제어문자는 U+0001 192 / U+0007 47 / U+0008 4 개다
AI_GUIDE_PDF = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf"
AI_GUIDE_PDF_CLEANED_ELEMENTS = 202
AI_GUIDE_PDF_CLEANED_CELLS = 26


# 그림을 건너뛰는 설정. OCR/Vision 없이 텍스트만 빠르게 확인하기 위함
@pytest.fixture
def structure_only_config() -> IngestConfig:
    config = IngestConfig()
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            openai_api_key=None,
            llama_cloud_api_key=None,
            image_handler="skip",
            cache_enabled=False,
        ),
    )
    return config


# fixtures 안의 파일 하나를 찾는다. 없으면 테스트를 건너뛴다
def _fixture(name: str) -> Path:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"tests/fixtures 에 '{name}' 이 없습니다.")
    return path


# 문서에 남은 제어·서식문자 수 (개행·탭은 정상이므로 뺀다)
def _residual_control_chars(document: ParsedDocument) -> int:
    return sum(
        1
        for element in document.elements
        for ch in element.text
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf")
    )


# PDF 는 글머리 기호를 제어 코드포인트로 떨어뜨린다. 정리 후 하나도 남으면 안 된다
def test_pdf_control_chars_are_gone(structure_only_config: IngestConfig) -> None:
    document = ingest(_fixture(AI_GUIDE_PDF), config=structure_only_config)

    assert _residual_control_chars(document) == 0
    assert document.metadata.cleaned_elements == AI_GUIDE_PDF_CLEANED_ELEMENTS


# 표의 rows 도 함께 정리돼야 text 와 어긋나지 않는다
def test_pdf_table_rows_are_cleaned(structure_only_config: IngestConfig) -> None:
    document = ingest(_fixture(AI_GUIDE_PDF), config=structure_only_config)
    dirty = [
        cell
        for element in document.elements
        for row in element.metadata.get("rows") or []
        for cell in row
        if strip_control_chars(cell) != cell
    ]

    assert dirty == []


# 정리해도 요소를 잃지 않는다. 이 단계는 글자만 고친다
def test_pdf_keeps_every_element(structure_only_config: IngestConfig) -> None:
    path = _fixture(AI_GUIDE_PDF)
    document = ingest(path, config=structure_only_config)

    # clean() 을 한 번 더 태워도 개수·순서가 그대로여야 한다 (멱등)
    again = clean(document, structure_only_config.cleaner)
    assert len(again.elements) == len(document.elements)
    assert [e.order for e in again.elements] == [e.order for e in document.elements]
    assert again is document, "이미 깨끗한 문서는 새 객체를 만들지 않는다"


# HWPX 는 애초에 제어문자가 없다. 멀쩡한 문서를 건드리지 않는다는 증거다
@pytest.mark.parametrize(
    "name",
    [
        "2025년 학교기록물 관리 지침.hwpx",
        "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.hwpx",
        "시행령 개정안 안내서(정보시스템의무사항알림).hwpx",
    ],
)
def test_hwpx_is_left_alone(name: str, structure_only_config: IngestConfig) -> None:
    document = ingest(_fixture(name), config=structure_only_config)

    assert document.metadata.cleaned_elements == 0
    assert _residual_control_chars(document) == 0


# 실측한 rows 셀 변경 수를 잠근다. 로더가 셀 텍스트를 다르게 뽑기 시작하면 여기서 걸린다
def test_pdf_cleaned_cell_count(structure_only_config: IngestConfig) -> None:
    path = _fixture(AI_GUIDE_PDF)
    config = IngestConfig()
    object.__setattr__(config, "_content", structure_only_config.content)
    object.__setattr__(config, "_cleaner", type(config.cleaner)(remove_control_chars=False))

    raw = ingest(path, config=config)
    dirty_cells = sum(
        1
        for element in raw.elements
        for row in element.metadata.get("rows") or []
        for cell in row
        if strip_control_chars(cell) != cell
    )

    assert dirty_cells == AI_GUIDE_PDF_CLEANED_CELLS
