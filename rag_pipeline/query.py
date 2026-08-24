"""질의 경로의 진입점. 파일이 아니라 **질문**을 받는다.

`ingestion/pipeline.py` 의 `ingest()`·`index_document()` 와 나란한 세 번째 진입점이고,
저 둘과 마찬가지로 **HTTP 를 전혀 모른다.** 스크립트나 노트북에서도 그대로 쓸 수 있다.

## 왜 `ingestion/` 아래가 아닌가

적재 단계가 아니기 때문이다. 저쪽은 파일 하나를 받아 저장소에 넣는 흐름이고 이쪽은 이미
들어간 것을 꺼내는 흐름이다. 같은 폴더에 두면 `ingestion` 이라는 이름이 거짓이 된다.

## 왜 `store` 와 `embedder` 를 인자로 받나

`index_document()` 는 `store` 를 **안 넘기면** `get_store()` 로 자기 클라이언트를 열고
`finally` 에서 닫는다. 한 번 돌고 끝나는 배치(스크립트·평가 러너)라 그게 맞다. 앱 안에서
부를 때는 넘겨서 그 경로를 피한다. **질의는 다르다** — qdrant 로컬 파일 모드가 저장 디렉터리에
배타 잠금을 걸어 요청마다 열면 두 번째 질의가 죽는다. 그래서 수명을 부르는 쪽이 정한다:
backend 는 startup 에 하나 열어 앱 수명 내내 들고 있고, 스크립트는 `with` 로 쓴다.

**여기서 열지 않는 것이 곧 계약이다.** 편의로 기본값을 만들어 두면(없으면 열어준다) 그
경로를 밟은 두 번째 호출이 잠금 오류로 죽는데, 원인이 이 함수에 안 보인다.

## `bm25` 도 같은 이유로 인자다

BM25 인덱스는 저장소를 전량 훑어 짓는다. 여기서 지으면 **질의마다 다시 훑는다.**
`store`·`embedder` 와 나란히 두어 수명을 부르는 쪽이 정하게 한다 — backend 는 첫 질의에
지어 들고 있고, 평가 러너는 실행마다 새로 짓는다.

`None` 이면 dense 한 갈래로 돈다(`retrieval/hybrid.py` 가 그 동등성을 계약으로 박아 뒀다).
그래서 이 인자를 더해도 **기존 호출은 한 줄도 안 바뀐다.**

## `cache` 도 같은 모양이다

답변 캐시 역시 인자이고 `None` 이면 캐시 없이 돈다 — `bm25` 와 같은 이유이자 같은 스위치다.

**붙는 자리는 `retrieve()` 앞이다.** 이 docstring 이 한동안 *"`retrieve()` 와
`generate_answer()` 사이"* 라고 적어 뒀는데 틀렸다. 거기 두면 검색은 이미 돌아버려 아끼는
것이 LLM 호출 하나뿐이다. 앞에 두면 검색·생성이 통째로 빠진다.

미스일 때 질의 임베딩이 두 번 일어난다(캐시 조회 + `search_dense`). 그대로 두는 이유는
`_embed_all()` 이 `ResultCache` 를 거쳐 **같은 문자열이면 API 를 다시 안 타서**다. 그것
하나 때문에 `retrieve()`·`hybrid`·`search_dense` 셋의 시그니처를 넓히면, 벡터를 넘기는
경로와 안 넘기는 경로가 갈려 어느 쪽이 실제로 돌았는지 호출부마다 달라진다.

## 여기서 안 하는 것

Evaluator·Rewriter 재검색 루프 · reranker. 둘 다 `plan_log/검색-생성.md` 의 Phase 3~5 이고,
붙는 자리는 `retrieve()` 와 `generate_answer()` 사이다.
"""

from __future__ import annotations

from rag_pipeline.answer_cache import QdrantAnswerCache
from rag_pipeline.config import IngestConfig
from rag_pipeline.generation import generate_answer
from rag_pipeline.ingestion.embedding.base import Embedder
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.retrieval import Bm25Index, retrieve
from rag_pipeline.schemas.answer import Answer

__all__ = ["answer_question"]


# 질문 하나에 근거를 붙여 답한다. 검색 -> 생성을 합성하는 것이 하는 일의 전부다
def answer_question(
    query: str,
    *,
    store: VectorStore,
    embedder: Embedder,
    config: IngestConfig | None = None,
    top_k: int | None = None,
    bm25: Bm25Index | None = None,
    cache: QdrantAnswerCache | None = None,
) -> Answer:
    cfg = config or IngestConfig()
    limit = top_k if top_k is not None else cfg.generation.generation_top_k

    # 캐시가 먼저다. 히트하면 검색도 생성도 안 돈다 (모듈 docstring 참고)
    if cache is not None:
        cached = cache.lookup(query, embedder=embedder)
        if cached is not None:
            return cached

    chunks = retrieve(
        query,
        store=store,
        embedder=embedder,
        top_k=limit,
        bm25=bm25,
        config=cfg.retrieval,
    )
    text = generate_answer(query, chunks, cfg.generation)
    answer = Answer(query=query, text=text, sources=chunks)

    # 저장 실패는 답변을 막지 않는다 — put() 이 아무것도 안 던지는 것이 그 계약이다
    if cache is not None:
        cache.put(answer, embedder=embedder)
    return answer
