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

## `reranker` 도 같은 모양이다

후보 풀을 다시 정렬하는 구현체 역시 인자이고 `None` 이면 RRF 순서로 돈다. **다만 붙는 자리가
`bm25` 와 다르다** — 이것은 `retrieve()` **안**에서 RRF 뒤·절단 앞에 선다. 여기서 하는 일은
수명을 정해 넘기는 것뿐이다(`retrieval/hybrid.py` 가 왜 안인지를 적어 뒀다).

수명을 부르는 쪽이 정하는 이유는 `embedder` 와 같다 — 로컬 갈래는 모델을 올리므로 질의마다
만들면 그 비용을 매번 낸다. backend 는 첫 질의에 만들어 들고, 평가 러너는 실행마다 만든다.

## `rewriter` 도 같은 모양이고 자리는 `reranker` 와 **같은 함수 안**이다

질의를 다시 쓰는 구현체 역시 인자이고 `None` 이면 재작성 없이 돈다. `retrieve()` 안에서
1차 검색 **뒤**·합류 **앞**에 서므로, 여기가 하는 일은 **넘겨주는 것뿐**이다.

**여기서 질의를 미리 다시 써서 `retrieve()` 에 넘기면 안 된다.** `evaluation/run_eval.py` 가
`retrieve()` 를 직접 부르므로, 재작성이 이 함수 안에 있으면 **평가가 그 경로를 안 밟아
재작성을 영원히 못 잰다**(`plan_log/검색-생성.md` 7회차 기각 항목). `reranker` 를 반환 뒤가
아니라 `retrieve()` 안에 둔 것과 같은 종류의 판단인데 이유는 다르다 — 저쪽은 **절단 위치**,
이쪽은 **누가 그 경로를 밟느냐**다.

**답변 캐시가 히트하면 재작성도 안 돈다.** 캐시 검사가 `retrieve()` 앞이라 저절로 그렇게
되고, 그것이 곧 질의당 LLM 호출 하나를 아끼는 것이다.

**다시 쓴 질의는 `retrieve()` 가 아니라 재작성기에서 읽는다.** 반환이 청크 목록이라 문자열이
낄 자리가 없고, 넓히면 `run_eval` 을 포함한 모든 호출부가 함께 바뀐다(`retrieval/rewrite.py`
docstring). 읽어서 `Answer.rewrite` 에 싣는 것까지가 여기 몫이고 — **그 필드는 관측이지
스위치가 아니다.** 판정이 답변을 안 바꾸는 것과 같은 자리인데, 이쪽은 반대로 **답변을 이미
바꾼 뒤에** 그 사실을 적는 것이다. 화면과 `query_log` 가 둘 다 이 하나를 읽는다.

## `cache` 도 같은 모양이다

답변 캐시 역시 인자이고 `None` 이면 캐시 없이 돈다 — `bm25` 와 같은 이유이자 같은 스위치다.

**붙는 자리는 `retrieve()` 앞이다.** 이 docstring 이 한동안 *"`retrieve()` 와
`generate_answer()` 사이"* 라고 적어 뒀는데 틀렸다. 거기 두면 검색은 이미 돌아버려 아끼는
것이 LLM 호출 하나뿐이다. 앞에 두면 검색·생성이 통째로 빠진다.

미스일 때 질의 임베딩이 두 번 일어난다(캐시 조회 + `search_dense`). 그대로 두는 이유는
`_embed_all()` 이 `ResultCache` 를 거쳐 **같은 문자열이면 API 를 다시 안 타서**다. 그것
하나 때문에 `retrieve()`·`hybrid`·`search_dense` 셋의 시그니처를 넓히면, 벡터를 넘기는
경로와 안 넘기는 경로가 갈려 어느 쪽이 실제로 돌았는지 호출부마다 달라진다.

## `evaluator` 도 같은 모양이다 — 다만 **판정이 답변을 안 바꾼다**

근거가 충분한지 판정하는 구현체 역시 인자이고 `None` 이면 판정 없이 돈다. 자리는 `retrieve()`
와 `generate_answer()` **사이**다 — `bm25`·`reranker` 처럼 검색 안이 아니라, 검색이 끝난 것을
보고 판정하기 때문이다.

**지금은 판정을 `Answer.verdict` 에 싣기만 한다.** *불충분* 이어도 생성을 막지 않고 프롬프트도
안 바꾼다. 재시도할 Rewriter 가 없기도 하고(`plan_log/검색-생성.md` 7회차가 물렸다), 생성을
바꾸면 *판정이 맞았나* 와 *답이 좋아졌나* 가 한 실행에 섞이는데 **답변 품질을 재는 자[尺]가
아직 없다.** 판정을 무엇으로 재는지는 `evaluation/verdicts.py` 다.

