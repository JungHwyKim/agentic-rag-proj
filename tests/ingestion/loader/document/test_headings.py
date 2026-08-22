"""제목 승격 규칙.

세 규칙이 실측에서 나왔고, 그 셋이 여기 잠긴다 — 본문 크기는 문서별 최빈값이고,
배율이 아니라 부등호로 판정하며, 문단 안에 본문 이하 글자가 섞이면 강조로 본다.
"""

from __future__ import annotations

from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType
from rag_pipeline.ingestion.loader.document.headings import FONT_SIZES_KEY, promote_headings
from rag_pipeline.schemas.document import ElementType


# 글자 크기를 실은 텍스트 리전 하나
def text_region(text: str, sizes: list[float] | None, *, order: int = 0) -> ContentRegion:
    metadata: dict = {"element_type": ElementType.PARAGRAPH}
    if sizes is not None:
        metadata[FONT_SIZES_KEY] = sizes
    return ContentRegion(
        content_type=ContentType.TEXT,
        order=order,
        source_format="hwpx",
        text=text,
        metadata=metadata,
    )


# 리전의 최종 요소 종류
def type_of(region: ContentRegion) -> ElementType:
    return ElementType(region.metadata.get("element_type", ElementType.PARAGRAPH))


# 본문(최빈값)보다 균일하게 큰 문단은 제목이 된다
def test_uniformly_larger_paragraph_is_promoted() -> None:
    regions = [
        text_region("제목", [1600]),
        text_region("본문 1", [1200]),
        text_region("본문 2", [1200]),
        text_region("본문 3", [1200]),
    ]

    promote_headings(regions)

    assert type_of(regions[0]) is ElementType.TITLE
    assert all(type_of(r) is ElementType.PARAGRAPH for r in regions[1:])


# **이 규칙의 존재 이유.** 큰 글씨가 제목이 아니라 강조일 수 있다 —
# 본문 크기 이하 글자가 하나라도 섞이면 승격하지 않는다
def test_mixed_sizes_are_emphasis_not_heading() -> None:
    regions = [
        text_region("작성 김동현 수석", [1200, 1600, 1200]),
        text_region("본문 1", [1200]),
        text_region("본문 2", [1200]),
    ]

    promote_headings(regions)

    assert all(type_of(r) is ElementType.PARAGRAPH for r in regions)


# 반복 머리말은 본문보다 작다. 승격되면 cleaner 의 머리말 제거가 죽는다
def test_smaller_than_body_is_not_promoted() -> None:
    regions = [
        text_region("공공기관 신뢰가능 AI 구현 실용가이드", [810, 880]),
        text_region("본문 1", [950]),
        text_region("본문 2", [950]),
    ]

    promote_headings(regions)

    assert all(type_of(r) is ElementType.PARAGRAPH for r in regions)


# 본문 크기는 문서마다 다르다. 절대 임계값을 쓰면 한쪽 문서에서 통째로 어긋난다
def test_body_size_is_per_document() -> None:
    small = [text_region("제목", [1000]), *[text_region("본문", [900]) for _ in range(3)]]
    large = [text_region("본문", [1000]) for _ in range(3)] + [text_region("제목", [1100])]

    promote_headings(small)
    promote_headings(large)

    assert type_of(small[0]) is ElementType.TITLE
    # 같은 1000 이 이 문서에서는 본문이다
    assert all(type_of(r) is ElementType.PARAGRAPH for r in large[:3])
    assert type_of(large[3]) is ElementType.TITLE


# 배율 임계값을 쓰면 안 된다. 실측으로 제목 1600 / 본문 1500 인 문서가 있다
def test_small_size_gap_still_promotes() -> None:
    regions = [text_region("□ 개요", [1600]), *[text_region("본문", [1500]) for _ in range(5)]]

    promote_headings(regions)

    assert type_of(regions[0]) is ElementType.TITLE


# 여러 크기가 있으면 큰 것부터 1등이다
def test_heading_level_ranks_by_size() -> None:
    regions = [
        text_region("대제목", [2400]),
        text_region("중제목", [1600]),
        text_region("소제목", [1300]),
        *[text_region("본문", [1000]) for _ in range(3)],
    ]

    promote_headings(regions)

    assert [r.metadata.get("heading_level") for r in regions[:3]] == [1, 2, 3]


# 같은 크기의 제목들은 같은 층이다
def test_same_size_shares_level() -> None:
    regions = [
        text_region("가", [1600]),
        text_region("나", [1600]),
        *[text_region("본문", [1000]) for _ in range(3)],
    ]

    promote_headings(regions)

    assert [r.metadata.get("heading_level") for r in regions[:2]] == [1, 1]


# 최빈값이 동률이면 큰 쪽을 본문으로 본다. 작은 쪽을 고르면 같은 빈도의 큰 본문이 통째로
# 제목이 되어 문서가 수백 개 섹션으로 쪼개진다
def test_tie_prefers_larger_as_body() -> None:
    regions = [text_region("A", [1000]), text_region("B", [1200])]

    promote_headings(regions)

    assert all(type_of(r) is ElementType.PARAGRAPH for r in regions)


# 로더가 이미 종류를 정해 보낸 요소(docx 의 제목·목록)는 건드리지 않는다
def test_non_paragraph_is_left_alone() -> None:
    listed = text_region("목록 항목", [2000])
    listed.metadata["element_type"] = ElementType.LIST
    regions = [listed, *[text_region("본문", [1000]) for _ in range(3)]]

    promote_headings(regions)

    assert type_of(regions[0]) is ElementType.LIST


# 표·이미지 리전은 대상이 아니다
def test_non_text_region_is_left_alone() -> None:
    table = ContentRegion(
        content_type=ContentType.TABLE,
        order=0,
        source_format="hwpx",
        rows=[["가"]],
        metadata={FONT_SIZES_KEY: [3000]},
    )
    regions = [table, *[text_region("본문", [1000]) for _ in range(3)]]

    promote_headings(regions)

    assert "element_type" not in table.metadata


# 꺼두면 아무것도 안 바뀐다
def test_disabled_promotes_nothing() -> None:
    regions = [text_region("제목", [1600]), *[text_region("본문", [1000]) for _ in range(3)]]

    promote_headings(regions, enabled=False)

    assert all(type_of(r) is ElementType.PARAGRAPH for r in regions)


# 판정에만 쓰고 버린다. 안 그러면 요소마다 숫자 리스트가 딸려 나가 metadata 가 부푼다
def test_font_sizes_are_always_stripped() -> None:
    regions = [text_region("제목", [1600]), text_region("본문", [1000])]

    promote_headings(regions, enabled=False)

    assert all(FONT_SIZES_KEY not in r.metadata for r in regions)


# 크기를 모르는 문서(글자 크기 개념이 없는 로더)는 조용히 지나간다
def test_regions_without_sizes_are_untouched() -> None:
    regions = [text_region("가", None), text_region("나", None)]

    promote_headings(regions)

    assert all(type_of(r) is ElementType.PARAGRAPH for r in regions)


# 빈 목록이 섞여도 죽지 않는다. 글자 없는 run 만 있는 문단이 그렇다
def test_empty_size_list_is_safe() -> None:
    regions = [text_region("가", []), text_region("나", [1000]), text_region("다", [1000])]

    promote_headings(regions)

    assert type_of(regions[0]) is ElementType.PARAGRAPH
