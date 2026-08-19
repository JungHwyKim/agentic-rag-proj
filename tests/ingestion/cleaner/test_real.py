"""실제 문서로 정리 단계를 검증하는 회귀 테스트.

합성 문서로는 "이 규칙이 실제로 필요한가"를 못 보여준다. PDF 에서만 제어문자가 나오고
HWPX 에서는 0개라는 사실이 이 규칙의 존재 이유이자 안전 근거이므로, 둘 다 잠근다.

tests/fixtures/ 에 실제 파일이 있을 때만 돌고 없으면 조용히 건너뛴다.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from rag_pipeline.config import CleanerConfig, ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.cleaner import clean
from rag_pipeline.ingestion.cleaner.characters import strip_control_chars
from rag_pipeline.ingestion.pipeline import ingest
from rag_pipeline.schemas.document import ParsedDocument

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# 사람이 직접 세어 확인한 값. 이 문서의 제어문자는 U+0001 192 / U+0007 47 / U+0008 4 개다
AI_GUIDE_PDF = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf"
AI_GUIDE_HWPX = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.hwpx"
AI_GUIDE_PDF_CLEANED_ELEMENTS = 202
# 2026-08-19 에 26→32 로 늘었다. PDF 병합 셀을 되풀이로 채우면서, 제어문자가 든 칸이
# 자기 span 만큼 복제됐기 때문이다. 늘어난 6개는 새로 더러워진 칸이 아니라 **같은 칸의 사본**이다
AI_GUIDE_PDF_CLEANED_CELLS = 32

# 같은 문서를 로더는 HWPX 97쪽 / PDF 28쪽으로 센다(쪽 계산 별건). 그런데도 머리말 제거 개수가
# 25 로 같은 것이 '걸친 폭' 판정이 쪽 수 오류에 강하다는 증거다 — 등장 횟수 비율이었다면
# 25.8% 와 89.3% 로 갈려 한쪽이 임계값을 넘거나 못 넘었을 것이다
AI_GUIDE_REMOVED_HEADERS = 25


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


# 문자 규칙만 켠 설정. 제어문자 실측값을 다른 규칙과 섞이지 않게 재려고 쓴다
@pytest.fixture
def chars_only_config(structure_only_config: IngestConfig) -> IngestConfig:
    config = IngestConfig()
    object.__setattr__(config, "_content", structure_only_config.content)
    object.__setattr__(
        config,
        "_cleaner",
        CleanerConfig(normalize_whitespace=False, remove_repeated_headers=False),
    )
    return config


# fixtures 안의 파일 하나를 찾는다. 없으면 테스트를 건너뛴다
def _fixture(name: str) -> Path:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"tests/fixtures 에 '{name}' 이 없습니다.")
    return path


# 표 요소 수. rows 를 가진 요소로 센다
def _table_count(document: ParsedDocument) -> int:
    return sum(1 for e in document.elements if e.metadata.get("rows") is not None)


# 문서에 남은 제어·서식문자 수 (개행·탭은 정상이므로 뺀다)
def _residual_control_chars(document: ParsedDocument) -> int:
    return sum(
        1
        for element in document.elements
        for ch in element.text
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cf")
    )


# PDF 는 글머리 기호를 제어 코드포인트로 떨어뜨린다. 정리 후 하나도 남으면 안 된다.
# 개수는 문자 규칙만 켜고 재야 공백 규칙이 고친 것과 섞이지 않는다
def test_pdf_control_chars_are_gone(chars_only_config: IngestConfig) -> None:
    document = ingest(_fixture(AI_GUIDE_PDF), config=chars_only_config)

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


# 정리는 멱등이어야 한다. 한 번 더 태워도 아무것도 안 바뀌고 새 객체도 안 만든다.
# 원본 객체 반환 규약이 세 층에서 다 지켜져야만 성립하는 성질이라, 이 한 줄이
# "안 바뀌었으면 그대로 돌려준다"를 실제 문서로 잠근다
def test_cleaning_is_idempotent(structure_only_config: IngestConfig) -> None:
    document = ingest(_fixture(AI_GUIDE_PDF), config=structure_only_config)

    again = clean(document, structure_only_config.cleaner)

    assert again is document, "이미 정리된 문서는 새 객체를 만들지 않는다"


# HWPX 는 애초에 제어문자가 없다. 문자 규칙이 멀쩡한 문서를 건드리지 않는다는 증거다
@pytest.mark.parametrize(
    "name",
    [
        "2025년 학교기록물 관리 지침.hwpx",
        AI_GUIDE_HWPX,
        "시행령 개정안 안내서(정보시스템의무사항알림).hwpx",
    ],
)
def test_hwpx_has_no_control_chars(name: str, chars_only_config: IngestConfig) -> None:
    document = ingest(_fixture(name), config=chars_only_config)

    assert document.metadata.cleaned_elements == 0
    assert _residual_control_chars(document) == 0


# 머리말이 없는 문서에서는 아무것도 지우면 안 된다. 오탐 0 을 실제 문서로 잠근다
@pytest.mark.parametrize(
    "name",
    [
        "2025년 학교기록물 관리 지침.hwpx",
        "시행령 개정안 안내서(정보시스템의무사항알림).hwpx",
        "시행령 개정안 안내서(정보시스템의무사항알림)_가로쪽.hwpx",
    ],
)
def test_documents_without_headers_lose_nothing(
    name: str, structure_only_config: IngestConfig
) -> None:
    document = ingest(_fixture(name), config=structure_only_config)

    assert document.metadata.removed_elements == 0
    assert document.metadata.removed_texts == []


# 같은 문서를 HWPX 97쪽 / PDF 28쪽으로 세는데도 제거 개수가 25 로 같아야 한다.
# 이게 '걸친 폭' 판정을 고른 이유이고, 등장 횟수 비율로는 성립하지 않는 성질이다
@pytest.mark.parametrize("name", [AI_GUIDE_HWPX, AI_GUIDE_PDF])
def test_running_header_removed_in_both_formats(
    name: str, structure_only_config: IngestConfig
) -> None:
    document = ingest(_fixture(name), config=structure_only_config)

    assert document.metadata.removed_elements == AI_GUIDE_REMOVED_HEADERS
    assert any("공공기관 신뢰가능 AI" in t for t in document.metadata.removed_texts)

    # 같은 문구가 표지·부제·뒤표지에도 있지만 그건 쪽 첫 요소가 아니라 내용이다.
    # 텍스트만 보고 지웠다면 이 셋까지 사라졌을 것이다 — 위치 조건이 지키는 것이 이거다
    left = [e for e in document.elements if "공공기관 신뢰가능 AI 구현 실용가이드" in e.text]
    assert len(left) == 3
    assert {e.page for e in left} <= {1, 2, 4, 28, 97}


# 머리말을 지우면서 표를 잃으면 안 된다. 표는 판정 대상이 아니다
def test_tables_survive_header_removal(structure_only_config: IngestConfig) -> None:
    path = _fixture(AI_GUIDE_PDF)
    no_headers = IngestConfig()
    object.__setattr__(no_headers, "_content", structure_only_config.content)
    object.__setattr__(no_headers, "_cleaner", CleanerConfig(remove_repeated_headers=False))

    before = ingest(path, config=no_headers)
    after = ingest(path, config=structure_only_config)

    assert _table_count(after) == _table_count(before)


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
