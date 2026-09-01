"""로컬 cross-encoder 리랭커. 질의-후보 쌍마다 점수를 내고 그것으로 다시 정렬한다.

## 왜 이것이 두 번째 구현체인가

1회차가 *"로컬 우선 + 상용은 두 번째"* 로 정했는데 **그 근거 둘이 각각 무너졌다.**
*"키 없이 통과하는 것이 로컬뿐"* 은 2회차의 인자 주입이 지웠고(안 넘기는 것이 곧 끄는
스위치라 유료든 무료든 기본 테스트가 안 깨진다), *"torch 를 안 들인다"* 는 임베더가 로컬로
가며 `torch`·`sentence-transformers` 가 본체 의존성이 되어 지워졌다. 그래서 순서가 뒤집혀
OpenAI 가 먼저 붙고 이쪽이 뒤에 왔다 — **둘 다 만들어 같은 세트로 견주는 것**이 결론이다
(1회차가 세운 비교 축 6번, `.claude/wiki/plan_log/검색-생성.md` 5회차).

## 비용의 축이 다르다

OpenAI 갈래는 **토큰**이 비용이고 여기는 **지연**이 비용이다. cross-encoder 는 질의-후보 쌍을
하나씩 통과시키므로 후보 수에 선형이고, CPU 에서는 그것이 그대로 화면 대기시간이 된다.
그래서 `RerankUsage.reranked`(점수를 매긴 후보 수)가 이쪽의 비용 지표다 —
`api_calls`·`total_tokens` 는 늘 0 이다.

**얼마나 느린지는 아직 안 쟀다.** 화면에 못 쓸 만큼이면 그 사실 자체가 비교표의 결과다.

## 왜 장치를 설정으로 못 박나

`rerank_device` 기본값이 `cpu` 인 것은 성능 선택이 아니라 **재현성**이다. CPU 와 GPU 가
미세하게 다른 점수를 내면 같은 명령이 머신마다 다른 순위를 만들고, 비교표가 에러 없이
흔들린다. `EMBEDDING_DEVICE` 가 같은 이유로 같은 기본값을 갖는다 — 이름 규칙을 맞춘 것도
두 축이 같은 함정이기 때문이다.

## 모킹 이음새

`_load_model()` 은 **모듈 레벨 함수**여야 한다. 테스트가
`mocker.patch.object(local_ce, "_load_model", ...)` 로 갈아끼운다. 여기를 `__init__` 으로
옮기거나 인라인하면 **기본 pytest 가 HuggingFace 에서 모델을 내려받게 된다** — 키 없이도
도는 갈래라 "키를 지운다" 방어가 안 통하는 자리이고, 그래서 이음새가 더 중요하다.
`sentence_transformers` import 도 이 함수 안에 있다 — 모듈을 불러오는 것만으로 torch 가
올라오면 `retrieve()` 를 부르는 모든 테스트가 느려진다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.config import RerankConfig
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.retrieval.reranker.base import (
    RerankUsage,
    apply_scores,
    rerank_cache_key,
    rerank_cache_salt_for,
)
from rag_pipeline.runtime import apply_torch_threads
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["SCORING_VERSION", "LocalCrossEncoderReranker"]

# 채점 규약이 바뀌면 올린다. OpenAI 갈래의 `PROMPT_VERSION` 과 같은 자리다 —
# 캐시가 옛 점수를 새 규약의 답인 척 돌려주는 것을 막는다
SCORING_VERSION = "v1"


# cross-encoder 를 올린다. 실패하면 None.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _load_model(model_name: str | None, device: str) -> Any | None:
    if not model_name:
        return None

    # 스레드 상한을 **모델을 올리기 전에** 먹인다. 임베더 쪽 `_make_model()` 과 같은 자리이고
    # 값이 프로세스 전역이라 먼저 오는 쪽이 적용한다 (`runtime.py` 의 멱등 문단)
    apply_torch_threads()

    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        return None
    try:
        return CrossEncoder(model_name, device=device)
    except Exception:  # noqa: BLE001 - 폴백이 있는 갈래다 (base.py docstring)
        return None


# 로컬 cross-encoder 로 후보 풀을 다시 정렬한다
class LocalCrossEncoderReranker:
    # 모델을 여기서 안 올린다. **게으름이 계약이다** — backend 가 첫 질의까지 미루는 것과
    # 같은 이유이고(로딩이 느리거나 실패해도 서버는 떠야 한다) 테스트의 모킹 이음새이기도 하다
    def __init__(self, config: RerankConfig) -> None:
        self._config = config
        self._usage = RerankUsage()
        self._warnings: list[str] = []
        self._cache = ResultCache(config.rerank_cache_dir, enabled=config.rerank_cache_enabled)
        self._model: Any | None = None
        self._load_failed = False

    # 폴백이 일어났을 때 쌓인다. 부르는 쪽이 읽는다
    @property
    def warnings(self) -> list[str]:
        return self._warnings

    # 지금까지 쌓인 비용. **여기서 뜻이 있는 것은 `reranked` 다** (모듈 docstring 참고)
    @property
    def usage(self) -> RerankUsage:
        return self._usage

    # **여기만 True 다.** 첫 질의가 무는 가중치 적재 6.2초를 기동으로 옮기는 것이
    # 워밍업의 값어치이고(.claude/wiki/verify_log/2026-08-26-첫질의-대표값.md), 그것이 있는 갈래는 이것뿐이다
    @property
    def needs_warmup(self) -> bool:
        return True

    # 캐시 salt. 규칙은 base 가 정한다
    @property
    def _salt(self) -> str:
        return rerank_cache_salt_for(self._config, prompt_version=SCORING_VERSION)

    # 후보를 점수 순으로. **예외를 안 던진다** — 실패는 RRF 순서로 내려가는 폴백이다
    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        text = query.strip()
        if not text or len(candidates) <= 1:
            return candidates

        pool = candidates[: max(self._config.rerank_candidates, 1)]
        key = content_hash(rerank_cache_key(text, pool, salt=self._salt))

        cached = self._cache.get(key)
        scores = cached.get("scores") if isinstance(cached, dict) else None
        if isinstance(scores, list) and len(scores) == len(pool):
            self._usage.cache_hits += 1
            return apply_scores(pool, [float(value) for value in scores]) + candidates[len(pool):]

        scores = self._predict(text, pool)
        if scores is None:
            return candidates

        self._cache.set(key, {"scores": scores})
        self._usage.reranked += len(pool)
        return apply_scores(pool, scores) + candidates[len(pool):]

    # 모델을 한 번만 올린다. **실패를 기억한다** — 안 그러면 질의마다 다시 시도해
    # 매번 같은 시간을 태우고 경고가 무한히 쌓인다
    def _ensure_model(self) -> Any | None:
        if self._model is None and not self._load_failed:
            self._model = _load_model(self._config.rerank_model, self._config.rerank_device)
            if self._model is None:
                self._load_failed = True
                self._warnings.append(
                    f"cross-encoder '{self._config.rerank_model}' 를 올리지 못해 "
                    "RRF 순서를 씁니다"
                )
        return self._model

    # 실제로 채점한다. 실패·손상은 전부 None 으로 바꿔 폴백에 맡긴다
    def _predict(self, query: str, pool: list[RetrievedChunk]) -> list[float] | None:
        model = self._ensure_model()
        if model is None:
            return None

        pairs = [(query, chunk.text) for chunk in pool]
        try:
            raw = model.predict(pairs)
        except Exception as exc:  # noqa: BLE001 - 폴백이 있는 갈래다 (base.py docstring)
            self._warnings.append(
                f"cross-encoder 채점에 실패해 RRF 순서를 씁니다: {type(exc).__name__}: {exc}"
            )
            return None

        # numpy 배열이 와도 list 로 받는다. **부분 성공을 만들지 않는다** —
        # 길이가 다르거나 수가 아니면 통째로 버린다
        try:
            scores = [float(value) for value in list(raw)]
        except (TypeError, ValueError):
            scores = []
        if len(scores) != len(pool):
            self._warnings.append(
                f"cross-encoder 가 후보 {len(pool)}개에 점수 {len(scores)}개를 줘 "
                "RRF 순서를 씁니다"
            )
            return None
        return scores
