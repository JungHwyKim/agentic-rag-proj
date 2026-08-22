"""표 핸들러(Vision 승급) — 무료 경로가 **원리적으로** 못 얻는 칸만 메운다.

승급은 2026-08-19 에 한 번 닫혔다가(`plan_log/표-경로.md` 3회차) 다시 열린 경로다. 닫은
이유는 노리던 결함 셋이 전부 무료로 해결됐다는 것이었고, 그때 적어둔 다시 볼 조건이
*"무료 경로가 못 고치는 표 결함이 실측으로 새로 나오면"* 이었다. 9회차에서 그것이 나왔다 —
AI가이드 PDF 12~17쪽 표의 체크 표시가 **벡터 드로잉**이라 `page.get_images()` 는 0개를
주고 `extract()` 는 빈 문자열을 준다. 그 표의 정보 내용이 사실상 체크 표시 전부이므로
**지금 색인되는 것은 빈 격자다.**

**그래서 이 핸들러는 표를 다시 읽지 않는다. 빈 칸만 채운다.**

`table_llamaparse` 는 표를 통째로 다시 파싱해 그 결과로 갈아끼우는데, 그러면 2~10회차가
쌓은 것(병합 셀 채우기 · 리전 되붙이기 · 쪽 경계 잇기 · 갈린 셀 잇기)을 전부 버리고 외부
모델이 다시 맞히기를 기대하게 된다. 그 넷은 각각 실측으로 오검출 0 을 확인한 것들이다.
그래서 여기서는 **PyMuPDF 격자가 정답이고** Vision 은 비어 있던 자리만 메운다.

**격자를 프롬프트에 함께 싣고 같은 크기를 요구하는 이유**도 같다. 크기가 다르게 오면
자리를 맞춰 볼 수 있지만, 열 이름으로 대응시키는 것은 이 저장소가 HWPX 병합 셀에서 겪은
자리 밀림(373행)과 같은 종류의 위험이다. **모양이 안 맞으면 통째로 버리고** 라우터가
`table_native` 로 내려간다 — 과금 한 번을 버리는 쪽이 조용히 밀린 표를 색인하는 것보다 싸다.

**결과가 마크다운이 아니라 행렬인 것이 LlamaParse 와 갈리는 지점이다.** LlamaParse 승급이
1회차에 기각된 이유가 HTML 파편을 줘서 `metadata["rows"]` 를 못 얻는 것이었는데(표청크
24→62, 여는 태그 없는 조각 16개), Vision 은 `image_vision` 이 이미 쓰는 `data_table` 규약을
그대로 쓸 수 있어 `rows` 가 살아 있다. 표 청킹이 `rows` 2차원 배열을 전제하므로 이것이 없으면
승급이 표를 고치면서 청킹을 깨뜨린다.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from typing import Any

import pymupdf

from rag_pipeline.ingestion.loader.content.base import (
    ContentRegion,
    LoadContext,
    ResultCache,
    content_hash,
    rows_to_markdown,
)
from rag_pipeline.schemas.document import Element, ElementType

# 표를 구울 때 긴 변이 이만큼 픽셀이 되도록 DPI 를 역산한다.
#
# **DPI 를 고정하지 않는 이유는 표 크기가 3배 넘게 벌어지기 때문이다** — AI가이드 12쪽은
# 391x494pt 인데 2026중등 22쪽은 751x269pt(가로 쪽)다. DPI 를 고정하면 둘의 픽셀 수가
# 그만큼 갈리고, OpenAI 는 512px 타일 단위로 과금하므로 비용도 함께 갈린다.
#
# **2048 은 모델 쪽 상한에서 나왔다.** OpenAI 는 이미지를 긴 변 2048 안에 들어오게 다시
# 줄이므로 그 위로 구워 봐야 전송량만 늘고 얻는 것이 없다. 처음에는 1600 이었는데(체크
# 표시 11.3pt 가 DPI 233 에서 36px 이 된다는 근거 하나뿐인 탐침값), 유료 실측에서
# 16쪽이 10칸 중 6칸만 잡혀 해상도를 의심하고 올렸다 — 그 결과는 verify_log 에 있다
_TARGET_LONG_EDGE_PX = 2048

# 역산한 DPI 의 상·하한. 아주 작은 표를 크게 확대하면 보간 흐림만 얻고(이미지 전처리에서
# 실측한 것과 같은 형태 — 원본 7토큰이 미세 업스케일 후 0토큰이 됐다), 아주 큰 표를 그대로
# 구우면 base64 가 상한에 다가간다
_MIN_DPI = 72
# 목표 픽셀에 닿을 수 있을 만큼은 열어 둔다. 작은 표(AI가이드 16쪽 표는 385x157pt)는
# 2048 에 닿으려면 380 이 넘게 필요하다 — 300 으로 막아 두면 목표값이 죽은 설정이 된다
_MAX_DPI = 420

# 격자를 통째로 되받으므로 이미지 설명보다 길다. 잘리면 JSON 이 깨져 전체를 버린다
_MAX_COMPLETION_TOKENS = 4000

_PROMPT = """아래는 PDF 표에서 뽑아낸 격자다. 글자는 이미 추출돼 있고, **빈 칸 중 일부에만**
글자가 아닌 표시(체크 표시, 동그라미, 도형 등)가 그려져 있다.

