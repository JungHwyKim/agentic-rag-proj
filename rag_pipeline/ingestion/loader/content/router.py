"""2차 라우팅 — 콘텐츠 유형에 맞는 핸들러로 리전을 분배한다.

여기가 '어떻게 텍스트로 만들 것인가'를 결정하는 유일한 지점이다. 문서 로더는
'어디에 무엇이 있는가'만 알려주므로, 표를 LlamaParse로 볼지 로컬로 볼지,
이미지를 Vision으로 볼지 OCR로 볼지는 전부 이 파일과 설정으로만 바뀐다.

**설정은 `ctx.config` 에서만 읽는다.** 핸들러가 전부 그렇게 하는데 라우터만 생성자로 받은
사본을 봤다. 오늘은 `document/base.py:load()` 가 같은 객체를 라우터와 `LoadContext` 양쪽에
넘겨 **갈릴 수가 없지만**, 출처가 둘이면 언젠가 갈린다. 그래서 라우터는 상태를 안 갖는다 —
`ContentRouter()` 는 인자를 받지 않는다.

## 무료 OCR 만 병렬로 돈다

전면 스캔 PDF 에서 이 루프가 적재 시간의 거의 전부였다 — 실측(2026-08-24, 47쪽 스캔본
13MB)으로 `ingest()` 전체 237.5초 중 Tesseract 가 213.9초(약 90%)다. 쪽당 4.55초가 47번
직렬로 쌓인 값이고, 사용자는 3분 30초에 창을 닫았다.

`pytesseract` 는 Tesseract 를 subprocess 로 부르므로 대기 중 GIL 을 놓는다. 스레드 병렬이
실제로 듣는 조건이고, **가르는 축은 유료/무료다.**

- **무료 OCR 은 병렬로 돈다.** 다만 워커는 공유 `ctx` 가 아니라 **자식 `LoadContext`**
  (`ctx.child()`)에 쓰고, 조립 구간이 문서 순서 자리에서 `ctx.absorb()` 로 합친다
- **유료 Vision·LlamaParse 는 직렬로 남는다.** `ctx.vision_calls` 상한과 `ctx.mark_symbols`
  메모가 **검사와 갱신으로 갈라져** 있어, 병렬로 올리면 상한을 넘겨 과금되고 "서명당 1회"가
  여러 번으로 늘어난다. 락을 더하는 것과 다른 일이라 이번 범위 밖이다
- **표·텍스트도 직렬이다.** 실측에서 비용이 이미지 쪽에 몰려 있고, 표는 유료 경로를 탈 수
  있어 위와 같은 이유가 걸린다

**판정(`classify`)을 먼저 해야 무료/유료가 갈린다.** 그런데 그 판정 안의 `run_ocr` 이 위
실측의 정확히 절반(108.0초)이다 — 가르는 행위 자체가 비용의 절반이라, 판정도 병렬 구간
안에 있어야 한다. 그래서 여는 것 · 판정 · 무료 OCR 이 **한 워커 안에서 이어진다**.

**디코드한 이미지를 병렬 구간 밖으로 내보내지 않는다.** 판정 결과를 전부 모아 두는 모양이
더 단순해 보이지만, 47쪽 스캔본이면 2500x3535 RGB 가 47장이라 **1.2GB 를 동시에 들고
있게 된다**(`Image.open` 은 게으르지만 `classify` 안의 `preprocess` 가 `load()` 를 강제한다).
이미지의 수명이 워커 안에서 끝나면 살아 있는 것은 워커 수만큼이다. Vision 이 실패해 OCR 로
되돌아갈 때만 다시 연다 — 드문 경로라 재디코드 값이 싸다.

**반환 직전의 `e.order` 재정렬이 이 병렬화의 전제다.** 원래부터 있던 줄이고,
`ContentRegion.order` 가 *"핸들러가 병렬 실행돼도 이 값으로 다시 정렬한다"* 며 이 자리를
미리 열어 뒀다. 지우면 요소 순서가 완료 순서로 흐트러진다.

**경고는 그 정렬로 못 되돌린다 — 그래서 자식 컨텍스트가 있다.** 요소는 `order` 를 들고
다니지만 **경고는 문자열이라 자기 자리를 모른다.** 워커가 공유 목록에 바로 append 하면
순서가 완료 시각에 실리고, 그 뒤로는 복원할 근거가 없다. 자식에 모았다가 조립 루프가
문서 순서로 흡수하면 직렬 핸들러(텍스트·표·Vision)의 경고와도 제자리에 섞인다.
`absorb()` 가 **유료 카운터가 비었는지 함께 검사한다** — 그 가드의 이유는 `base.py` 에 있다.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image

from rag_pipeline.ingestion.loader.content import (
    image_ocr,
    image_vision,
    table_llamaparse,
    table_native,
    table_vision,
    text_native,
)
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.content.classify import Decision, ImageRoute, classify
from rag_pipeline.schemas.document import Element

# 무료 OCR 리전을 동시에 몇 개까지 돌릴지의 상한.
#
# **한계값이 아니라 무릎에서 골랐다.** 실측(2026-08-24, 16코어, 47쪽 스캔본) —
# 1: 239.8초 / 2: 137.7초 / 4: 75.6초 / 8: 51.8초 / 16: 42.3초. 직전 대비 이득이
# -102.1 / -62.1 / -23.8 / **-9.5** 초라 8에서 꺾인다. 16이 더 빠르긴 하다(18%) —
# **"8을 넘기면 안 빨라진다"가 아니다.** 워커를 두 배로 늘려 9.5초를 더 얻는 대신,
# 색인이 도는 42초 동안 코어를 전부 물지 않는 쪽을 골랐다. 이 로더는 uvicorn 안에서
# `run_in_threadpool` 로 돌고 같은 프로세스가 질의도 받는다.
#
# **선형으로 안 줄어드는 이유가 둘이다.** Tesseract 자체가 내부 멀티스레드라 프로세스끼리
# 코어를 뺏고, 그리고 병렬로 안 되는 바닥이 23.6초 있다(PyMuPDF 쪽 루프·300dpi 래스터·
# cleaner — 직렬 237.5초에서 Tesseract 213.9초를 뺀 값). 16워커의 이론 하한이 37초라
# 실측 42.3초는 이미 그 근처다 — 워커를 더 늘려서 얻을 것이 없다.
#
# **설정(.env)으로 열지 않았다.** 이 저장소는 합법값이 실제로 갈리는 두 번째 경로가 생긴
# 뒤에 설정을 연다 — 지금은 실측으로 값 하나를 고르면 되는 자리다
_MAX_OCR_WORKERS = 8


# 병렬 구간이 이미지 리전 하나에 대해 내놓는 것.
#
# `elements` 가 None 이면 **아직 안 끝났다**는 뜻이다 — 유료 핸들러가 필요해 직렬 구간으로
# 넘긴다. 빈 목록과 구분해야 하므로 참/거짓으로 보면 안 된다.
#
# `context` 는 그 워커가 쓴 자식 `LoadContext` 다. 경고를 **여기 담아 나와야** 조립 구간이
# 문서 순서로 합칠 수 있다. `eq=False` 인 것은 가변 컨텍스트를 담아 비교·해시가 뜻을 잃어서다
@dataclass(frozen=True, eq=False)
class ImageOutcome:
    elements: list[Element] | None
    decision: Decision | None
    context: LoadContext


# 실제로 띄울 워커 수. 리전이 적으면 그만큼만 띄운다
def _ocr_workers(count: int) -> int:
    return max(1, min(_MAX_OCR_WORKERS, os.cpu_count() or 1, count))


# 이미지 리전 하나를 병렬 구간에서 처리한다 — 여는 것 · 판정 · **무료 OCR 까지**.
#
# 유료로 갈리면 요소를 안 만들고 판정만 돌려준다. 이때 이미지를 함께 넘기지 않는 것이
# 요점이다(모듈 docstring 의 메모리 문단 참고) — 필요해지면 핸들러가 다시 연다
def _route_image_free(
    region: ContentRegion, ctx: LoadContext, router: ContentRouter
) -> ImageOutcome:
    # **공유 ctx 가 아니라 자식에 쓴다.** 워커가 공유 목록에 바로 쓰면 경고 순서가 완료
    # 시각에 실린다 — 조립 구간이 문서 순서로 합칠 수 있도록 자식째 돌려준다
    child = ctx.child()

    try:
        image = image_ocr.open_image(region.blob or b"")
    except OSError:
        child.warn("이미지를 열 수 없어 건너뛰었습니다.")
        return ImageOutcome(elements=[], decision=None, context=child)

    decision = classify(image, child.config, bbox=region.bbox)
    if decision.route is ImageRoute.VISION:
        return ImageOutcome(elements=None, decision=decision, context=child)

    # SKIP·OCR 판정을 여기서 다시 쓰지 않는다 — 그 분기는 `_route_image` 한 곳에만 둔다
    return ImageOutcome(
        elements=router.route(region, child, decision=decision, image=image),
        decision=decision,
        context=child,
    )


# 이미지 리전 전부를 병렬로 훑는다. 리전 목록에서의 자리(index)를 키로 돌려준다.
#
# **`order` 가 아니라 index 로 키를 잡는다** — order 는 문서 안에서 유일해야 하지만 그걸
# 여기서 강제하지 않으므로, 중복이 있으면 결과가 조용히 덮인다
def _route_images_free(
    ordered: list[ContentRegion], ctx: LoadContext, router: ContentRouter
) -> dict[int, ImageOutcome]:
    targets = [
        index
        for index, region in enumerate(ordered)
        if region.content_type is ContentType.IMAGE and region.blob
    ]
    if not targets:
        return {}
    if len(targets) == 1:
        return {targets[0]: _route_image_free(ordered[targets[0]], ctx, router)}

    with ThreadPoolExecutor(max_workers=_ocr_workers(len(targets))) as pool:
        outcomes = pool.map(lambda index: _route_image_free(ordered[index], ctx, router), targets)
        return dict(zip(targets, outcomes, strict=True))


# 콘텐츠 리전을 알맞은 핸들러로 보내고, 실패하면 로컬 폴백으로 내려간다
class ContentRouter:
    # 리전 하나를 Element 목록으로 변환하는 공개 진입점.
    #
    # `decision`·`image` 는 병렬 구간이 이미 구한 것을 되돌려주는 자리다. 안 넘기면 이
    # 함수가 직접 열고 판정하므로, 라우터를 단독으로 부르는 경로(테스트)는 그대로 산다
    def route(
        self,
        region: ContentRegion,
        ctx: LoadContext,
        *,
        decision: Decision | None = None,
        image: Image.Image | None = None,
    ) -> list[Element]:
        if region.content_type is ContentType.TEXT:
            return text_native.handle(region, ctx)
        if region.content_type is ContentType.TABLE:
            return self._route_table(region, ctx)
        if region.content_type is ContentType.IMAGE:
            return self._route_image(region, ctx, decision=decision, image=image)
        return []

    # 표: 네이티브 구조가 있으면 그대로, PDF/이미지 출처면 외부 API 시도 후 로컬 폴백.
    #
    # **`hybrid` 는 신호가 있을 때만 올린다.** 이미지 쪽 `image_handler="hybrid"` 와 같은
    # 모양이고 이유도 같다 — 무료 로컬을 기본으로 두고, 무료로는 못 얻는 것이 실제로
    # 있을 때만 과금한다. 그 신호가 `mark_cells` 이고 로더가 잰다(`pdf.py:_mark_cells`).
    # 켜 두어도 이 코퍼스에서 올라가는 표는 94개 중 6개다
    def _route_table(self, region: ContentRegion, ctx: LoadContext) -> list[Element]:
        handler = ctx.config.table_pdf_handler
        has_blob = region.blob is not None

        if handler == "llamaparse" and has_blob:
            elements = table_llamaparse.handle(region, ctx)
            if elements:
                return elements
            # LlamaParse가 실패했거나 키가 없다 -> 로컬 추출 결과로 내려간다

        if handler == "hybrid" and has_blob and region.mark_cells:
            elements = table_vision.handle(region, ctx)
            if elements:
                return elements
            # 승급이 실패했거나 채울 것이 없었다 -> 로컬 추출 결과가 이미 정답이다

        return table_native.handle(region, ctx)

    # 이미지: 글자 위주면 OCR, 그림 위주면 Vision. Vision 실패 시 OCR로 폴백.
    #
    # `image` 가 None 이면 핸들러가 `region.blob` 에서 다시 연다 — 병렬 구간이 유료로
    # 넘긴 리전이 그 경우다
    def _route_image(
        self,
        region: ContentRegion,
        ctx: LoadContext,
        *,
        decision: Decision | None = None,
        image: Image.Image | None = None,
    ) -> list[Element]:
        if not region.blob:
            return []

        if decision is None:
            try:
                image = image_ocr.open_image(region.blob)
            except OSError:
                ctx.warn("이미지를 열 수 없어 건너뛰었습니다.")
                return []
            decision = classify(image, ctx.config, bbox=region.bbox)

        if decision.route is ImageRoute.SKIP:
            return []

        if decision.route is ImageRoute.OCR:
            return image_ocr.handle(region, ctx, precomputed=decision.ocr, image=image)

        elements = image_vision.handle(region, ctx)
        if elements:
            return elements

        # Vision이 실패했다 -> classify에서 이미 돌린 OCR 결과라도 살려서 쓴다
        return image_ocr.handle(region, ctx, precomputed=decision.ocr, image=image)


# 여러 리전을 처리해 Element 목록으로 합친다. 무료 OCR 만 병렬이고 order 기준으로 재정렬한다.
# 무엇을 왜 병렬로 돌리는지는 모듈 docstring 에 있다
def route_all(
    regions: list[ContentRegion], ctx: LoadContext, router: ContentRouter
) -> list[Element]:
    ordered = sorted(regions, key=lambda r: r.order)
    outcomes = _route_images_free(ordered, ctx, router)

    elements: list[Element] = []
    for index, region in enumerate(ordered):
        outcome = outcomes.get(index)
        if outcome is not None:
            # **이 자리에서 합치는 것이 곧 순서다.** 경고는 문자열이라 자기 자리를 몰라,
            # 문서 순서로 도는 이 루프가 유일하게 순서를 되돌릴 수 있는 지점이다.
            # 유료가 병렬 구간에서 돌았으면 여기서 터진다(`LoadContext.absorb` 참고)
            ctx.absorb(outcome.context)
        # 빈 목록도 끝난 것이다 — 참/거짓으로 보면 이미 한 일을 다시 한다
        if outcome is not None and outcome.elements is not None:
            elements.extend(outcome.elements)
            continue
        decision = outcome.decision if outcome is not None else None
        elements.extend(router.route(region, ctx, decision=decision))
    return sorted(elements, key=lambda e: e.order)
