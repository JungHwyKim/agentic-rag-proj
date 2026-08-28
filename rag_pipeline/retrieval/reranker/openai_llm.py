"""OpenAI LLM 리랭커. 후보 전부를 한 프롬프트에 싣고 점수를 받는다(listwise).

## 왜 후보마다 한 번씩(pointwise)이 아닌가

후보 50개면 질의당 호출이 50회가 되어 비용도 지연도 50배다. 더 중요한 것은 **pointwise 는
후보끼리 견주지 못한다는 것**이다 — 리랭커가 하는 일이 정확히 "이 질문에는 A 가 B 보다
낫다"인데, 한 번에 하나만 보여주면 그 비교가 모델 안에서 일어나지 않는다.

listwise 는 호출 1회다. 후보 50 × 최대 1200자라 입력이 최대 6만 토큰인데 `gpt-4.1-mini` 의
입력 상한에 여유가 있다.

## 왜 청크를 안 자르나

이 코퍼스는 **정답 span 이 마크다운 표의 행에 흩어져 있다.** 앞 N 자만 실으면 표 청크는
헤더만 보이고 정답이 든 행을 통째로 못 본다. 그리고 N 을 정할 근거가 없다 — 이 저장소의
임계값은 전부 분포의 틈에서 나왔다(표 되붙이기 12pt, 표시 면적비 0.08 vs 1.00).

## 왜 순위가 아니라 점수를 받나 / 실패하면 왜 전부 버리나

`base.py` 모듈 docstring 에 있다. 요지는 **전부 아니면 전무**다 — 항목 수가 후보 수와 다르면
아무것도 안 채우고 RRF 순서로 내려간다.

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 테스트가
`mocker.patch.object(openai_llm, "_make_client", ...)` 로 갈아끼운다. 클라이언트를
`__init__` 에서 만들거나 이 함수를 메서드로 옮기면 외부 API 테스트가 전부 깨진다.
`embedding/openai_api.py` · `content/image_vision.py` 와 같은 규약이다.

## 프롬프트를 고치면 캐시가 갈린다

`PROMPT_VERSION` 이 캐시 salt 에 들어간다. 고칠 때 함께 올리지 않으면 **옛 응답이 새
프롬프트의 답인 척** 돌아오는데, 에러가 아니라 수치로만 드러난다(`표-경로.md` 14회차).
"""

from __future__ import annotations

import json
from typing import Any

from rag_pipeline.config import RerankConfig
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.retrieval.reranker.base import (
    RerankUsage,
    apply_scores,
    rerank_cache_key,
    rerank_cache_salt_for,
)
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["PROMPT_VERSION", "OpenAILLMReranker"]

# 프롬프트를 고치면 **반드시 올린다** (모듈 docstring 참고)
PROMPT_VERSION = "v1"

_SYSTEM = (
    "당신은 한국어 공문서 검색 결과를 다시 정렬하는 채점자입니다. "
    "질문에 답하는 데 각 후보가 얼마나 쓸모 있는지를 0.0~10.0 으로 매깁니다. "
    "후보의 순서를 바꾸거나 개수를 줄이지 말고, 받은 모든 후보에 점수를 매기세요. "
    '출력은 설명 없이 JSON 객체 하나입니다: {"scores": [{"i": 0, "score": 8.5}, ...]}'
)


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


# 후보들을 번호 붙인 프롬프트로. 번호가 곧 응답의 `i` 라 자리가 여기서 정해진다
def _build_prompt(query: str, candidates: list[RetrievedChunk]) -> str:
    blocks = [f"[{index}]\n{chunk.text}" for index, chunk in enumerate(candidates)]
    body = "\n\n".join(blocks)
    return f"질문: {query}\n\n후보 {len(candidates)}개:\n\n{body}"


# 응답 본문에서 후보 수만큼의 점수를 자리 맞춰 꺼낸다. **하나라도 못 얻으면 None** —
# 그것이 곧 "통째로 버린다"이고, 부분 성공을 여기서 만들지 않는 것이 이 함수의 요점이다.
#
# 자리는 `i` 로 정한다. 응답 순서를 그대로 믿지 않는 것은 `embedding/openai_api.py` 의
# `_ordered_vectors()` 와 같은 이유다 — 한 칸만 밀려도 엉뚱한 청크가 1위가 되는데
# 아무 에러도 안 난다
def _parse_scores(content: str | None, expected: int) -> list[float] | None:
    if not content:
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None

    items = payload.get("scores") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or len(items) != expected:
        return None

    slots: list[float | None] = [None] * expected
    for item in items:
        if not isinstance(item, dict):
            return None
        index, score = item.get("i"), item.get("score")
        if not isinstance(index, int) or not 0 <= index < expected:
            return None
        if slots[index] is not None:
            return None
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        slots[index] = float(score)

    if any(slot is None for slot in slots):
        return None
    return [slot for slot in slots if slot is not None]


