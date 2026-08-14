"""이미지 핸들러(Vision) — 차트·도표·사진의 의미를 gpt-4.1-mini로 텍스트화한다.

OCR이 축 라벨 글자만 나열하는 것과 달리, 그래프가 무엇을 보여주는지와
표에 담긴 수치까지 검색 가능한 문장으로 옮긴다.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from rag_pipeline.ingestion.loader.content.base import (
    ContentRegion,
    LoadContext,
    ResultCache,
    content_hash,
    rows_to_markdown,
)
from rag_pipeline.schemas.document import Element, ElementType

# 검색에 바로 쓰일 한국어 텍스트를 얻기 위한 고정 프롬프트
_PROMPT = """이 이미지를 검색 가능한 한국어 텍스트로 옮겨라.

반드시 아래 JSON 형식으로만 답하라:
{
  "description": "이미지가 무엇을 보여주는지 2~3문장으로 설명",
  "extracted_text": "이미지 안에 보이는 모든 글자를 그대로 옮긴 것",
  "data_table": [["헤더1","헤더2"],["값1","값2"]]
}

규칙:
- 차트나 그래프라면 어떤 추세를 보여주는지 설명하고, 축 라벨과 범례를 빠짐없이 적어라.
- 수치가 보이면 data_table에 행/열 구조로 옮겨라. 수치가 없으면 data_table은 null로 둬라.
- 이미지에 글자가 없으면 extracted_text는 빈 문자열로 둬라.
- 추측하지 마라. 보이는 것만 적어라."""


# OpenAI 클라이언트를 만든다. 키가 없으면 None
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


# 모델 응답 문자열에서 JSON을 꺼낸다. 형식이 어긋나면 전체를 설명으로 취급
def _parse_response(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"description": content.strip(), "extracted_text": "", "data_table": None}


# Vision 모델을 호출해 이미지 설명을 얻는다. 실패하면 None (라우터가 OCR로 폴백)
def _call_vision(blob: bytes, ctx: LoadContext) -> dict[str, Any] | None:
    cfg = ctx.config
    cache = ResultCache(cfg.cache_dir, enabled=cfg.cache_enabled)
    key = content_hash(blob, salt=f"vision:{cfg.vision_model}")

    cached = cache.get(key)
    if cached is not None:
        return cached

    if ctx.vision_calls >= cfg.max_vision_calls_per_doc:
        ctx.warn(
            f"Vision 호출 상한({cfg.max_vision_calls_per_doc}회)에 도달해 "
            "나머지 이미지는 OCR로 처리했습니다."
        )
        return None

    client = _make_client(cfg.openai_api_key)
    if client is None:
        ctx.warn("OPENAI_API_KEY가 없어 이미지를 OCR로 처리했습니다.")
        return None

    b64 = base64.b64encode(blob).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=cfg.vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=1500,
        )
    except Exception as exc:  # noqa: BLE001 - 외부 API 장애가 적재를 멈추면 안 된다
        ctx.warn(f"Vision 호출 실패로 OCR로 폴백했습니다: {type(exc).__name__}")
        return None

    content = (response.choices[0].message.content or "").strip()
    result = _parse_response(content)
    ctx.vision_calls += 1
    cache.set(key, result)
    return result


# Vision 응답을 검색용 단일 텍스트로 합친다
def _build_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    description = (result.get("description") or "").strip()
    if description:
        parts.append(description)

    extracted = (result.get("extracted_text") or "").strip()
    if extracted:
        parts.append(extracted)

    table = result.get("data_table")
    if isinstance(table, list) and table:
        markdown = rows_to_markdown(
            [[str(c) for c in row] for row in table if isinstance(row, list)]
        )
        if markdown:
            parts.append(markdown)

    return "\n\n".join(parts)


# 이미지 리전을 Vision으로 처리. 실패 시 빈 리스트를 반환해 라우터가 OCR로 폴백하도록 한다
def handle(region: ContentRegion, ctx: LoadContext) -> list[Element]:
    if not region.blob:
        return []

    result = _call_vision(region.blob, ctx)
    if result is None:
        return []

    text = _build_text(result)
    if not text.strip():
        return []

    metadata = dict(region.metadata)
    metadata["image_source"] = "vision"
    metadata["vision_model"] = ctx.config.vision_model
    if isinstance(result.get("data_table"), list):
        metadata["rows"] = result["data_table"]
    if region.bbox is not None:
        metadata["bbox"] = list(region.bbox)

    return [
        Element(
            type=ElementType.IMAGE_TEXT,
            text=text,
            page=region.page,
            order=region.order,
            metadata=metadata,
        )
    ]
