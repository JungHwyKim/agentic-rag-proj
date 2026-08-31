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
from rag_pipeline.ingestion.loader.content.base import LoadContext
from rag_pipeline.ingestion.loader.document.pdf import PdfLoader
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

# AI가이드 PDF 12~17쪽 표. 체크 표시가 벡터 드로잉이라 `extract()` 로는 원리적으로 못 얻는다
AI_GUIDE = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드"
# 기하가 잡는 표시 자리 수. 73 이던 것이 격자 구멍까지 넓히며 17쪽에서 하나 늘었다
AI_GUIDE_MARK_CELLS = 74
# 그 74칸이 전부 **같은 벡터 경로**다. Vision 을 서명당 한 번만 부르는 근거다
AI_GUIDE_MARK_SIGNATURES = 1
# 17쪽 표의 행 라벨. 표 bbox 안(y=229.7)인데 `find_tables()` 가 그 자리에 셀 사각형을 안 내
# 어느 청크에도 안 실리던 것이다(.claude/wiki/plan_log/표-경로.md 9회차 미결, 14회차에서 닫힘)
AI_GUIDE_LOST_ROW_LABEL = "책임성"
AI_GUIDE_LOST_LABEL_PAGE = 17


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


# --- 격자 구멍 (AI가이드 12~17쪽) ---
#
# 합성 픽스처는 "규칙이 도는가"에 답하지만, **`find_tables()` 가 실제로 셀 사각형을 빠뜨린다**
# 는 것은 실파일만 보여준다. 아래 둘이 그 자리를 잠근다


# 표시 자리는 기하가 무과금으로 정확히 안다. 격자 구멍까지 넓혀 73 → 74 다
def test_ai_guide_mark_cells_include_grid_hole(structure_only_config: IngestConfig) -> None:
    path = _find(AI_GUIDE)
    ctx = LoadContext(config=structure_only_config.content)
    regions = PdfLoader(structure_only_config).detect_regions(path, ctx)

    marks = sum(len(region.mark_cells) for region in regions)
    signatures = {region.mark_signature for region in regions if region.mark_signature}

    assert marks == AI_GUIDE_MARK_CELLS
    # 서명이 하나라는 것이 "서명당 한 번"이 이 문서에서 호출 1회가 되는 근거다
    assert len(signatures) == AI_GUIDE_MARK_SIGNATURES


# 격자 구멍에 있던 행 라벨이 표 안으로 돌아와야 한다. 없으면 어느 청크에도 안 실린다
def test_ai_guide_recovers_lost_row_label(structure_only_config: IngestConfig) -> None:
    document = ingest(_find(AI_GUIDE), config=structure_only_config)

    labels = [
        element
        for element in document.elements
        if element.type is ElementType.TABLE
        and element.page == AI_GUIDE_LOST_LABEL_PAGE
        and any(
            cell.strip() == AI_GUIDE_LOST_ROW_LABEL
            for row in element.metadata.get("rows", [])
            for cell in row
        )
    ]

    assert labels, f"{AI_GUIDE_LOST_LABEL_PAGE}쪽 표에 '{AI_GUIDE_LOST_ROW_LABEL}' 이 없다"
