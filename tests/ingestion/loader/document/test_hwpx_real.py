"""실제 한컴 산출물로 검증하는 회귀 테스트.

합성 HWPX는 구조가 단순해서 실제 파일에서만 드러나는 문제를 못 잡는다.
tests/fixtures/ 에 실제 파일이 있을 때만 돌고, 없으면 조용히 건너뛴다.

문서마다 내용이 다르므로 두 층으로 나눈다:
  - 모든 .hwpx 에 공통으로 성립해야 하는 것 (구조·순서·인코딩)
  - 특정 문서에만 해당하는 것 (파일명으로 찾고, 없으면 skip)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.exceptions import UnsupportedFormatError
from rag_pipeline.ingestion.loader.content.base import ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.hwpx import HwpxLoader
from rag_pipeline.ingestion.pipeline import ingest, peek_format
from rag_pipeline.schemas.document import ElementType, FileFormat

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"

# 문서별 테스트에서 쓰는 파일명 조각
# 도형 안 텍스트(hp:drawText) 검증용. **원래 AI가이드였는데 2026-08-20 에 그 문서를
# 실데이터에서 빼면서 옮겼다** — 깨진 도형·표가 많다고 사용자가 판단했다.
# 이 문서에도 drawText 가 22개 있어 같은 경로가 그대로 검증된다(AI가이드는 376개였다)
BASIC_PLAN = "기초학력"
SCHOOL_GUIDE = "학교기록물"
# 이 둘은 이름이 겹친다(원본 이름이 가로쪽 판 이름의 접두사다). _find 가 정확 이름으로
# 갈라내야 하므로 조각이 아니라 파일명 전체를 적는다
DECREE_GUIDE = "시행령 개정안 안내서(정보시스템의무사항알림)"
# 같은 문서를 가로 쪽 섹션이 들어가게 고친 판. 쪽 기하 판정을 검증하는 데 쓴다
DECREE_LANDSCAPE = "시행령 개정안 안내서(정보시스템의무사항알림)_가로쪽"
# 2026-08-23 에 들어온 신뢰가능 AI 가이드의 한컴 변환본. 여기도 조각이 아니라 파일명
# 전체다 — 옛 HWPX 판의 이름이 이 이름의 **접두사**라, 짧은 조각(`신뢰가능`)으로 두면
# _find 가 두 파일을 함께 집는다. 시행령/가로쪽 짝과 같은 상황이다
AI_GUIDE_FIXED = "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드_fixed"

# 시행령 개정안 안내서를 사람이 직접 열어 확인한 쪽 번호. 로더 결과를 여기에 맞춘다
DECREE_TABLE_PAGES = [1, 4, 6, 7, 11, 13, 14, 15, 24, 25, 26]
DECREE_IMAGE_PAGES = [10, 17, 18, 19, 20, 21, 22, 23]
DECREE_PAGE_COUNT = 26


# 확장자에 맞는 실제 파일 목록. 없으면 빈 리스트
def _real_files(suffix: str) -> list[Path]:
    return sorted(FIXTURES.glob(f"*{suffix}"))


# 파일명 조각으로 실제 파일 하나를 찾는다. 없으면 테스트를 건너뛴다.
# 조각이 여러 파일에 걸리면 어느 문서를 검증하는지 모르게 되므로 조용히 첫 번째를 고르지
# 않는다 — 이름이 조각과 정확히 같은 후보가 있으면 그걸 쓰고, 없으면 실패시킨다.
# (실측: "_가로쪽" 판이 들어오면서 "시행령 개정안" 이 두 파일에 걸렸다. 두 문서는 쪽 수가
#  26/28 로 달라, 사람이 확인한 정답이 엉뚱한 문서에 적용될 뻔했다)
# 파일이 없어서 건너뛰는 것(정상)과 어느 파일인지 몰라서 멈추는 것(사람이 개입해야 함)을
# 가르는 게 이 함수의 핵심이다
def _find(fragment: str, suffix: str = ".hwpx") -> Path:
    candidates = [path for path in _real_files(suffix) if fragment in path.name]
    if not candidates:
        pytest.skip(f"tests/fixtures 에 '{fragment}' {suffix} 파일이 없습니다.")
    if len(candidates) == 1:
        return candidates[0]

    exact = [path for path in candidates if path.stem == fragment]
    if len(exact) == 1:
        return exact[0]

    names = ", ".join(path.name for path in candidates)
    pytest.fail(f"'{fragment}' 가 여러 파일에 걸립니다. 조각을 파일명 전체로 좁히세요: {names}")


# 그림을 건너뛰는 설정. OCR/Vision 없이 구조만 빠르게 검증하기 위함
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


# fixtures 안의 모든 .hwpx 를 하나씩 돌리기 위한 파라미터
def _hwpx_params():
    files = _real_files(".hwpx")
    if not files:
        return [pytest.param(None, marks=pytest.mark.skip(reason="실제 .hwpx 파일 없음"))]
    # id 를 자르지 않는다. 앞부분이 같은 파일이 둘 있으면 pytest 가 0/1 을 붙이는데,
    # 그러면 실패한 파일이 어느 것인지 이름으로 짚을 수 없다
    return [pytest.param(p, id=p.stem) for p in files]


# ---------------------------------------------------------------- 공통 검증


# 실제 한컴 파일도 매직바이트로 정확히 갈려야 한다
@pytest.mark.parametrize("path", _hwpx_params())
def test_detected_as_hwpx(path: Path) -> None:
    assert peek_format(path) is FileFormat.HWPX


# 본문이 충분히 추출되고 경고 없이 끝나야 한다
@pytest.mark.parametrize("path", _hwpx_params())
def test_extracts_body_without_warnings(path: Path, structure_only_config: IngestConfig) -> None:
    document = ingest(path, config=structure_only_config)
    paragraphs = [e for e in document.elements if e.type is ElementType.PARAGRAPH]

    assert len(paragraphs) > 100
    # 글자수는 **모든 요소**에서 센다. 문단만 세면 표 위주 문서를 파싱 실패로 오인한다 —
    # 2026 중등 기초학력 계획은 본문 27,431자 중 표가 21,504자(82%)여서 문단만 보면 4,653자다
    # (실측 2026-08-19). "본문을 뽑았는가" 를 묻는 자리이므로 표도 본문이다
    assert sum(len(e.text) for e in document.elements) > 5000
    assert document.metadata.warnings == []


# 한국어가 깨지지 않아야 한다
@pytest.mark.parametrize("path", _hwpx_params())
def test_korean_not_mojibake(path: Path, structure_only_config: IngestConfig) -> None:
    text = ingest(path, config=structure_only_config).full_text

    assert "�" not in text  # 디코딩 실패 대체 문자
    assert any("가" <= ch <= "힣" for ch in text)


# 리전 order 는 문서 순서대로 빈틈없이 증가해야 재조립이 안전하다
@pytest.mark.parametrize("path", _hwpx_params())
def test_region_order_is_sequential(path: Path, structure_only_config: IngestConfig) -> None:
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(path, LoadContext(config=structure_only_config.content))

    assert [r.order for r in regions] == list(range(len(regions)))


# BinData 그림이 이미지 리전으로 잡혀야 한다.
# 본문 XML에는 바이트가 없고 binaryItemIDRef 로 참조만 되어 있어 놓치기 쉽다
@pytest.mark.parametrize("path", _hwpx_params())
def test_embedded_images_extracted(path: Path, structure_only_config: IngestConfig) -> None:
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(path, LoadContext(config=structure_only_config.content))
    images = [r for r in regions if r.content_type is ContentType.IMAGE]

    assert images, "BinData 그림이 하나도 추출되지 않았습니다"
    assert all(r.blob for r in images)
    assert all(r.metadata.get("binary_item") for r in images)


# ---------------------------------------------------------------- 문서별 검증


# 정부 보고서는 상당량의 텍스트가 도형 안 hp:drawText 에 들어있다.
# 아래 두 문구는 이 문서에서 **drawText 안에만** 있다(실측: 안 2회 / 밖 0회) —
# 도형을 안 훑으면 목차와 장 제목이 통째로 사라진다
def test_shapes_carry_section_headings(structure_only_config: IngestConfig) -> None:
    document = ingest(_find(BASIC_PLAN), config=structure_only_config)

    assert "다각적 현장 지원 방안" in document.full_text
    assert "기대효과" in document.full_text


# 표가 많은 문서에서 표 구조가 제대로 복원되어야 한다
def test_school_guide_parses_tables(structure_only_config: IngestConfig) -> None:
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(
        _find(SCHOOL_GUIDE), LoadContext(config=structure_only_config.content)
    )
    tables = [r for r in regions if r.content_type is ContentType.TABLE]

    assert len(tables) > 30
    assert all(r.rows for r in tables)
    # 셀에 실제 내용이 들어있어야 한다
    assert any(any(any(c.strip() for c in row) for row in (r.rows or [])) for r in tables)


# 표 셀 안의 그림도 빠짐없이 나와야 한다.
# 이 문서는 그림 20개 중 18개가 표 안에 있어서, 표에서 리턴해버리면 대부분이 사라진다
def test_school_guide_finds_images_inside_tables(structure_only_config: IngestConfig) -> None:
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(
        _find(SCHOOL_GUIDE), LoadContext(config=structure_only_config.content)
    )
    images = [r for r in regions if r.content_type is ContentType.IMAGE]

    assert len(images) == 20


# 배치 정보가 있는 문서에서는 쪽 번호가 매겨져야 한다
def test_school_guide_assigns_page_numbers(structure_only_config: IngestConfig) -> None:
    document = ingest(_find(SCHOOL_GUIDE), config=structure_only_config)
    pages = [e.page for e in document.elements if e.page is not None]

    assert document.metadata.page_count is not None
    assert document.metadata.page_count > 30
    assert pages == sorted(pages), "쪽 번호가 문서 순서대로 증가해야 한다"
    assert min(pages) == 1


# 표·그림이 실제로 놓인 쪽과 로더가 매긴 쪽이 맞아야 한다.
# 사람이 문서를 열어 확인한 목록이 기준이다. 쪽을 넘기는 표(43행·67행) 2개를
# 한 쪽으로 세던 시절에는 그 뒤로 전부 밀려서 이 목록과 어긋났다
def test_decree_guide_page_numbers_match_manual_check(
    structure_only_config: IngestConfig,
) -> None:
    path = _find(DECREE_GUIDE)
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(path, LoadContext(config=structure_only_config.content))

    table_pages: set[int] = set()
    for region in regions:
        if region.content_type is ContentType.TABLE and region.page is not None:
            # 여러 쪽에 걸친 표는 걸친 쪽을 모두 차지한 것으로 본다
            end = region.metadata.get("page_end", region.page)
            table_pages.update(range(region.page, end + 1))

    image_pages = {r.page for r in regions if r.content_type is ContentType.IMAGE}

    assert loader.extract_metadata(path)["page_count"] == DECREE_PAGE_COUNT
    assert sorted(table_pages) == DECREE_TABLE_PAGES
    assert sorted(image_pages) == DECREE_IMAGE_PAGES


# 제목·인용을 그린 껍데기 표는 표로 남으면 안 된다.
# 이 문서는 표 27개 중 17개가 껍데기였고, 그대로 두면 "Ⅰ. 개 요" 같은 제목이
# 마크다운 표 문법 안에 갇혀 검색·임베딩 품질이 떨어진다
def test_decree_guide_layout_boxes_demoted(structure_only_config: IngestConfig) -> None:
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(
        _find(DECREE_GUIDE), LoadContext(config=structure_only_config.content)
    )
    tables = [r for r in regions if r.content_type is ContentType.TABLE]
    demoted = [r for r in regions if r.metadata.get("layout_table")]

    assert len(tables) == 9
    assert len(demoted) == 17
    # 남은 표는 전부 실제 데이터 표라 내용이 든 칸이 넉넉하다
    assert all(sum(1 for row in (r.rows or []) for c in row if c.strip()) >= 9 for r in tables)
    # 제목이 표 문법에 갇히지 않고 본문으로 나온다
    assert any(r.text == "Ⅰ. 개 요" for r in demoted)


# 쪽을 넘긴 표는 끝 쪽을 함께 남겨야 뒷부분을 인용할 때 쪽이 맞는다
def test_decree_guide_records_spanning_tables(structure_only_config: IngestConfig) -> None:
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(
        _find(DECREE_GUIDE), LoadContext(config=structure_only_config.content)
    )
    spans = [
        (r.page, r.metadata["page_end"]) for r in regions if r.metadata.get("page_end") is not None
    ]

    assert spans == [(13, 14), (24, 25)]


# 가로 섹션은 세로 여유를 height 가 아니라 width 에서 얻어야 한다.
# 한컴은 가로 쪽에서도 width/height 에 물리 용지(세로) 치수를 그대로 넣고 방향은
# landscape 속성으로만 표시하기 때문이다. 이 문서는 가로 섹션의 표가 작아서 쪽이
# 실제로 밀리지는 않지만, NARROWLY/WIDELY 의 의미가 뒤집히면 여기서 잡힌다
def test_decree_landscape_section_uses_width(structure_only_config: IngestConfig) -> None:
    import zipfile

    from lxml import etree

    from rag_pipeline.ingestion.loader.document.hwpx import _usable_body_height

    with zipfile.ZipFile(_find(DECREE_LANDSCAPE)) as zf:
        sections = {
            name: etree.fromstring(zf.read(name))
            for name in zf.namelist()
            if name.startswith("Contents/section")
        }

    geometry = {
        root.find(".//{*}pagePr").get("landscape"): _usable_body_height(root)
        for root in sections.values()
    }

    # 세로(WIDELY)는 84188-12753, 가로(NARROWLY)는 59528-12753 이어야 한다
    assert geometry == {"WIDELY": 71435, "NARROWLY": 46775}


# 한컴 변환본은 본문을 통째로 도형으로 만들어 놓았다. **나쁜 상태를 일부러 잠근다.**
#
# 이 파일은 `hp:tbl` 이 **0개**다 — 표가 강등된 것이 아니라 문서에 없다(`hp:drawText` 가
# 옛 판 376 → 860 개, 섹션 14 → 1 개). 그리고 `vertpos` 888개가 **전부 0** 이라 쪽 계산
# 휴리스틱이 한 번도 발화하지 못해 쪽이 1로 붕괴한다.
#
# 값을 잠그는 이유는 `_is_layout_box` 나 쪽 계산을 손댈 때 **이 테스트가 먼저 울리게**
# 하려는 것이다. 그래야 조용한 변화가 '의도한 변화'로 불린다 — 표 9·강등 17 을 잠그는
# test_decree_guide_layout_boxes_demoted 와 같은 모양이다
def test_ai_guide_fixed_has_no_tables_and_collapses_to_one_page(
    structure_only_config: IngestConfig,
) -> None:
    path = _find(AI_GUIDE_FIXED)
    loader = HwpxLoader(structure_only_config)
    regions = loader.detect_regions(path, LoadContext(config=structure_only_config.content))

    # 둘 다 0 이어야 "강등됐다"와 "처음부터 없다"의 구분이 남는다
    assert [r for r in regions if r.content_type is ContentType.TABLE] == []
    assert [r for r in regions if r.metadata.get("layout_table")] == []

    # 쪽이 1로 붕괴한다. 쪽을 넘긴 리전이 있을 수 없는 상태다
    assert loader.extract_metadata(path)["page_count"] == 1
    assert {r.page for r in regions} == {1}
    assert all(r.metadata.get("page_end") is None for r in regions)


# 쪽이 1로 붕괴하면 반복 머리말 제거가 통째로 죽는다. 그 대가를 수치로 남긴다.
# cleaner/headers.py 는 '걸친 폭'으로 판정하는데 걸칠 쪽이 하나뿐이기 때문이다 —
# 같은 문서의 옛 HWPX 판(97쪽)과 PDF 판(28쪽)은 똑같이 25개를 지운다
def test_ai_guide_fixed_cannot_remove_running_headers(
    structure_only_config: IngestConfig,
) -> None:
    document = ingest(_find(AI_GUIDE_FIXED), config=structure_only_config)
    texts = [e.text.strip() for e in document.elements]

    assert document.metadata.removed_elements == 0
    assert texts.count("공공기관 신뢰가능 AI 구현 실용가이드") == 28
    assert texts.count("DNA플러스 2019") == 27


# ---------------------------------------------------------------- .hwp 거부


# 실제 .hwp 는 HWPX 변환 안내와 함께 거부되어야 한다
def test_real_hwp_rejected_with_guidance(structure_only_config: IngestConfig) -> None:
    files = _real_files(".hwp")
    if not files:
        pytest.skip("tests/fixtures 에 실제 .hwp 파일이 없습니다.")

    with pytest.raises(UnsupportedFormatError) as info:
        ingest(files[0], config=structure_only_config)

    assert info.value.detected == "hwp"
    assert "HWPX" in info.value.user_message
