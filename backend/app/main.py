"""FastAPI 앱 진입점. 실행: uvicorn backend.app.main:app --reload"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import documents, pages, query
from backend.app.core.config import get_settings
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
# 딸린 제약: index_document() 는 get_store() 로 **자기 클라이언트를 직접 열어** finally 에서
# 닫는다. 그래서 업로드 버튼을 진짜로 붙이는 순간 같은 프로세스 안에서 이 싱글턴과 충돌한다.
# 그때 첫 후보는 도커가 아니라 index_document(store=...) 주입이다 (plan_log/backend.md 미결)
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = IngestConfig()
    app.state.ingest_config = config
    app.state.store = get_store(config.indexing, config.embedding)
    app.state.embedder = None  # 첫 질의에 만든다 — 키가 없어도 서버는 떠야 한다
    # BM25 도 첫 질의에 짓는다. 여기서 지으면 색인이 아직 없을 때 저장소를 헛되이 열고,
    # 무엇보다 store 의 게으름을 깨는 것이 위 주석이 피하려는 바로 그 일이다
    app.state.bm25 = None
    try:
        yield
    finally:
        # 안 닫으면 다음 실행이 잠금으로 통째로 실패한다. 예외로 빠져나갈 때도 반드시 푼다
        app.state.store.close()


# FastAPI 앱을 만들어 반환한다
def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(documents.router, prefix=settings.api_prefix)
    app.include_router(query.router, prefix=settings.api_prefix)
    # 화면 라우터에는 api_prefix 를 안 붙인다 — 사람이 여는 주소이지 API 가 아니다
    app.include_router(pages.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # 파이프라인 예외를 사용자용 메시지가 담긴 JSON 응답으로 변환
    @app.exception_handler(RagPipelineError)
    async def handle_pipeline_error(request: Request, exc: RagPipelineError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for(exc),
            content={"detail": exc.user_message},
        )

    return app


app = create_app()
