"""2차 라우팅 — 콘텐츠 유형에 맞는 핸들러로 리전을 분배한다.

여기가 '어떻게 텍스트로 만들 것인가'를 결정하는 유일한 지점이다. 문서 로더는
'어디에 무엇이 있는가'만 알려주므로, 표를 LlamaParse로 볼지 로컬로 볼지,
이미지를 Vision으로 볼지 OCR로 볼지는 전부 이 파일과 설정으로만 바뀐다.
"""

from __future__ import annotations

from rag_pipeline.config import ContentRoutingConfig
from rag_pipeline.ingestion.loader.content import (
    image_ocr,
    image_vision,
    table_llamaparse,
    table_native,
    text_native,
)
from rag_pipeline.ingestion.loader.content.base import ContentRegion, ContentType, LoadContext
from rag_pipeline.ingestion.loader.content.classify import ImageRoute, classify
from rag_pipeline.schemas.document import Element


# 콘텐츠 리전을 알맞은 핸들러로 보내고, 실패하면 로컬 폴백으로 내려간다
class ContentRouter:
    def __init__(self, config: ContentRoutingConfig) -> None:
        self.config = config

    # 리전 하나를 Element 목록으로 변환하는 공개 진입점
    def route(self, region: ContentRegion, ctx: LoadContext) -> list[Element]:
        if region.content_type is ContentType.TEXT:
            return text_native.handle(region, ctx)
        if region.content_type is ContentType.TABLE:
            return self._route_table(region, ctx)
        if region.content_type is ContentType.IMAGE:
            return self._route_image(region, ctx)
        return []

    # 표: 네이티브 구조가 있으면 그대로, PDF/이미지 출처면 LlamaParse 시도 후 로컬 폴백
    def _route_table(self, region: ContentRegion, ctx: LoadContext) -> list[Element]:
        use_llamaparse = self.config.table_pdf_handler == "llamaparse" and region.blob is not None

        if use_llamaparse:
            elements = table_llamaparse.handle(region, ctx)
            if elements:
                return elements
            # LlamaParse가 실패했거나 키가 없다 -> 로컬 추출 결과로 내려간다

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

        decision = classify(image, self.config, bbox=region.bbox)

        if decision.route is ImageRoute.SKIP:
            return []

        if decision.route is ImageRoute.OCR:
            return image_ocr.handle(region, ctx, precomputed=decision.ocr)

        elements = image_vision.handle(region, ctx)
        if elements:
            return elements

        # Vision이 실패했다 -> classify에서 이미 돌린 OCR 결과라도 살려서 쓴다
        return image_ocr.handle(region, ctx, precomputed=decision.ocr)


# 여러 리전을 순서대로 처리해 Element 목록으로 합친다. order 기준으로 재정렬한다
def route_all(
    regions: list[ContentRegion], ctx: LoadContext, router: ContentRouter
) -> list[Element]:
    elements: list[Element] = []
    for region in sorted(regions, key=lambda r: r.order):
        elements.extend(router.route(region, ctx))
    return sorted(elements, key=lambda e: e.order)
