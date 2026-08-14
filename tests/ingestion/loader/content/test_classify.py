"""이미지 유형 판별 테스트 — 글자 위주면 OCR, 그림 위주면 Vision."""

from __future__ import annotations

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