**실패는 예외가 아니라 제3의 상태다** — `verdict="failed"` 가 그대로 실려 나가고 사유는
`evaluator.warnings` 에 쌓인다. 여기서 *불충분* 으로 바꿔 적으면 부정 예시 질의에서 죽은
Evaluator 가 만점을 받는다(`generation/evaluate.py`).

## 여기서 안 하는 것

Rewriter 재검색 루프. `plan_log/검색-생성.md` 의 Phase 5 이고 7회차에서 물렸다 — 되살릴
조건도 거기 있다. **리랭커는 여기 있었다가 빠졌다** — 그 자리가 아니라 `retrieve()` 안이기
때문이다(위 "`reranker` 도 같은 모양이다").
"""

from __future__ import annotations

from rag_pipeline.answer_cache import QdrantAnswerCache, pipeline_fingerprint
from rag_pipeline.config import IngestConfig
from rag_pipeline.generation import OpenAIEvaluator, generate_answer
from rag_pipeline.ingestion.embedding.base import Embedder
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.retrieval import Bm25Index, QueryRewriter, Reranker, retrieve, uses_bm25
from rag_pipeline.schemas.answer import Answer, RewriteTrace

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
    reranker: Reranker | None = None,
    rewriter: QueryRewriter | None = None,
    cache: QdrantAnswerCache | None = None,
    evaluator: OpenAIEvaluator | None = None,
) -> Answer:
    cfg = config or IngestConfig()
    limit = top_k if top_k is not None else cfg.generation.generation_top_k

    # 이 답을 무엇이 만드나. **여기가 유일하게 셋을 다 아는 자리다** — 설정, 실제로 넘어온
    # bm25·reranker·rewriter, 그리고 유효 top_k. 설정만 보면 틀린다: 모델 이름이 차 있어도
    # 부르는 쪽이 객체를 안 넘기면 그 단계는 안 돈다(`evaluation/run_eval.py` 가 그렇게 부른다).
    # 저장과 조회에 **같은 값**을 넘겨야 하므로 한 번만 만든다 (answer_cache/fingerprint.py)
    fingerprint = pipeline_fingerprint(
        cfg,
        use_bm25=uses_bm25(bm25),
        reranker_on=reranker is not None,
        rewriter_on=rewriter is not None,
        top_k=limit,
    )

    # 캐시가 먼저다. 히트하면 검색도 생성도 안 돈다 (모듈 docstring 참고)
    if cache is not None:
        cached = cache.lookup(query, embedder=embedder, fingerprint=fingerprint)
        if cached is not None:
            return cached

    chunks = retrieve(
        query,
        store=store,
        embedder=embedder,
        top_k=limit,
        bm25=bm25,
        reranker=reranker,
        # 재작성도 `retrieve()` **안**이다 — 여기서 질의를 미리 다시 써서 넘기면 평가 러너가
        # `retrieve()` 를 직접 부르므로 그 경로를 안 밟는다 (모듈 docstring 참고)
        rewriter=rewriter,
        config=cfg.retrieval,
    )
    # 판정은 생성 앞이지만 **생성을 막지 않는다** (모듈 docstring 참고).
    # 실패도 그대로 실린다 — 여기서 판정 하나로 접으면 러너가 실패를 따로 못 센다
    verdict = evaluator.evaluate(query, chunks) if evaluator is not None else None

    # 무엇으로 검색했나. **`retrieve()` 뒤에 읽어야 값이 있다** — 재작성은 그 안에서 돈다.
    # 폴백으로 내려갔으면 `last_rewritten` 이 None 이고, 그때는 원 질의로만 검색한 것이라
    # 흔적도 None 이 맞다 (모듈 docstring 참고)
    rewrite = (
        RewriteTrace(query=rewriter.last_rewritten, mode=rewriter.mode)
        if rewriter is not None and rewriter.last_rewritten
        else None
    )

    text = generate_answer(query, chunks, cfg.generation)
    answer = Answer(query=query, text=text, sources=chunks, verdict=verdict, rewrite=rewrite)

    # 저장 실패는 답변을 막지 않는다 — put() 이 아무것도 안 던지는 것이 그 계약이다
    if cache is not None:
        cache.put(answer, embedder=embedder, fingerprint=fingerprint)
    return answer
