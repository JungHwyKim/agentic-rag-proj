"""FastAPI 앱 진입점. 실행: uvicorn backend.app.main:app --reload"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.routes import documents
from backend.app.core.config import get_settings
from rag_pipeline.exceptions import (
    CorruptedFileError,
    DecompressionLimitError,
    EmbeddingError,
    FileTooLargeError,
    RagPipelineError,
    UnsupportedFormatError,
)

# 파이프라인 예외를 HTTP 상태코드로 매핑한다.
# 사용자가 무엇을 해야 하는지 알 수 있도록 user_message 를 그대로 내보낸다
_STATUS_BY_EXCEPTION: list[tuple[type[RagPipelineError], int]] = [
    (UnsupportedFormatError, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE),
    (FileTooLargeError, status.HTTP_413_CONTENT_TOO_LARGE),
    (DecompressionLimitError, status.HTTP_413_CONTENT_TOO_LARGE),
    (CorruptedFileError, status.HTTP_422_UNPROCESSABLE_CONTENT),
    # 임베딩은 아직 ingest() 밖에 있어 이 줄에 닿는 HTTP 경로가 없다. 그래도 지금 넣는 이유는
    # 끼우는 순간 살아나는데 그때 빠져 있으면 _status_for() 가 500 을 돌려주기 때문이다
    (EmbeddingError, status.HTTP_503_SERVICE_UNAVAILABLE),
]


# 예외 타입에 대응하는 HTTP 상태코드를 찾는다. 못 찾으면 500
def _status_for(exc: RagPipelineError) -> int:
    for exc_type, code in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            return code
    return status.HTTP_500_INTERNAL_SERVER_ERROR


# FastAPI 앱을 만들어 반환한다
def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(documents.router, prefix=settings.api_prefix)

    # 파이프라인 예외를 사용자용 메시지가 담긴 JSON 응답으로 변환
    @app.exception_handler(RagPipelineError)
    async def handle_pipeline_error(request: Request, exc: RagPipelineError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for(exc),
            content={"detail": exc.user_message},
        )

    return app


app = create_app()
