"""OCR 설정 테스트 — 한글 인식 품질에 직결되는 옵션들."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content.image_ocr import (
    build_tesseract_config,
    extract_text,
    preprocess,
    run_ocr,
)


# 기본 옵션에는 PSM 6 과 띄어쓰기 보존이 함께 들어가야 한다
def test_default_tesseract_options() -> None:
    options = build_tesseract_config(ContentRoutingConfig())

    assert "--psm 6" in options
    assert "preserve_interword_spaces=1" in options


# 띄어쓰기 보존은 끌 수 있어야 한다
def test_preserve_spaces_can_be_disabled() -> None:
    options = build_tesseract_config(ContentRoutingConfig(ocr_preserve_spaces=False))

    assert "preserve_interword_spaces" not in options


# 최종 텍스트에서 한글이 음절 단위로 쪼개지지 않아야 한다.
# "대한민국"이 "대 한 민 국"으로 나오면 임베딩·검색이 통째로 망가진다
def test_korean_words_not_split_into_syllables(korean_image_file: Path) -> None:
    with Image.open(korean_image_file) as image:
        text = extract_text(image, ContentRoutingConfig())

    assert "대한민국" in text
    assert "대 한 민 국" not in text


# run_ocr 의 text 는 판정용이라 음절이 쪼개져 있어도 된다.
# 최종 텍스트는 extract_text 가 만든다는 역할 분리를 명시적으로 못박는다
def test_run_ocr_is_metrics_only(korean_image_file: Path) -> None:
    with Image.open(korean_image_file) as image:
        metrics = run_ocr(image, ContentRoutingConfig())
        text = extract_text(image, ContentRoutingConfig())

    assert metrics.char_count > 0
    assert len(text.replace("\n", "")) <= metrics.char_count


# 인식 신뢰도가 함께 나와야 classify가 분기할 수 있다
def test_reports_confidence(korean_image_file: Path) -> None:
    with Image.open(korean_image_file) as image:
        result = run_ocr(image, ContentRoutingConfig())

    assert result.char_count > 0
    assert 0 < result.mean_confidence <= 100


# 이미 충분히 큰 이미지는 확대하지 않는다. 미세한 업스케일은 오히려 인식률을 떨어뜨린다
def test_large_image_not_upscaled() -> None:
    image = Image.new("RGB", (900, 300), "white")

    assert preprocess(image).size == (900, 300)


# 작은 이미지는 2배로 확대해 글리프를 또렷하게 만든다
def test_small_image_upscaled() -> None:
    image = Image.new("RGB", (300, 100), "white")

    assert preprocess(image).size == (600, 200)


# 전처리는 그레이스케일로 변환한다
def test_preprocess_converts_to_grayscale() -> None:
    assert preprocess(Image.new("RGB", (900, 300), "white")).mode == "L"
