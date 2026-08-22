"""이미지 핸들러(OCR) — Tesseract로 글자만 뽑는다. 무료·오프라인·빠름.

**Tesseract 호출을 삼키는 것은 `contracts.md` 2절 때문이 아니다.** 그 규약은 유료 외부 API
것이고 "내려갈 층이 있어서" 성립한다(LlamaParse→PyMuPDF, Vision→OCR). OCR 은 로컬·무료이고
**사슬의 맨 아래**라 아래에 층이 없다 — 여기서 삼키는 이유는 폴백을 살리기 위해서가 아니라
**이미지 하나가 문서 전체 적재를 멈추는 것을 막기 위해서**다. 규약 위반이 아니라 계약 밖
강인화이므로, 이걸 근거로 "OCR 도 폴백 규약 대상"이라고 일반화하면 안 된다.

`ctx` 가 선택 인자인 이유: `classify()` 는 `LoadContext` 를 안 받아 경고를 남길 수 없다.
그 경로에서는 `Decision.reason` 이 "글자 0자, 신뢰도 0"으로 흔적을 남기고, 사용자에게 보이는
경로인 `handle()` 은 `ctx` 를 갖고 있으므로 거기서 경고가 남는다.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pytesseract
from PIL import Image, ImageOps

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content.base import ContentRegion, LoadContext
from rag_pipeline.schemas.document import Element, ElementType

# 이 폭보다 작을 때만 확대한다. 실측 결과 이미 충분히 큰 이미지를 조금 늘리면
# 보간 흐림 때문에 오히려 인식률이 떨어졌다 (93.0 -> 92.6)
_MIN_OCR_WIDTH = 600
# 확대 배율. 작은 이미지는 2배로 키워야 글리프가 또렷해진다
_UPSCALE_FACTOR = 2


# 설정에 tesseract 경로가 지정돼 있으면 pytesseract에 알려준다
def configure_tesseract(config: ContentRoutingConfig) -> None:
    if config.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = config.tesseract_cmd


# Tesseract에 넘길 옵션 문자열을 만든다
def build_tesseract_config(config: ContentRoutingConfig) -> str:
    options = [f"--psm {config.ocr_psm}"]
    if config.ocr_preserve_spaces:
        options.append("-c preserve_interword_spaces=1")
    return " ".join(options)


# OCR 정확도를 높이기 위한 전처리: 그레이스케일 변환 + 작은 이미지만 확대
def preprocess(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    if gray.width < _MIN_OCR_WIDTH:
        new_size = (gray.width * _UPSCALE_FACTOR, gray.height * _UPSCALE_FACTOR)
        gray = gray.resize(new_size, Image.LANCZOS)
    return gray


# OCR 판정에 쓰는 지표. classify가 이 값으로 OCR/Vision을 가른다.
#
# **텍스트 자체는 안 담는다.** image_to_data 가 주는 값은 한글 음절이 쪼개져 있어
# ("주 식 회 사") 최종 텍스트로 못 쓰는데, 필드로 노출해 두면 다음 사람이 그걸 본문으로
# 쓴다. 실제로 읽는 곳이 하나도 없었다(2026-08-22 감사). 최종 텍스트는 extract_text() 다
@dataclass
class OcrResult:
    char_count: int
    mean_confidence: float


# 이미지에서 인식 지표(글자 수·평균 신뢰도)를 뽑는다.
#
# image_to_data 는 Tesseract의 단어 상자를 그대로 주는데, 한글은 음절 단위로
# 쪼개져 있다("주 식 회 사"). 상자 간격으로 다시 붙이는 방법은 실측해보니
# 한글 음절 내부 간격(0.26~0.56)과 영문 단어 경계(0.48~0.59)가 겹쳐서 불가능했다.
# 그래서 여기서는 판정 지표만 얻고, 최종 텍스트는 extract_text()가 따로 만든다.
def run_ocr(
    image: Image.Image, config: ContentRoutingConfig, *, ctx: LoadContext | None = None
) -> OcrResult:
    configure_tesseract(config)

    try:
        prepared = preprocess(image)
        data = pytesseract.image_to_data(
            prepared,
            lang=config.ocr_lang,
            config=build_tesseract_config(config),
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:  # noqa: BLE001 - Tesseract 실패가 적재를 멈추면 안 된다
        if ctx is not None:
            ctx.warn(f"OCR 실행에 실패해 이미지를 건너뛰었습니다: {type(exc).__name__}")
        return OcrResult(char_count=0, mean_confidence=0.0)

    words: list[str] = []
    confidences: list[float] = []
    for word, conf in zip(data.get("text", []), data.get("conf", []), strict=False):
        if not word or not word.strip():
            continue
        words.append(word.strip())
        try:
            value = float(conf)
        except (TypeError, ValueError):
            continue
        # Tesseract는 인식 실패한 블록에 -1을 준다
        if value >= 0:
            confidences.append(value)

    text = " ".join(words)
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResult(char_count=len(text), mean_confidence=mean_conf)


# 최종 텍스트를 뽑는다. Tesseract가 preserve_interword_spaces 로 직접 만든
# 띄어쓰기를 그대로 쓰기 때문에 한글 단어가 음절로 쪼개지지 않는다
def extract_text(
    image: Image.Image, config: ContentRoutingConfig, *, ctx: LoadContext | None = None
) -> str:
    configure_tesseract(config)

    try:
        prepared = preprocess(image)
        raw = pytesseract.image_to_string(
            prepared, lang=config.ocr_lang, config=build_tesseract_config(config)
        )
    except Exception as exc:  # noqa: BLE001 - Tesseract 실패가 적재를 멈추면 안 된다
        if ctx is not None:
            ctx.warn(f"OCR 실행에 실패해 이미지를 건너뛰었습니다: {type(exc).__name__}")
        return ""

    # 빈 줄을 정리해 문단 형태를 유지한다
    lines = [line.strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


# 바이트에서 PIL 이미지를 연다
def open_image(blob: bytes) -> Image.Image:
    return Image.open(io.BytesIO(blob))


# 이미지 리전을 OCR로 처리해 Element로 만든다.
# classify 단계에서 이미 지표를 구했다면 precomputed로 넘겨 신뢰도 계산을 재사용한다
def handle(
    region: ContentRegion, ctx: LoadContext, *, precomputed: OcrResult | None = None
) -> list[Element]:
    if not region.blob:
        return []

    try:
        image = open_image(region.blob)
    except OSError:
        ctx.warn("이미지를 열 수 없어 건너뛰었습니다.")
        return []

    # 판정용 지표는 재사용하되, 최종 텍스트는 띄어쓰기가 살아있는 쪽으로 다시 뽑는다
    metrics = precomputed if precomputed is not None else run_ocr(image, ctx.config, ctx=ctx)
    text = extract_text(image, ctx.config, ctx=ctx)

    ctx.ocr_applied = True
    if not text.strip():
        return []

    metadata = dict(region.metadata)
    metadata["image_source"] = "ocr"
    metadata["ocr_confidence"] = round(metrics.mean_confidence, 1)
    if region.bbox is not None:
        metadata["bbox"] = list(region.bbox)

    return [
        Element(
            type=ElementType.IMAGE_TEXT,
            text=text,
            page=region.page,
            order=region.order,
            metadata=metadata,
        )
    ]
