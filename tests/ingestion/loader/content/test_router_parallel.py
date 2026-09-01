"""무료 OCR 병렬 실행 테스트 — 순서·동치·공유 상태·유료 경계.

`route_all()` 이 이미지 리전의 무료 OCR 만 스레드로 돌린다. 여기서 지키려는 것은 넷이다.

1. **완료 순서가 아니라 `order` 순서로 나온다** — 그래서 일부러 늦게 끝나는 리전을 앞에 둔다
2. **직렬과 결과가 같다** — 워커 수만 바꿔 두 번 돌려 비교한다
3. **`ctx.warn()` 이 중복을 안 만든다** — 검사와 append 가 갈라져 있어 락 없이는 샌다
4. **유료 Vision 은 병렬 구간에 안 들어간다** — 상한이 지켜지고, 호출이 메인 스레드에서 난다

Tesseract 설치 여부에 결과가 흔들리지 않도록 `run_ocr`·`extract_text` 를 모킹한다.
외부 API 키 없이 전 항목이 통과해야 한다.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import image_ocr, image_vision, text_native
from rag_pipeline.ingestion.loader.content import router as router_module
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.content.image_ocr import OcrResult
from rag_pipeline.ingestion.loader.content.router import ContentRouter, route_all


# 이미지 리전 하나. 폭으로 리전을 구별해 OCR 결과와 맞물리는지 본다
def _image_region(order: int, blob: bytes) -> ContentRegion:
    return ContentRegion(
        content_type=ContentType.IMAGE,
        order=order,
        source_format="pdf",
        page=order + 1,
        blob=blob,
        blob_kind="png",
    )


# Tesseract 를 대신한다. 뒤 리전일수록 빨리 끝나게 해 완료 순서를 일부러 뒤집는다
def _fake_ocr(mocker, count: int, *, delay: float = 0.005, on_call=None):
    mocker.patch.object(
        image_ocr, "run_ocr", lambda image, config, **kw: OcrResult(10, 90.0)
    )

    def _extract(image, config, **kwargs):
        index = image.width - 400
        time.sleep((count - index) * delay)
        if on_call is not None:
            on_call(kwargs.get("ctx"), index)
        return f"텍스트-{index}"

    mocker.patch.object(image_ocr, "extract_text", _extract)


# 강제 OCR 설정. 판정에서 Tesseract 를 안 태워 모킹 대상이 하나로 줄어든다
def _ocr_config(**overrides) -> ContentRoutingConfig:
    defaults = {"image_handler": "ocr", "cache_enabled": False, "openai_api_key": ""}
    defaults.update(overrides)
    return ContentRoutingConfig(**defaults)


# 늦게 끝나는 리전을 앞에 둬도 반환은 order 순서다.
# 이 재정렬이 병렬화의 전제라, 깨지면 요소 순서가 완료 순서로 흐트러진다
def test_parallel_output_keeps_order(mocker, png_bytes) -> None:
    count = 12
    _fake_ocr(mocker, count)

    # 입력을 역순으로 넣어 route_all 의 정렬까지 함께 본다
    regions = [_image_region(i, png_bytes(400 + i, 200)) for i in reversed(range(count))]
    ctx = LoadContext(config=_ocr_config())

    elements = route_all(regions, ctx, ContentRouter())

    assert [e.order for e in elements] == list(range(count))
    assert [e.text for e in elements] == [f"텍스트-{i}" for i in range(count)]


# 워커 수만 바꿔 두 번 돌린 결과가 완전히 같아야 한다
def test_parallel_matches_serial(mocker, png_bytes) -> None:
    count = 10
    blobs = [png_bytes(400 + i, 200) for i in range(count)]

    def _run(workers: int):
        _fake_ocr(mocker, count, delay=0.001)
        ctx = LoadContext(config=_ocr_config(ocr_max_workers=workers))
        elements = route_all(
            [_image_region(i, blobs[i]) for i in range(count)], ctx, ContentRouter()
        )
        return [(e.order, e.text, e.page, e.metadata["image_source"]) for e in elements], ctx

    serial, serial_ctx = _run(1)
    parallel, parallel_ctx = _run(8)

    assert parallel == serial
    assert parallel_ctx.warnings == serial_ctx.warnings
    assert parallel_ctx.ocr_applied is serial_ctx.ocr_applied is True
    assert parallel_ctx.vision_calls == serial_ctx.vision_calls == 0


# 여러 워커가 같은 경고를 남겨도 한 번만 쌓여야 한다.
# warn() 은 `not in` 검사와 append 가 갈라져 있어 락이 없으면 중복이 샌다
def test_warn_is_not_duplicated_under_workers(mocker, png_bytes) -> None:
    count = 24
    _fake_ocr(mocker, count, delay=0.001, on_call=lambda ctx, _index: ctx.warn("같은 경고"))

    regions = [_image_region(i, png_bytes(400 + i, 200)) for i in range(count)]
    ctx = LoadContext(config=_ocr_config())

    route_all(regions, ctx, ContentRouter())

    assert ctx.warnings == ["같은 경고"]


# LoadContext 자체를 스레드로 두드려도 경고가 한 번씩만 쌓인다
def test_load_context_warn_is_thread_safe() -> None:
    ctx = LoadContext(config=_ocr_config())
    start = threading.Barrier(16)

    def _hammer() -> None:
        start.wait()
        for index in range(50):
            ctx.warn(f"경고 {index % 5}")

    threads = [threading.Thread(target=_hammer) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(ctx.warnings) == [f"경고 {i}" for i in range(5)]


# 유료 Vision 은 병렬 구간에 안 들어간다 — 상한이 지켜지고 호출이 메인 스레드에서 난다.
# 상한 검사는 `if ctx.vision_calls >= 상한` 과 증가로 갈라져 있어 병렬이면 넘겨 과금된다
def test_vision_stays_serial_and_respects_cap(mocker, png_bytes) -> None:
    payload = '{"description": "그림", "extracted_text": "", "data_table": []}'
    idents: list[int] = []

    def _create(**kwargs):
        idents.append(threading.get_ident())
        time.sleep(0.005)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )

    mocker.patch.object(
        image_vision,
        "_make_client",
        return_value=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
        ),
    )

    config = _ocr_config(
        image_handler="vision", openai_api_key="test-key", max_vision_calls_per_doc=2
    )
    ctx = LoadContext(config=config)
    regions = [_image_region(i, png_bytes(400 + i, 300)) for i in range(6)]

    route_all(regions, ctx, ContentRouter())

    assert ctx.vision_calls == 2
    assert len(idents) == 2
    assert set(idents) == {threading.get_ident()}


# 텍스트·표는 병렬 구간을 안 타지만 순서는 이미지와 함께 맞물려야 한다
def test_mixed_regions_interleave_by_order(mocker, png_bytes) -> None:
    _fake_ocr(mocker, 4, delay=0.002)

    regions = [
        ContentRegion(content_type=ContentType.TEXT, order=0, source_format="pdf", text="머리"),
        _image_region(1, png_bytes(400, 200)),
        ContentRegion(
            content_type=ContentType.TABLE,
            order=2,
            source_format="pdf",
            rows=[["이름", "점수"], ["홍길동", "95"]],
        ),
        _image_region(3, png_bytes(402, 200)),
    ]
    ctx = LoadContext(config=_ocr_config())

    elements = route_all(regions, ctx, ContentRouter())

    assert [e.order for e in elements] == [0, 1, 2, 3]
    assert elements[0].text == "머리"
    assert elements[1].text == "텍스트-0"
    assert "홍길동" in elements[2].text
    assert elements[3].text == "텍스트-2"


# 열 수 없는 이미지는 병렬 구간에서 걸러지고, 직렬 구간이 다시 열지 않는다
def test_unopenable_image_is_not_retried(mocker, png_bytes) -> None:
    _fake_ocr(mocker, 3, delay=0.001)
    spy = mocker.spy(image_ocr, "open_image")

    regions = [
        _image_region(0, png_bytes(400, 200)),
        _image_region(1, b"not-an-image"),
        _image_region(2, png_bytes(402, 200)),
    ]
    ctx = LoadContext(config=_ocr_config(ocr_max_workers=4))

    elements = route_all(regions, ctx, ContentRouter())

    assert [e.order for e in elements] == [0, 2]
    assert ctx.warnings == ["이미지를 열 수 없어 건너뛰었습니다."]
    # 리전당 정확히 한 번씩만 연다 — 실패한 것을 직렬 구간이 다시 열면 3보다 커진다
    assert spy.call_count == 3


# 리전마다 다른 경고를 남기고 완료 순서를 뒤집어도 ctx.warnings 가 문서 순서여야 한다.
#
# **이것이 요소 순서와 다른 문제다.** 요소는 order 를 들고 다녀 정렬로 되돌릴 수 있지만
# 경고는 문자열이라 자기 자리를 모른다 — 순서가 append 시각에 실리면 되돌릴 방법이 없다
def test_warning_order_follows_document_order(mocker, png_bytes) -> None:
    count = 12
    _fake_ocr(mocker, count, on_call=lambda ctx, index: ctx.warn(f"경고 {index:02d}"))

    regions = [_image_region(i, png_bytes(400 + i, 200)) for i in range(count)]
    ctx = LoadContext(config=_ocr_config())

    route_all(regions, ctx, ContentRouter())

    assert ctx.warnings == [f"경고 {i:02d}" for i in range(count)]


# 직렬 핸들러(텍스트)와 병렬 OCR 이 낸 경고도 문서 순서로 섞여야 한다
def test_serial_and_parallel_warnings_interleave(mocker, png_bytes) -> None:
    _fake_ocr(mocker, 6, on_call=lambda ctx, index: ctx.warn(f"이미지 {index}"))

    def _text_handle(region, ctx):
        ctx.warn(f"텍스트 {region.order}")
        return []

    mocker.patch.object(text_native, "handle", _text_handle)

    regions = [
        _image_region(0, png_bytes(400, 200)),
        ContentRegion(content_type=ContentType.TEXT, order=1, source_format="pdf", text="가"),
        _image_region(2, png_bytes(402, 200)),
        ContentRegion(content_type=ContentType.TEXT, order=3, source_format="pdf", text="나"),
    ]
    ctx = LoadContext(config=_ocr_config())

    route_all(regions, ctx, ContentRouter())

    assert ctx.warnings == ["이미지 0", "텍스트 1", "이미지 2", "텍스트 3"]


# 자식을 흡수해도 중복 제거가 살아야 한다 — 여러 자식이 같은 문구를 내는 경우
def test_absorb_keeps_dedup_across_children() -> None:
    parent = LoadContext(config=_ocr_config())
    parent.warn("공통")

    for _ in range(3):
        child = parent.child()
        child.warn("공통")
        child.warn("자식만")
        parent.absorb(child)

    assert parent.warnings == ["공통", "자식만"]


# ocr_applied 는 자식 중 하나라도 True 면 부모도 True
def test_absorb_ors_ocr_applied() -> None:
    parent = LoadContext(config=_ocr_config())
    assert parent.ocr_applied is False

    quiet = parent.child()
    parent.absorb(quiet)
    assert parent.ocr_applied is False

    busy = parent.child()
    busy.mark_ocr_applied()
    parent.absorb(busy)
    assert parent.ocr_applied is True


# **유료 핸들러가 병렬 구간에서 돈 흔적이 있으면 흡수가 터진다.**
#
# 지금 이 경계는 주석과 테스트에만 있어서, 되돌리는 사람이 안 읽으면 상한이 새고 과금이
# 늘어난다 — 키 없는 기본 pytest 는 그걸 못 잡는다. 시끄럽게 죽는 편이 낫다
def test_absorb_rejects_paid_counters() -> None:
    for field_name, bump in (
        ("vision_calls", lambda c: setattr(c, "vision_calls", 1)),
        ("llamaparse_calls", lambda c: setattr(c, "llamaparse_calls", 1)),
        ("mark_symbols", lambda c: c.mark_symbols.update({"sig": "V"})),
    ):
        parent = LoadContext(config=_ocr_config())
        child = parent.child()
        bump(child)
        with pytest.raises(RuntimeError, match="유료"):
            parent.absorb(child)
        assert parent.vision_calls == 0, field_name
        assert parent.llamaparse_calls == 0, field_name
        assert parent.mark_symbols == {}, field_name


# 자식은 설정을 공유하되 누적 상태는 자기 것이어야 한다
def test_child_shares_config_but_not_state() -> None:
    parent = LoadContext(config=_ocr_config())
    parent.warn("부모 것")
    child = parent.child()

    assert child.config is parent.config
    assert child.warnings == []
    child.warn("자식 것")
    assert parent.warnings == ["부모 것"]


# 워커 수는 **셋의 최솟값**이다 — 설정 상한 · 이 머신의 코어 수 · 실제 할 일의 수.
# 설정을 안 보고 상수로 돌아가면 발열 손잡이가 조용히 사라진다
def test_ocr_workers_respects_config_cap(mocker) -> None:
    mocker.patch.object(router_module.os, "cpu_count", return_value=16)

    assert router_module._ocr_workers(100, _ocr_config(ocr_max_workers=2)) == 2
    assert router_module._ocr_workers(100, _ocr_config(ocr_max_workers=8)) == 8
    # 할 일이 상한보다 적으면 그만큼만
    assert router_module._ocr_workers(3, _ocr_config(ocr_max_workers=8)) == 3


# 코어 수가 상한보다 적으면 코어 수가 이긴다. 상한만 보면 1코어 머신에서 8개를 띄운다
def test_ocr_workers_never_exceeds_cpu_count(mocker) -> None:
    mocker.patch.object(router_module.os, "cpu_count", return_value=2)

    assert router_module._ocr_workers(100, _ocr_config(ocr_max_workers=8)) == 2


# 0 이나 음수를 넣어도 최소 1이다. 0을 넘기면 ThreadPoolExecutor 가 거기서 터진다
def test_ocr_workers_floor_is_one(mocker) -> None:
    mocker.patch.object(router_module.os, "cpu_count", return_value=16)

    assert router_module._ocr_workers(100, _ocr_config(ocr_max_workers=0)) == 1


# 기본값은 8이다. **이 값이 바뀌면 .claude/wiki/verify_log/2026-08-24-ocr-병렬화.md 의 51.8초가
# 다른 설정의 수가 된다** — 기본을 안 낮추기로 한 결정을 여기서 잠근다
def test_default_cap_is_eight() -> None:
    assert ContentRoutingConfig(openai_api_key="").ocr_max_workers == 8

