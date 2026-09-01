"""Cohere 전용 리랭킹 API 갈래. 후보를 한 번에 보내고 후보별 점수를 받는다.

## 왜 세 번째 구현체인가 — 목적이 품질이 아니라 **GPU** 다

`local_ce` 는 품질로 이 코퍼스 최고인데(`nDCG@10` 0.7327 · 폴백 0/60) **GPU 를 문다.**
실측(2026-08-27)으로 리랭커를 GPU 에 올려 둔 채 적재하면 같은 181청크 임베딩이
**46.28초 → 206.92초**로 느려지고 두 조건의 **범위가 안 겹친다**
(`.claude/wiki/verify_log/2026-08-27-임베딩-지연-리랭커경합.md`). 8151MiB 가 임베더와 리랭커를 동시에
못 드는 것이다.

그 처방으로 `RERANK_DEVICE=cpu` 는 **질의당 63.9초**를 되사오므로 화면에 못 쓴다. 그래서
GPU 를 아예 안 쓰는 갈래가 필요하고, 이것이 그 자리다. **품질은 동률 유지가 합격선이지
향상이 목표가 아니다**(`.claude/wiki/plan_log/검색-생성.md` 14회차).

## 왜 기존 `openai_llm` 이 아닌가

저쪽은 리랭커가 아니라 **LLM 채점기**다(후보 전부를 한 프롬프트에 싣고 JSON 을 받는다).
8회차가 그것을 탈락시킨 이유가 **17% 폴백**인데, 그 폴백은 모델이 후보 수만큼 항목을 못
돌려줬을 때 *전부 아니면 전무* 가 발동한 것이었다. 점수 API 에는 그 실패 모양이 없을 것으로
**기대**한다 — **기대지 실측이 아니고, 이 갈래를 만든 목적이 그것을 재는 것이다.**

## 길이: 안 자른다. 다만 **과금 단위는 다른 자를 쓴다**

`max_tokens_per_doc` 기본값이 **4096** 이고 이 코퍼스의 청크는 최대 약 1,200자·토큰이라
(`CLAUDE.md` 의 *한국어 청크는 문자 ≈ 토큰이다*) **잘리지 않는다.** `openai_llm` 이
*"청크를 안 자른다"* 를 정한 이유(정답 span 이 마크다운 표의 행에 흩어져 있다)가 여기서는
안 문다. 그래도 **값을 명시로 넘긴다** — 기본값이 바뀌면 조용히 잘리기 시작하는데 그것은
에러가 아니라 수치로만 드러난다.

**과금은 이것과 별개다.** Cohere 정의는 *"one query with up to 100 documents"* 이고
**500 토큰을 넘는 문서는 자동으로 쪼개져 각 조각이 별개 문서로 센다.** 즉 1,200토큰 청크
하나가 과금상 3개이고, 후보 50개면 100~150 문서라 **질의당 약 2 search unit** 이다 —
*질의당 1회* 로 적으면 틀린다. `usage.api_calls` 는 HTTP 호출 수라 이 배수를 모른다.

## `total_tokens` 를 왜 0 으로 두나

Cohere 는 토큰이 아니라 **search unit** 과금이다. `RerankUsage.total_tokens` 에 억지로 채우면
`openai_llm` 의 토큰 수와 **다른 단위가 같은 칸**에 들어가, 비교표를 읽는 사람이 두 값을 더하게
된다. 로컬 갈래가 같은 칸을 0으로 두는 것과 같은 판단이다(`base.py`).

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 테스트가
`mocker.patch.object(cohere_api, "_make_client", ...)` 로 갈아끼운다. 클라이언트를
`__init__` 에서 만들거나 이 함수를 메서드로 옮기면 외부 API 테스트가 전부 깨진다 —
`openai_llm` · `embedding/openai_api` · `content/image_vision` 과 같은 규약이다.

## 실패하면 왜 전부 버리나 / 왜 순위가 아니라 점수인가

`base.py` 모듈 docstring 에 있다. 요지는 **전부 아니면 전무**다 — 응답이 후보 수만큼 점수를
안 주면 아무것도 안 채우고 RRF 순서로 내려간다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.config import RerankConfig
from rag_pipeline.cost_log import CostOutcome, append_cost_log
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.retrieval.reranker.base import (
    RerankUsage,
    apply_scores,
    rerank_cache_key,
    rerank_cache_salt_for,
)
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["API_VERSION", "CohereReranker"]

# 호출 규약이 바뀌면 올린다(모델·파라미터가 아니라 **우리가 보내는 모양**이 바뀔 때).
# `openai_llm.PROMPT_VERSION` · `local_ce.SCORING_VERSION` 과 같은 자리다 —
# 캐시가 옛 점수를 새 규약의 답인 척 돌려주는 것을 막는다
API_VERSION = "v1"


# Cohere 클라이언트를 만든다. 키가 없거나 SDK 가 없으면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_client(api_key: str | None) -> Any | None:
    if not api_key:
        return None
    try:
        import cohere
    except ImportError:
        return None
    try:
        return cohere.ClientV2(api_key=api_key)
    except Exception:  # noqa: BLE001 - 폴백이 있는 갈래다 (base.py docstring)
        return None


# 응답에서 후보별 점수를 **입력 순서로** 되돌린다. 손상은 전부 None 으로 떨어뜨린다.
#
# **`index` 로 되돌리는 것이 요점이다** — Cohere 는 점수 순으로 정렬해 돌려주므로 받은 순서를
# 그대로 쓰면 점수가 엉뚱한 후보에 붙는다. 에러가 아니라 **순위로만** 드러나는 종류다.
def _scores_in_input_order(response: Any, expected: int) -> list[float] | None:
    results = getattr(response, "results", None)
    if not isinstance(results, list) or len(results) != expected:
        return None

    scores: list[float | None] = [None] * expected
    for item in results:
        index = getattr(item, "index", None)
        score = getattr(item, "relevance_score", None)
        if not isinstance(index, int) or not isinstance(score, (int, float)):
            return None
        if not 0 <= index < expected or scores[index] is not None:
            # 범위 밖이거나 같은 자리가 두 번 오면 대응이 깨진 것이다
            return None
        scores[index] = float(score)

    if any(value is None for value in scores):
        return None
    return [float(value) for value in scores]  # type: ignore[arg-type]


# 응답이 스스로 말하는 과금 단위. **모양을 안 믿는다** — 없으면 0 이고 예외를 안 낸다.
#
# **문서 수로 짐작하지 않는 것이 요점이다.** 지금은 후보 수와 얼추 비례하지만 그 비례는
# Cohere 의 정책이지 우리 계약이 아니라, 정책이 바뀌면 **에러 없이 수치로만** 틀린다 —
# `_scores_in_input_order()` 가 순서를 짐작하지 않고 `index` 로 되돌리는 것과 같은 잣대다.
# 0 이 나오면 그것이 곧 *"이 응답이 과금 단위를 안 알려줬다"* 이고, 리포트가 그 줄을
# 「금액 못 냄」으로 따로 센다(0으로 채워 안 쓴 것처럼 보이게 하지 않는다)
def _billed_search_units(response: Any) -> int:
    billed = getattr(getattr(response, "meta", None), "billed_units", None)
    units = getattr(billed, "search_units", None)
    return units if isinstance(units, int) else 0


# Cohere 리랭킹 API 로 후보 풀을 다시 정렬한다
class CohereReranker:
    # 클라이언트를 여기서 만들지 않는다. `_make_client()` 를 호출 시점에 부르는 것이
    # 모킹 이음새이고, 키가 없어도 객체 생성은 성공해야 폴백이 성립한다
    def __init__(self, config: RerankConfig) -> None:
        self._config = config
        self._usage = RerankUsage()
        self._warnings: list[str] = []
        self._cache = ResultCache(config.rerank_cache_dir, enabled=config.rerank_cache_enabled)

    # 폴백이 일어났을 때 쌓인다. 부르는 쪽이 읽는다
    @property
    def warnings(self) -> list[str]:
        return self._warnings

    # 지금까지 쌓인 비용. **`total_tokens` 는 0 이다** (모듈 docstring 참고)
    @property
    def usage(self) -> RerankUsage:
        return self._usage

    # 데울 가중치가 없다. 기동 워밍업이 부르면 **얻는 것 없이 과금만** 된다
    @property
    def needs_warmup(self) -> bool:
        return False

    # 캐시 salt. 규칙은 base 가 정한다
    @property
    def _salt(self) -> str:
        return rerank_cache_salt_for(self._config, prompt_version=API_VERSION)

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
            # **아낀 것도 남긴다** — 안 남기면 「얼마 아꼈나」를 못 낸다 (cost_log.py)
            self._log_cost("cache_hit", items=len(pool))
            return apply_scores(pool, [float(value) for value in scores]) + candidates[len(pool):]

        scores = self._call_api(text, pool)
        if scores is None:
            # 경고는 남기고 순서는 안 건드린다. ranking_score 가 fused_score 로 떨어진다.
            # **폴백을 남기는 것이 이 갈래에서 특히 값어치다** — Trial 키의 분당 10회 한도로
            # 60건 중 30건이 429 로 떨어진 실행이 지표 여덟 개를 전부 품질 저하로 보이게 했다
            self._log_cost(
                "failed",
                items=len(pool),
                detail=self._warnings[-1] if self._warnings else None,
            )
            return candidates

        # **못 쓸 응답은 캐시에 안 넣는다** (표-경로.md 13회차가 고친 그 실수다)
        self._cache.set(key, {"scores": scores})
        self._usage.reranked += len(pool)
        return apply_scores(pool, scores) + candidates[len(pool):]

    # 실제로 보낸다. 실패·손상은 전부 None 으로 바꿔 폴백에 맡긴다
    def _call_api(self, query: str, pool: list[RetrievedChunk]) -> list[float] | None:
        config = self._config
        client = _make_client(config.cohere_api_key)
        if client is None:
            self._warnings.append("COHERE_API_KEY 가 없어 리랭커를 건너뛰고 RRF 순서를 씁니다")
            return None

        try:
            response = client.rerank(
                model=config.rerank_model,
                query=query,
                documents=[chunk.text for chunk in pool],
                # **`top_n` 을 안 넘긴다** — 전부 아니면 전무라 후보 전원의 점수가 필요하다.
                # 넘기면 잘린 응답이 와서 매 질의가 폴백으로 떨어진다
                max_tokens_per_doc=config.rerank_max_tokens_per_doc,
            )
        except Exception as exc:  # noqa: BLE001 - 폴백이 있는 갈래다 (base.py docstring)
            self._warnings.append(
                f"Cohere 리랭커 호출에 실패해 RRF 순서를 씁니다: {type(exc).__name__}: {exc}"
            )
            return None

        scores = _scores_in_input_order(response, len(pool))
        if scores is None:
            # **부분 성공을 만들지 않는다** — 반쯤 바뀐 순서는 판정을 오염시킨다
            self._warnings.append(
                f"Cohere 응답에서 후보 {len(pool)}개의 점수를 못 얻어 RRF 순서를 씁니다"
            )
            return None

        # 카운터는 성공한 뒤에만 올린다 (embedding 과 같은 규약).
        # **`api_calls` 는 HTTP 호출 수지 과금 단위가 아니다** (모듈 docstring 참고) —
        # 과금 단위는 응답이 스스로 말해주는 `meta.billed_units.search_units` 다.
        # 문서 수를 세어 짐작하면 **한도 정책이 바뀌는 날 조용히 틀린다**
        self._usage.api_calls += 1
        units = _billed_search_units(response)
        self._usage.search_units += units
        self._log_cost("call", search_units=units, items=len(pool))
        return scores

    # 비용 로그 한 줄. **카운터가 오르는 자리 옆에 둔다** — 두 번째 진실이 안 생긴다.
    # 토큰 칸을 안 채우는 것이 이 갈래의 정체다: 0 은 *"안 썼다"* 가 아니라
    # *"이 API 는 토큰으로 안 판다"* 이고, 그것을 `provider` 가 가른다 (cost_log.py)
    def _log_cost(
        self,
        outcome: CostOutcome,
        *,
        search_units: int | None = None,
        items: int = 0,
        detail: str | None = None,
    ) -> None:
        append_cost_log(
            stage="rerank",
            provider="cohere",
            model=self._config.rerank_model,
            outcome=outcome,
            search_units=search_units,
            items=items,
            detail=detail,
        )
