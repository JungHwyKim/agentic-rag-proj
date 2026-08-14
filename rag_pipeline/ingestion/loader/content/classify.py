"""이미지가 '글자 위주'인지 '그림 위주'인지 판별해 OCR과 Vision 중 하나로 보낸다.

Tesseract를 먼저 돌리는 게 낭비처럼 보이지만, 로컬 실행이라 밀리초 단위이고
결과가 좋으면 그대로 채택하므로 오히려 Vision 호출을 아낀다. 200페이지 스캔본을
전부 Vision에 보내면 비용과 시간이 감당이 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PIL import Image

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content.image_ocr import OcrResult, run_ocr


# 이미지를 어느 핸들러로 보낼지에 대한 판정 결과
class ImageRoute(StrEnum):
    OCR = "ocr"
    VISION = "vision"
    SKIP = "skip"


# 판정 결과와 그 근거. OCR을 이미 돌렸다면 결과를 재사용하도록 함께 돌려준다
@dataclass
class Decision:
    route: ImageRoute
    reason: str
    ocr: OcrResult | None = None


# 로고·아이콘·구분선처럼 내용이 없는 작은 이미지인지 판단
def is_too_small(image: Image.Image, config: ContentRoutingConfig) -> bool:
    return image.width < config.min_image_width or image.height < config.min_image_height


# 이미지를 어떤 핸들러로 보낼지 결정한다
def classify(image: Image.Image, config: ContentRoutingConfig) -> Decision:
    if config.image_handler == "skip":
        return Decision(ImageRoute.SKIP, "image_handler=skip")

    # 크기 필터는 핸들러 선택과 무관하게 항상 먼저 건다.
    # 실제 문서에는 5x5 짜리 장식용 점이 수십 개씩 들어있어서(정부 보고서 실측: 28개 중 17개),
    # 강제 모드라고 필터를 건너뛰면 쓰레기 텍스트가 쌓이고 시간·비용만 날아간다
    if is_too_small(image, config):
        return Decision(
            ImageRoute.SKIP,
            f"{image.width}x{image.height} < "
            f"{config.min_image_width}x{config.min_image_height} (로고·아이콘 추정)",
        )

    # 강제 모드는 내용 판정 없이 바로 결정
    if config.image_handler == "ocr":
        return Decision(ImageRoute.OCR, "image_handler=ocr")
    if config.image_handler == "vision":
        return Decision(ImageRoute.VISION, "image_handler=vision")

    # hybrid: OCR을 먼저 돌려보고 글자가 충분히 잡히면 그 결과를 그대로 쓴다
    ocr = run_ocr(image, config)
    text_rich = ocr.char_count >= config.ocr_min_chars
    confident = ocr.mean_confidence >= config.ocr_min_confidence

    if text_rich and confident:
        return Decision(
            ImageRoute.OCR,
            f"글자 {ocr.char_count}자, 신뢰도 {ocr.mean_confidence:.0f} — 글자 위주로 판정",
            ocr=ocr,
        )

    return Decision(
        ImageRoute.VISION,
        f"글자 {ocr.char_count}자, 신뢰도 {ocr.mean_confidence:.0f} — 그림 위주로 판정",
        ocr=ocr,
    )
