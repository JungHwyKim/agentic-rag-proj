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
from rag_pipeline.retrieval.reranker.base import Reranker, RerankUsage
from rag_pipeline.retrieval.reranker.local_ce import LocalCrossEncoderReranker
from rag_pipeline.retrieval.reranker.openai_llm import OpenAILLMReranker

__all__ = [
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
    if cfg.rerank_provider == "local":
        return LocalCrossEncoderReranker(cfg)
    return OpenAILLMReranker(cfg)
