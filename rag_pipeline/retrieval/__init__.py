"""검색 단계. 질의를 받아 근거가 될 청크를 고른다.

갈래는 둘이다 — **dense**(저장소 벡터 검색)와 **BM25**(낱말 색인). 둘을 쓸 때는 RRF 로
합친다. reranker 는 `plan_log/검색-생성.md` 가 Phase 3 으로 예약해 두었고, 붙는 자리는
`retrieve()` 의 반환 뒤다(`RetrievedChunk.rerank_score` 칸이 이미 비어 있다).

**BM25 인덱스는 이 패키지가 안 들고 있다.** `build_bm25_index(store)` 로 지어 부르는 쪽이
들고 `retrieve(..., bm25=...)` 로 넘긴다 — `store`·`embedder` 와 같은 대우다.

## 이 단계의 규약이 어디 있나

- 왜 `store`·`embedder`·`bm25` 를 인자로 받나 / dense 를 먼저 부르는 이유 — `hybrid.py` docstring
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

__all__ = ["Bm25Index", "build_bm25_index", "retrieve"]
