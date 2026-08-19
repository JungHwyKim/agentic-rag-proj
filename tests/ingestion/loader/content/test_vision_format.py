"""Vision 으로 보내는 이미지 포맷 정규화 — 무엇을 다시 굽고 무엇을 그대로 보내는가.

로더들이 ContentRegion.blob_kind 에 "png" 를 하드코딩하는데 blob 은 문서에 박힌 원본
바이트다. 그래서 bmp·jpx 가 data:image/png 라는 이름표를 달고 나갔다가 전부 400 으로
거절당했다(실측: jpx 만 든 문서와 bmp 만 든 문서 셋이 vision_calls=0, 포맷이 섞인
문서만 부분 성공). 여기서 보는 것은 두 가지다 — 바이트에서 포맷을 읽는가, 그리고
이미 지원되는 포맷을 괜히 다시 굽지 않는가(전부 구우면 JPEG 이 3배로 부푼다).
"""

from __future__ import annotations

import base64
import io
from types import SimpleNamespace

from PIL import Image

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import image_vision
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext

_PAYLOAD = '{"description": "설명", "extracted_text": "", "data_table": null}'


# 보낸 요청을 그대로 붙잡아 두는 가짜 클라이언트. 모킹 이음새는 _make_client 다
def _capturing_client(mocker):
    create = mocker.Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=_PAYLOAD))]
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    mocker.patch.object(image_vision, "_make_client", return_value=client)
    return create


# 실제로 나간 data URL 에서 (MIME, 바이트) 를 되꺼낸다
def _sent_image(create) -> tuple[str, bytes]:
    content = create.call_args.kwargs["messages"][0]["content"]
    url = next(part for part in content if part["type"] == "image_url")["image_url"]["url"]
    header, encoded = url.split(",", 1)
    assert header.startswith("data:image/") and header.endswith(";base64")
    mime = header[len("data:image/") : -len(";base64")]
    return mime, base64.b64decode(encoded)


def _image(fmt: str, size: tuple[int, int] = (400, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=fmt)
    return buffer.getvalue()


def _region(blob: bytes) -> ContentRegion:
    # blob_kind 는 로더가 하는 그대로 거짓말을 시킨다. 정규화가 이걸 안 봐야 한다
    return ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="hwpx",
        blob=blob,
        blob_kind="png",
    )


def _ctx() -> LoadContext:
    config = ContentRoutingConfig(openai_api_key="test-key", cache_enabled=False)
    return LoadContext(config=config)


# 지원 밖 포맷(bmp)은 PNG 로 다시 구워 보낸다. 이게 400 을 만들던 자리다
def test_unsupported_format_is_reencoded_to_png(mocker) -> None:
    create = _capturing_client(mocker)
    original = _image("BMP")
    ctx = _ctx()

    image_vision.handle(_region(original), ctx)

    mime, sent = _sent_image(create)
    assert mime == "png"
    assert sent != original
    assert Image.open(io.BytesIO(sent)).format == "PNG"
    assert ctx.vision_calls == 1


# 지원 포맷(jpeg)은 손대지 않고 그대로 보낸다.
# 전부 PNG 로 구우면 JPEG 사진이 3배로 부풀어(실측 3.8MB -> 11.1MB) 상한에 다가간다
def test_supported_format_is_sent_untouched(mocker) -> None:
    create = _capturing_client(mocker)
    original = _image("JPEG")

    image_vision.handle(_region(original), _ctx())

    mime, sent = _sent_image(create)
    assert mime == "jpeg"
    assert sent == original


# blob_kind 가 "png" 라고 우겨도 바이트가 gif 면 gif 로 나간다
def test_mime_comes_from_bytes_not_from_blob_kind(mocker) -> None:
    create = _capturing_client(mocker)

    image_vision.handle(_region(_image("GIF")), _ctx())

    assert _sent_image(create)[0] == "gif"


# 열리지 않는 바이트는 예외가 아니라 빈 결과다. 폴백 규약을 깨면 라우터가 OCR로 못 내려간다
def test_undecodable_image_falls_back_quietly(mocker) -> None:
    create = _capturing_client(mocker)
    # 헤더는 멀쩡하고 몸통이 잘린 bmp — Image.open 은 되고 load() 에서 깨진다
    truncated = _image("BMP")[:200]
    ctx = _ctx()

    assert image_vision.handle(_region(truncated), ctx) == []
    create.assert_not_called()
    assert any("폴백" in warning for warning in ctx.warnings)
    assert ctx.vision_calls == 0
