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
from rag_pipeline.cost_log import CostOutcome, append_cost_log, split_tokens
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


# 비용 로그 한 줄. **이 단계만 `usage` 인스턴스가 없어 모듈 레벨 함수다** —
# `generate_answer()` 가 함수라 카운터를 얹을 자리가 없고, `Answer` 에 usage 칸을 더하면
# 그 공개 계약이 캐시 payload·화면·`query_log` 까지 번진다(`schemas/answer.py`).
#
# **그래서 이 자리가 call-level 로그를 고른 값어치를 그대로 보여준다** — 함수형 단계가
# 인스턴스 usage 를 못 갖는데 로그는 가질 수 있다. 지금까지 `.claude/wiki/verify_log/` 에 생성 비용이
# `생성 LLM 2회` 처럼 **횟수만** 남은 이유가 이것이었다.
#
# **재시도 루프(`agent/loop.py`)는 이 함수를 한 번만 부른다.** `step_generate` 가 유일한
# 정상 출구라 `step_budget()` 이 `generate` 를 1로 못 박는다 — 계획 단계에서 *"루프가
# 생성을 여러 번 부르니 줄 수가 곧 재시도 횟수"* 라고 적었는데 **틀렸다.** 재시도와 함께
# 늘어나는 것은 `evaluate` 다(`step_retrieve` 안에 있어 재시도+1회).
#
# 그래도 루프 쪽에 로그를 안 붙인 이유는 그대로다 — 붙이면 같은 호출이 두 번 세진다.
# `tests/cost/test_cost_in_the_retry_loop.py` 가 그 둘(생성 1 · 판정 재시도+1)을 잠근다
def _log_cost(
    config: GenerationConfig,
    outcome: CostOutcome,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    items: int = 0,
    detail: str | None = None,
) -> None:
    append_cost_log(
        stage="generate",
        provider="openai",
        model=config.generation_model,
        outcome=outcome,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        items=items,
        detail=detail,
    )


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
        # **올리기 전에 한 줄 남긴다.** 이 갈래는 예외로 위로 올라가므로 호출부가
        # `usage` 를 읽을 기회 자체가 없다 — 로그가 아니면 실패가 흔적 없이 사라진다
        _log_cost(cfg, "failed", items=len(chunks), detail=f"{type(exc).__name__}: {exc}")
        raise GenerationError(
            f"답변 생성에 실패했습니다: {type(exc).__name__}: {exc}",
            user_message="답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc

    # **응답이 돌아온 이 자리에서 남긴다.** 아래 두 검사(빈 choices·빈 본문)는 예외로
    # 올라가는데, 그때도 **돈은 이미 나갔다** — 과금은 응답이 온 시점의 일이지 우리가
    # 그것을 쓸 수 있었는지와 무관하다. 검사 뒤로 옮기면 그 회차가 공짜로 기록된다
    prompt_tokens, completion_tokens = split_tokens(response)
    _log_cost(
        cfg,
        "call",
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        items=len(chunks),
    )

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
