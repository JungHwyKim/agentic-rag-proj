"""외부 호출이 **성공한 뒤** 무너지는 경로 — 폴백이 실제로 발동하는가.

이 저장소에서 제일 자주 비는 테스트가 폴백 경로이고, 그중에서도 *호출 실패*가 아니라
**응답이 이상한 경우**가 비어 있었다. 2026-08-22 감사에서 둘이 나왔다.

- `image_vision._call_vision()` 이 응답 해석을 `try` 밖에 둬서 `choices=[]` 하나로
  `IndexError` 가 `handle()` 밖까지 샜다. 라우터의 OCR 폴백이 발동할 기회가 없었고,
  호출은 이미 과금됐는데 `vision_calls` 는 0이었다
- `image_ocr` 이 Tesseract 호출을 아예 안 감싸서 `TesseractError` 가 `route()` 밖으로 샜다.
  이쪽은 계약 위반이 아니라 계약 밖 강인화다 — OCR 은 사슬의 맨 아래라 내려갈 층이 없다

기존 `test_router.py:test_vision_failure_falls_back_to_ocr` 는 `create()` 가 **예외를 던지는**
경우만 본다. 여기서 보는 것은 호출이 **성공하고** 응답 모양이 어긋나는 경우다.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import image_ocr, image_vision
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext


# Tesseract 가 내부 오류를 낸 상황을 흉내낸다
class _FakeTesseractError(RuntimeError):
    pass


# 크기 필터(100x100)를 통과하는 PNG
def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _region() -> ContentRegion:
    return ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="pdf",
        blob=_png(),
        blob_kind="png",
    )


def _ctx() -> LoadContext:
    # 키가 있어야 _make_client 앞의 분기를 통과한다. 실제 SDK 는 안 부른다
    config = ContentRoutingConfig(
        openai_api_key="sk-fake", image_handler="ocr", cache_enabled=False
    )
    return LoadContext(config=config)


# 응답 모양이 어긋나는 두 경우. 둘 다 호출은 성공했으므로 과금은 이미 됐다
@pytest.mark.parametrize(
    ("label", "response"),
    [
        ("choices 가 비었다", SimpleNamespace(choices=[])),
        ("message 가 없다", SimpleNamespace(choices=[SimpleNamespace()])),
    ],
)
def test_vision_bad_response_shape_falls_back(mocker, label: str, response) -> None:
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=mocker.Mock(return_value=response)))
    )
    mocker.patch.object(image_vision, "_make_client", return_value=client)
    ctx = _ctx()

    elements = image_vision.handle(_region(), ctx)

    # 예외가 handle() 밖으로 새면 라우터가 OCR 로 못 내려간다
    assert elements == [], label
    # 호출이 나갔으므로 장부에 남아야 한다. 해석 실패를 이유로 빼먹으면 돈만 나간다
    assert ctx.vision_calls == 1, label
    assert any("해석하지 못해" in w for w in ctx.warnings), label


# Tesseract 가 던져도 지표만 비고 적재는 계속된다
def test_run_ocr_survives_tesseract_error(mocker) -> None:
    mocker.patch.object(
        image_ocr.pytesseract, "image_to_data", side_effect=_FakeTesseractError("boom")
    )
    ctx = _ctx()

    with Image.open(io.BytesIO(_png())) as image:
        result = image_ocr.run_ocr(image, ctx.config, ctx=ctx)

    assert result.char_count == 0
    assert result.mean_confidence == 0.0
    assert any("OCR 실행에 실패" in w for w in ctx.warnings)


# ctx 를 안 넘기는 경로(classify)에서도 예외가 안 샌다. 대신 경고는 못 남긴다
def test_run_ocr_without_ctx_is_silent(mocker) -> None:
    mocker.patch.object(
        image_ocr.pytesseract, "image_to_data", side_effect=_FakeTesseractError("boom")
    )

    with Image.open(io.BytesIO(_png())) as image:
        result = image_ocr.run_ocr(image, ContentRoutingConfig())

    assert result.char_count == 0


def test_extract_text_survives_tesseract_error(mocker) -> None:
    mocker.patch.object(
        image_ocr.pytesseract, "image_to_string", side_effect=_FakeTesseractError("boom")
    )
    ctx = _ctx()

    with Image.open(io.BytesIO(_png())) as image:
        text = image_ocr.extract_text(image, ctx.config, ctx=ctx)

    assert text == ""
    assert any("OCR 실행에 실패" in w for w in ctx.warnings)


# 사슬 전체 — 이미지 하나가 문서 적재를 멈추지 않는다
def test_ocr_handler_returns_empty_instead_of_raising(mocker) -> None:
    mocker.patch.object(
        image_ocr.pytesseract, "image_to_data", side_effect=_FakeTesseractError("boom")
    )
    mocker.patch.object(
        image_ocr.pytesseract, "image_to_string", side_effect=_FakeTesseractError("boom")
    )
    ctx = _ctx()

    assert image_ocr.handle(_region(), ctx) == []
    assert any("OCR 실행에 실패" in w for w in ctx.warnings)
