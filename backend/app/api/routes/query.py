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
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from backend.app.core.templates import templates
from rag_pipeline.ingestion.embedding import get_embedder
from rag_pipeline.query import answer_question

router = APIRouter(tags=["query"])


# 앱 수명 동안 하나뿐인 embedder 를 얻는다. 없으면 이때 만든다 (모듈 docstring 참고)
def _embedder(request: Request):  # noqa: ANN202 - Embedder Protocol 은 런타임 타입이 아니다
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        config = request.app.state.ingest_config
        embedder = get_embedder(config.embedding)
        request.app.state.embedder = embedder
    return embedder


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
    )
    return templates.TemplateResponse(request, "_answer.html", {"answer": answer, "query": text})


# 사이드바의 색인된 문서 목록 조각
@router.get("/documents", response_class=HTMLResponse)
async def document_list(request: Request) -> HTMLResponse:
    store = request.app.state.store
    # list_documents() 는 Protocol 이 아니라 QdrantStore 에만 있다 — backend 가 구체 타입을
    # 아는 유일한 자리이고, 그 판단의 근거는 plan_log/frontend.md 에 있다.
    # 없는 구현체가 끼면 목록만 빈다(화면은 산다)
    lister = getattr(store, "list_documents", None)
    documents = await run_in_threadpool(lister) if lister is not None else []
    return templates.TemplateResponse(request, "_documents.html", {"documents": documents})
