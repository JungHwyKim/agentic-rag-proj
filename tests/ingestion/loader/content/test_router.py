"""2차 라우팅 테스트 — 어떤 핸들러가 선택되고, 실패하면 어디로 폴백하는가.

외부 API는 전부 모킹한다. API 키 없이도 전 항목이 통과해야 한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import image_vision, table_llamaparse, table_vision
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.content.router import ContentRouter
from rag_pipeline.schemas.document import ElementType


# 외부 API 키가 있다고 가정한 설정. 실제 호출은 모킹으로 가로챈다
def _online_config(**overrides) -> ContentRoutingConfig:
    defaults = {
        "openai_api_key": "test-key",
        "llama_cloud_api_key": "test-key",
        # 기본값이 pymupdf 로 바뀌었으므로(2026-08-18) 여기서 명시한다.
        # 이 파일의 주제가 LlamaParse 경로라 설정에 기대지 않고 못 박는 것이 맞다
        "table_pdf_handler": "llamaparse",
        "cache_enabled": False,
    }
    defaults.update(overrides)
    return ContentRoutingConfig(**defaults)


# 표 리전을 만든다. rows 는 PyMuPDF가 뽑은 폴백용 데이터를 흉내낸다
def _table_region(*, with_blob: bool) -> ContentRegion:
    return ContentRegion(
        content_type=ContentType.TABLE,
        order=0,
        source_format="pdf",
        page=1,
        rows=[["이름", "점수"], ["홍길동", "95"]],
        blob=b"%PDF-fake" if with_blob else None,
        blob_kind="pdf" if with_blob else None,
    )


# 네이티브 구조가 있고 blob이 없으면(docx 등) LlamaParse를 부르지 않는다
def test_native_table_skips_llamaparse(mocker) -> None:
    spy = mocker.patch.object(table_llamaparse, "_make_client")
    config = _online_config()
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=False), ctx)

    spy.assert_not_called()
    assert elements[0].metadata["table_source"] == "native"
    assert ctx.llamaparse_calls == 0


# 실제 응답 모양을 흉내낸다. `markdown` 은 문자열이 아니라 pages 를 담은 객체이고
# 성공한 페이지에만 markdown 이, 실패한 페이지에는 error 가 들어온다.
# **이 모양이 이 파일의 핵심이다** — 예전 가짜는 문자열이라 코드와 똑같이 틀린 채 통과했다
def _fake_parse_result(*pages: object) -> SimpleNamespace:
    return SimpleNamespace(markdown=SimpleNamespace(pages=list(pages)))


# 파싱에 성공한 페이지 하나
def _ok_page(markdown: str, page_number: int = 1) -> SimpleNamespace:
    return SimpleNamespace(markdown=markdown, page_number=page_number, success=True)


# LlamaParse 클라이언트를 가짜로 갈아끼운다. 이음새는 모듈 레벨 _make_client 다
def _patch_client(mocker, *, result=None, side_effect=None) -> None:
    parse = mocker.Mock(return_value=result, side_effect=side_effect)
    mocker.patch.object(
        table_llamaparse,
        "_make_client",
        return_value=SimpleNamespace(parsing=SimpleNamespace(parse=parse)),
    )


# PDF 표는 LlamaParse로 가고 pages 안의 마크다운을 꺼내 쓴다
def test_pdf_table_uses_llamaparse(mocker) -> None:
    _patch_client(mocker, result=_fake_parse_result(_ok_page("| A | B |\n| --- | --- |\n| 1 | 2 |")))

    config = _online_config()
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=True), ctx)

    assert elements[0].metadata["table_source"] == "llamaparse"
    assert "| 1 | 2 |" in elements[0].text
    assert ctx.llamaparse_calls == 1


# 표가 여러 장에 걸쳐 오면 성공한 페이지만 순서대로 이어 붙인다
def test_llamaparse_joins_pages_and_skips_failed(mocker) -> None:
    failed = SimpleNamespace(error="rate limited", page_number=2, success=False)
    _patch_client(
        mocker,
        result=_fake_parse_result(_ok_page("| A |\n| --- |"), failed, _ok_page("| B |", 3)),
    )

    config = _online_config()
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=True), ctx)

    assert elements[0].text == "| A |\n| --- |\n\n| B |"
    assert ctx.llamaparse_calls == 1


# 응답 모양이 예상과 다르면 예외를 밖으로 내보내지 않고 로컬 추출로 내려간다.
# 이 규약이 깨지면 라우터의 폴백이 발동할 기회 자체가 없어 적재 전체가 멈춘다
def test_llamaparse_bad_response_shape_falls_back(mocker) -> None:
    class Exploding:
        @property
        def pages(self):
            raise TypeError("모양이 바뀌었다")

    _patch_client(mocker, result=SimpleNamespace(markdown=Exploding()))

    config = _online_config()
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=True), ctx)

    assert elements[0].metadata["table_source"] == "native"
    assert any("폴백" in w for w in ctx.warnings)
    # 호출은 이미 나갔으므로 과금 카운터는 올라가 있어야 한다
    assert ctx.llamaparse_calls == 1


# LlamaParse가 실패하면 PyMuPDF가 뽑은 rows 로 내려가야 한다.
# 외부 API 장애가 적재 전체를 멈추면 안 된다
def test_llamaparse_failure_falls_back_to_rows(mocker) -> None:
    fake_client = SimpleNamespace(
        parsing=SimpleNamespace(parse=mocker.Mock(side_effect=RuntimeError("boom")))
    )
    mocker.patch.object(table_llamaparse, "_make_client", return_value=fake_client)

    config = _online_config()
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=True), ctx)

    assert elements[0].metadata["table_source"] == "native"
    assert "| 홍길동 | 95 |" in elements[0].text
    assert any("폴백" in w for w in ctx.warnings)


# 키가 없으면 아예 호출하지 않고 바로 로컬 추출로 간다
def test_missing_key_falls_back_silently() -> None:
    config = _online_config(llama_cloud_api_key=None)
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=True), ctx)

    assert elements[0].metadata["table_source"] == "native"
    assert any("LLAMA_CLOUD_API_KEY" in w for w in ctx.warnings)


# 설정을 pymupdf로 두면 blob이 있어도 LlamaParse를 부르지 않는다
def test_config_can_disable_llamaparse(mocker) -> None:
    spy = mocker.patch.object(table_llamaparse, "_make_client")
    config = _online_config(table_pdf_handler="pymupdf")
    ctx = LoadContext(config=config)

    elements = ContentRouter().route(_table_region(with_blob=True), ctx)

    spy.assert_not_called()
    assert elements[0].metadata["table_source"] == "native"


# 텍스트 리전은 그대로 통과한다
def test_text_passthrough() -> None:
    config = _online_config()
    region = ContentRegion(
        content_type=ContentType.TEXT,
        order=3,
        source_format="docx",
        text="  본문입니다.  ",
        metadata={"element_type": ElementType.TITLE},
    )

    elements = ContentRouter().route(region, LoadContext(config=config))

    assert elements[0].text == "본문입니다."
    assert elements[0].type is ElementType.TITLE
    assert elements[0].order == 3


# 빈 텍스트는 요소를 만들지 않는다
def test_empty_text_dropped() -> None:
    config = _online_config()
    region = ContentRegion(content_type=ContentType.TEXT, order=0, source_format="docx", text="   ")

    assert ContentRouter().route(region, LoadContext(config=config)) == []


# 작은 이미지는 로고로 보고 건너뛴다. PDF에 널린 아이콘을 전부 Vision에 보내면 비용이 터진다
def test_tiny_image_skipped(png_bytes) -> None:
    config = _online_config(image_handler="hybrid")
    region = ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="pdf",
        blob=png_bytes(40, 40),
        blob_kind="png",
    )

    assert ContentRouter().route(region, LoadContext(config=config)) == []


# Vision 응답을 검색 가능한 텍스트로 합쳐야 한다
def test_vision_builds_searchable_text(mocker, png_bytes) -> None:
    payload = (
        '{"description": "분기별 매출 추이를 보여주는 막대그래프.", '
        '"extracted_text": "1분기 2분기", '
        '"data_table": [["분기","매출"],["1분기","100"]]}'
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=mocker.Mock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
                    )
                )
            )
        )
    )
    mocker.patch.object(image_vision, "_make_client", return_value=fake_client)

    config = _online_config(image_handler="vision")
    ctx = LoadContext(config=config)
    region = ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="pdf",
        blob=png_bytes(400, 300),
        blob_kind="png",
    )

    elements = ContentRouter().route(region, ctx)

    assert elements[0].type is ElementType.IMAGE_TEXT
    assert "막대그래프" in elements[0].text
    assert "| 1분기 | 100 |" in elements[0].text
    assert elements[0].metadata["image_source"] == "vision"
    assert ctx.vision_calls == 1


# Vision이 실패하면 OCR로 내려가야 한다
def test_vision_failure_falls_back_to_ocr(mocker, korean_image_file) -> None:
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocker.Mock(side_effect=RuntimeError("boom")))
        )
    )
    mocker.patch.object(image_vision, "_make_client", return_value=fake_client)

    config = _online_config(image_handler="vision")
    ctx = LoadContext(config=config)
    region = ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="image",
        blob=korean_image_file.read_bytes(),
        blob_kind="png",
    )

    elements = ContentRouter().route(region, ctx)

    assert any("폴백" in w for w in ctx.warnings)
    if elements:
        assert elements[0].metadata["image_source"] == "ocr"


# Vision 호출 상한에 도달하면 더 부르지 않는다
def test_vision_call_cap(mocker, png_bytes) -> None:
    spy = mocker.patch.object(image_vision, "_make_client")
    config = _online_config(image_handler="vision", max_vision_calls_per_doc=0)
    ctx = LoadContext(config=config)
    region = ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="pdf",
        blob=png_bytes(400, 300),
        blob_kind="png",
    )

    ContentRouter().route(region, ctx)

    spy.assert_not_called()
    assert any("상한" in w for w in ctx.warnings)


# image_handler=skip 이면 이미지를 아예 처리하지 않는다
def test_image_skip_mode(png_bytes) -> None:
    config = _online_config(image_handler="skip")
    region = ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="pdf",
        blob=png_bytes(400, 300),
        blob_kind="png",
    )

    assert ContentRouter().route(region, LoadContext(config=config)) == []


# 깨진 이미지 바이트가 들어와도 예외로 죽지 않아야 한다
def test_corrupt_image_is_warned_not_raised() -> None:
    config = _online_config()
    ctx = LoadContext(config=config)
    region = ContentRegion(
        content_type=ContentType.IMAGE,
        order=0,
        source_format="pdf",
        blob=b"not-an-image",
        blob_kind="png",
    )

    assert ContentRouter().route(region, ctx) == []
    assert any("이미지를 열 수 없어" in w for w in ctx.warnings)


# 알 수 없는 콘텐츠 유형은 조용히 무시한다
def test_unknown_content_type() -> None:
    config = _online_config()
    region = ContentRegion(
        content_type="mystery",  # type: ignore[arg-type]
        order=0,
        source_format="pdf",
    )

    assert ContentRouter().route(region, LoadContext(config=config)) == []


# --- 승급(hybrid) 경로 ---
#
# 여기서 보는 것은 **언제 올리지 않는가**다. 트리거가 실측으로 오검출 0 이어도,
# 라우터가 신호를 안 보고 올리면 그 실측이 아무 의미가 없다.
#
# `mark_cells` 에 **자리 목록**을 넣는다. 개수(int)로 두면 라우터가 `> 0` 으로 되돌아가도
# 통과해서, 실제 생산자(`pdf.py:_marks_within()`)가 주는 타입에서 터지는 것을 못 잡는다


# 신호가 없는 표는 hybrid 여도 올리지 않는다. 표 94개 중 88개가 이 경로다
def test_hybrid_without_marks_stays_native(mocker) -> None:
    spy = mocker.patch.object(table_vision, "_make_client")
    config = _online_config(table_pdf_handler="hybrid")
    ctx = LoadContext(config=config)
    region = _table_region(with_blob=True)
    region.mark_cells = []

    elements = ContentRouter().route(region, ctx)

    spy.assert_not_called()
    assert elements[0].metadata["table_source"] == "native"
    assert ctx.vision_calls == 0


# blob 이 없으면 신호가 있어도 못 올린다. 쪽을 가로지르는 표가 여기로 빠진다
def test_hybrid_without_blob_stays_native(mocker) -> None:
    spy = mocker.patch.object(table_vision, "_make_client")
    config = _online_config(table_pdf_handler="hybrid")
    ctx = LoadContext(config=config)
    region = _table_region(with_blob=False)
    region.mark_cells = [(1, 1)]

    elements = ContentRouter().route(region, ctx)

    spy.assert_not_called()
    assert elements[0].metadata["table_source"] == "native"


# 기본값(pymupdf)에서는 신호가 있어도 안 올린다. 과금 경로는 켜야 돈다
def test_default_handler_never_escalates(mocker) -> None:
    spy = mocker.patch.object(table_vision, "_make_client")
    config = _online_config(table_pdf_handler="pymupdf")
    ctx = LoadContext(config=config)
    region = _table_region(with_blob=True)
    region.mark_cells = [(1, 1)]

    elements = ContentRouter().route(region, ctx)

    spy.assert_not_called()
    assert elements[0].metadata["table_source"] == "native"


# 승급이 실패하면 로컬 추출로 내려간다. 빈 리스트 규약이 여기서 발화한다
def test_hybrid_falls_back_when_escalation_fails(mocker) -> None:
    mocker.patch.object(table_vision, "handle", return_value=[])
    config = _online_config(table_pdf_handler="hybrid")
    ctx = LoadContext(config=config)
    region = _table_region(with_blob=True)
    region.mark_cells = [(1, 1)]

    elements = ContentRouter().route(region, ctx)

    assert elements[0].metadata["table_source"] == "native"
