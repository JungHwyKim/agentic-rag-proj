"""질의·문서 목록 엔드포인트. HTMX 가 부르므로 JSON 이 아니라 **HTML 조각**을 돌려준다.

## 왜 JSON 이 아닌가

화면이 서버 렌더라 응답이 곧 DOM 조각이다. JSON 을 돌려주고 브라우저에서 다시 조립하면
템플릿이 두 벌(Jinja2 와 JS 문자열)이 되고, 그때부터 소스 태그 모양이 두 곳에서 갈린다.

## store 는 왜 여기서 안 여나

`app.state` 의 싱글턴을 쓴다 — qdrant 로컬 파일 모드가 배타 잠금을 걸어 요청마다 열면
두 번째 질의가 죽는다 (`main.py:lifespan` · `rag_pipeline/query.py` docstring).

## embedder 는 왜 늦게 만드나

`get_embedder()` 는 키가 없으면 **그 자리에서 예외**다(embedding 의 실패 계약). startup 에서
만들면 키 없는 개발 환경에서 **서버가 아예 안 뜬다** — 화면도 문서 목록도 못 본다.
첫 질의까지 미루면 그때 503 과 한국어 메시지로 떨어진다.

## 답변 캐시는 왜 startup 에서 만드나

`get_answer_cache()` 는 `store` 를 **참조만 하고 클라이언트를 안 당긴다.** 그래서 embedder·
BM25 와 달리 게으름을 깨지 않는다. 임계값이 없으면 `None` 이 돌아와 캐시 없이 도는데,
그 판정은 설정 하나를 읽는 일이라 첫 질의까지 미룰 이유가 없다.

## 리랭커도 embedder 쪽 패턴이다

`get_reranker()` 는 저장소를 안 훑지만 **로컬 갈래가 cross-encoder 가중치를 올린다.**
startup 에서 만들면 그 시간만큼 서버가 안 뜨고, 모델을 못 받는 환경에서는 화면조차 못 본다.
그래서 첫 질의로 미룬다 — `embedder` 와 같은 축이고 `bm25` 와는 이유가 다르다(저쪽은
저장소의 게으름을 깨지 않으려는 것이다).

**색인 뒤 되돌릴 것이 없다.** 리랭커는 저장소 스냅샷을 안 들고 질의마다 후보를 받아 점수만
매기므로, 아래 BM25 무효화 계약이 여기에는 해당하지 않는다. 답변 캐시 쪽 분류다.

## 판정(Evaluator)과 재작성(Rewriter)은 질의마다 새로 만든다

embedder·BM25·리랭커는 `app.state` 싱글턴인데 이 둘은 아니다. **가르는 것은 가중치다** —
저 셋이 싱글턴인 이유는 KURE 2.27GB 적재 · cross-encoder 적재 · 저장소 전량 훑기이고,
`OpenAIEvaluator` 에는 그런 것이 **하나도 없다**(`_make_client()` 를 호출 시점에 부르고
캐시는 디스크다). 이유가 없는데 모양만 따라가면 딸려 오는 것이 있다 — `warnings` 와
`usage` 가 **인스턴스에 누적**돼서, `EVALUATOR_MODEL` 은 찼는데 `OPENAI_API_KEY` 가 없는
조합이면 질의마다 경고 한 줄이 서버 수명 내내 쌓인다. 읽는 사람도 없고 에러도 안 난다.

질의마다 만들면 그 누적이 원리적으로 없고, `warnings` 가 **그 질의의 것**이라는 뜻이 산다.

**`QueryRewriter` 가 2026-08-27 에 같은 자리로 왔다.** 근거가 글자 그대로 같다 — 올릴
가중치가 없고(클라이언트는 호출 시점에 만든다) `warnings`·`usage` 가 인스턴스에 누적된다.

**한때 축이 하나 더 있어 보였다** — 재작성기는 `ResultCache` 를 들고 있어 *싱글턴이어야
싸지 않나* 로 읽혔다. 아니다: 그 객체는 **경로 + 불리언뿐**이고 상태를 안 들고 디스크를
본다. 그래서 질의마다 만든 인스턴스가 **같은 캐시 파일을 읽는다** — 축이 아니라 착시였다.

## BM25 인덱스도 같은 패턴이다

`build_bm25_index()` 는 저장소를 전량 훑는다. startup 에서 지으면 **색인이 아직 없는 상태**
에서 저장소를 여는 셈이라 `lifespan` 이 일부러 지킨 게으름이 깨진다. 첫 질의에 지어
`app.state` 에 두면 그 뒤로는 공짜다.

**딸린 감수 사항**: 서버가 사는 동안 색인이 바뀌면 이 인덱스가 낡는다. **이제 실제로 바뀐다** —
`POST /documents/index` 가 같은 저장소에 쓴다. 그래서 그 라우트가 색인 성공 뒤
`app.state.bm25 = None` 으로 되돌리고 다음 질의가 다시 짓는다 (`routes/documents.py`).
답변 캐시는 **대우가 다르다** — 객체는 그대로 두고 `clear()` 로 내용만 비운다. 그쪽은
스냅샷이 아니라 `store` 참조만 들고 질의마다 새로 읽으므로 갈아끼울 이유가 없고(경고만 잃는다),
낡은 항목은 조회 시점에 근거 청크를 되짚어 스스로 걸러낸다. **그런데도 비우는 것이 따로
필요한 이유**는 그 대조가 *새 문서가 들어온 경우*를 원리적으로 못 잡아서다 — 사라진 것이
없어 옛 답의 근거는 여전히 멀쩡하다 (`rag_pipeline/answer_cache/`).
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from backend.app.api.routes.documents import original_exists
from backend.app.core.config import get_settings
from backend.app.core.query_log import QUERY_SOURCE_HEADER, append_query_log
from backend.app.core.singletons import BUILD_LOCK, embedder_for
from backend.app.core.templates import templates
from rag_pipeline.generation.evaluate import get_evaluator
from rag_pipeline.query import answer_question
from rag_pipeline.retrieval import build_bm25_index, get_reranker, get_rewriter
from rag_pipeline.schemas.retrieval import RetrievedChunk

router = APIRouter(tags=["query"])

# **embedder getter 와 그 락은 `core/singletons.py` 로 옮겼다** — 적재 라우트도 같은
# 인스턴스를 써야 하는데 `routes/query.py` 가 이미 `routes/documents.py` 를 import 해서,
# 반대 방향 import 가 순환이 되기 때문이다. `_rerank_ready` 는 표시지 락이 아니다.


# 앱 수명 동안 하나뿐인 BM25 인덱스를 얻는다. 없으면 이때 짓는다 (모듈 docstring 참고).
# **예외를 안 던진다** — build_bm25_index() 가 실패를 빈 인덱스 + 경고로 바꿔 주므로,
# 색인 전이라도 질의는 dense 로 돈다
def _bm25(app: FastAPI):  # noqa: ANN202 - Bm25Index 를 라우트 시그니처에 노출하지 않는다
    with BUILD_LOCK:
        index = getattr(app.state, "bm25", None)
        if index is None:
            index = build_bm25_index(app.state.store)
            app.state.bm25 = index
        return index


# 앱 수명 동안 하나뿐인 리랭커를 얻는다. 없으면 이때 만든다 (모듈 docstring 참고).
#
# **`None` 이 정상값이다** — `RERANK_MODEL` 이 비어 있으면 `get_reranker()` 가 None 을
# 돌려주고 그것이 곧 꺼진 상태다. 그래서 만들었는지를 `state` 에 없는 것으로 판정하면
# **꺼져 있을 때 질의마다 다시 만들게 된다.** 지금은 만드는 비용이 없지만 로컬 갈래에서는
# 그 자리가 곧 모델 적재라, 만들었다는 사실 자체를 `_rerank_ready` 로 따로 표시한다
def _reranker(app: FastAPI):  # noqa: ANN202 - Reranker 를 라우트 시그니처에 노출하지 않는다
    with BUILD_LOCK:
        state = app.state
        if not getattr(state, "_rerank_ready", False):
            state.reranker = get_reranker(state.ingest_config.rerank)
            state._rerank_ready = True
        return state.reranker


# 워밍업이 쓰는 버리는 입력. **호출마다 달라야 한다.**
#
# 임베딩 캐시도 리랭크 캐시도 **텍스트로 키를 만든다.** 고정 문자열을 쓰면 워밍업이
# **자기 입력을 캐시에 넣고**, 다음 기동부터는 그 캐시에 걸려 **모델을 안 올린다** —
# 그런데 예외가 없어서 `warmup_errors` 는 빈 채이고 첫 질의만 다시 21.8초를 문다.
# 실제로 그렇게 만들었다가 잡았다(`task_log/2026-08-26-기동-워밍업.md` 4절).
#
# 대가는 기동당 캐시 항목 2건(질문 벡터 1 · 점수 1)이고, 서버 재시작 수만큼만 쌓인다.
#
# **`time.time_ns()` 를 쓰면 안 된다** — Windows 의 시계 분해능이 거칠어 연달아 부르면
# **같은 값이 나온다**(실측: 한 테스트 안의 두 호출이 동일). 유일성이 목적이므로 유일성을
# 보장하는 것을 쓴다
def _warmup_text() -> str:
    return f"워밍업 {uuid.uuid4().hex}"


# 리랭커를 데울 후보. **둘이어야 한다** — `rerank()` 가 후보 1개 이하면 모델을 안 올리고
# 받은 것을 그대로 돌려준다(`local_ce.py`). 하나만 주면 워밍업이 조용히 아무것도 안 한다.
# **후보 텍스트도 질의와 함께 캐시 키에 들어가므로** 같이 달라진다
def _warmup_candidates(text: str) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id=f"warmup:{i}", document_id="warmup", text=f"{text} {i}")
        for i in range(2)
    ]


# 기동 직후 배경 스레드에서 모델 가중치를 미리 올린다. `main.py` 의 lifespan 이 부른다.
#
# **객체를 만드는 것으로는 아무것도 안 데워진다** — `get_embedder()`·`get_reranker()` 자체는
# 0.002초이고 가중치는 **첫 사용**에 올라온다. 그래서 버리는 인코딩·채점을 실제로 돌린다.
# 회수되는 것은 첫 질의 21.8초 중 약 19초다 (verify_log/2026-08-26-첫질의-대표값.md).
#
# **저장소는 안 건드린다.** `embed_query()` 도 `rerank()` 도 qdrant 를 안 열어서, lifespan 이
# 일부러 남겨 둔 게으름(*"색인이 아직 없으면 서버가 아예 안 뜬다"*)이 그대로 유지된다.
# BM25 도 일부러 뺐다 — 414청크에서 0.09초라 회수할 것이 없다.
#
# **실패해도 안 던진다.** 워밍업이 서버를 죽이면 게으른 적재보다 나빠진다. 못 데우면 그냥
# 안 데워진 채로 두고 첫 질의가 지금처럼 물면 된다 — 다만 **조용히 넘기지는 않는다**
def warm_up(app: FastAPI) -> None:
    errors: list[str] = []
    text = _warmup_text()
    try:
        embedder_for(app).embed_query(text)
    except Exception as exc:  # noqa: BLE001 - 워밍업 실패는 기능이 아니다. 게으른 적재로 떨어진다
        errors.append(f"embedder: {exc}")
    try:
        reranker = _reranker(app)
        # None 이 정상값이다 — `RERANK_MODEL` 이 비면 리랭커가 꺼진 것이라 데울 것이 없다.
        #
        # **`needs_warmup` 을 보는 것이 요점이다.** 네트워크 갈래(openai·cohere)는 올릴
        # 가중치가 없어 여기서 얻는 것이 0인데, 워밍업 입력이 `uuid4` 라 **늘 캐시 미스**이므로
        # 부르면 기동마다 유료 호출이 하나씩 나간다(`--reload` 는 코드 고칠 때마다 재기동한다).
        # **provider 이름으로 분기하지 않는다** — 구현체가 늘 때마다 여기를 따라 고쳐야 하고
        # 빠뜨려도 아무 에러가 안 난다. 판단은 구현체가 갖는다 (`reranker/base.py`)
        if reranker is not None and reranker.needs_warmup:
            reranker.rerank(text, _warmup_candidates(text))
    except Exception as exc:  # noqa: BLE001 - 위와 같다
        errors.append(f"reranker: {exc}")
    app.state.warmup_errors = errors


# 질문 하나를 받아 답변 말풍선 조각을 돌려준다
@router.post("/query", response_class=HTMLResponse)
async def ask(request: Request, query: str = Form(...)) -> HTMLResponse:
    text = query.strip()
    if not text:
        return templates.TemplateResponse(
            request, "_answer.html", {"answer": None, "echo": ""}
        )

    # 검색·생성은 동기·네트워크 바운드라 이벤트 루프 밖으로 뺀다 (업로드가 로더를 넘기는 것과 같다)
    started = time.perf_counter()
    answer = await run_in_threadpool(
        answer_question,
        text,
        store=request.app.state.store,
        # **임베더도 threadpool 로 넘긴다** — 리랭커와 같은 이유인데 이쪽이 더 크다.
        # 워밍업이 꺼져 있으면 첫 인코딩이 11.8~14.7초이고(verify_log/2026-08-26-첫질의-대표값.md)
        # 그동안 이벤트 루프에서 하면 **서버 전체가 그만큼 멈춘다**
        embedder=await run_in_threadpool(embedder_for, request.app),
        config=request.app.state.ingest_config,
        bm25=await run_in_threadpool(_bm25, request.app),
        # 모델 이름이 없으면 None 이라 RRF 순서로 돈다. 라우트에 스위치를 두지 않는 이유는
        # bm25·cache 와 같다 — 안 넘기는 것이 곧 끄는 것이다.
        # **threadpool 로 넘기는 것은 로컬 갈래 때문이다** — cross-encoder 적재가 초 단위라
        # 이벤트 루프에서 하면 첫 질의 동안 서버 전체가 멈춘다
        reranker=await run_in_threadpool(_reranker, request.app),
        # 임계값이 없으면 None 이라 캐시 없이 돈다. 라우트에 스위치를 두지 않는 이유는
        # bm25 와 같다 — 안 넘기는 것이 곧 끄는 것이다.
        # **getattr 인 것은 lifespan 을 안 거치고 app 을 세우는 호출부가 있어서**다 —
        # 그때 여기서 죽으면 캐시가 없다는 이유로 질의 전체가 죽는다
        cache=getattr(request.app.state, "answer_cache", None),
        # 모델 이름이 없으면 None 이라 판정 없이 돈다. 라우트에 스위치를 두지 않는 이유는
        # bm25·cache·reranker 와 같다 — 안 넘기는 것이 곧 끄는 것이다.
        # **질의마다 새로 만든다** — 저 셋과 달리 가중치가 없어 만드는 값이 사실상 0 이고,
        # 싱글턴으로 두면 `warnings` 가 서버 수명 내내 누적된다 (모듈 docstring 참고).
        # `config.evaluator` 는 lazy 프로퍼티라 첫 호출 뒤에는 `.env` 를 다시 안 읽는다
        evaluator=get_evaluator(request.app.state.ingest_config.evaluator),
        # 모델 이름이 없으면 None 이라 재작성 없이 돈다. 라우트에 스위치를 두지 않는 이유는
        # bm25·cache·reranker·evaluator 와 같다 — 안 넘기는 것이 곧 끄는 것이다.
        # **evaluator 와 같이 질의마다 새로 만든다** — 올릴 가중치가 없어 만드는 값이 사실상
        # 0 이고(클라이언트는 호출 시점에 만든다), 싱글턴으로 두면 `warnings`·`usage` 가 서버
        # 수명 내내 누적된다. **딸린 `ResultCache` 는 경로 + 불리언뿐이라 축이 아니다** —
        # 상태를 안 들고 디스크를 보므로 질의마다 만든 인스턴스가 같은 캐시 파일을 읽는다.
        # **threadpool 로 안 감싸는 것도 evaluator 와 같다** — 적재가 없어 감쌀 것이 없다
        rewriter=get_rewriter(request.app.state.ingest_config.rewrite),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    # **성공한 질의만 남는다** — 위에서 예외가 나면 여기 못 온다. 그것이 분모의 뜻을
    # "답이 나온 질의" 하나로 고정한다 (`core/query_log.py`). 파일 쓰기라 루프 밖으로 뺀다
    settings = get_settings()
    if settings.query_log_enabled:
        await run_in_threadpool(
            append_query_log,
            settings.query_log_path,
            text,
            answer,
            elapsed_ms=elapsed_ms,
            # **다듬는 것은 여기가 아니라 `core/query_log.py` 다** — 헤더를 그대로 넘기고
            # 정규화(빈 값 → `user` · 길이 자르기)를 한 곳에 둔다. 화면에서 온 질의는
            # 이 헤더가 없어 실사용으로 세어진다
            source=request.headers.get(QUERY_SOURCE_HEADER),
        )

    # **`query` 가 아니라 `echo` 로 넘긴다.** 질문 말풍선은 이제 화면이 그린다 — 보내는 즉시
    # 올라가야 하는데 이 응답은 3~6초 뒤에 오기 때문이다 (`chat.html` 의 `#composer-js`).
    #
    # **그런데 서버가 질문을 아예 안 실으면 진단 하나가 죽는다.** 한국어 질의를 셸에서 보내면
    # cp949 로 깨진 채 도착하는데 에러가 안 나고 답도 그럴듯해서, 가르는 방법이 *"응답이
    # 되돌려준 질문 문구를 보낸 문자열과 대조하는 것"* 하나다 (CLAUDE.md 함정 절). 그 대조가
    # 성립한 이유는 화면이 **서버가 준 글자**를 말풍선에 실었기 때문이다. 화면이 자기 입력값으로
    # 그리기 시작하면 **깨진 질의도 화면엔 멀쩡해 보여** 진단이 사라지는 것이 아니라 거짓말을 한다.
    #
    # 그래서 숨은 한 줄로 계속 실어 보내고 화면이 자기가 그린 글자와 대조한다 — 눈으로 하던
    # 것이 자동 검사가 된다 (`_answer.html` 의 `.echo`)
    return templates.TemplateResponse(request, "_answer.html", {"answer": answer, "echo": text})


# 서버 상태등 조각. 사이드바 맨 아래에서 10초마다 다시 불린다 (`chat.html`).
#
# **`app.state` 를 읽기만 한다 — `embedder_for(app)` 를 부르면 안 된다.** 그러면 등이 상태를
# 재는 대신 **만들고**, 키가 없는 환경에서는 상태 조회가 그 자리에서 예외로 죽는다
# (`get_embedder()` 의 실패 계약). 등이 서버를 무겁게 만들거나 죽이면 등이 아니라 부하다.
#
# **색이 셋인 것이 요점이다.** `warmup_on_startup` 기본값이 꺼짐이라 (`core/config.py`)
# 첫 질의 전에는 `embedder is None` 이 **정상**이다. 초록/빨강 둘로 접으면 평소가 빨강이 되어
# 등이 경고를 잃는다. 셋째 색(회색 = 서버가 응답을 안 한다)은 **여기서 못 만든다** — 응답이
# 없는 상태라 화면이 `htmx:sendError` 에서 칠한다.
#
# `warmup_errors` 는 워밍업이 **끝났을 때만** 생기는 속성이라(`warm_up()` 마지막 줄) `getattr`
# 의 기본값이 *"안 돌았거나 도는 중"* 을 뜻한다 — 빈 리스트(성공)와 갈라야 한다.
#
# **JSON 이 아니라 HTML 조각인 것**은 화면 규약이다. `GET /health` 와 나란한 것이 아니라 다른
# 물건이다 — 저쪽은 기계용 liveness 이고 상수를 돌려준다. 같은 상태를 두 곳에서 파생하면
# 두 번째 진실이 생긴다
@router.get("/status", response_class=HTMLResponse)
async def server_status(request: Request) -> HTMLResponse:
    state = request.app.state
    warmup_errors = getattr(state, "warmup_errors", None)
    return templates.TemplateResponse(
        request,
        "_status.html",
        {
            "state": "ready" if getattr(state, "embedder", None) is not None else "idle",
            # 워밍업이 실패한 채로도 임베더가 서 있을 수 있다(리랭커만 실패한 경우).
            # 그때도 사유는 보여준다 — 사유가 있는데 안 보이면 등이 절반만 말한다
            "reason": " / ".join(warmup_errors) if warmup_errors else "",
        },
    )


# 사이드바의 색인된 문서 목록 조각
@router.get("/documents", response_class=HTMLResponse)
async def document_list(request: Request) -> HTMLResponse:
    store = request.app.state.store
    # list_documents() 는 Protocol 이 아니라 QdrantStore 에만 있다 — backend 가 구체 타입을
    # 아는 유일한 자리이고, 그 판단의 근거는 plan_log/backend.md 에 있다.
    # 없는 구현체가 끼면 목록만 빈다(화면은 산다)
    lister = getattr(store, "list_documents", None)
    documents = await run_in_threadpool(lister) if lister is not None else []
    documents = await run_in_threadpool(_mark_shadow_gaps, request, documents)
    documents = await run_in_threadpool(_mark_missing_originals, documents)
    originals_mb = await run_in_threadpool(_originals_megabytes)
    return templates.TemplateResponse(
        request,
        "_documents.html",
        {"documents": documents, "originals_mb": originals_mb},
    )


# 그림자 컬렉션에 없는 문서에 `shadow_missing` 을 달아 준다.
#
# **그림자 색인은 실패해도 색인을 안 막는다**(pipeline.py `_index_shadow`). 그 대가로
# 구멍이 조용히 생길 수 있어, **무엇이 빠졌는지 보이는 자리가 반드시 하나 있어야 한다.**
# 여기가 그 자리다 — 목록은 이미 렌더되고 있고 두 번째 진실을 만들지 않는다.
#
# **채우는 방법은 그 문서를 다시 올리는 것이다.** 색인이 delete -> upsert 라 멱등이고
# `document_id` 가 파일 이름에서 파생되므로 재업로드가 두 컬렉션을 함께 채운다.
# 표시가 있어야 무엇을 다시 올릴지 안다 — 그것이 이 함수의 존재 이유다.
#
# **스크롤이 한 번 더 는다.** 지금 규모(494 point · 6문서)에서 무시할 만하고,
# 느려지면 그것이 `bm25` 처럼 스냅샷으로 옮길 신호다. 그림자가 꺼져 있거나 목록을 못 내는
# 저장소면 아무것도 안 달고 그대로 돌려준다 — 없는 축을 화면에 만들지 않는다
def _mark_shadow_gaps(request: Request, documents: list[dict]) -> list[dict]:
    shadow_store = getattr(request.app.state, "shadow_store", None)
    if shadow_store is None or not documents:
        return documents

    lister = getattr(shadow_store, "list_documents", None)
    if lister is None:
        return documents

    try:
        shadowed = {row.get("document_id") for row in lister()}
    except Exception:  # noqa: BLE001 - 목록은 보여야 한다. 그림자 조회 실패로 화면을 죽이지 않는다
        return documents

    return [{**row, "shadow_missing": row.get("document_id") not in shadowed} for row in documents]


# 원본이 디스크에 없는 문서에 `original_missing` 을 달아 준다.
#
# **재색인 버튼을 그릴지 정하는 값이다.** 7회차 이전에 올린 문서는 원본이 없어 재색인이
# 404 인데, 버튼을 늘 그려두면 **눌러야 그것을 안다** — 6회차에 사용자가 물고 온 것이 정확히
# 그 종류다. `_mark_shadow_gaps` 와 같은 자리·같은 모양이라 새 패턴이 아니다.
#
# 저장 이름 규칙을 여기 옮겨 적지 않고 `documents.original_exists()` 를 부른다. 복사하면
# 규칙이 두 벌이 되어 한쪽만 고쳐진다.
#
# 행마다 `is_file()` 을 부른다. 폴더를 한 번 훑어 이름 집합을 만드는 최적화는 안 했다 —
# 문서가 10개 안팎이고, 그러면 `_originals_megabytes()` 와 폴더 상태를 나눠 갖게 된다
def _mark_missing_originals(documents: list[dict]) -> list[dict]:
    if not documents:
        return documents

    settings = get_settings()
    return [
        {
            **row,
            "original_missing": not (
                row.get("document_id") and original_exists(row["document_id"], settings)
            ),
        }
        for row in documents
    ]


# 원본 보관함이 지금 몇 MB 인지. 사이드바 맨 아래 줄에 함께 실린다.
#
# **상한도 자동 정리도 안 만든 대신 이 숫자를 보여준다** — 값을 지금 정하면 관측 없는
# 숫자가 되고, 자동 삭제는 사용자가 모르는 사이 원문만 사라지는 상태를 만든다.
# 대신 얼마나 쓰는지 보이고 삭제 버튼을 손에 쥐어 준다 (plan_log/backend.md 7회차).
#
# 폴더가 없으면 0 이다 — 아직 아무것도 안 올린 상태가 정상이라 예외로 만들지 않는다
def _originals_megabytes() -> float:
    directory = get_settings().originals_dir
    if not directory.is_dir():
        return 0.0
    total = sum(item.stat().st_size for item in directory.iterdir() if item.is_file())
    return round(total / (1024 * 1024), 1)
