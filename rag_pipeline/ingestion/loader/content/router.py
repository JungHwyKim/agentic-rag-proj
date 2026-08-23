"""2차 라우팅 — 콘텐츠 유형에 맞는 핸들러로 리전을 분배한다.

여기가 '어떻게 텍스트로 만들 것인가'를 결정하는 유일한 지점이다. 문서 로더는
'어디에 무엇이 있는가'만 알려주므로, 표를 LlamaParse로 볼지 로컬로 볼지,
이미지를 Vision으로 볼지 OCR로 볼지는 전부 이 파일과 설정으로만 바뀐다.

**설정은 `ctx.config` 에서만 읽는다.** 핸들러가 전부 그렇게 하는데 라우터만 생성자로 받은
사본을 봤다. 오늘은 `document/base.py:load()` 가 같은 객체를 라우터와 `LoadContext` 양쪽에
넘겨 **갈릴 수가 없지만**, 출처가 둘이면 언젠가 갈린다. 그래서 라우터는 상태를 안 갖는다 —
`ContentRouter()` 는 인자를 받지 않는다.
"""

from __future__ import annotations

from rag_pipeline.ingestion.loader.content import (
    image_ocr,
    image_vision,
    table_llamaparse,
    table_native,
    table_vision,
    text_native,
)
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.content.classify import ImageRoute, classify
from rag_pipeline.schemas.document import Element


# 콘텐츠 리전을 알맞은 핸들러로 보내고, 실패하면 로컬 폴백으로 내려간다
class ContentRouter:
    # 리전 하나를 Element 목록으로 변환하는 공개 진입점
    def route(self, region: ContentRegion, ctx: LoadContext) -> list[Element]:
        if region.content_type is ContentType.TEXT:
            return text_native.handle(region, ctx)
        if region.content_type is ContentType.TABLE:
            return self._route_table(region, ctx)
        if region.content_type is ContentType.IMAGE:
            return self._route_image(region, ctx)
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

    # 이미지: 글자 위주면 OCR, 그림 위주면 Vision. Vision 실패 시 OCR로 폴백
    def _route_image(self, region: ContentRegion, ctx: LoadContext) -> list[Element]:
        if not region.blob:
            return []

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


# 여러 리전을 순서대로 처리해 Element 목록으로 합친다. order 기준으로 재정렬한다
def route_all(
    regions: list[ContentRegion], ctx: LoadContext, router: ContentRouter
) -> list[Element]:
    elements: list[Element] = []
    for region in sorted(regions, key=lambda r: r.order):
        elements.extend(router.route(region, ctx))
    return sorted(elements, key=lambda e: e.order)
