"""검색의 공개 진입 함수. dense 한 갈래만 쓸 수도 있고 BM25 를 얹을 수도 있다.

## 왜 `bm25` 를 인자로 받나

`store`·`embedder` 와 **같은 이유이자 같은 대우**다 — *"수명을 부르는 쪽이 정한다"*
(`rag_pipeline/query.py` docstring). backend 는 첫 질의에 지어 `app.state` 에 두고, 평가
러너는 실행마다 새로 짓는다. 여기서 지으면 질의마다 저장소를 통째로 다시 훑는다.

**그래서 `use_bm25` 설정이 없다.** 인덱스를 안 넘기는 것이 곧 끄는 스위치라, 설정으로 두면
합법값이 하나뿐인 죽은 설정이 된다(`embedding.provider` 를 안 만든 것과 같은 잣대).

## 왜 dense 를 먼저 부르나

BM25 실패는 삼켜서 dense 로 내려가는데, 순서를 바꾸면 **"컬렉션이 없다"가 경고로 삼켜진다.**
그러면 색인을 한 번도 안 한 상태가 *"BM25 만 실패했네"* 로 보이고, 정작 사용자가 해야 할 일
(문서를 색인하라)이 화면에 안 나온다. dense 의 `IndexingError` 는 그대로 올라가야 한다.

## 왜 실패가 예외가 아니라 폴백인가

로더의 *빈 결과 + `warn`* 규약을 따른다. 그 규약이 성립하는 조건은 **내려갈 층이 있는가**이고
여기는 있다 — dense 는 독립적으로 돌고 지금 기준선도 그것이다. embedding·indexing 이 예외인
이유는 내려갈 층이 없어서였다(`plan_log/embedding.md`).

경고는 `Bm25Index.warnings` 에 쌓여 **부르는 쪽이 읽는다.** 반환 계약
(`-> list[RetrievedChunk]`)을 안 바꾸는 것이 요점이다 — 바꾸면 Phase 3~5(리랭커·Evaluator·
Rewriter)의 계약이 전부 따라 바뀐다.

## `bm25=None` 이면 예전과 글자 그대로 같다

dense 결과를 `top_k` 만큼 받아 `fused_score` 에 저장소 점수를 그대로 넣는다. RRF 를 태우지
않는 이유는 경로가 하나뿐일 때 순위 변환이 **정보를 잃기만 하기 때문**이다(점수 간격이
사라진다). 이 동등성이 깨지면 hybrid 비교의 기준선이 함께 움직여 무엇이 달라졌는지 못 가른다.

**`reranker=None` 도 같은 대우다** — 안 넘기면 예전과 글자 그대로 같은 경로다.

## 왜 리랭커가 이 함수 **안**인가

후보 풀은 원래부터 밖에서 닿았다 — `retrieve(top_k=50, bm25=...)` 이면 아래 `limit` 이
`candidate_k` 로 넓어져 풀 전체가 나오고, 리랭커 여지를 잰 탐침이 그렇게 쟀다
(`verify_log/2026-08-24-리랭커-여지.md`). 그러니 물음은 *"풀에 닿느냐"* 가 아니라
**"절단을 누가 하느냐"** 였다.

밖으로 옮기면 `top_k` 의 뜻이 **"최종 개수"와 "후보 수"로 갈린다.** 그러면 `query.py` ·
`evaluation/run_eval.py` · `backend/routes/query.py` 셋이 각자 풀 폭과 절단을 알아야 하고,
**한 곳만 빠뜨리면 조용히 리랭커 없이 돈다.** 풀 전체를 보는 자리는 여기 하나뿐이라
여기서 자른다.

## 리랭커를 켜면 dense-only 갈래도 풀을 넓힌다

`limit` 이 `use_bm25` 만 보던 때는 BM25 가 없으면 `top_k` 만 가져왔다. 그대로 두면
**BM25 없이 리랭커만 켰을 때 리랭커가 볼 것이 `top_k` 개뿐**이라, 이 회차가 노린 11~50위의
여지를 통째로 버린다. 그래서 둘 중 하나만 있어도 넓힌다.

## 리랭커가 실패하면 여기서 아무 일도 안 일어난다

구현체가 예외를 안 던지고 **받은 목록을 그대로 돌려주는 것**이 계약이다
(`reranker/base.py`). 그러면 `rerank_score` 가 `None` 으로 남고 `ranking_score` 가
`fused_score` 로 떨어져 **RRF 순서 그대로** 잘린다. 경고는 `Reranker.warnings` 에 쌓여
부르는 쪽이 읽는다 — `Bm25Index.warnings` 와 같은 자리다.
"""

