"""FastAPI 앱 진입점. 실행: uvicorn backend.app.main:app --reload"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.api.routes import documents, pages, query
from backend.app.core.config import get_settings
from backend.app.core.templates import templates
from rag_pipeline.answer_cache import get_answer_cache
from rag_pipeline.config import IngestConfig
from rag_pipeline.exceptions import (
    CorruptedFileError,
    DecompressionLimitError,
    EmbeddingError,
    FileTooLargeError,
    GenerationError,
    IndexingError,
    RagPipelineError,
    UnsupportedFormatError,
)
from rag_pipeline.ingestion.indexing import get_store

# 파이프라인 예외를 HTTP 상태코드로 매핑한다.
# 사용자가 무엇을 해야 하는지 알 수 있도록 user_message 를 그대로 내보낸다
_STATUS_BY_EXCEPTION: list[tuple[type[RagPipelineError], int]] = [
    (UnsupportedFormatError, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
    (FileTooLargeError, status.HTTP_413_CONTENT_TOO_LARGE),
    (DecompressionLimitError, status.HTTP_413_CONTENT_TOO_LARGE),
    (CorruptedFileError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    # 이 셋은 /query 가 생기며 **처음으로 살아났다.** 질의 경로가 임베딩(질의 벡터)과
    # 색인(검색)과 생성을 차례로 밟는다 — 색인된 문서가 없으면 IndexingError 가 여기로 온다.
    # 셋 다 503 인 것은 "사용자 입력이 틀린 게 아니라 지금 서비스가 못 한다"가 공통이어서다
    (EmbeddingError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (IndexingError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (GenerationError, status.HTTP_503_SERVICE_UNAVAILABLE),
]


# 예외 타입에 대응하는 HTTP 상태코드를 찾는다. 못 찾으면 500
def _status_for(exc: RagPipelineError) -> int:
    for exc_type, code in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR


# 이 요청이 화면(htmx)에서 왔는가. htmx 가 모든 요청에 붙이는 표준 헤더다.
#
# **가르는 축이 헤더인 이유**: 라우트마다 try/except 로 갈랐다면 415 를 던지는
# `_reject_unsupported_extension()` 이 try **이전**이라 거기까지 감싸야 하고, 상태코드를
# 알려면 아래 `_status_for()` 를 라우트가 끌어써 매핑의 두 번째 진실이 생긴다.
# 헤더면 아래 두 핸들러가 같은 규칙 한 줄을 나눠 쓴다 (plan_log/backend.md 5회차)
def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") is not None


# 실패 한 건을 화면용 HTML 조각으로 그린다. **상태코드는 그대로 싣는다** —
# 200 으로 내리면 htmx 가 그냥 swap 해서 편하지만, 그것이 frontend.md 1회차가 스트리밍을
# 기각한 바로 그 자리다(200 을 먼저 보내면 _STATUS_BY_EXCEPTION 이 죽는다).
# 대신 chat.html 의 htmx:beforeSwap 한 줄이 4xx/5xx 도 그리게 한다
def _error_fragment(request: Request, message: str, status_code: int) -> Response:
    return templates.TemplateResponse(
        request, "_error.html", {"message": message}, status_code=status_code
    )


STATIC_DIR = Path(__file__).parent / "static"


# 앱 수명 동안 벡터 저장소를 **하나만** 연다.
#
# **요청마다 열면 두 번째 질의가 죽는다** — qdrant 로컬 파일 모드가 저장 디렉터리에 배타
# 잠금을 걸어 두 번째 클라이언트가 RuntimeError 를 낸다. 그래서 여기서 한 번 열고
# answer_question() 에 넘긴다 (rag_pipeline/query.py docstring).
#
# **열기를 startup 에서 시도하지 않는다.** QdrantStore 는 처음 쓸 때 클라이언트를 만들도록
# 늦춰져 있고, 그 게으름을 여기서 깨면 색인이 아직 없는 상태에서 서버가 아예 안 뜬다.
#
# 이 store 는 색인 라우트에도 그대로 넘어간다. index_document() 는 store 를 안 받으면
# get_store() 로 자기 클라이언트를 열고 finally 에서 닫는데, 업로드 라우트가 그 경로를 타면
# 같은 프로세스 안에서 이 싱글턴과 충돌한다. 그래서 routes/documents.py 가 store= 로 넘긴다
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = IngestConfig()
    app.state.ingest_config = config
    app.state.store = get_store(config.indexing, config.embedding)
    app.state.embedder = None  # 첫 질의에 만든다 — 키가 없어도 서버는 떠야 한다
    # BM25 도 첫 질의에 짓는다. 여기서 지으면 색인이 아직 없을 때 저장소를 헛되이 열고,
    # 무엇보다 store 의 게으름을 깨는 것이 위 주석이 피하려는 바로 그 일이다
    app.state.bm25 = None
    # 답변 캐시. **임계값이 없으면 None 이고 그것이 곧 꺼진 상태다** (answer_cache/__init__.py).
    # 여기서 만들어도 store 를 참조만 하므로 위 게으름이 안 깨진다 — 클라이언트는 첫 질의에
    # 열린다. 닫을 것도 여전히 store 하나뿐이라 finally 가 안 늘어난다
    app.state.answer_cache = get_answer_cache(app.state.store, config)
    try:
        yield
    finally:
        # 안 닫으면 다음 실행이 잠금으로 통째로 실패한다. 예외로 빠져나갈 때도 반드시 푼다
        app.state.store.close()


# FastAPI 앱을 만들어 반환한다
def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # CORS 미들웨어는 없다. 화면을 이 앱이 직접 서빙하므로 브라우저가 교차 출처 요청을
    # 아예 안 낸다 — 되살릴 조건은 plan_log/backend.md "안 하기로 한 것" 에 있다
    app.include_router(documents.router, prefix=settings.api_prefix)
    app.include_router(query.router, prefix=settings.api_prefix)
    # 화면 라우터에는 api_prefix 를 안 붙인다 — 사람이 여는 주소이지 API 가 아니다
    app.include_router(pages.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # 파이프라인 예외를 사용자용 메시지가 담긴 응답으로 변환한다.
    # 화면에서 온 요청이면 HTML 조각, 아니면 지금까지처럼 JSON 이다 — 공개 API 소비자의
    # 응답 모양은 한 자도 안 바뀐다 (tests/api 의 503 매핑 테스트가 그 갈래를 잠근다)
    @app.exception_handler(RagPipelineError)
    async def handle_pipeline_error(request: Request, exc: RagPipelineError) -> Response:
        code = _status_for(exc)
        if _is_htmx(request):
            return _error_fragment(request, exc.user_message, code)
        return JSONResponse(status_code=code, content={"detail": exc.user_message})

    # **업로드가 제일 자주 낼 오류는 여기로 온다.** 415(확장자)·413(용량)은
    # RagPipelineError 가 아니라 HTTPException 이다 — routes/documents.py 의
    # _reject_unsupported_extension() 과 _save_upload() 가 직접 던진다.
    # 위 핸들러만 고치면 그 둘이 여전히 JSON 으로 나가 화면에 아무것도 안 뜬다.
    #
    # 화면 요청이 아니면 FastAPI 기본 핸들러에 그대로 위임한다 — 여기서 JSONResponse 를
    # 직접 만들면 기본 동작(헤더 처리 등)의 두 번째 진실이 생긴다
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if _is_htmx(request):
            return _error_fragment(request, str(exc.detail), exc.status_code)
        return await http_exception_handler(request, exc)

    return app


app = create_app()