이미지를 보고 **빈 칸에 그려진 표시만** 채워라.

반드시 아래 JSON 형식으로만 답하라:
{{"rows": [["...", "..."], ["...", "..."]]}}

규칙:
- 행 수와 열 수를 **그대로** 유지하라. {rows}행 {cols}열이다.
- 이미 글자가 있는 칸은 **그대로 옮겨라.** 고치거나 다듬지 마라.
- 빈 칸에 표시가 있으면 **보이는 기호를 그대로** 적어라(○, ●, ✓, ■ 등). 말로 바꾸지 마라.
- 빈 칸에 아무것도 없으면 빈 문자열로 둬라. 배경색이 칠해진 것은 표시가 아니다.
- 추측하지 마라. 보이는 것만 적어라.

격자:
{grid}"""


# OpenAI 클라이언트를 만든다. 키가 없으면 None.
# **이 함수가 이 모듈의 유일한 모킹 이음새다** — 인라인화하면 외부 API 테스트가 전부 깨진다
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


# 표 영역 blob(단일 쪽 PDF)을 PNG 로 굽는다. 못 구우면 None.
#
# `image_vision._normalize_for_vision()` 을 못 쓰는 이유: 저쪽은 `PIL.Image.open()` 으로
# 판정하는데 표 blob 은 **PDF** 라 PIL 이 열지 못한다. 로더가 `blob_kind="pdf"` 로 정확히
# 표시해 두고 있으므로 여기서 갈라 받는다
def _render(blob: bytes, kind: str) -> bytes | None:
    if kind != "pdf":
        # 이미지 출처 표는 원본 바이트가 이미 이미지다. 그 경로는 아직 표본이 없다
        return None

    try:
        with pymupdf.open(stream=blob, filetype="pdf") as document:
            if document.page_count < 1:
                return None
            page = document[0]
            long_edge = max(page.rect.width, page.rect.height)
            if long_edge <= 0:
                return None
            dpi = int(round(_TARGET_LONG_EDGE_PX / long_edge * 72))
            dpi = max(_MIN_DPI, min(_MAX_DPI, dpi))
            return page.get_pixmap(dpi=dpi).tobytes("png")
    except Exception:  # noqa: BLE001 - 깨진 blob 하나가 적재를 멈추면 안 된다
        return None


# 모델이 돌려준 격자를 원본에 겹친다. 모양이 다르면 None (라우터가 폴백한다).
#
# **채우기는 한 방향뿐이다** — 원본이 비어 있던 자리에만 넣고, 값이 있던 칸은 모델이 무엇을
# 보냈든 건드리지 않는다. 그래야 `_fill_merged_cells` 가 채운 세로 병합 값과
# `_join_split_cells` 가 이어 붙인 갈린 셀이 그대로 살아남는다
def _merge_rows(original: list[list[str]], returned: Any) -> tuple[list[list[str]], int] | None:
    if not isinstance(returned, list) or len(returned) != len(original):
        return None

    merged: list[list[str]] = []
    filled = 0
    for row, incoming in zip(original, returned, strict=True):
        if not isinstance(incoming, list) or len(incoming) != len(row):
            return None

        new_row = list(row)
        for index, value in enumerate(incoming):
            if new_row[index].strip():
                continue
            text = "" if value is None else str(value).strip()
            if text:
                new_row[index] = text
                filled += 1
        merged.append(new_row)

    return merged, filled


# 응답 문자열에서 격자를 꺼낸다. 못 꺼내면 None.
#
# `image_vision._parse_response()` 처럼 값을 건져내지 **않는다.** 저쪽은 설명 문장이라
# 일부만 살려도 쓸모가 있지만, 격자는 잘리면 행 수가 달라져 어차피 `_merge_rows` 가 버린다.
# 반쯤 온 격자를 억지로 맞추는 것이 이 파일이 피하려는 바로 그 위험이다
def _rows_of(content: str) -> Any | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get("rows")


# Vision 을 불러 채워진 격자를 얻는다. 실패하면 None
def _call_vision(
    region: ContentRegion, rows: list[list[str]], ctx: LoadContext, *, use_cache: bool = True
) -> Any | None:
    cfg = ctx.config
    cache = ResultCache(cfg.cache_dir, enabled=cfg.cache_enabled)

    # **키를 `region.blob` 으로 만들면 안 된다.** 그것은 `pdf.py:_crop_pdf()` 가 만든 단일 쪽
    # PDF 인데 PyMuPDF 가 생성 시각과 문서 ID 를 넣어 **매번 다른 바이트가 나온다**(실측
    # 2026-08-22: 같은 표를 두 번 뽑아 sha256 이 6/6 전부 달랐다). 그래서 캐시가 **한 번도
    # 든 적이 없고** 재실행마다 전액 과금이었는데 에러가 안 나서 아무도 몰랐다.
    #
    # 그래서 **실제로 보내는 것**으로 키를 만든다 — 구운 PNG 와 격자다. 렌더 결과는
    # 결정론적이다(같은 실측에서 6/6 일치). 격자를 넣는 것도 필수다: 프롬프트에 그것이
    # 실려 나가므로 `rows` 가 바뀌면 다른 질문인데, 안 넣으면 옛 답이 돌아온다 —
    # 대각선 셀 수정으로 `rows[0][0]` 이 바뀐 것이 정확히 그 경우다.
    # 해상도는 PNG 에 이미 반영돼 있어 salt 에 따로 안 넣는다
    png = _render(region.blob or b"", region.blob_kind or "")
    if png is None:
        ctx.warn("표 영역을 이미지로 굽지 못해 로컬 추출로 폴백했습니다.")
        return None

    grid = json.dumps(rows, ensure_ascii=False)
    key = content_hash(png + grid.encode("utf-8"), salt=f"table_vision:{cfg.vision_model}")

    # 재시도는 캐시를 안 읽는다. 같은 키를 읽으면 방금 받은 그 응답이 돌아온다
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached.get("rows")

    # 상한은 이미지와 **공유**한다. 목적이 문서당 Vision 과금 총량 통제라 축을 나누면
    # 둘 다 예산이 남았는데 한쪽만 막히는 상황이 생긴다
    if ctx.vision_calls >= cfg.max_vision_calls_per_doc:
        ctx.warn(
            f"Vision 호출 상한({cfg.max_vision_calls_per_doc}회)에 도달해 "
            "나머지 표는 로컬 추출로 처리했습니다."
        )
        return None

    client = _make_client(cfg.openai_api_key)
    if client is None:
        ctx.warn("OPENAI_API_KEY가 없어 표를 로컬 추출로 처리했습니다.")
        return None

    prompt = _PROMPT.format(
        rows=len(rows),
        cols=len(rows[0]) if rows else 0,
        grid=grid,
    )
    b64 = base64.b64encode(png).decode("ascii")

    try:
        response = client.chat.completions.create(
            model=cfg.vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=_MAX_COMPLETION_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 - 외부 API 장애가 적재를 멈추면 안 된다
        ctx.warn(f"표 Vision 호출 실패로 로컬 추출로 폴백했습니다: {type(exc).__name__}")
        return None

    # 여기서 올린다. 아래 해석이 실패해도 이 호출은 이미 과금됐다
    ctx.vision_calls += 1

    try:
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            ctx.warn("표 Vision 응답이 길이 상한에서 잘려 로컬 추출로 폴백했습니다.")
            return None
        returned = _rows_of((choice.message.content or "").strip())
    except Exception as exc:  # noqa: BLE001 - 응답 모양이 바뀌어도 폴백으로 내려가야 한다
        ctx.warn(f"표 Vision 응답을 해석하지 못해 로컬 추출로 폴백했습니다: {type(exc).__name__}")
        return None

    if returned is None:
        return None

    # **못 쓸 응답은 캐시에 넣지 않는다.** 넣으면 다음 실행이 그것을 꺼내 또 버리고, 그때마다
    # 재호출이 한 번씩 더 나간다 — 실측(2026-08-22)으로 캐시가 살아난 첫 실행에서 표 6개 중
    # 하나가 정확히 그 상태였다. 모양 검사는 `_merge_rows` 가 하는 것과 같은 것을 얕게 본다
    if not isinstance(returned, list) or len(returned) != len(rows):
        return returned

    cache.set(key, {"rows": returned})
    return returned


# 모델이 놓친 자리를 기하가 아는 자리로 메운다. 채운 칸 수를 돌려준다.
#
# **자리는 무과금으로 정확히 알고 있고 모델이 더해주는 것은 기호 하나뿐이다.** 실측
# (2026-08-22, 유료 3회)으로 Vision 은 같은 표에서 자리를 실행마다 다르게 잡았고
# (65/68/70 of 73, 틀리는 쪽이 매번 옮겨 다녔다) 해상도를 올려도 없어지지 않았다.
# 반면 기하는 오검출 0 · 미검출 0 이다(`pdf.py:_MARK_AREA_RATIO`).
#
# **기호는 지어내지 않는다** — 그 응답이 실제로 쓴 것 중 최빈값을 쓴다. 모델이 한 칸도
# 못 채웠으면 무엇을 넣을지 알 수 없으므로 아무것도 안 한다. 이것이 *"문서에 없는 글자를
# 만들지 않는다"* 를 지키는 선이다(6회차가 `항목 2`·`항목 3` 번호 붙이기를 기각한 잣대)
def _fill_from_geometry(rows: list[list[str]], marks: list[tuple[int, int]]) -> int:
    missing = [
        (r, c)
        for r, c in marks
        if 0 <= r < len(rows) and 0 <= c < len(rows[r]) and not rows[r][c].strip()
    ]
    if not missing:
        return 0

    written = [cell for row in rows for cell in row if cell.strip()]
    symbols = Counter(
        cell for cell in written if len(cell) <= 2 and not cell.isalnum()
    )
    if not symbols:
        return 0

    symbol = symbols.most_common(1)[0][0]
    for r, c in missing:
        rows[r][c] = symbol
    return len(missing)


# 한 번 부르고 겹쳐 본 결과. 실패하면 None
def _attempt(
    region: ContentRegion, rows: list[list[str]], ctx: LoadContext, *, use_cache: bool
) -> tuple[list[list[str]], int] | None:
    returned = _call_vision(region, rows, ctx, use_cache=use_cache)
    if returned is None:
        return None

    merged = _merge_rows(rows, returned)
    if merged is None:
        ctx.warn("표 Vision 이 다른 크기의 격자를 돌려줘 그 응답을 버렸습니다.")
        return None
    return merged


# 표 리전의 빈 칸을 Vision 으로 메운다. 실패 시 빈 리스트를 반환해 라우터가 폴백하도록 한다.
#
# **`mark_cells` 를 정답 수로 쓴다.** 그것은 기하로 센 독립적인 값이라(`pdf.py:_mark_cells`)
# 모델 응답이 얼마나 모자란지를 알려준다. 실측(2026-08-22)으로 같은 16쪽을 한 호출은
# 10/10 으로 잡고 다른 호출은 6/10 만 잡았다 — **규칙이 틀린 것이 아니라 호출마다 흔들린다**
# (embedding 절의 *"호출 간 비트 재현성은 보장되지 않는다"* 와 같은 성질이다).
#
# 그래서 부족하면 **한 번만** 다시 부르고 더 나은 쪽을 쓴다. 크기 불일치로 버린 경우도
# 같은 조건에 걸린다(채운 칸이 0이다) — 실측 1회차의 14쪽 거절이 이 규칙으로 구제된다.
# **두 번째도 부족하면 버리지 않고 채운 만큼 쓴다.** 부분이 아무것도 없는 것보다 낫고,
# 부족은 아래 metadata 와 경고로 드러난다
def handle(region: ContentRegion, ctx: LoadContext) -> list[Element]:
    rows = region.rows or []
    if not region.blob or not rows:
        return []

    best = _attempt(region, rows, ctx, use_cache=True)
    expected = len(region.mark_cells)

    if best is None or best[1] < expected:
        again = _attempt(region, rows, ctx, use_cache=False)
        if again is not None and (best is None or again[1] > best[1]):
            best = again

    if best is None:
        return []

    filled_rows, filled = best
    # 모델이 놓친 자리를 기하로 메운다. 자리는 이미 알고 있고 모자란 것은 **기호**뿐이다
    filled += _fill_from_geometry(filled_rows, region.mark_cells)

    if not filled:
        # 채운 것이 없으면 `table_native` 와 결과가 같다. 같은 것을 두 이름으로 내지 않는다
        return []

    if filled < expected:
        ctx.warn(
            f"표 Vision 이 표시 {expected}칸 중 {filled}칸만 옮겼습니다"
            f"({region.page}쪽). 나머지는 빈 칸으로 남습니다."
        )

    markdown = rows_to_markdown(filled_rows)
    if not markdown:
        return []

    metadata = dict(region.metadata)
    metadata["rows"] = filled_rows
    metadata["table_source"] = "native+vision"
    metadata["vision_model"] = ctx.config.vision_model
    metadata["vision_filled_cells"] = filled
    # 개수만으로는 부족을 못 알아챈다. 기하가 센 기대값을 나란히 남긴다 —
    # cleaner 가 `removed_elements` 옆에 `removed_texts` 를 남긴 것과 같은 판단이다
    metadata["vision_expected_cells"] = expected
    if region.bbox is not None:
        metadata["bbox"] = list(region.bbox)

    return [
        Element(
            type=ElementType.TABLE,
            text=markdown,
            page=region.page,
            order=region.order,
            metadata=metadata,
        )
    ]
