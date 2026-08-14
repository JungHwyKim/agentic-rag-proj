"""문서 업로드 엔드포인트.

여기서 하는 일은 업로드 수신 / 1차 검증 / 임시 저장 / 정리뿐이다.
파싱은 전부 rag_pipeline.ingestion.pipeline.ingest() 가 한다.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from backend.app.core.config import get_settings
from rag_pipeline.ingestion.loader.registry import SUPPORTED_EXTENSIONS
from rag_pipeline.ingestion.pipeline import ingest
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


# 파일을 업로드해 파싱된 문서를 돌려준다
@router.post("/documents/upload", response_model=ParsedDocument)
async def upload_document(file: Annotated[UploadFile, File()]) -> ParsedDocument:
    settings = get_settings()

    original_name = file.filename or "unnamed"
    suffix = Path(original_name).suffix.lower()

    # 1차 검증: 확장자 화이트리스트. 실제 포맷은 매직바이트로 다시 검증한다
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"'{suffix or '확장자 없음'}' 형식은 지원하지 않습니다. "
                "PDF, Word(.docx), PowerPoint(.pptx), Excel(.xlsx/.xls), 한글(.hwpx), "
                "이미지, 텍스트 파일을 업로드해 주세요."
            ),
        )

    temp_path = await _save_upload(file, settings.max_upload_bytes)
    try:
        # 로더는 동기·CPU 바운드라 스레드풀로 넘겨 이벤트 루프를 막지 않는다
        return await run_in_threadpool(ingest, temp_path, original_name=original_name)
    finally:
        temp_path.unlink(missing_ok=True)


# 서비스 상태 확인용
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
