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

## 왜 갈래가 둘이 아니라 N 개인가

질의 재작성(Phase 5)을 켜면 경로가 넷이 된다 — 원 질의 dense·BM25 + 재작성 질의 dense·BM25.
그래서 `fuse_branches()` 가 갈래 목록을 받는다. **두 갈래짜리 `fuse()` 를 남기지 않았다** —
호출부가 `hybrid.py` 하나로 줄어 production 소비자가 0 이 되었고, 그러면 이 저장소가 일관되게
기각해 온 *"합법값이 하나뿐인 죽은 것"* 과 같은 자리가 된다. 계약 테스트는 이름을 옮겼다.

**1차 결과를 다시 RRF 에 넣지 않는다.** 재작성 갈래를 합칠 때 쓸 것은 *이미 합쳐진 pool* 이
아니라 **원 검색 결과**다. 합친 것을 다시 넣으면 순위 압축이 두 번 걸려, 이 모듈이 점수를
버리고 순위를 쓰며 치른 대가를 한 번 더 치른다(`reranker/base.py` 가 *"순위만 받으면 지어낸
수를 넣게 된다"* 로 같은 이야기를 적어 뒀다).

## 왜 갈래마다 점수 칸 이름을 들고 다니나

RRF 자체는 순위만 쓰지만 **점수는 되짚기 위해 남긴다**(`schemas/retrieval.py`). 어느 갈래의
점수인지를 갈래가 스스로 알고 있어야, 갈래가 늘 때 이 함수가 분기를 늘리지 않는다 —
늘리는 쪽으로 두면 새 갈래를 더할 때 한 줄을 빠뜨려도 **점수만 조용히 안 실린다.**
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import StoredChunk

__all__ = ["Branch", "fuse_branches"]


# 검색 경로 하나가 내놓은 결과. RRF 는 순위만 쓰므로 **등수 순의 목록**이면 충분하다
@dataclass(frozen=True)
class Branch:
    # (청크, 점수) 를 등수 순으로. dense 의 SearchResult 도 StoredChunk 라 그대로 들어온다
    ranked: list[tuple[StoredChunk, float]]
    # 이 갈래의 점수가 RetrievedChunk 의 어느 칸에 실리나 (모듈 docstring 참고)
    slot: str


# 갈래 여럿을 RRF 로 합쳐 상위 top_k 개를 돌려준다. **갈래 수는 열려 있다** — 질의 재작성을
# 켜면 넷이다 (모듈 docstring 참고).
#
# 한쪽에만 걸린 후보의 반대쪽 점수는 **None 으로 남는다** — 0.0 을 채우면 "점수 0" 과
# "후보가 아니었음" 이 안 갈린다 (schemas/retrieval.py)
def fuse_branches(
    branches: list[Branch],
    *,
    rrf_k: int,
    top_k: int,
) -> list[RetrievedChunk]:
    merged: dict[str, RetrievedChunk] = {}
    fused: dict[str, float] = {}

    for branch in branches:
        for rank, (chunk, score) in enumerate(branch.ranked, start=1):
            contribution = 1.0 / (rrf_k + rank)
            existing = merged.get(chunk.id)
            if existing is None:
                existing = RetrievedChunk.from_stored(chunk)
                merged[chunk.id] = existing
                fused[chunk.id] = contribution
            else:
                fused[chunk.id] += contribution
            # 갈래가 자기 칸 이름을 들고 있다 — 여기서 갈래별로 분기하지 않는 이유는
            # 모듈 docstring 에 있다. **같은 갈래에 같은 청크가 두 번 오면 뒤가 이긴다**
            setattr(existing, branch.slot, score)

    for key, chunk in merged.items():
        chunk.fused_score = fused[key]

    # 삽입 순서가 첫 갈래(=dense) 우선이고 sort 가 stable 이라 동점은 그쪽이 이긴다
    ranked = sorted(merged.values(), key=lambda chunk: chunk.fused_score, reverse=True)
    return ranked[: max(top_k, 0)]
