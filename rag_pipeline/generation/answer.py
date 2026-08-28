"""검색된 청크로 답변 문장을 만든다.

## 왜 빈 문자열이 아니라 예외인가

로더 쪽 외부 API 핸들러는 **예외를 던지지 않고 빈 리스트를 반환**하는 것이 규약이지만,
그것은 내려갈 폴백 층이 있어서 성립한다(LlamaParse -> PyMuPDF, Vision -> OCR). 생성에는
그 층이 없다. 빈 답변을 돌려주면 화면에 빈 말풍선이 뜨고, 사용자는 **"근거를 못 찾았다"와
"생성이 실패했다"를 구분할 수 없다** — 앞은 질문을 바꿔야 하고 뒤는 키나 네트워크를 봐야
하는데도. embedding·indexing 이 예외인 것과 같은 판단이다.

**검색 0건은 실패가 아니다.** 모델을 부르지 않고 `NO_EVIDENCE_ANSWER` 를 그대로 돌려준다 —
근거 없이 부르면 돈을 쓰면서 환각할 기회만 준다.

## 모델 축 — `gpt-4.1-mini` 와 `gpt-5.6-luna` 를 둘 다 받는다

**토큰 상한은 분기가 없다.** gpt-5 계열이 `max_tokens` 를 400
(`Use 'max_completion_tokens' instead`)으로 거부하는데 `gpt-4.1-mini` 는 **새 이름도 받으므로**
`max_completion_tokens` 하나로 둘 다 돈다(2026-08-27 실측 7호출).

**갈리는 것은 `temperature` 하나다.** gpt-5 계열은 기본값(1) 말고는 400
(`Only the default (1) value is supported`)이라 `generation_temperature` 가 `float | None` 이고
**`None` 이 «보내지 않는다» 는 뜻**이다. `1.0` 을 대신 싣지 않는다 — 그건 기본값을 명시하는
것뿐이면서 *0.2 가 «근거를 벗어나지 않되 베끼지는 않는다» 로 고른 값이었다* 는 사실을 지운다.

**대가는 재현성이다** — luna 로 잰 답변 수치는 온도를 못 고정해 다시 돌리면 달라질 수 있다.
그래서 `evaluation/config.py` 의 기본 생성 프로필은 `prod`(mini) 그대로다.

`generation/evaluate.py` · `retrieval/rewrite.py` 가 같은 모양이다.
**`retrieval/reranker/openai_llm.py` 를 본보기로 삼지 말 것** — 이 실측 이전 코드라 아직
`max_tokens=` 를 쓴다(리랭커를 `gpt-4.1-mini` 로만 돌려 안 깨졌을 뿐이다).

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

    # **`max_completion_tokens` 는 분기가 없다** — gpt-5 계열이 `max_tokens` 를 400 으로
    # 거부하는데 gpt-4.1-mini 는 새 이름도 받는다(2026-08-27 실측 7호출). 갈리는 것은
    # temperature 하나라, **None 이면 인자 자체를 안 싣는다** (모듈 docstring 참고)
    kwargs: dict[str, Any] = {
        "model": cfg.generation_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(query, chunks)},
        ],
        "max_completion_tokens": cfg.generation_max_tokens,
    }
    if cfg.generation_temperature is not None:
        kwargs["temperature"] = cfg.generation_temperature

    try:
        response = client.chat.completions.create(**kwargs)
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
