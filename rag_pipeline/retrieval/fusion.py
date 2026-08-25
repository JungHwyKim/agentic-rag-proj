"""여러 검색 경로의 결과를 하나의 순위로 합친다.

## 왜 점수를 안 더하고 순위를 쓰나

**dense 점수와 BM25 점수는 단위가 다르다.** dense 는 단위 벡터의 내적이라 대략 [-1, 1] 이고
(`qdrant_store.py` 가 Dot 으로 고정한 근거) BM25 는 상한이 없다. 그대로 더하면 BM25 가 늘
이긴다. 정규화하려면 두 분포를 알아야 하는데, 문서마다 질의마다 달라 **정규화 상수를
지어내게 된다.**

RRF(Reciprocal Rank Fusion)는 점수를 버리고 **순위만** 쓴다 — `Σ 1/(rrf_k + rank)`.
정규화가 필요 없는 것이 아니라 정규화할 것이 애초에 없다.

## 왜 point id 로 합치나

`chunk_id`(사람이 읽는 이름)가 아니라 `StoredChunk.id` 다. 저장소가 부여한 신원이라
어느 경로로 얻어도 같은 값이고, dense 는 검색으로 BM25 는 열거로 얻었어도 일치한다.

## 동점은 dense 가 이긴다

정렬이 stable 이고 dense 를 먼저 넣기 때문이다. **의도된 것이다** — dense 는 저장소가
매긴 점수를 갖고 있어 근거가 있고, 동점인 두 후보 사이에서 순서를 임의로 흔들 이유가 없다.
"""

from __future__ import annotations

from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import SearchResult, StoredChunk

__all__ = ["fuse"]


# 경로별 후보를 RRF 로 합쳐 상위 top_k 개를 돌려준다.
# 한쪽에만 걸린 후보의 반대쪽 점수는 **None 으로 남는다** — 0.0 을 채우면 "점수 0" 과
# "후보가 아니었음" 이 안 갈린다 (schemas/retrieval.py)
def fuse(
    dense: list[SearchResult],
    sparse: list[tuple[StoredChunk, float]],
    *,
    rrf_k: int,
    top_k: int,
) -> list[RetrievedChunk]:
    merged: dict[str, RetrievedChunk] = {}
    fused: dict[str, float] = {}

    for rank, result in enumerate(dense, start=1):
        merged[result.id] = RetrievedChunk.from_stored(result, dense_score=result.score)
        fused[result.id] = 1.0 / (rrf_k + rank)

    for rank, (chunk, score) in enumerate(sparse, start=1):
        contribution = 1.0 / (rrf_k + rank)
        existing = merged.get(chunk.id)
        if existing is None:
            merged[chunk.id] = RetrievedChunk.from_stored(chunk, bm25_score=score)
            fused[chunk.id] = contribution
        else:
            existing.bm25_score = score
            fused[chunk.id] += contribution

    for key, chunk in merged.items():
        chunk.fused_score = fused[key]

    # 삽입 순서가 dense 우선이고 sort 가 stable 이라 동점은 dense 가 이긴다
    ranked = sorted(merged.values(), key=lambda chunk: chunk.fused_score, reverse=True)
    return ranked[: max(top_k, 0)]
