"""검색 단계. 질의를 받아 근거가 될 청크를 고른다.

갈래는 둘이다 — **dense**(저장소 벡터 검색)와 **BM25**(낱말 색인). 둘을 쓸 때는 RRF 로
합친다. 그 위에 **리랭커**가 선택적으로 얹혀 후보 풀의 순서를 다시 매긴다.

**리랭커가 붙는 자리는 `retrieve()` 의 반환 뒤가 아니라 안이다.** 이 docstring 이 한동안
*"반환 뒤"* 라고 적어 뒀는데 틀렸다 — 뒤에 두면 리랭커가 보는 것이 `top_k` 개뿐이라
11~50위에 잠긴 여지를 통째로 버린다. 근거와 절단 위치는 `hybrid.py` docstring.

그 앞에 **질의 재작성**이 선택적으로 붙는다 — 1차 검색 결과를 보고 질의를 다시 쓰고
(pseudo-relevance feedback), 그 결과를 원 질의와 합치거나(`fuse`) 갈아끼운다(`replace`).

**BM25 인덱스·리랭커·재작성기는 이 패키지가 안 들고 있다.** `build_bm25_index(store)` ·
`get_reranker(config)` · `get_rewriter(config)` 로 지어 부르는 쪽이 들고
`retrieve(..., bm25=..., reranker=..., rewriter=...)` 로 넘긴다 — `store`·`embedder` 와 같은
대우이고, **안 넘기는 것이 곧 끄는 스위치**다.

## 이 단계의 규약이 어디 있나

- 왜 `store`·`embedder`·`bm25` 를 인자로 받나 / dense 를 먼저 부르는 이유 /
  리랭커가 왜 `retrieve()` 안인가 — `hybrid.py` docstring
- 리랭커 실패가 왜 폴백인가 / 왜 반쯤 안 채우나 / 왜 순위가 아니라 점수인가 —
  `reranker/base.py` docstring
- `rerank_model` 이 비면 왜 꺼지나 / 왜 Protocol 을 만드나 — `reranker/__init__.py` docstring
- 왜 코퍼스가 아니라 1차 검색 결과를 보나 / 왜 질문 문장 하나인가 / 캐시 salt 에 `rewrite_mode`
  가 없는 이유 / 왜 여기는 Protocol 을 **안** 만드나 — `rewrite.py` docstring
- 재작성이 어디에 끼나 / 리랭커에 원 질의를 넘기는 이유 / `fuse` 와 `replace` — `hybrid.py` docstring
- 갈래가 왜 둘이 아니라 N 개인가 / RRF-of-RRF 를 안 만드는 이유 — `fusion.py` docstring
- BM25 실패가 예외가 아니라 dense 폴백인 이유 / `k1`·`b` 를 설정으로 안 여는 이유 — `bm25.py` docstring
- 한글은 bigram, 영문·숫자는 낱말인 이유 — `tokenize.py` docstring
- 점수를 안 더하고 순위를 쓰는 이유(RRF) / 동점 처리 — `fusion.py` docstring
- `top_k` 를 왜 호출 쪽이 넘기나 — `dense.py` docstring
- `RetrievedChunk` 의 필드와 경로별 점수가 `float | None` 인 이유 — `schemas/retrieval.py` docstring
- `SearchResult` 와 `StoredChunk` 가 갈려 있는 이유 — `schemas/vector.py` docstring
- `search()` 가 컬렉션이 없을 때 0건이 아니라 예외인 이유 / `iter_chunks()` 는 반대인 이유 —
  `ingestion/indexing/qdrant_store.py`
- 저장소 중립을 어떻게 지키나(`VectorStore` Protocol 만 본다) — `ingestion/indexing/base.py`
- `candidate_k`·`rrf_k` 가 임시값인 것과 재판정 조건 — `rag_pipeline/config.py` 의 `RetrievalConfig`
"""

from __future__ import annotations

from rag_pipeline.retrieval.bm25 import Bm25Index, build_bm25_index
from rag_pipeline.retrieval.hybrid import retrieve
from rag_pipeline.retrieval.reranker import Reranker, get_reranker
from rag_pipeline.retrieval.rewrite import QueryRewriter, get_rewriter

__all__ = [
    "Bm25Index",
    "QueryRewriter",
    "Reranker",
    "build_bm25_index",
    "get_reranker",
    "get_rewriter",
    "retrieve",
]
