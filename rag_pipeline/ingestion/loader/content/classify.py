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


# 쪽 위 **표시 크기**로 장식인지 본다. 버릴 이유를 문자열로, 남길 것이면 None.
#
# 위의 픽셀 자[尺]와 서로를 대신하지 못한다. blob 은 문서에 박힌 원본이라 저장 해상도가
# 표시 크기와 따로 논다 — 12.8x15.2pt 짜리 반복 글리프가 115x127px 로 저장돼 픽셀 필터를
# 그냥 통과한다(실측: 학교기록물 PDF 는 핸들러 도달 51개 중 29개가 표시상 글리프).
#
# 신호가 둘인 이유는 하나로는 반이 새기 때문이다. 짧은 변은 정사각 글리프(13.6x15.2)를
# 잡고, 가로세로비는 크지만 납작한 제목 띠(454.4x30.8 이 6쪽에 반복)를 잡는다.
#
# 크기만으로 못 가른다는 것이 이 규칙의 출발점이다. 짧은 변 분포의 최대 틈이 1.38배뿐이라
# 표 되붙이기(2.8배 틈) 방식이 안 통했고, 무료 OCR 로 훑어보니 크기와 쓸모가 단조롭지도
# 않았다 — 45.3pt 는 8자 쓰레기인데 41.4pt(145.2x41.4)는 33자 실제 문구다.
# bbox 가 없으면(HWPX 이미지 리전) 잴 수가 없어 판정하지 않는다.
def decorative_reason(
    bbox: tuple[float, float, float, float] | None, config: ContentRoutingConfig
) -> str | None:
    if bbox is None:
        return None

    width = abs(bbox[2] - bbox[0])
    height = abs(bbox[3] - bbox[1])
    short, long = min(width, height), max(width, height)
    if short <= 0:
        return None

    display = f"표시 {width:.1f}x{height:.1f}pt"
    if short < config.min_image_display_pt:
        return f"{display} — 짧은 변 {short:.1f} < {config.min_image_display_pt}pt (장식 글리프 추정)"
    if long / short > config.max_image_aspect_ratio:
        return (
            f"{display} — 가로세로비 {long / short:.1f}:1 > "
            f"{config.max_image_aspect_ratio}:1 (제목 띠·구분선 추정)"
        )
    return None


# 이미지를 어떤 핸들러로 보낼지 결정한다.
# bbox 는 쪽 위 표시 크기를 재기 위한 것이고, 없으면 그 판정만 건너뛴다
def classify(
    image: Image.Image,
    config: ContentRoutingConfig,
    *,
    bbox: tuple[float, float, float, float] | None = None,
) -> Decision:
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

    # 저장 픽셀이 커도 쪽 위에서는 장식일 수 있다. 같은 이유로 강제 모드보다 앞에 둔다
    reason = decorative_reason(bbox, config)
    if reason is not None:
        return Decision(ImageRoute.SKIP, reason)

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
