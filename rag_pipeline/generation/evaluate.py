"""검색된 근거가 질문에 답하기에 충분한지 판정한다. **답은 안 쓴다.**

`generate_answer()` 의 이웃이고 `answer_question()` 안에서 `retrieve()` 뒤 · 생성 앞에 선다.
지금은 **판정을 내기만 하고 아무것도 안 바꾼다** — 재시도(Rewriter)가 물려 있어 갈 곳이 없고,
생성을 막거나 프롬프트를 바꾸면 *판정이 맞았나* 와 *답이 좋아졌나* 가 한 실행에 섞이는데
**답변 품질을 재는 방법이 아직 없다**(`.claude/wiki/plan_log/검색-생성.md` 11회차).

## 왜 여기 있나 — `retrieve()` 안이 아니라

리랭커는 순서를 바꾸므로 검색 안이지만, verdict 는 **집합도 순서도 안 바꾼다.** 그리고
`retrieve()` 의 반환은 `list[RetrievedChunk]` 라 판정을 담을 칸이 없다 — 담으려면 2회차가
기각한 `RetrievalResult` 타입 신설로 돌아가고, 그러면 Phase 3~5 의 계약이 전부 따라 바뀐다.

## 실패는 예외가 아니라 **제3의 상태**다

키 없음 · 호출 실패 · 응답 손상 셋 다 `verdict="failed"` 로 나가고 사유는 `warnings` 에 쌓인다.
**어느 판정으로도 안 떨어뜨리는 것이 요점이다.** 실패를 *불충분* 으로 떨어뜨리면 답이 코퍼스에
없는 부정 예시 질의에서 **죽은 Evaluator 가 만점**을 받아 점수가 실력이 아니라 실패율을
재고, *충분* 으로 떨어뜨리면 `NO_EVIDENCE_ANSWER` 방어를 무력화한다(10회차).

**폴백은 호출부가 정한다.** 여기서 정하면 재시도 루프 · 평가 러너 · 화면이 원하는 것이 다른데
러너는 실패를 **따로 세야** 한다. 리랭커의 *"전부 아니면 전무"* + `Reranker.warnings` 와 같은
모양이라 새 기준이 아니다.

## 왜 Protocol 이 없나 — 리랭커는 만들었는데

가르는 것은 **두 번째 구현체가 실재하는가**다. 리랭커는 OpenAI 와 로컬 cross-encoder 둘이라
`Reranker` Protocol 과 `rerank_provider` 가 죽은 추상이 아니었고, 여기는 하나뿐이라
`answer_cache` 쪽 판단을 따른다. 둘째가 생기면 그때 만든다.

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 테스트가
`mocker.patch.object(evaluate, "_make_client", ...)` 로 갈아끼운다. 클라이언트를 `__init__`
에서 만들거나 이 함수를 메서드로 옮기면 외부 API 테스트가 전부 깨진다. `answer.py` ·
`reranker/openai_llm.py` · `content/image_vision.py` 와 같은 규약이다.

## 호출 인자가 모델을 탄다 — 갈리는 것은 `temperature` 하나다

2026-08-27 실측(7호출)으로 `gpt-5.6-luna` 는 `max_tokens` 를 **400** 으로 거부하고
(`Use 'max_completion_tokens' instead`) `temperature=0.0` 도 **400** 이다
(`Only the default (1) value is supported`). 그런데 **`gpt-4.1-mini` 도
`max_completion_tokens` 를 받으므로** 토큰 상한은 모델별 분기가 필요 없다.

`temperature` 만 다르고, 그것을 **`None` = 안 보낸다** 로 표현한다. `1.0` 을 보내는 것으로
흉내 내지 않는 이유는 그게 기본값을 명시하는 것뿐이면서 *0.0 이 재현을 위한 값이었다* 는
사실을 코드에서 지우기 때문이다(`EvaluatorConfig` 주석).

**대가는 재현성이다** — gpt-5 계열은 온도를 못 고정하므로 그 모델로 잰 수치는 다시 돌리면
달라질 수 있다. 캐시가 받아 주는 것은 재현이 아니라 **같은 응답을 되읽는 것**이다.

## 캐시 키에 무엇이 들어가나

질의 · 후보 신원(`chunk_id`) · 모델 · 프롬프트 버전이다. **후보를 순서까지 싣는 이유**는
프롬프트의 번호가 순서에서 나와, 같은 집합이어도 순서가 다르면 모델이 다른 것을 보기
때문이다. 프롬프트 버전이 빠지면 프롬프트를 고쳐도 옛 응답이 새 프롬프트의 답인 척
돌아오는데 **에러가 아니라 수치로만** 드러난다(`표-경로.md` 14회차).

`rerank_cache_salt_for()` 를 빌려 쓰지 않는 것은 **저쪽 규칙에 `rerank_device` 가 들어
있어서**다 — 여기는 로컬 갈래가 없어 실을 것이 없고, 남의 단계 규칙을 빌리면 저쪽이 축을
더할 때 이쪽이 조용히 따라간다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from rag_pipeline.config import OPENAI_CONNECT_TIMEOUT_SEC, EvaluatorConfig
from rag_pipeline.cost_log import CostOutcome, append_cost_log, split_tokens
from rag_pipeline.generation.prompts import (
    EVALUATOR_PROMPT_VERSION,
    EVALUATOR_SYSTEM_PROMPT,
    build_evaluator_prompt,
)
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["EvaluatorUsage", "OpenAIEvaluator", "get_evaluator"]


# 판정하는 동안 쌓이는 비용. 질의당으로 읽으려면 호출부가 reset() 한다.
# **`RerankUsage` 와 같은 모양인 것이 의도다** — 리포트 모양이 갈리면 비용표를 손으로 맞춘다
@dataclass
class EvaluatorUsage:
    # 실제로 나간 외부 API 호출 수. **성공한 뒤에만** 오른다
    api_calls: int = 0
    # 그 호출들이 쓴 토큰. **입력과 출력을 가른다** — 단가가 서로 달라 합계 하나로는
    # 금액을 원리적으로 못 낸다 (`RerankUsage` 와 같은 모양을 지킨다)
    input_tokens: int = 0
    output_tokens: int = 0
    # 캐시로 아낀 질의 수
    cache_hits: int = 0
    # 실제로 판정을 얻은 질의 수. 실패는 여기 안 센다 — 그것이 실패 버킷의 분자다
    evaluated: int = 0

    # 합계는 **파생이지 필드가 아니다** (`RerankUsage.total_tokens` 참고)
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    # 질의 하나를 끝내고 다음으로 넘어갈 때 부른다
    def reset(self) -> None:
        self.api_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_hits = 0
        self.evaluated = 0


# OpenAI 클라이언트를 만든다. 키가 없으면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_client(api_key: str | None, timeout: float) -> Any | None:
    if not api_key:
        return None
    try:
        import httpx
        from openai import OpenAI
    except ImportError:
        return None
    # 맨 float 을 주면 connect 까지 덮여 연결 실패가 5초에서 이 값으로 늘어난다 (config.py)
    return OpenAI(
        api_key=api_key,
        timeout=httpx.Timeout(timeout, connect=OPENAI_CONNECT_TIMEOUT_SEC),
    )


# 캐시 키. 질의·후보 신원·모델·프롬프트 버전이 전부 들어간다 (모듈 docstring 참고)
def _cache_key(query: str, chunks: list[RetrievedChunk], *, salt: str) -> bytes:
    ids = "\n".join(chunk.chunk_id for chunk in chunks)
    return f"{salt}\n{query}\n{ids}".encode()


# 응답 본문에서 판정을 꺼낸다. **모델은 "failed" 를 말할 수 없다** — 그것은 판정이 아니라
# 판정을 못 했다는 뜻이라 부르는 쪽이 붙인다. 모델이 자기 실패를 선언하게 두면 실패 버킷이
# 모델의 말버릇을 재게 된다. 모양이 틀리면 None 이고, 그것이 곧 실패다
def _parse_verdict(content: str | None) -> EvaluatorVerdict | None:
    if not content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return _verdict_from_payload(payload)


# 응답이든 캐시 항목이든 **같은 검사를 탄다.** 캐시 쪽만 느슨하게 두면 옛 스키마로 저장된
# 항목이 검사 없이 되살아나는데, 그건 응답 손상과 구분이 안 되는 자리다
def _verdict_from_payload(payload: Any) -> EvaluatorVerdict | None:
    if not isinstance(payload, dict):
        return None

    verdict = payload.get("verdict")
    if verdict not in ("sufficient", "insufficient"):
        return None

    reasoning = payload.get("reasoning")
    return EvaluatorVerdict(
        verdict=verdict,
        reasoning=reasoning.strip() if isinstance(reasoning, str) else "",
    )


# 근거가 충분한지 LLM 한 번으로 판정한다
class OpenAIEvaluator:
    # 클라이언트를 여기서 만들지 않는다. _make_client() 를 호출 시점에 부르는 것이
    # 테스트의 모킹 이음새다
    def __init__(self, config: EvaluatorConfig) -> None:
        self._config = config
        self._usage = EvaluatorUsage()
        self._warnings: list[str] = []
        self._cache = ResultCache(
            config.evaluator_cache_dir, enabled=config.evaluator_cache_enabled
        )

    # 실패했을 때 그 사유가 쌓인다. 부르는 쪽이 읽는다 —
    # 경고가 없으면 "판정이 나빴다"와 "판정을 못 했다"가 구분되지 않는다
    @property
    def warnings(self) -> list[str]:
        return self._warnings

    # 지금까지 쌓인 비용. 질의당으로 읽으려면 호출부가 reset() 한다
    @property
    def usage(self) -> EvaluatorUsage:
        return self._usage

    # 캐시 salt. 프롬프트 버전이 들어가는 이유는 모듈 docstring 에 있다
    @property
    def _salt(self) -> str:
        return f"evaluator:{self._config.evaluator_model}:{EVALUATOR_PROMPT_VERSION}"

    # 근거가 충분한지 판정한다. **예외를 안 던진다** — 실패는 제3의 상태로 나간다
    def evaluate(self, query: str, chunks: list[RetrievedChunk]) -> EvaluatorVerdict:
        text = query.strip()
        if not text:
            # 판정할 질문이 없다. *불충분* 이 아니라 판정을 못 한 것이다
            self._warnings.append("질의가 비어 있어 판정하지 않았습니다")
            return EvaluatorVerdict(verdict="failed", reasoning="질의가 비어 있습니다")

        # 근거가 0건이면 모델을 안 부른다. generate_answer() 가 0건에서
        # NO_EVIDENCE_ANSWER 를 그냥 돌려주는 것과 같은 자리이고, **실패가 아니다**
        if not chunks:
            return EvaluatorVerdict(
                verdict="insufficient", reasoning="검색된 근거가 없습니다"
            )

        key = content_hash(_cache_key(text, chunks, salt=self._salt))
        cached = self._cache.get(key)
        restored = _verdict_from_payload(cached)
        if restored is not None:
            self._usage.cache_hits += 1
            # **아낀 것도 남긴다** (cost_log.py)
            self._log_cost("cache_hit", items=len(chunks))
            return restored

        verdict = self._call_api(text, chunks)
        if verdict is None:
            # 경고는 이미 _call_api 가 남겼다. 판정을 지어내지 않는다.
            # **실패도 한 줄 남긴다** — 제3의 상태(`verdict="failed"`)를 만든 이유가
            # *"죽은 판정자가 만점을 받으면 안 된다"* 인데, 비용 쪽도 같은 자리다:
            # 실패한 호출은 돈을 안 쓰지만 **안 남기면 회계가 «안 돌았다» 와 안 갈린다**
            self._log_cost(
                "failed",
                items=len(chunks),
                detail=self._warnings[-1] if self._warnings else None,
            )
            return EvaluatorVerdict(verdict="failed", reasoning="판정을 얻지 못했습니다")

        # **못 쓸 응답은 캐시에 안 넣는다** (표-경로.md 13회차가 고친 그 실수다)
        self._cache.set(key, verdict.model_dump())
        self._usage.evaluated += 1
        return verdict

    # 실제로 보낸다. 실패·손상은 전부 None 으로 바꿔 제3의 상태에 맡긴다
    def _call_api(self, query: str, chunks: list[RetrievedChunk]) -> EvaluatorVerdict | None:
        config = self._config
        client = _make_client(config.openai_api_key, config.openai_timeout_sec)
        if client is None:
            self._warnings.append("OPENAI_API_KEY 가 없어 근거 충분성을 판정하지 못했습니다")
            return None

        # **`max_completion_tokens` 는 분기가 없다** — gpt-5 계열이 `max_tokens` 를 400 으로
        # 거부하는데 gpt-4.1-mini 는 새 이름도 받는다(2026-08-27 실측 7호출). 갈리는 것은
        # temperature 하나라, **None 이면 인자 자체를 안 싣는다** (모듈 docstring 참고)
        kwargs: dict[str, Any] = {
            "model": config.evaluator_model,
            "messages": [
                {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
                {"role": "user", "content": build_evaluator_prompt(query, chunks)},
            ],
            "max_completion_tokens": config.evaluator_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if config.evaluator_temperature is not None:
            kwargs["temperature"] = config.evaluator_temperature

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 실패가 예외가 아니라 제3의 상태다 (docstring)
            self._warnings.append(
                f"판정 호출에 실패했습니다: {type(exc).__name__}: {exc}"
            )
            return None

        verdict = _parse_verdict(self._content_of(response))
        if verdict is None:
            self._warnings.append("판정 응답의 모양이 달라 판정을 못 얻었습니다")
            return None

        # 카운터는 성공한 뒤에만 올린다 (embedding·리랭커와 같은 규약)
        self._usage.api_calls += 1
        prompt_tokens, completion_tokens = split_tokens(response)
        self._usage.input_tokens += prompt_tokens
        self._usage.output_tokens += completion_tokens
        self._log_cost(
            "call",
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            items=len(chunks),
        )
        return verdict

    # 비용 로그 한 줄. **카운터가 오르는 자리 옆에 둔다** — 두 번째 진실이 안 생긴다
    def _log_cost(
        self,
        outcome: CostOutcome,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        items: int = 0,
        detail: str | None = None,
    ) -> None:
        append_cost_log(
            stage="evaluate",
            provider="openai",
            model=self._config.evaluator_model,
            outcome=outcome,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            items=items,
            detail=detail,
        )

    # 응답에서 본문을 꺼낸다. 모양이 틀려도 예외를 안 내고 None 으로 떨어뜨린다 —
    # 여기서 AttributeError 가 새면 제3의 상태가 아니라 500 이 된다
    @staticmethod
    def _content_of(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        return content if isinstance(content, str) else None


# 설정에 맞는 Evaluator 를 만든다. **모델 이름이 없으면 None** — 그것이 곧 꺼진 상태다.
#
# `get_reranker()` 와 글자 그대로 같은 모양이고 이유도 같다: 켜지는 순간 질의당 LLM 호출이
# 1회에서 2회가 되므로, 값을 하는지 재기 전에 과금이 먼저 붙지 않게 한다
def get_evaluator(config: EvaluatorConfig | None = None) -> OpenAIEvaluator | None:
    cfg = config or EvaluatorConfig()
    if not cfg.evaluator_model:
        return None
    return OpenAIEvaluator(cfg)
