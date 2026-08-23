"""실제 문서로 청킹을 검증하는 회귀 테스트.

합성 문서로는 "이 단계가 왜 자르기가 아니라 병합인가"를 보여줄 수 없다. 요소 자수 중앙값이
15~40자이고 1200자를 넘는 텍스트 요소가 한 개도 없다는 사실이 이 설계의 근거이자 안전
근거이므로, 실제 문서에서 잠근다.

tests/fixtures/ 에 실제 파일이 있을 때만 돌고 없으면 조용히 건너뛴다.

설정은 전부 명시해서 넘긴다. 개발자의 .env 가 상한이나 승격 여부를 덮으면 기대값이 흔들린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.config import ContentRoutingConfig, IngestConfig, SplitterConfig
from rag_pipeline.ingestion.pipeline import ingest
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.schemas.document import ElementType

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

AI_GUIDE_PDF = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf"
# 2026-08-23 에 들어온 한컴 변환본. 2026-08-20 에 뺀 옛 HWPX 판을 대신한다.
# **옛 값(요소 721 / 제목 54 / 청크 47)과 대조하지 말 것 — 다른 파일이다.**
# 옛 판도 fixtures 에 남아 있지만 cleaner/test_real.py 의 머리말 테스트 전용이다
AI_GUIDE_HWPX_FIXED = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드_fixed.hwpx"
DECREE_HWPX = "시행령 개정안 안내서(정보시스템의무사항알림).hwpx"

# 사람이 결과를 눈으로 확인한 값이다. 요소 -> 청크로 얼마나 뭉쳐지는지가 이 단계의 핵심이라
# 양쪽을 함께 잠근다. 승격된 제목 수가 곧 섹션 경계 수다
EXPECTED = {
    AI_GUIDE_PDF: {"elements": 509, "titles": 74, "chunks": 70},
    # 변환본은 표가 0개라 청크가 전부 본문이다. PDF 판과 청크 수가 68 / 70 으로 비슷해
    # 보이지만 구성이 다르다 — 저쪽 70 에는 표 청크 10개가 들어 있다.
    #
    # **이 문서만 잔챙이 청크 상한을 따로 준다**(8, 기본 10% 규칙으로는 6). 느슨하게 푸는
    # 것이 아니라 **알려진 결함의 대가를 수치로 잠그는 것**이다 — 8개 중 7개가 이 파일의
    # 두 결함에서 곧바로 나온다: 3개는 쪽이 1로 붕괴해 cleaner 가 못 지운 머리말·꼬리말
    # (`공공기관 신뢰가능 AI 구현 실용가이드` / `DNA플러스 2019`)이고, 4개는 [붙임]의
    # 위험도 2x2 상자가 표가 아니라 각각 별개 도형이라 따로 떨어진 것이다.
    # 옛 HWPX 판은 0/47 · PDF 판은 6/70 이라 **이 문서의 변환본에서만 넘친다**
    AI_GUIDE_HWPX_FIXED: {"elements": 856, "titles": 99, "chunks": 68, "tiny_max": 8},
    DECREE_HWPX: {"elements": 285, "titles": 27, "chunks": 47},
}

CAP = 1200


# 그림을 건너뛰고 표도 로컬로만 파싱하는 설정. 외부 API 를 절대 부르지 않는다
@pytest.fixture
def offline_ingest_config() -> IngestConfig:
    config = IngestConfig(detect_headings=True)
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


# 기대값을 잰 그대로의 청킹 설정
@pytest.fixture
def splitter_config() -> SplitterConfig:
    return SplitterConfig(
        max_chunk_chars=CAP, chunk_overlap_chars=150, include_heading_in_text=True
    )


# 파일이 있으면 파싱해서 돌려주고, 없으면 건너뛴다
def _chunks(name: str, ingest_config: IngestConfig, splitter_config: SplitterConfig):
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} 이(가) 없어 건너뜁니다.")
    document = ingest(path, config=ingest_config)
    return document, split(document, splitter_config)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_element_and_chunk_counts(
    name: str, offline_ingest_config: IngestConfig, splitter_config: SplitterConfig
) -> None:
    document, chunks = _chunks(name, offline_ingest_config, splitter_config)
    titles = [e for e in document.elements if e.type is ElementType.TITLE]

    assert len(document.elements) == EXPECTED[name]["elements"]
    assert len(titles) == EXPECTED[name]["titles"]
    assert len(chunks) == EXPECTED[name]["chunks"]


# 상한은 제목 머리를 붙인 **뒤에도** 지켜져야 한다. 붙이고 나서 넘기면 상한이 거짓이 된다
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_chunk_exceeds_cap(
    name: str, offline_ingest_config: IngestConfig, splitter_config: SplitterConfig
) -> None:
    _, chunks = _chunks(name, offline_ingest_config, splitter_config)

    assert [c.chunk_index for c in chunks if len(c.text) > CAP] == []


# 병합이 실제로 일어났는지 본다. 요소를 그대로 청크로 만들면 PDF 는 절반이 20자 미만이다
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_merging_removes_tiny_chunks(
    name: str, offline_ingest_config: IngestConfig, splitter_config: SplitterConfig
) -> None:
    document, chunks = _chunks(name, offline_ingest_config, splitter_config)

    tiny = [c for c in chunks if len(c.text) < 100]
    # 기본은 청크의 10% 다. 문서별 상한은 EXPECTED 에 사유와 함께 적었을 때만 쓴다 —
    # 규칙을 통째로 낮추면 이 검사가 다른 문서에서도 안 울린다
    ceiling = EXPECTED[name].get("tiny_max", len(chunks) // 10)
    assert len(chunks) < len(document.elements) / 3
    assert len(tiny) <= ceiling


# 재적재마다 id 가 바뀌면 색인이 중복된다
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_chunk_ids_are_stable_and_unique(
    name: str, offline_ingest_config: IngestConfig, splitter_config: SplitterConfig
) -> None:
    document, chunks = _chunks(name, offline_ingest_config, splitter_config)
    again = split(document, splitter_config)

    assert [c.chunk_id for c in chunks] == [c.chunk_id for c in again]
    assert len({c.chunk_id for c in chunks}) == len(chunks)


# 쪼갠 표 조각은 모두 헤더 행을 갖고 있어야 한다. 행 하나만 검색됐을 때 열 이름이 없으면
# 숫자만 남아 의미를 복원할 수 없다
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_split_table_pieces_keep_header(
    name: str, offline_ingest_config: IngestConfig, splitter_config: SplitterConfig
) -> None:
    _, chunks = _chunks(name, offline_ingest_config, splitter_config)

    # 같은 원본 표에서 나온 조각들끼리 모은다
    by_source: dict[int, list] = {}
    for chunk in chunks:
        if chunk.type.value == "table" and chunk.metadata.get("part_count"):
            by_source.setdefault(chunk.element_orders[0], []).append(chunk)

    if not by_source:
        pytest.skip(f"{name} 에는 쪼개진 표가 없습니다.")

    for pieces in by_source.values():
        headers = {tuple(c.metadata["rows"][0]) for c in pieces}
        # 조각이 몇 개로 갈리든 헤더 행은 하나여야 한다
        assert len(headers) == 1
        assert all(len(c.metadata["rows"]) >= 2 for c in pieces)
