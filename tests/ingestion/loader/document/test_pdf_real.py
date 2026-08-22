"""실제 PDF 로 쪽 경계 처리를 검증하는 회귀 테스트.

합성 문서는 "규칙이 도는가"에만 답한다. **이 규칙이 실제로 필요한가**는 실파일만 보여준다 —
시행령 안내서 24~25쪽에 걸친 67행 표가 그 근거이고, 사용자가 원본과 대조해 잡아낸 결함이다.

기대값은 **같은 문서 HWPX 판과 셀 단위로 대조해** 얻었다(2026-08-21). 상수로 박아 두는 이유는
HWPX 판이 없는 환경에서도 이 규칙이 검증되게 하려는 것이다.

tests/fixtures/ 에 실제 파일이 있을 때만 돌고 없으면 조용히 건너뛴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.pipeline import ingest
from rag_pipeline.schemas.document import ElementType

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

# 이름이 겹친다 — 가로쪽 판 이름이 이 이름을 접두사로 갖는다. 조각이 아니라 전체를 적는다
DECREE = "시행령 개정안 안내서(정보시스템의무사항알림)"

# 24~25쪽에 걸친 표. 사람이 확인한 값이고 HWPX 판 67행과 셀 단위로 맞다
DECREE_SPAN_TABLE_ROWS = 67
DECREE_SPAN_TABLE_PAGES = (24, 25)
# 쪽 경계가 이 셀을 가로질러 24쪽 `업무정보` + 25쪽 `(4)` 로 갈려 있었다.
# 안 갈린 형제 셀이 `업무정보\n(3)` 이므로 이어 붙인 모양도 같아야 한다
DECREE_SPLIT_CELL = "업무정보\n(4)"
DECREE_SPLIT_CELL_ROWS = range(32, 36)


# 파일명 전체로 실제 파일 하나를 찾는다. 없으면 테스트를 건너뛴다
def _find(stem: str) -> Path:
    path = FIXTURES / f"{stem}.pdf"
    if not path.exists():
        pytest.skip(f"tests/fixtures 에 '{stem}.pdf' 가 없습니다.")
    return path


# 그림을 건너뛰는 설정. 표 구조만 빠르게 본다
@pytest.fixture
def structure_only_config() -> IngestConfig:
    config = IngestConfig()
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            openai_api_key=None,
            llama_cloud_api_key=None,
            table_pdf_handler="pymupdf",
            image_handler="skip",
            cache_enabled=False,
        ),
    )
    return config


# 24~25쪽에 걸친 표를 되돌려준다
def _span_table(config: IngestConfig) -> list[list[str]]:
    document = ingest(_find(DECREE), config=config)
    start, end = DECREE_SPAN_TABLE_PAGES
    for element in document.elements:
        if element.type is not ElementType.TABLE:
            continue
        if element.page == start and element.metadata.get("page_end") == end:
            return element.metadata["rows"]
    pytest.fail(f"{start}~{end}쪽에 걸친 표를 못 찾았습니다.")


# 쪽을 가로지르는 표가 하나로 합쳐지고 행 수가 HWPX 판과 같아야 한다.
# 33 + 35 - 되풀이된 헤더 1 = 67 이다
def test_decree_span_table_row_count(structure_only_config: IngestConfig) -> None:
    assert len(_span_table(structure_only_config)) == DECREE_SPAN_TABLE_ROWS


# 쪽 경계가 가로지른 셀이 이어지고, 그 셀이 덮던 네 행이 **모두** 같은 값을 가져야 한다.
# 이어지지 않으면 `(4)` 만 남은 행이 몇 번째 업무정보인지 알 수 없다
def test_decree_split_cell_is_joined(structure_only_config: IngestConfig) -> None:
    rows = _span_table(structure_only_config)
    values = [rows[i][1] for i in DECREE_SPLIT_CELL_ROWS]

    assert values == [DECREE_SPLIT_CELL] * len(DECREE_SPLIT_CELL_ROWS)


# 세로 병합 앵커가 쪽 너머로 이어져야 한다. 25쪽 리전에는 앵커 셀이 없어 첫 열이 통째로 비었다
def test_decree_merge_anchor_carries_across_pages(structure_only_config: IngestConfig) -> None:
    rows = _span_table(structure_only_config)

    assert all(row[0].strip() for row in rows[1:]), "쪽을 넘긴 뒤 첫 열이 비었다"
