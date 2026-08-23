"""검색된 청크로 답변 문장을 만든다.

## 왜 빈 문자열이 아니라 예외인가

로더 쪽 외부 API 핸들러는 **예외를 던지지 않고 빈 리스트를 반환**하는 것이 규약이지만,
그것은 내려갈 폴백 층이 있어서 성립한다(LlamaParse -> PyMuPDF, Vision -> OCR). 생성에는
그 층이 없다. 빈 답변을 돌려주면 화면에 빈 말풍선이 뜨고, 사용자는 **"근거를 못 찾았다"와
"생성이 실패했다"를 구분할 수 없다** — 앞은 질문을 바꿔야 하고 뒤는 키나 네트워크를 봐야
하는데도. embedding·indexing 이 예외인 것과 같은 판단이다.

**검색 0건은 실패가 아니다.** 모델을 부르지 않고 `NO_EVIDENCE_ANSWER` 를 그대로 돌려준다 —
근거 없이 부르면 돈을 쓰면서 환각할 기회만 준다.

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 인라인화하거나 메서드로 옮기면
`mocker.patch.object(answer, "_make_client", ...)` 가 깨진다. `image_vision.py` ·
`table_vision.py` · `qdrant_store.py` 가 전부 같은 형태다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.config import GenerationConfig
from rag_pipeline.exceptions import GenerationError
from rag_pipeline.generation.prompts import (
    NO_EVIDENCE_ANSWER,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["generate_answer"]


# OpenAI 클라이언트를 만든다. 키가 없으면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


# 근거 청크로 답변을 만든다. 공개 진입 함수이고 HTTP 를 모른다
def generate_answer(
    query: str,
    chunks: list[RetrievedChunk],
    config: GenerationConfig | None = None,
) -> str:
    cfg = config or GenerationConfig()

    # 근거가 없으면 모델을 안 부른다. 실패가 아니라 정상 경로다
    if not chunks:
        return NO_EVIDENCE_ANSWER

    client = _make_client(cfg.openai_api_key)
    if client is None:
        raise GenerationError(
            "OPENAI_API_KEY 가 없어 답변을 만들 수 없습니다",
            user_message="답변 생성에 필요한 API 키가 설정되지 않았습니다.",
        )

    try:
        response = client.chat.completions.create(
            model=cfg.generation_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(query, chunks)},
            ],
            max_tokens=cfg.generation_max_tokens,
            temperature=cfg.generation_temperature,
        )
    except Exception as exc:  # noqa: BLE001 - 원인을 GenerationError로 바꿔 올린다
        raise GenerationError(
            f"답변 생성에 실패했습니다: {type(exc).__name__}: {exc}",
            user_message="답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc

    # 응답 모양을 믿지 않는다 — image_vision 이 choices=[] 에서 IndexError 를 낸 적이 있다
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise GenerationError(
            "모델이 빈 응답을 돌려줬습니다",
            user_message="답변을 만들지 못했습니다. 다시 시도해 주세요.",
        )

    text = (getattr(choices[0].message, "content", None) or "").strip()
    if not text:
        raise GenerationError(
            "모델 응답에 본문이 없습니다",
            user_message="답변을 만들지 못했습니다. 다시 시도해 주세요.",
        )
    return text
