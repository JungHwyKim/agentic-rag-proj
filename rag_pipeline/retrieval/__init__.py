"""검색 단계. 질의를 받아 근거가 될 청크를 고른다.

지금 갈래는 **dense 하나뿐이다.** hybrid(BM25)·reranker 는 `plan_log/검색-생성.md` 가
Phase 2 이후로 예약해 두었고, 붙는 자리는 여기다 — `retrieve()` 가 `RetrievedChunk` 를
돌려주는 것이 그 준비다(경로별 점수 칸이 이미 있다).

## 이 단계의 규약이 어디 있나

- 왜 `store`·`embedder` 를 인자로 받나 / `top_k` 를 왜 호출 쪽이 넘기나 — `dense.py` docstring
- `RetrievedChunk` 의 필드와 `float | None` 인 이유 — `schemas/retrieval.py` docstring
- `search()` 가 컬렉션이 없을 때 0건이 아니라 예외인 이유 — `ingestion/indexing/qdrant_store.py`
- 저장소 중립을 어떻게 지키나(`VectorStore` Protocol 만 본다) — `ingestion/indexing/base.py`
"""

from __future__ import annotations

from rag_pipeline.retrieval.dense import retrieve

__all__ = ["retrieve"]
