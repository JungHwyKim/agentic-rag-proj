"""이미지 유형 판별 테스트 — 글자 위주면 OCR, 그림 위주면 Vision."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content.classify import ImageRoute, classify, is_too_small


# 글자가 큼직하게 들어간 이미지는 OCR로 충분하다 (Vision 비용을 아낀다)
def test_text_heavy_image_goes_to_ocr(korean_image_file: Path) -> None:
    config = ContentRoutingConfig(image_handler="hybrid", cache_enabled=False)

    with Image.open(korean_image_file) as image:
        decision = classify(image, config)

    assert decision.route is ImageRoute.OCR
    assert decision.ocr is not None
    assert decision.ocr.char_count > 0


# 글자가 거의 없는 도표는 Vision으로 보내야 의미를 뽑을 수 있다
def test_chart_like_image_goes_to_vision(chart_like_image_file: Path) -> None:
    config = ContentRoutingConfig(image_handler="hybrid", cache_enabled=False)

    with Image.open(chart_like_image_file) as image:
        decision = classify(image, config)

    assert decision.route is ImageRoute.VISION


# 로고·아이콘 크기는 건너뛴다
def test_tiny_image_skipped(tiny_image_file: Path) -> None:
    config = ContentRoutingConfig(image_handler="hybrid", cache_enabled=False)

    with Image.open(tiny_image_file) as image:
        decision = classify(image, config)

    assert decision.route is ImageRoute.SKIP
    assert "로고" in decision.reason


# 강제 모드는 판정을 건너뛴다
def test_forced_modes(chart_like_image_file: Path) -> None:
    with Image.open(chart_like_image_file) as image:
        assert classify(image, ContentRoutingConfig(image_handler="ocr")).route is ImageRoute.OCR
        assert (
            classify(image, ContentRoutingConfig(image_handler="vision")).route is ImageRoute.VISION
        )
        assert classify(image, ContentRoutingConfig(image_handler="skip")).route is ImageRoute.SKIP


# 크기 임계값은 설정으로 조절 가능해야 한다
def test_min_size_is_configurable(tiny_image_file: Path) -> None:
    lenient = ContentRoutingConfig(min_image_width=10, min_image_height=10)

    with Image.open(tiny_image_file) as image:
        assert is_too_small(image, ContentRoutingConfig()) is True
        assert is_too_small(image, lenient) is False


# 크기 필터는 강제 모드보다 우선한다.
# 실제 문서에는 5x5 짜리 장식용 점이 수십 개 들어있어서, 강제 모드에서 필터를 건너뛰면
# 쓰레기 텍스트만 쌓이고 시간·비용이 날아간다 (정부 보고서 실측: 그림 28개 중 17개가 장식)
@pytest.mark.parametrize("mode", ["ocr", "vision", "hybrid"])
def test_size_filter_applies_to_every_mode(mode: str, tiny_image_file: Path) -> None:
    with Image.open(tiny_image_file) as image:
        decision = classify(image, ContentRoutingConfig(image_handler=mode))

    assert decision.route is ImageRoute.SKIP


# --- 쪽 위 표시 크기 필터 ---
#
# 저장 픽셀 기준과 이 기준은 서로를 대신하지 못한다. 아래 크기들은 실측에서 나온
# 값이고, 근거는 .claude/wiki/plan_log/이미지-경로.md 2회차다.


# 반복 장식 글리프. 저장 픽셀은 필터를 통과하지만 쪽 위에서는 13.6x15.2pt 다
def test_tiny_display_glyph_is_skipped(png_bytes) -> None:
    image = Image.open(io.BytesIO(png_bytes(115, 127)))

    decision = classify(image, ContentRoutingConfig(), bbox=(0.0, 0.0, 13.6, 15.2))

    assert decision.route is ImageRoute.SKIP
    assert "짧은 변" in decision.reason


# 6쪽에 반복되는 제목 띠. 짧은 변은 20pt 를 넘지만 14.8:1 로 납작하다.
# 크기만 보는 규칙으로는 이것이 그대로 살아남는다.
# 저장은 900x150px 이라 픽셀 필터에도 안 걸린다 — 두 기준이 갈리는 자리다
def test_wide_banner_is_skipped_by_aspect_ratio(png_bytes) -> None:
    image = Image.open(io.BytesIO(png_bytes(900, 150)))

    decision = classify(image, ContentRoutingConfig(), bbox=(0.0, 0.0, 454.4, 30.8))

    assert decision.route is ImageRoute.SKIP
    assert "가로세로비" in decision.reason


# 145.2x41.4pt 는 '더 특 별 한 / 학 생 중 심 미 래 교 육' 33자를 내놓는 실제 문구다.
# 1회차의 탐침값 50pt 로 자르면 이것이 날아간다 — 그래서 20pt 로 내렸다
def test_small_but_real_text_survives(png_bytes) -> None:
    image = Image.open(io.BytesIO(png_bytes(400, 120)))

    decision = classify(
        image, ContentRoutingConfig(image_handler="ocr"), bbox=(0.0, 0.0, 145.2, 41.4)
    )

    assert decision.route is ImageRoute.OCR


# 표시가 작아도 저장 해상도가 크면 실제 그림으로 본다(59.8x59.0pt / 2269x2481px).
# 비율이 1.01:1 이고 짧은 변이 20pt 를 넘어 두 신호 모두 안 걸린다
def test_small_square_high_resolution_image_survives(png_bytes) -> None:
    image = Image.open(io.BytesIO(png_bytes(600, 640)))

    decision = classify(
        image, ContentRoutingConfig(image_handler="ocr"), bbox=(0.0, 0.0, 59.8, 59.0)
    )

    assert decision.route is ImageRoute.OCR


# HWPX 이미지 리전에는 bbox 가 없다. 잴 수 없으면 판정하지 않는다 —
# 여기서 SKIP 하면 한컴 문서의 그림이 통째로 사라진다
def test_missing_bbox_is_not_judged(png_bytes) -> None:
    image = Image.open(io.BytesIO(png_bytes(400, 300)))

    assert classify(image, ContentRoutingConfig(image_handler="ocr"), bbox=None).route is (
        ImageRoute.OCR
    )


# 강제 모드라고 건너뛰면 안 된다. 픽셀 필터와 같은 이유다
@pytest.mark.parametrize("mode", ["ocr", "vision"])
def test_display_filter_applies_in_forced_modes(png_bytes, mode) -> None:
    image = Image.open(io.BytesIO(png_bytes(115, 127)))

    decision = classify(
        image, ContentRoutingConfig(image_handler=mode), bbox=(0.0, 0.0, 13.6, 15.2)
    )

    assert decision.route is ImageRoute.SKIP
