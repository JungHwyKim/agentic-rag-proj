"""검색의 공개 진입 함수. dense 한 갈래만 쓸 수도 있고 BM25 를 얹을 수도 있다.

## 왜 `bm25` 를 인자로 받나

`store`·`embedder` 와 **같은 이유이자 같은 대우**다 — *"수명을 부르는 쪽이 정한다"*
(`rag_pipeline/query.py` docstring). backend 는 첫 질의에 지어 `app.state` 에 두고, 평가
러너는 실행마다 새로 짓는다. 여기서 지으면 질의마다 저장소를 통째로 다시 훑는다.

**그래서 `use_bm25` 설정이 없다.** 인덱스를 안 넘기는 것이 곧 끄는 스위치라, 설정으로 두면
합법값이 하나뿐인 죽은 설정이 된다(`embedding.provider` 를 안 만든 것과 같은 기준).

## 왜 dense 를 먼저 부르나

BM25 실패는 삼켜서 dense 로 내려가는데, 순서를 바꾸면 **"컬렉션이 없다"가 경고로 삼켜진다.**
그러면 색인을 한 번도 안 한 상태가 *"BM25 만 실패했네"* 로 보이고, 정작 사용자가 해야 할 일
(문서를 색인하라)이 화면에 안 나온다. dense 의 `IndexingError` 는 그대로 올라가야 한다.

## 왜 실패가 예외가 아니라 폴백인가

로더의 *빈 결과 + `warn`* 규약을 따른다. 그 규약이 성립하는 조건은 **내려갈 층이 있는가**이고
여기는 있다 — dense 는 독립적으로 돌고 지금 기준선도 그것이다. embedding·indexing 이 예외인
이유는 내려갈 층이 없어서였다(`.claude/wiki/plan_log/embedding.md`).

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
(`.claude/wiki/verify_log/2026-08-24-리랭커-여지.md`). 그러니 물음은 *"풀에 닿느냐"* 가 아니라
**"절단을 누가 하느냐"** 였다.

밖으로 옮기면 `top_k` 의 뜻이 **"최종 개수"와 "후보 수"로 갈린다.** 그러면 `query.py` ·
`evaluation/run_eval.py` · `backend/routes/query.py` 셋이 각자 풀 폭과 절단을 알아야 하고,
**한 곳만 빠뜨리면 조용히 리랭커 없이 돈다.** 풀 전체를 보는 자리는 여기 하나뿐이라
여기서 자른다.

## 리랭커를 켜면 dense-only 갈래도 풀을 넓힌다

`limit` 이 `use_bm25` 만 보던 때는 BM25 가 없으면 `top_k` 만 가져왔다. 그대로 두면
**BM25 없이 리랭커만 켰을 때 리랭커가 볼 것이 `top_k` 개뿐**이라, 이 회차가 노린 11~50위의
여지를 통째로 버린다. 그래서 둘 중 하나만 있어도 넓힌다.

## 질의 재작성은 1차 검색 **뒤**·합류 **앞**이다

`rewriter` 도 안 넘기는 것이 곧 끄는 스위치다. 넘기면 순서가 이렇다 —

    1차 검색(원 질의) → 재작성 → 재작성 질의로 다시 검색 → 합류 → 리랭커 → 절단

1차 검색이 **재작성의 입력이자 그대로 폴백 층**이다. 재작성이 `None` 을 돌려주면 그 pool 을
그대로 쓰므로 **오늘과 글자 그대로 같은 경로**가 된다 — 따로 되돌릴 것이 없다.

**리랭커에 넘기는 질의는 원 질의다.** 재작성 질의는 검색용 확장어라 *"이 본문이 질문에
답하나"* 의 대상이 아니고, 더 실제적으로는 `rerank_cache_key()` 가 query 를 싣기 때문에
재작성 프롬프트를 한 줄 고칠 때마다 **리랭킹 캐시가 전량 미스**가 된다 — 그것은 곧 유료
호출이다(`.claude/wiki/plan_log/검색-생성.md` 22회차).

**합류 방식이 둘이고 둘 다 실재한다** — `fuse` 는 원 질의 갈래를 남기고, `replace` 는
재작성 질의로만 검색한다. **21회차가 잰 상한(`sih-07` 102위 → 2위)은 `replace` 의 수치다**
(프로브가 질의 문자열을 갈아끼워 쟀다). 어느 쪽이 나은지는 재봐야 알아서 둘 다 만들었다.

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
from rag_pipeline.retrieval.fusion import Branch, fuse_branches
from rag_pipeline.retrieval.reranker.base import Reranker
from rag_pipeline.retrieval.rewrite import QueryRewriter
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


# BM25 갈래가 실제로 도는가. **이 판정이 한 벌이어야 한다** (모듈 docstring 참고).
#
# `retrieve()` 의 풀 넓히기, `_branches_for()` 의 갈래 추가, 그리고 답변 캐시의 파이프라인
# 지문이 전부 이것을 본다. 세 자리가 각자 `bm25 is not None and len(bm25) > 0` 을 적으면
# 한쪽만 고쳐졌을 때 **지문이 조용히 어긋나** 낡은 답이 히트한다 — 에러가 안 나는 종류다
def uses_bm25(bm25: Bm25Index | None) -> bool:
    return bm25 is not None and len(bm25) > 0


# 한 질의로 dense (+ BM25) 를 돌려 갈래 목록을 만든다. **BM25 실패는 dense 만 남기는
# 폴백이고 예외가 아니다** (모듈 docstring 참고).
#
# 갈래를 `Branch` 로 돌려주는 이유는 재작성 갈래와 **같은 모양이어야** 하기 때문이다 —
# 원 질의만 특별 취급하면 합류 코드가 두 벌이 되고, 한쪽만 고쳐져도 아무 에러가 안 난다
def _branches_for(
    text: str,
    *,
    store: VectorStore,
    embedder: Embedder,
    bm25: Bm25Index | None,
    limit: int,
    dense_slot: str,
    bm25_slot: str,
) -> list[Branch]:
    # **dense 가 먼저다** (모듈 docstring 참고). 여기서 나는 IndexingError 는 안 삼킨다
    dense_results = search_dense(text, store=store, embedder=embedder, top_k=limit)
    branches = [
        Branch([(result, result.score) for result in dense_results], slot=dense_slot)
    ]

    if not uses_bm25(bm25):
        return branches

    try:
        sparse = bm25.search(text, limit)
    except Exception as exc:  # noqa: BLE001 - 폴백이 있는 갈래다 (모듈 docstring 참고)
        bm25.warnings.append(f"BM25 검색에 실패해 dense 만 씁니다: {type(exc).__name__}: {exc}")
        return branches

    branches.append(Branch(sparse, slot=bm25_slot))
    return branches


# 질의에 맞는 근거 청크를 top_k 개 고른다. 공개 진입 함수이고 HTTP 를 모른다
def retrieve(
    query: str,
    *,
    store: VectorStore,
    embedder: Embedder,
    top_k: int = 5,
    bm25: Bm25Index | None = None,
    reranker: Reranker | None = None,
    rewriter: QueryRewriter | None = None,
    config: RetrievalConfig | None = None,
) -> list[RetrievedChunk]:
    text = query.strip()
    if not text:
        return []

    cfg = config or RetrievalConfig()
    # 합칠 것이 있거나 다시 정렬할 것이 있을 때만 후보를 넓힌다. 둘 다 없으면 넓혀 봐야
    # 버릴 뿐인데 저장소는 이미 훑는다. **리랭커만 켰을 때도 넓혀야 한다** (모듈 docstring).
    # **재작성도 같다** — 안 넓히면 PRF 가 top_k 개만 보고 재작성 갈래도 좁은 폭으로 돌아,
    # 이 단계가 노린 11~50위의 여지를 통째로 버린다
    use_bm25 = uses_bm25(bm25)
    widen = use_bm25 or reranker is not None or rewriter is not None
    limit = max(cfg.candidate_k, top_k) if widen else top_k

    branches = _branches_for(
        text,
        store=store,
        embedder=embedder,
        bm25=bm25 if use_bm25 else None,
        limit=limit,
        dense_slot="dense_score",
        bm25_slot="bm25_score",
    )
    # **갈래가 하나뿐이면 RRF 를 안 태운다** — 순위 변환이 정보를 잃기만 한다(모듈 docstring).
    # 이 동등성이 깨지면 hybrid 비교의 기준선이 함께 움직인다
    pool = (
        _dense_only([chunk for chunk, _ in branches[0].ranked], limit)
        if len(branches) == 1
        else fuse_branches(branches, rrf_k=cfg.rrf_k, top_k=limit)
    )

    if rewriter is None:
        return _finish(pool, text, reranker, top_k)

    # 1차 pool 이 재작성의 입력이자 그대로 폴백 층이다 — None 이면 아래를 안 밟고,
    # 그것이 곧 재작성 없이 도는 경로다 (모듈 docstring)
    rewritten = rewriter.rewrite(text, pool)
    if rewritten is None:
        return _finish(pool, text, reranker, top_k)

    rewrite_branches = _branches_for(
        rewritten,
        store=store,
        embedder=embedder,
        bm25=bm25 if use_bm25 else None,
        limit=limit,
        dense_slot="rewrite_dense_score",
        bm25_slot="rewrite_bm25_score",
    )
    # **1차 pool 이 아니라 원 검색 결과를 합친다** — 합쳐진 것을 다시 RRF 에 넣으면 순위
    # 압축이 두 번 걸린다 (fusion.py docstring)
    merged = branches + rewrite_branches if rewriter.mode == "fuse" else rewrite_branches
    pool = fuse_branches(merged, rrf_k=cfg.rrf_k, top_k=limit)
    # **리랭커에는 원 질의를 넘긴다** (모듈 docstring 참고)
    return _finish(pool, text, reranker, top_k)
