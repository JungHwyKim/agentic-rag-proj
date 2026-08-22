"""표 Vision 승급 — **빈 칸만 채우는가, 그리고 못 믿을 응답을 버리는가.**

이 핸들러가 지키는 실패 모드는 둘이고 값이 서로 다르다.

  원본 값을 덮는다      2~10회차가 실측으로 오검출 0 을 확인한 것들(병합 셀 채우기 ·
                       되붙이기 · 쪽 경계 잇기 · 갈린 셀 잇기)을 외부 모델이 조용히
                       뒤엎는다. **에러가 안 나서 아무도 모른다**
  모양이 다른 격자를 쓴다  자리가 밀린 채 색인된다. 이 저장소가 HWPX 병합 셀에서 겪은
                       373행 밀림과 같은 형태다

그래서 아래 테스트는 "채웠나"보다 **"안 건드렸나"와 "버렸나"** 에 무게를 둔다.

외부 API는 전부 모킹한다. API 키 없이 전 항목이 통과해야 한다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import table_vision
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext

# PyMuPDF 가 뽑은 격자를 흉내낸다. 3열 중 2·3열이 비어 있다 — 체크 표시가 들어올 자리다
_ROWS = [
    ["원칙", "기획", "데이터"],
    ["투명성", "", ""],
    ["책임성", "", ""],
]


# 외부 키가 있다고 가정한 설정. 실제 호출은 모킹으로 가로챈다
def _config(**overrides) -> ContentRoutingConfig:
    defaults = {
        "openai_api_key": "test-key",
        "table_pdf_handler": "hybrid",
        "cache_enabled": False,
    }
    defaults.update(overrides)
    return ContentRoutingConfig(**defaults)


# `marks` 는 로더가 기하로 잡은 표시 **자리**다. 여기 있는데 비어 있는 칸은 핸들러가
# 재호출 뒤 같은 기호로 메운다. 기본값은 아래 가짜 응답이 채우는 두 자리와 같게 뒀다
_MARKS = [(1, 1), (2, 2)]


def _region(
    *,
    rows=None,
    blob: bytes | None = b"%PDF-fake",
    kind: str | None = "pdf",
    marks=None,
    signature: str = "",
):
    return ContentRegion(
        content_type=ContentType.TABLE,
        order=0,
        source_format="pdf",
        page=1,
        rows=[list(row) for row in (rows if rows is not None else _ROWS)],
        blob=blob,
        blob_kind=kind,
        mark_cells=list(_MARKS if marks is None else marks),
        mark_signature=signature,
    )


# OpenAI 응답 모양. content 에 JSON 문자열이 실린다
def _fake_client(mocker, payload: object, *, finish_reason: str = "stop"):
    message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocker.Mock(return_value=response))
        )
    )
    return mocker.patch.object(table_vision, "_make_client", return_value=client)


# 굽기는 진짜 PDF 가 있어야 하므로 이 파일에서는 가로챈다.
# 렌더링 자체는 아래 전용 테스트가 실제 PDF 로 본다
def _stub_render(mocker) -> None:
    mocker.patch.object(table_vision, "_render", return_value=b"\x89PNG-fake")


# 빈 칸에만 값이 들어간다
def test_fills_only_empty_cells(mocker) -> None:
    _stub_render(mocker)
    _fake_client(
        mocker,
        {
            "rows": [
                ["원칙", "기획", "데이터"],
                ["투명성", "○", ""],
                ["책임성", "", "○"],
            ]
        },
    )
    ctx = LoadContext(config=_config())

    elements = table_vision.handle(_region(), ctx)

    assert len(elements) == 1
    assert elements[0].metadata["rows"] == [
        ["원칙", "기획", "데이터"],
        ["투명성", "○", ""],
        ["책임성", "", "○"],
    ]
    assert elements[0].metadata["vision_filled_cells"] == 2
    assert elements[0].metadata["table_source"] == "native+vision"
    assert elements[0].metadata["vision_expected_cells"] == 2
    # 신호(2)와 채운 수(2)가 같으므로 재호출이 없다
    assert ctx.vision_calls == 1


# **원본에 값이 있던 칸은 모델이 무엇을 보내든 안 바뀐다.**
# 병합 셀 채우기·갈린 셀 잇기 결과가 여기서 지켜진다
def test_existing_values_are_never_overwritten(mocker) -> None:
    _stub_render(mocker)
    _fake_client(
        mocker,
        {
            "rows": [
                ["원칙", "기획", "데이터"],
                ["투명성(수정됨)", "○", ""],
                ["다른 값", "", "○"],
            ]
        },
    )
    ctx = LoadContext(config=_config())

    elements = table_vision.handle(_region(), ctx)

    rows = elements[0].metadata["rows"]
    assert rows[1][0] == "투명성"
    assert rows[2][0] == "책임성"


# 마크다운과 rows 가 같은 것을 말해야 한다. cleaner 가 표에 못 박은 규약과 같은 형태다
def test_text_and_rows_agree(mocker) -> None:
    _stub_render(mocker)
    _fake_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    ctx = LoadContext(config=_config())

    element = table_vision.handle(_region(), ctx)[0]

    for row in element.metadata["rows"]:
        for cell in row:
            if cell:
                assert cell in element.text


# 행 수가 다르면 통째로 버린다. 자리를 맞춰 보려 들지 않는다
def test_row_count_mismatch_is_discarded(mocker) -> None:
    _stub_render(mocker)
    _fake_client(mocker, {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""]]})
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(), ctx) == []
    assert any("다른 크기" in w for w in ctx.warnings)


# 열 수가 다른 행이 하나만 있어도 버린다
def test_column_count_mismatch_is_discarded(mocker) -> None:
    _stub_render(mocker)
    _fake_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○"], ["책임성", "", "○"]]},
    )
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(), ctx) == []


# 채운 것이 없으면 table_native 와 결과가 같다. 같은 것을 두 이름으로 내지 않는다
def test_nothing_filled_falls_back(mocker) -> None:
    _stub_render(mocker)
    _fake_client(mocker, {"rows": [list(row) for row in _ROWS]})
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(), ctx) == []


# 잘린 응답은 쓰지 않는다. 격자는 일부만 살려도 행 수가 달라져 어차피 못 쓴다
def test_truncated_response_is_discarded(mocker) -> None:
    _stub_render(mocker)
    _fake_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
        finish_reason="length",
    )
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(), ctx) == []
    assert any("잘려" in w for w in ctx.warnings)
    # 잘렸어도 호출은 이미 과금됐고, 실패는 재호출 조건에 걸려 두 번 부른다
    assert ctx.vision_calls == 2


# 키가 없으면 예외가 아니라 빈 리스트다. 이 규약을 깨면 라우터 폴백이 죽는다
def test_missing_key_returns_empty(mocker) -> None:
    _stub_render(mocker)
    ctx = LoadContext(config=_config(openai_api_key=None))

    assert table_vision.handle(_region(), ctx) == []
    assert any("OPENAI_API_KEY" in w for w in ctx.warnings)
    assert ctx.vision_calls == 0


# 외부 API 장애도 빈 리스트다
def test_api_failure_returns_empty(mocker) -> None:
    _stub_render(mocker)
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocker.Mock(side_effect=RuntimeError("boom")))
        )
    )
    mocker.patch.object(table_vision, "_make_client", return_value=client)
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(), ctx) == []
    assert ctx.vision_calls == 0


# 상한은 이미지 Vision 과 **공유**한다. 이미 다 쓴 문서에서는 표가 안 올라간다
def test_shares_the_vision_call_cap(mocker) -> None:
    _stub_render(mocker)
    spy = _fake_client(mocker, {"rows": []})
    config = _config(max_vision_calls_per_doc=2)
    ctx = LoadContext(config=config)
    ctx.vision_calls = 2

    assert table_vision.handle(_region(), ctx) == []
    spy.assert_not_called()
    assert any("상한" in w for w in ctx.warnings)


# rows 가 없으면 채울 격자가 없다. 부르지도 않는다
def test_no_rows_never_calls(mocker) -> None:
    spy = mocker.patch.object(table_vision, "_make_client")
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(rows=[]), ctx) == []
    spy.assert_not_called()


# blob 이 없으면 대상이 아니다. 쪽을 가로지르는 표가 이 경로로 빠진다
def test_no_blob_never_calls(mocker) -> None:
    spy = mocker.patch.object(table_vision, "_make_client")
    ctx = LoadContext(config=_config())

    assert table_vision.handle(_region(blob=None, kind=None), ctx) == []
    spy.assert_not_called()


# 굽기 — 실제 단일 쪽 PDF 로 본다. 긴 변 기준으로 DPI 를 역산하므로,
# 가로로 긴 표와 세로로 긴 표가 **비슷한 픽셀 수**로 나와야 한다
def test_render_normalizes_by_long_edge() -> None:
    import pymupdf

    sizes: list[int] = []
    for width, height in ((400, 500), (750, 270)):
        document = pymupdf.open()
        document.new_page(width=width, height=height)
        blob = document.tobytes()
        document.close()

        png = table_vision._render(blob, "pdf")
        assert png is not None
        # **픽셀을 직접 읽는다.** PNG 를 문서로 열면 rect 가 포인트로 나와 원본 치수가
        # 그대로 돌아온다 — 그러면 DPI 역산이 도는지 안 도는지 구분이 안 된다
        pixmap = pymupdf.Pixmap(png)
        sizes.append(max(pixmap.width, pixmap.height))

    # 둘 다 목표 긴 변 근처여야 한다. 고정 DPI 였다면 1.9배 벌어진다
    for size in sizes:
        assert abs(size - table_vision._TARGET_LONG_EDGE_PX) <= 40


# PDF 가 아닌 blob 은 굽지 않는다. 이미지 출처 표는 아직 표본이 없다
def test_render_rejects_non_pdf() -> None:
    assert table_vision._render(b"\x89PNG", "png") is None


# --- 미검출 재호출 ---
#
# 실측(2026-08-22)으로 같은 16쪽을 한 호출은 10/10 으로 잡고 다른 호출은 6/10 만 잡았다.
# `mark_cells` 는 기하로 센 독립적인 값이라 그 부족을 알아챌 수 있는 유일한 자[尺]다


# 응답을 순서대로 내주는 가짜. 첫 호출과 재호출이 다른 것을 돌려주는 상황을 만든다
def _sequence_client(mocker, *payloads):
    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(p, ensure_ascii=False)),
                    finish_reason="stop",
                )
            ]
        )
        for p in payloads
    ]
    create = mocker.Mock(side_effect=responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    mocker.patch.object(table_vision, "_make_client", return_value=client)
    return create


# 신호보다 적게 채우면 한 번 더 부른다
def test_undercount_triggers_one_retry(mocker) -> None:
    _stub_render(mocker)
    create = _sequence_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", ""]]},
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    ctx = LoadContext(config=_config())

    element = table_vision.handle(_region(marks=_MARKS), ctx)[0]

    assert create.call_count == 2
    assert element.metadata["vision_filled_cells"] == 2
    assert ctx.vision_calls == 2


# 재호출이 더 나쁘면 첫 응답을 지킨다
def test_retry_does_not_lose_a_better_first_answer(mocker) -> None:
    _stub_render(mocker)
    _sequence_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "", ""], ["책임성", "", ""]]},
    )
    ctx = LoadContext(config=_config())

    rows = table_vision.handle(_region(marks=[*_MARKS, (1, 2), (2, 1)]), ctx)[0].metadata["rows"]

    # 나쁜 재응답이 좋은 첫 응답을 밀어내지 않았다. 나머지 두 자리는 기하가 메운다
    assert rows[1][1] == "○"
    assert rows[2][2] == "○"


# 기하가 메울 수 없는 자리가 남으면 **버리지 않고** 채운 만큼 쓰고 경고한다.
#
# 기하 메우기가 들어온 뒤 이 경로는 좁아졌다 — 자리를 알면 대부분 메워지므로, 남는 것은
# **격자 밖을 가리키는 자리**뿐이다. 그것이 생기면 행 오프셋이 어긋났다는 신호라 경고가
# 그대로 값어치가 있다(`_marks_within` 과 쪽 경계 병합이 행 번호를 미는 자리들)
def test_unfillable_mark_keeps_partial_and_warns(mocker) -> None:
    _stub_render(mocker)
    payload = {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]}
    _sequence_client(mocker, payload, payload)
    ctx = LoadContext(config=_config())

    element = table_vision.handle(_region(marks=[*_MARKS, (9, 9)]), ctx)[0]

    assert element.metadata["vision_filled_cells"] == 2
    assert element.metadata["vision_expected_cells"] == 3
    assert any("3칸 중 2칸" in w for w in ctx.warnings)


# 크기 불일치도 재호출 조건이다. 1회차 실측에서 14쪽이 이렇게 버려졌다
def test_size_mismatch_is_retried(mocker) -> None:
    _stub_render(mocker)
    create = _sequence_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""]]},
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    ctx = LoadContext(config=_config())

    element = table_vision.handle(_region(marks=_MARKS), ctx)[0]

    assert create.call_count == 2
    assert element.metadata["vision_filled_cells"] == 2


# 상한에 걸리면 재호출도 안 한다. 상한 검사가 재호출 앞에서도 돈다
def test_retry_respects_the_call_cap(mocker) -> None:
    _stub_render(mocker)
    create = _sequence_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", ""]]},
    )
    ctx = LoadContext(config=_config(max_vision_calls_per_doc=1))

    element = table_vision.handle(_region(marks=[*_MARKS, (1, 2), (2, 1), (0, 2)]), ctx)[0]

    assert create.call_count == 1
    assert any("상한" in w for w in ctx.warnings)
    # 재호출은 막혔지만 기하 메우기는 무과금이라 그대로 돈다.
    # 5자리 중 (0,2)는 헤더라 값이 있어 안 채워진다 — **기하 메우기도 값 있는 칸은 안 건드린다**
    assert element.metadata["vision_filled_cells"] == 4


# --- 캐시 키 ---
#
# **`region.blob` 으로 키를 만들면 캐시가 한 번도 안 든다.** 그것은 PyMuPDF 가 만든 단일 쪽
# PDF 라 생성 시각·문서 ID 가 매번 달라진다(실측 2026-08-22: 같은 표 6개의 sha256 이 두
# 실행에서 6/6 전부 달랐다). 에러가 안 나서 재실행마다 전액 과금인 것을 아무도 몰랐다


# 같은 요청은 두 번째에 호출 없이 캐시로 돌아온다
def test_same_request_hits_the_cache(mocker, tmp_path) -> None:
    _stub_render(mocker)
    create = _sequence_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    config = _config(cache_enabled=True, cache_dir=tmp_path / "cache")

    first = table_vision.handle(_region(), LoadContext(config=config))
    second = table_vision.handle(_region(), LoadContext(config=config))

    assert create.call_count == 1
    assert first[0].metadata["rows"] == second[0].metadata["rows"]


# **격자가 바뀌면 다른 질문이다.** 프롬프트에 격자가 실려 나가므로 키에도 있어야 한다 —
# 대각선 셀 수정으로 `rows[0][0]` 이 바뀐 것이 정확히 그 경우다
def test_changed_grid_misses_the_cache(mocker, tmp_path) -> None:
    _stub_render(mocker)
    create = _sequence_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
        {"rows": [["원칙(수정)", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    config = _config(cache_enabled=True, cache_dir=tmp_path / "cache")

    table_vision.handle(_region(), LoadContext(config=config))
    changed = [["원칙(수정)", "기획", "데이터"], ["투명성", "", ""], ["책임성", "", ""]]
    table_vision.handle(_region(rows=changed), LoadContext(config=config))

    assert create.call_count == 2


# --- 같은 모양의 표시는 한 번만 묻는다 ---
#
# Vision 이 더해주는 것은 **"그게 무슨 기호인가"** 하나뿐이고 자리는 기하가 안다. 그래서
# 같은 벡터 경로(= 같은 서명)의 표시는 문서 안에서 한 번만 물어도 된다 — 실측(2026-08-22)
# 으로 AI가이드 PDF 의 표시 74칸이 **서명 1종**이라 표 6개가 호출 1회가 된다.
#
# **문서당 flat 1회가 아닌 이유**는 그것이 재본 적 없는 가정이기 때문이다. 서명이 둘인
# 문서는 알아서 두 번 문다


# 이미 아는 서명이면 **부르지 않는다.** 자리는 기하가 알고 기호는 기억이 안다
def test_known_signature_skips_the_call(mocker) -> None:
    client = mocker.patch.object(table_vision, "_make_client")
    render = mocker.patch.object(table_vision, "_render")
    ctx = LoadContext(config=_config(), mark_symbols={"sig-a": "○"})

    elements = table_vision.handle(_region(signature="sig-a"), ctx)

    assert client.call_count == 0
    assert render.call_count == 0
    assert ctx.vision_calls == 0
    rows = elements[0].metadata["rows"]
    assert rows[1][1] == "○" and rows[2][2] == "○"
    assert elements[0].metadata["vision_symbol_reused"] is True


# 부른 뒤에는 그 서명의 기호를 기억한다. 다음 표가 위 경로를 타게 하는 자리다
def test_signature_is_remembered_after_a_call(mocker) -> None:
    _stub_render(mocker)
    _fake_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    ctx = LoadContext(config=_config())

    table_vision.handle(_region(signature="sig-a"), ctx)

    assert ctx.mark_symbols == {"sig-a": "○"}


# **못 쓴 호출의 서명은 기억하지 않는다.** 기억하면 그 문서의 남은 표가 전부 빈 기호로
# 죽는다 — 못 쓸 응답을 캐시에 넣지 않는 것과 같은 이유다
def test_rejected_response_does_not_remember_signature(mocker) -> None:
    _stub_render(mocker)
    _fake_client(mocker, {"rows": [["원칙", "기획"], ["투명성", "○"]]})
    ctx = LoadContext(config=_config())

    elements = table_vision.handle(_region(signature="sig-a"), ctx)

    assert elements == []
    assert ctx.mark_symbols == {}


# 서명이 없으면(리전 안에 여러 모양이 섞였다) 늘 부른다 — 어느 기호를 재사용할지 모른다
def test_mixed_signatures_still_call(mocker) -> None:
    _stub_render(mocker)
    create = _fake_client(
        mocker,
        {"rows": [["원칙", "기획", "데이터"], ["투명성", "○", ""], ["책임성", "", "○"]]},
    )
    ctx = LoadContext(config=_config(), mark_symbols={"sig-a": "○"})

    table_vision.handle(_region(signature=""), ctx)

    assert create.call_count == 1
    assert ctx.vision_calls == 1
