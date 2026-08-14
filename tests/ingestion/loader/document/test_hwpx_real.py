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
AI_GUIDE = "신뢰가능_AI"
SCHOOL_GUIDE = "학교기록물"


# 확장자에 맞는 실제 파일 목록. 없으면 빈 리스트
def _real_files(suffix: str) -> list[Path]:
    return sorted(FIXTURES.glob(f"*{suffix}"))


# 파일명 조각으로 실제 파일 하나를 찾는다. 없으면 테스트를 건너뛴다
def _find(fragment: str, suffix: str = ".hwpx") -> Path:
    for path in _real_files(suffix):
        if fragment in path.name:
            return path
    pytest.skip(f"tests/fixtures 에 '{fragment}' {suffix} 파일이 없습니다.")


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
    return [pytest.param(p, id=p.stem[:24]) for p in files]


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
    assert sum(len(e.text) for e in paragraphs) > 5000
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


# 정부 보고서는 상당량의 텍스트가 도형 안 hp:drawText 에 들어있다
def test_ai_guide_captures_text_inside_shapes(structure_only_config: IngestConfig) -> None:
    document = ingest(_find(AI_GUIDE), config=structure_only_config)

    assert "OECD" in document.full_text
    assert "공공기관" in document.full_text


# 표가 많은 문서에서 표 구조가 제대로 복원되어야 한다.
# 앞선 AI 가이드 문서에는 표가 하나도 없어 이 경로가 검증되지 않았다
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