# LLM 한 번으로 후보 풀 전체를 다시 정렬한다
class OpenAILLMReranker:
    # 클라이언트를 여기서 만들지 않는다. _make_client() 를 호출 시점에 부르는 것이
    # 테스트의 모킹 이음새다
    def __init__(self, config: RerankConfig) -> None:
        self._config = config
        self._usage = RerankUsage()
        self._warnings: list[str] = []
        self._cache = ResultCache(config.rerank_cache_dir, enabled=config.rerank_cache_enabled)

    # 폴백이 일어났을 때 쌓인다. 부르는 쪽이 읽는다
    @property
    def warnings(self) -> list[str]:
        return self._warnings

    # 지금까지 쌓인 비용. 질의당으로 읽으려면 호출부가 reset() 한다
    @property
    def usage(self) -> RerankUsage:
        return self._usage

    # 데울 가중치가 없다. **이 갈래는 지금까지 기동마다 유료 호출을 하나씩 내고 있었다** —
    # 후보 2개짜리 작은 호출이라 눈에 안 띄었을 뿐이다(2026-08-27 에 Cohere 갈래를 만들며 발견)
    @property
    def needs_warmup(self) -> bool:
        return False

    # 캐시 salt. 규칙은 base 가 정한다 — 구현체가 다시 짜면 한쪽이 빠뜨려도 에러가 안 난다
    @property
    def _salt(self) -> str:
        return rerank_cache_salt_for(self._config, prompt_version=PROMPT_VERSION)

    # 후보를 점수 순으로. **예외를 안 던진다** — 실패는 RRF 순서로 내려가는 폴백이다
    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        text = query.strip()
        # 후보가 1개 이하면 정렬할 것이 없다. 호출을 아끼는 것이 아니라 **뜻이 없다**
        if not text or len(candidates) <= 1:
            return candidates

        pool = candidates[: max(self._config.rerank_candidates, 1)]
        key = content_hash(rerank_cache_key(text, pool, salt=self._salt))

        cached = self._cache.get(key)
        scores = cached.get("scores") if isinstance(cached, dict) else None
        if isinstance(scores, list) and len(scores) == len(pool):
            self._usage.cache_hits += 1
            return apply_scores(pool, [float(value) for value in scores]) + candidates[len(pool):]

        scores = self._call_api(text, pool)
        if scores is None:
            # 경고는 남기고 순서는 안 건드린다. ranking_score 가 fused_score 로 떨어진다
            return candidates

        # **못 쓸 응답은 캐시에 안 넣는다** (표-경로.md 13회차가 고친 그 실수다)
        self._cache.set(key, {"scores": scores})
        self._usage.reranked += len(pool)
        return apply_scores(pool, scores) + candidates[len(pool):]

    # 실제로 보낸다. 실패·손상은 전부 None 으로 바꿔 폴백에 맡긴다
    def _call_api(self, query: str, pool: list[RetrievedChunk]) -> list[float] | None:
        config = self._config
        client = _make_client(config.openai_api_key)
        if client is None:
            self._warnings.append("OPENAI_API_KEY 가 없어 리랭커를 건너뛰고 RRF 순서를 씁니다")
            return None

        try:
            response = client.chat.completions.create(
                model=config.rerank_model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _build_prompt(query, pool)},
                ],
                temperature=config.rerank_temperature,
                max_tokens=config.rerank_max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 - 폴백이 있는 갈래다 (base.py docstring)
            self._warnings.append(
                f"리랭커 호출에 실패해 RRF 순서를 씁니다: {type(exc).__name__}: {exc}"
            )
            return None

        content = self._content_of(response)
        scores = _parse_scores(content, len(pool))
        if scores is None:
            # **부분 성공을 만들지 않는다** — 반쯤 바뀐 순서는 판정을 오염시킨다
            self._warnings.append(
                f"리랭커 응답에서 후보 {len(pool)}개의 점수를 못 얻어 RRF 순서를 씁니다"
            )
            return None

        # 카운터는 성공한 뒤에만 올린다 (embedding 과 같은 규약)
        self._usage.api_calls += 1
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "total_tokens", None)
        if isinstance(tokens, int):
            self._usage.total_tokens += tokens
        return scores

    # 응답에서 본문을 꺼낸다. 모양이 틀려도 예외를 안 내고 None 으로 떨어뜨린다 —
    # 여기서 AttributeError 가 새면 폴백이 아니라 500 이 된다
    @staticmethod
    def _content_of(response: Any) -> str | None:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        return content if isinstance(content, str) else None
