"""문서 업로드·색인 엔드포인트.

여기서 하는 일은 업로드 수신 / 1차 검증 / 임시 저장 / 정리뿐이다.
파싱은 `ingest()` 가, 색인은 `index_document()` 가 한다.

## 왜 업로드와 색인이 두 엔드포인트인가

`upload` 의 `response_model` 이 `ParsedDocument` 라 **그 반환이 곧 공개 API 스키마**다.
거기에 색인을 얹으면 파싱만 원하던 소비자의 응답이 바뀐다. 그래서 색인은 옆에 새로 냈고,
화면의 업로드 버튼이 부르는 것은 이쪽이다.

## 왜 색인 쪽은 HTML 조각이 아니라 204 인가

돌려줄 것이 문서 목록인데 그 조각은 `routes/query.py` 의 `GET /documents` 가 이미 만든다.
여기서 다시 렌더하면 같은 목록의 두 번째 진실이 생긴다. 대신 `HX-Trigger` 로 이벤트만
보내고 사이드바가 자기 갱신을 하게 한다 — 목록 로직은 한 곳에 남는다.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from backend.app.core.config import get_settings
from rag_pipeline.ingestion.loader.registry import SUPPORTED_EXTENSIONS
from rag_pipeline.ingestion.pipeline import index_document, ingest
from rag_pipeline.schemas.document import ParsedDocument

router = APIRouter(tags=["documents"])

# 한 번에 읽어들일 청크 크기
_CHUNK_SIZE = 1024 * 1024


# 업로드 파일을 임시 경로에 저장하면서 크기 상한을 검사한다.
# 전체를 메모리에 올리지 않고 청크 단위로 누적 검사해야 큰 파일에도 안전하다
async def _save_upload(upload: UploadFile, limit: int) -> Path:
    # 저장 파일명은 UUID로 만든다. 원본 파일명을 경로에 쓰면 path traversal에 노출된다
    suffix = Path(upload.filename or "").suffix.lower()
    # 파싱이 끝난 뒤 라우트가 지워야 하므로 delete=False로 열고 수명을 직접 관리한다.
    # with 블록을 쓰면 파일이 닫히는 순간 사라져 로더가 열 수 없다
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        delete=False, suffix=suffix, prefix=f"upload-{uuid.uuid4().hex}-"
    )
    temp_path = Path(handle.name)

    written = 0
    try:
        while chunk := await upload.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"파일이 너무 큽니다. 최대 {limit // (1024 * 1024)}MB까지 업로드할 수 있습니다.",
                )
            handle.write(chunk)
    except Exception:
        handle.close()
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if not handle.closed:
            handle.close()

    return temp_path


# 1차 검증: 확장자 화이트리스트. 실제 포맷은 매직바이트로 로더가 다시 검증한다.
# **두 라우트가 나눠 쓴다** — 복사해 두면 지원 목록이 한쪽만 늘어난다
def _reject_unsupported_extension(original_name: str) -> None:
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"'{suffix or '확장자 없음'}' 형식은 지원하지 않습니다. "
                "PDF, Word(.docx), PowerPoint(.pptx), Excel(.xlsx/.xls), 한글(.hwpx), "
                "이미지, 텍스트 파일을 업로드해 주세요."
            ),
        )


# 파일을 업로드해 파싱된 문서를 돌려준다. **색인은 안 한다** (모듈 docstring 참고)
@router.post("/documents/upload", response_model=ParsedDocument)
async def upload_document(file: Annotated[UploadFile, File()]) -> ParsedDocument:
    settings = get_settings()

    original_name = file.filename or "unnamed"
    _reject_unsupported_extension(original_name)

    temp_path = await _save_upload(file, settings.max_upload_bytes)
    try:
        # 로더는 동기·CPU 바운드라 스레드풀로 넘겨 이벤트 루프를 막지 않는다
        return await run_in_threadpool(ingest, temp_path, original_name=original_name)
    finally:
        temp_path.unlink(missing_ok=True)


# 파일을 업로드해 파싱·청킹·임베딩까지 마치고 벡터 저장소에 색인한다.
# 화면의 업로드 버튼이 부르는 자리다 (모듈 docstring 참고)
@router.post("/documents/index", status_code=status.HTTP_204_NO_CONTENT)
async def index_upload(request: Request, file: Annotated[UploadFile, File()]) -> Response:
    settings = get_settings()
    original_name = file.filename or "unnamed"
    _reject_unsupported_extension(original_name)

    temp_path = await _save_upload(file, settings.max_upload_bytes)
    try:
        # store 를 **인자로 넘긴다.** index_document() 가 스스로 열면 startup 싱글턴이 잡은
        # 배타 잠금과 같은 프로세스 안에서 부딪힌다 (rag_pipeline/ingestion/pipeline.py)
        await run_in_threadpool(
            index_document,
            temp_path,
            original_name=original_name,
            config=request.app.state.ingest_config,
            store=request.app.state.store,
        )
    finally:
        temp_path.unlink(missing_ok=True)

    # BM25 는 저장소를 전량 훑어 만든 **스냅샷**이라 새 문서가 들어오면 낡는다. None 으로
    # 되돌리면 다음 질의가 다시 짓는다 (routes/query.py 의 _bm25).
    # **답변 캐시는 건드리지 않는다** — 그쪽은 스냅샷이 아니라 store 참조만 들고 질의마다
    # 새로 읽으며, 낡은 항목은 조회 시점에 근거 청크를 되짚어 스스로 걸러낸다
    request.app.state.bm25 = None

    # 목록 조각을 여기서 렌더하지 않는다 (모듈 docstring 참고). 사이드바가 이 이벤트를 듣고
    # GET /documents 를 다시 부른다
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Trigger": "documents-changed"})


# 서비스 상태 확인용
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