from __future__ import annotations

from rag_pipeline.config import RetrievalConfig
from rag_pipeline.ingestion.embedding.base import Embedder
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.retrieval.bm25 import Bm25Index
from rag_pipeline.retrieval.dense import search_dense
from rag_pipeline.retrieval.fusion import fuse
from rag_pipeline.retrieval.reranker.base import Reranker
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["retrieve"]


# 저장소 결과를 그대로 검색 결과로 승격한다. 경로가 하나뿐일 때의 정렬 기준은 그 점수다
def _dense_only(results: list, top_k: int) -> list[RetrievedChunk]:
    chunks = []
    for result in results[:top_k]:
        chunk = RetrievedChunk.from_stored(result, dense_score=result.score)
        # fused_score 는 경로가 하나뿐이어도 채운다 — 정렬 기준이 늘 같은 자리에 있어야 한다
        # (schemas/retrieval.py 의 ranking_score)
        chunk.fused_score = result.score
        chunks.append(chunk)
    return chunks


# 풀을 리랭커에 먹이고 top_k 로 자른다. **리랭커가 없으면 자르기만 한다** —
# 그래야 reranker=None 인 경로가 예전과 글자 그대로 같다
def _finish(
    pool: list[RetrievedChunk], query: str, reranker: Reranker | None, top_k: int
) -> list[RetrievedChunk]:
    if reranker is None:
        return pool[:top_k]
    # 실패해도 예외가 아니라 받은 목록이 온다 (reranker/base.py 의 계약)
    return reranker.rerank(query, pool)[:top_k]


# 질의에 맞는 근거 청크를 top_k 개 고른다. 공개 진입 함수이고 HTTP 를 모른다
def retrieve(
    query: str,
    *,
    store: VectorStore,
    embedder: Embedder,
    top_k: int = 5,
    bm25: Bm25Index | None = None,
    reranker: Reranker | None = None,
    config: RetrievalConfig | None = None,
) -> list[RetrievedChunk]:
    text = query.strip()
    if not text:
        return []

    cfg = config or RetrievalConfig()
    # 합칠 것이 있거나 다시 정렬할 것이 있을 때만 후보를 넓힌다. 둘 다 없으면 넓혀 봐야
    # 버릴 뿐인데 저장소는 이미 훑는다. **리랭커만 켰을 때도 넓혀야 한다** (모듈 docstring)
    use_bm25 = bm25 is not None and len(bm25) > 0
    widen = use_bm25 or reranker is not None
    limit = max(cfg.candidate_k, top_k) if widen else top_k

    # **dense 가 먼저다** (모듈 docstring 참고). 여기서 나는 IndexingError 는 안 삼킨다
    dense_results = search_dense(text, store=store, embedder=embedder, top_k=limit)

    if not use_bm25:
        return _finish(_dense_only(dense_results, limit), text, reranker, top_k)

    assert bm25 is not None  # noqa: S101 - use_bm25 가 이미 보장한다. 타입 좁히기용
    try:
        sparse = bm25.search(text, limit)
    except Exception as exc:  # noqa: BLE001 - 폴백이 있는 갈래다 (모듈 docstring 참고)
        bm25.warnings.append(f"BM25 검색에 실패해 dense 만 씁니다: {type(exc).__name__}: {exc}")
        return _finish(_dense_only(dense_results, limit), text, reranker, top_k)

    # **fuse 를 limit 으로 자른다** — top_k 로 자르면 리랭커가 볼 풀이 여기서 사라진다
    pool = fuse(dense_results, sparse, rrf_k=cfg.rrf_k, top_k=limit)
    return _finish(pool, text, reranker, top_k)
