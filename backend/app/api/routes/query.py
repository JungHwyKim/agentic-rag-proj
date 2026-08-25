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

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import get_settings
from backend.app.core.templates import templates
from rag_pipeline.ingestion.embedding import get_embedder
from rag_pipeline.query import answer_question
from rag_pipeline.retrieval import build_bm25_index

router = APIRouter(tags=["query"])


# 앱 수명 동안 하나뿐인 embedder 를 얻는다. 없으면 이때 만든다 (모듈 docstring 참고)
def _embedder(request: Request):  # noqa: ANN202 - Embedder Protocol 은 런타임 타입이 아니다
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        config = request.app.state.ingest_config
        embedder = get_embedder(config.embedding)
        request.app.state.embedder = embedder
    return embedder


# 앱 수명 동안 하나뿐인 BM25 인덱스를 얻는다. 없으면 이때 짓는다 (모듈 docstring 참고).
# **예외를 안 던진다** — build_bm25_index() 가 실패를 빈 인덱스 + 경고로 바꿔 주므로,
# 색인 전이라도 질의는 dense 로 돈다
def _bm25(request: Request):  # noqa: ANN202 - Bm25Index 를 라우트 시그니처에 노출하지 않는다
    index = getattr(request.app.state, "bm25", None)
    if index is None:
        index = build_bm25_index(request.app.state.store)
        request.app.state.bm25 = index
    return index


# 질문 하나를 받아 답변 말풍선 조각을 돌려준다
@router.post("/query", response_class=HTMLResponse)
async def ask(request: Request, query: str = Form(...)) -> HTMLResponse:
    text = query.strip()
    if not text:
        return templates.TemplateResponse(
            request, "_answer.html", {"answer": None, "query": ""}
        )

    # 검색·생성은 동기·네트워크 바운드라 이벤트 루프 밖으로 뺀다 (업로드가 로더를 넘기는 것과 같다)
    answer = await run_in_threadpool(
        answer_question,
        text,
        store=request.app.state.store,
        embedder=_embedder(request),
        config=request.app.state.ingest_config,
        bm25=await run_in_threadpool(_bm25, request),
        # 임계값이 없으면 None 이라 캐시 없이 돈다. 라우트에 스위치를 두지 않는 이유는
        # bm25 와 같다 — 안 넘기는 것이 곧 끄는 것이다.
        # **getattr 인 것은 lifespan 을 안 거치고 app 을 세우는 호출부가 있어서**다 —
        # 그때 여기서 죽으면 캐시가 없다는 이유로 질의 전체가 죽는다
        cache=getattr(request.app.state, "answer_cache", None),
    )
    return templates.TemplateResponse(request, "_answer.html", {"answer": answer, "query": text})


# 사이드바의 색인된 문서 목록 조각
@router.get("/documents", response_class=HTMLResponse)
async def document_list(request: Request) -> HTMLResponse:
    store = request.app.state.store
    # list_documents() 는 Protocol 이 아니라 QdrantStore 에만 있다 — backend 가 구체 타입을
    # 아는 유일한 자리이고, 그 판단의 근거는 plan_log/backend.md 에 있다.
    # 없는 구현체가 끼면 목록만 빈다(화면은 산다)
    lister = getattr(store, "list_documents", None)
    documents = await run_in_threadpool(lister) if lister is not None else []
    originals_mb = await run_in_threadpool(_originals_megabytes)
    return templates.TemplateResponse(
        request,
        "_documents.html",
        {"documents": documents, "originals_mb": originals_mb},
    )


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
