"""표 핸들러(LlamaParse) — 구조 정보가 없는 PDF/이미지의 표를 외부 API로 파싱한다.

표 영역만 잘라 보내므로 문서 전체를 보내는 것보다 과금 페이지 수와 외부 전송량이 적다.
키가 없거나 호출이 실패하면 None을 돌려주고, 라우터가 로컬 폴백으로 내려간다.
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

    markdown = (getattr(result, "markdown", None) or "").strip()
    ctx.llamaparse_calls += 1
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
