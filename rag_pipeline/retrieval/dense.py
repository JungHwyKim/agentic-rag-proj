"""dense 검색 한 갈래. 질의를 벡터로 만들어 저장소에서 가까운 청크를 가져온다.

## 왜 `store` 와 `embedder` 를 인자로 받나

**스스로 열면 안 되기 때문이다.** `index_document()` 는 `get_store()` 로 자기 클라이언트를
만들고 `finally` 에서 닫는데, 그건 한 번 돌고 끝나는 배치라서 성립한다. 질의는 다르다 —
qdrant 로컬 파일 모드가 저장 디렉터리에 배타 잠금을 걸어, 요청마다 열면 두 번째 질의가
`RuntimeError` 로 죽는다. 그래서 backend 가 startup 에 하나 열어 들고 있고 여기로 넘긴다.

`embedder` 도 같은 이유는 아니고 **사용량 때문**이다. `evaluation/run_eval.py` 가 루프를
다 돈 뒤 `embedder.usage.total_tokens` 를 읽는다. 여기서 만들면 그 값을 밖에서 못 본다.

## 호출 쪽이 `top_k` 를 반드시 넘긴다

`run_eval` 은 `--k`(기본 20)로 후보를 **한 번** 받아 `axes_for(k)` = (1, 5, 10, 20) 축마다
따로 채점한다 — 검색은 한 번이고 채점만 여러 번이다. 여기 기본값 5 가 무심코 실리면
`@10`·`@20` 만 후보 부족으로 조용히 낮아지고, 원인이 **검색 품질처럼 보인다.**

## 왜 `RetrievedChunk` 로 승격하나

경로가 dense 하나뿐인 지금은 `SearchResult` 로도 충분하다(`schemas/retrieval.py` docstring).
그런데도 여기서 승격하는 것은 이 값이 `Answer.sources` 로 **그대로 나가는 공개 계약**이라,
BM25 가 붙는 날 타입이 바뀌면 라우트와 템플릿까지 함께 고쳐야 하기 때문이다.
`dense_score` 에 저장소 점수를 그대로 실어 두면 **임계값 논의를 나중에 실측으로** 할 수 있다.
"""

from __future__ import annotations

from rag_pipeline.ingestion.embedding.base import Embedder
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["retrieve"]


# 질의와 가까운 청크를 top_k 개 가져온다. 공개 진입 함수이고 HTTP 를 모른다
def retrieve(
    query: str,
    *,
    store: VectorStore,
    embedder: Embedder,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    text = query.strip()
    if not text:
        return []

    vector = embedder.embed_query(text)
    results = store.search(vector, top_k=top_k)
    # fused_score 는 경로가 하나뿐이어도 채운다 — 정렬 기준이 늘 같은 자리에 있어야 한다
    # (schemas/retrieval.py 의 ranking_score)
    chunks = []
    for result in results:
        chunk = RetrievedChunk.from_search_result(result, dense_score=result.score)
        chunk.fused_score = result.score
        chunks.append(chunk)
    return chunks
