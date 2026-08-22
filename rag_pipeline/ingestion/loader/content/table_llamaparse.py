"""표 핸들러(LlamaParse) — 구조 정보가 없는 PDF/이미지의 표를 외부 API로 파싱한다.

표 영역만 잘라 보내므로 문서 전체를 보내는 것보다 과금 페이지 수와 외부 전송량이 적다.
키가 없거나 호출이 실패하면 None을 돌려주고, 라우터가 로컬 폴백으로 내려간다.

**응답의 `markdown` 은 문자열이 아니다.** `llama-cloud` 의 `Markdown` 은 `pages` 를 담은
pydantic 모델이고, 성공한 페이지에만 `markdown: str` 이 있으며 실패한 페이지에는 `error` 가
대신 들어온다. 실제로 돌려보기 전까지 이것을 문자열로 알고 `.strip()` 을 불러 `AttributeError`
로 죽었다(2026-08-18 실측, 실파일 유료 패스 첫 실행). 표 영역 하나만 보내므로 보통 페이지는
하나지만 여러 장이 와도 이어 붙인다.

**응답을 해석하는 구간도 `try` 안에 둔다.** 호출만 감싸면 응답 모양이 바뀔 때 예외가
`handle()` 밖으로 새고, 그러면 라우터의 `table_native` 폴백이 발동할 기회 자체가 없다 —
실제로 그 예외가 `ingest()` 까지 올라가 적재 전체를 멈췄다. 호출 카운터는 해석보다 **먼저**
올린다. 해석이 실패해도 그 호출은 이미 과금됐기 때문이다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.ingestion.loader.content.base import (
    ContentRegion,
    LoadContext,
    ResultCache,
    content_hash,
)
from rag_pipeline.schemas.document import Element, ElementType


# LlamaParse 클라이언트를 만든다. 키가 없으면 None
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from llama_cloud import Client
    except ImportError:
        return None
    return Client(api_key=api_key)


# 응답에서 마크다운을 꺼낸다. `Markdown` 은 문자열이 아니라 pages 를 담은 모델이라
# 성공한 페이지의 markdown 만 모은다 — 실패한 페이지에는 error 가 대신 들어온다
def _markdown_of(result: Any) -> str:
    container = getattr(result, "markdown", None)
    if container is None:
        return ""
    # 옛 SDK 나 다른 tier 가 문자열을 그대로 주는 경우도 받아 둔다
    if isinstance(container, str):
        return container.strip()

    parts: list[str] = []
    for page in getattr(container, "pages", None) or []:
        text = getattr(page, "markdown", None)
        if text:
            parts.append(str(text).strip())
    return "\n\n".join(parts).strip()


# 표 영역 이미지를 LlamaParse로 파싱해 마크다운을 얻는다. 실패하면 None
def _parse_blob(blob: bytes, kind: str, ctx: LoadContext) -> str | None:
    cfg = ctx.config
    cache = ResultCache(cfg.cache_dir, enabled=cfg.cache_enabled)
    key = content_hash(blob, salt=f"llamaparse:{cfg.llamaparse_tier}:{cfg.llamaparse_version}")

    cached = cache.get(key)
    if cached is not None:
        return cached.get("markdown") or None

    client = _make_client(cfg.llama_cloud_api_key)
    if client is None:
        ctx.warn("LLAMA_CLOUD_API_KEY가 없어 표를 로컬 추출로 처리했습니다.")
        return None

    filename = "table.pdf" if kind == "pdf" else "table.png"
    mime = "application/pdf" if kind == "pdf" else "image/png"

    try:
        result = client.parsing.parse(
            tier=cfg.llamaparse_tier,
            version=cfg.llamaparse_version,
            upload_file=(filename, blob, mime),
            expand=["markdown"],
            timeout=cfg.llamaparse_timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001 - 외부 API 장애가 적재를 멈추면 안 된다
        ctx.warn(f"LlamaParse 호출 실패로 로컬 추출로 폴백했습니다: {type(exc).__name__}")
        return None

    # 여기서 올린다. 아래 해석이 실패해도 이 호출은 이미 과금됐다
    ctx.llamaparse_calls += 1

    try:
        markdown = _markdown_of(result)
    except Exception as exc:  # noqa: BLE001 - 응답 모양이 바뀌어도 폴백으로 내려가야 한다
        ctx.warn(f"LlamaParse 응답을 해석하지 못해 로컬 추출로 폴백했습니다: {type(exc).__name__}")
        return None

    cache.set(key, {"markdown": markdown})
    return markdown or None


# 표 리전을 LlamaParse로 처리. 실패 시 빈 리스트를 반환해 라우터가 폴백하도록 한다
def handle(region: ContentRegion, ctx: LoadContext) -> list[Element]:
    if not region.blob:
        return []

    markdown = _parse_blob(region.blob, region.blob_kind or "png", ctx)
    if not markdown:
        return []

    metadata = dict(region.metadata)
    metadata["table_source"] = "llamaparse"
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
