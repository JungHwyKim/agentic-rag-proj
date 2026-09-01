"""리랭커 단계. 후보 풀을 받아 **순서만** 다시 매긴다.

`retrieve()` 안에서 RRF 뒤·절단 앞에 선다. 밖이 아니라 안인 이유는 `hybrid.py` docstring 에
있다 — 요지는 **절단을 밖으로 옮기면 `top_k` 의 뜻이 호출부마다 갈린다**는 것이다.

## 왜 `rerank_model` 이 비면 꺼지나

**켜지는 순간 질의당 LLM 호출이 1회에서 2회가 된다.** 값을 하는지 재기 전에 과금이 먼저
붙는 것을 막는다. `get_answer_cache()` 가 임계값 없으면 `None` 을 돌려주는 것과 글자 그대로
같은 모양이고, 저장소에는 꺼진 채 커밋된다. 켜는 것은 재고 나서 `.env` 에 이름이 들어가는
순간이다.

**`bm25`·`cache` 와도 같은 자리다** — `retrieve()` 에 안 넘기는 것이 곧 끄는 스위치라,
`use_rerank` 같은 설정을 따로 만들지 않는다.

## 왜 Protocol 을 만드나 — `answer_cache` 는 안 만들었는데

가르는 것은 **두 번째 구현체가 실재하는가**다. 답변 캐시는 하나뿐이라 *"합법값이 하나뿐인
추상은 죽은 추상"* 이었고, 여기는 OpenAI 와 로컬 cross-encoder **둘이 실재한다.** 그래서
`rerank_provider` 설정도 죽은 설정이 아니다 — `embedding_provider` 가 두 번째 구현체가
생기면서 살아난 것과 같은 자리다.

## 이 단계의 규약이 어디 있나

- 실패가 왜 예외가 아니라 폴백인가 / 왜 반쯤 채우지 않는가 / 왜 순위가 아니라 점수인가 —
  `base.py` docstring
- 왜 후보마다가 아니라 한 프롬프트인가 / 왜 청크를 안 자르나 / 프롬프트와 캐시 키 —
  `openai_llm.py` docstring
- 비용의 축이 왜 토큰이 아니라 지연인가 / 왜 장치를 못 박나 — `local_ce.py` docstring
- 리랭커를 `recall@후보수` 로 재면 왜 차이가 0 인가 — `base.py` docstring · `evaluation/README.md`
"""

from __future__ import annotations

from rag_pipeline.config import RerankConfig
from rag_pipeline.retrieval.reranker.base import (
    Reranker,
    RerankUsage,
    rerank_cache_salt_for,
)
from rag_pipeline.retrieval.reranker.cohere_api import API_VERSION, CohereReranker
from rag_pipeline.retrieval.reranker.local_ce import (
    SCORING_VERSION,
    LocalCrossEncoderReranker,
)
from rag_pipeline.retrieval.reranker.openai_llm import PROMPT_VERSION, OpenAILLMReranker

__all__ = [
    "CohereReranker",
    "rerank_cache_salt",
    "LocalCrossEncoderReranker",
    "OpenAILLMReranker",
    "RerankUsage",
    "Reranker",
    "get_reranker",
]


# 설정에 맞는 리랭커를 만든다. **모델 이름이 없으면 None** — 그것이 곧 꺼진 상태다.
#
# `get_embedder()` 와 달리 `None` 을 돌려줄 수 있는 것은 이 단계가 **선택적**이기 때문이다.
# 임베딩은 없으면 색인이 성립하지 않지만 리랭커는 없으면 RRF 순서로 돈다
def get_reranker(config: RerankConfig | None = None) -> Reranker | None:
    cfg = config or RerankConfig()
    if not cfg.rerank_model:
        return None
    # **명시 분기다 — `else` 를 유료 갈래로 두지 않는다.** 구현체가 둘일 때는 `else` 가
    # 곧 openai 여도 읽혔지만, 셋이 되면 **오타 난 provider 가 조용히 과금 갈래로 간다**
    # (`Literal` 이 막아 주는 것은 설정 객체를 만들 때뿐이라, 직접 만든 config 는 안 걸린다)
    if cfg.rerank_provider == "local":
        return LocalCrossEncoderReranker(cfg)
    if cfg.rerank_provider == "cohere":
        return CohereReranker(cfg)
    return OpenAILLMReranker(cfg)


# 캐시 salt 를 **리랭커 객체 없이** 만든다. `.cache/rerank` 를 밖에서 읽는 도구가 쓰는
# 자리다(`evaluation/rerank_scores.py`) — 유료 갈래는 객체를 만드는 것만으로는 과금이
# 안 되지만, 객체를 안 만들면 **과금이 구조적으로 불가능**해진다
#
# **분기가 `get_reranker()` 와 같은 모양인 것이 요점이다.** 갈리는 것은 `prompt_version`
# 하나뿐인데 그 값이 구현체마다 다른 모듈 상수라, 부르는 쪽이 다시 적으면 `base.py` 가
# *"구현체마다 다시 짜면 한쪽이 빠뜨려도 아무 에러가 안 난다"* 로 경계한 그 상태가 된다.
# 여기서도 `else` 를 유료 갈래로 두지 않는다 — 오타 난 provider 가 조용히 그쪽 키를 읽는다
#
# **어긋나도 에러가 아니라 「캐시 미스」로 보인다** — 없는 키를 찾을 뿐이라 도구는 그것을
# *그 실행이 캐시에 없다* 로 읽는다. 그래서 `tests/retrieval/test_rerank.py` 가 세 갈래
# 모두에서 구현체의 `_salt` 와 글자까지 대조한다
def rerank_cache_salt(config: RerankConfig) -> str:
    if config.rerank_provider == "local":
        return rerank_cache_salt_for(config, prompt_version=SCORING_VERSION)
    if config.rerank_provider == "cohere":
        return rerank_cache_salt_for(config, prompt_version=API_VERSION)
    return rerank_cache_salt_for(config, prompt_version=PROMPT_VERSION)
