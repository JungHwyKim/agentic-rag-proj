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

## 원본은 왜 남기나 (7회차)

파서를 고친 뒤 재색인하려면 원본이 있어야 하는데 예전에는 `finally` 가 지워버려 사용자가
파일을 다시 올리는 수밖에 없었다(13MB 스캔본은 OCR 만 3.6분이다). 그리고 답변의 근거를
보다가 *"원문이 진짜 그런가"* 를 확인할 길이 없었다. 그래서 색인에 **성공한** 업로드만
`settings.originals_dir` 로 옮겨 두고, 사이드바와 출처 태그에서 열 수 있게 한다.

**저장 이름은 계산된다** — `_stored_name()` 참고. 매핑 파일을 두지 않는 것이 요점이다.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from backend.app.core.config import SHADOW_EMBEDDING, Settings, get_settings
from backend.app.core.singletons import embedder_for
from rag_pipeline.ingestion.embedding import get_embedder
from rag_pipeline.ingestion.indexing import document_id_for
from rag_pipeline.ingestion.loader.registry import SUPPORTED_EXTENSIONS
from rag_pipeline.ingestion.pipeline import index_document, ingest
from rag_pipeline.schemas.document import ParsedDocument

router = APIRouter(tags=["documents"])

# 한 번에 읽어들일 청크 크기
_CHUNK_SIZE = 1024 * 1024

# 파일 이름에 그대로 쓸 수 없는 글자. Windows 예약 문자 + 경로 구분자다.
# document_id_for() 가 경로는 이미 떼주지만(PurePath(...).name) 이 글자들은 안 거른다
_UNSAFE_NAME_CHARS = '<>:"|?*\\/'

# 확장자 → (미디어 타입, inline 으로 보여줄 수 있는가).
#
# **확장자로 정하는 근거**: 업로드가 화이트리스트(1차)와 매직바이트(2차)를 이미 통과했고,
# 3회차가 화이트리스트 22종을 detect_format() 에 왕복시켜 **잰 것 18 · 어긋남 0** 으로
# 둘이 어긋나지 않음을 실측했다. 그래서 저장된 파일의 확장자는 이미 뒷받침돼 있다.
#
# **inline 이 안전한 근거**: SUPPORTED_EXTENSIONS 에 `.svg` 도 `.html` 도 없다. 올린 파일이
# 같은 오리진에서 스크립트로 도는 경로가 화이트리스트 단계에서 이미 닫혀 있다
_MEDIA_TYPES: dict[str, tuple[str, bool]] = {
    ".pdf": ("application/pdf", True),
    ".png": ("image/png", True),
    ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True),
    ".gif": ("image/gif", True),
    ".webp": ("image/webp", True),
    ".bmp": ("image/bmp", True),
    ".tif": ("image/tiff", True),
    ".tiff": ("image/tiff", True),
    # 텍스트류는 text/plain 으로 내려야 브라우저가 그린다. text/csv 는 내려받기로 샌다
    ".txt": ("text/plain; charset=utf-8", True),
    ".md": ("text/plain; charset=utf-8", True),
    ".markdown": ("text/plain; charset=utf-8", True),
    ".csv": ("text/plain; charset=utf-8", True),
    ".tsv": ("text/plain; charset=utf-8", True),
    # 아래는 브라우저가 못 그린다 — 내려받기로 보낸다
    ".hwpx": ("application/octet-stream", False),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        False,
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        False,
    ),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    ".xls": ("application/vnd.ms-excel", False),
}


# 그림자 색인에 넘길 (저장소, 임베더) 쌍. 꺼져 있거나 저장소가 형제를 못 내면 None 이다.
#
# **임베더를 여기서 게으르게 만든다** — `_bm25()` 와 같은 자리이고 이유도 같다.
# lifespan 에서 만들면 키가 없는 환경에서 서버가 안 뜨는데, 그림자는 **없어도 서비스가
# 도는 것**이 이 회차의 계약이라 뜨는 것을 막으면 앞뒤가 안 맞는다.
#
# **여기서 예외를 안 잡는다.** `get_embedder()` 는 키가 없어도 객체는 만들고 실제 호출에서
# 던지므로, 삼킬 것은 `_index_shadow()` 안에 있다 — 두 곳에서 잡으면 이유가 갈린다
def _shadow(request: Request):  # noqa: ANN202 - tuple[VectorStore, Embedder] | None
    store = getattr(request.app.state, "shadow_store", None)
    if store is None:
        return None
    embedder = getattr(request.app.state, "shadow_embedder", None)
    if embedder is None:
        embedder = get_embedder(SHADOW_EMBEDDING)
        request.app.state.shadow_embedder = embedder
    return (store, embedder)


# 문서 키를 디스크에 쓸 수 있는 파일 이름으로 바꾼다.
#
# **해시 접미사를 항상 붙이는 것이 이 함수의 핵심이다.** 예약 문자를 치환만 하면
# `보고서:1.pdf` 와 `보고서_1.pdf` 가 같은 이름이 되어, 색인은 둘을 다른 문서로 아는데
# 원문은 하나뿐인 상태가 된다 — 한쪽의 "원문 보기"가 남의 파일을 **에러 없이** 보여준다.
#
# 부딪힐 때만 붙이는 안은 기각했다. 그러면 이름이 document_id 만으로 계산되지 않아
# "이 문서의 원본이 어느 파일인가"를 알려면 디스크를 뒤지거나 매핑을 들여야 한다 —
# 두 번째 진실이 생긴다 (.claude/wiki/plan_log/backend.md 7회차)
def _stored_name(document_id: str) -> str:
    suffix = "".join(
        "_" if ch in _UNSAFE_NAME_CHARS or ord(ch) < 32 else ch
        for ch in unicodedata.normalize("NFC", document_id)
    ).strip()
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:6]

    stem, dot, ext = suffix.rpartition(".")
    # 확장자가 없으면 rpartition 이 앞을 비운다. 그때는 전체가 이름이다
    if not dot:
        stem, ext = suffix, ""
    return f"{stem}__{digest}{'.' + ext if ext else ''}"


# 문서 키에 대응하는 원본 파일 경로. **경로 탈출을 여기서 막는다.**
#
# 라우트의 경로 파라미터는 사용자 입력이라 `..%2F..%2F.env` 가 들어온다.
# document_id_for() 가 PurePath 로 경로를 떼는 것이 1차이고, resolve() 한 결과가
# 저장 폴더 안인지 보는 것이 2차다. 하나만으로는 안 되고 둘 다 필요하다
def _original_path(document_id: str, settings: Settings) -> Path:
    root = settings.originals_dir.resolve()
    candidate = (root / _stored_name(document_id)).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="요청한 문서를 찾을 수 없습니다.",
        )
    return candidate


# 이 문서 키에 대응하는 원본이 디스크에 있는가. **목록이 재색인 버튼을 그릴지 정하는 값이다.**
#
# `routes/query.py` 가 이것을 부른다 — 저장 이름 규칙(`_stored_name()`)을 그쪽에 복사하면
# 규칙이 두 벌이 되어 한쪽만 고쳐진다. `_reject_unsupported_extension` 을 두 라우트가
# 나눠 쓰는 것과 같은 이유다.
#
# `_original_path()` 가 경로 탈출을 404 로 막는데, 목록을 그리다 그것을 만나면 화면 전체를
# 죽이는 대신 "원본 없음" 으로 본다 — 사이드바가 예외로 비는 것이 더 나쁘다
def original_exists(document_id: str, settings: Settings) -> bool:
    try:
        return _original_path(document_id_for(document_id), settings).is_file()
    except HTTPException:
        return False


# 임시 업로드를 원본 보관함으로 옮긴다. **색인이 성공한 뒤에만 부른다** —
# 실패한 원본까지 남기면 목록에 없는 파일이 디스크에 쌓여 화면과 어긋난다.
#
# move 라서 뒤따르는 unlink(missing_ok=True) 가 그대로 통과한다. 같은 볼륨이면 rename 이라
# 13MB 스캔본도 복사 비용이 안 든다
def _keep_original(temp_path: Path, original_name: str, settings: Settings) -> None:
    settings.originals_dir.mkdir(parents=True, exist_ok=True)
    destination = _original_path(document_id_for(original_name), settings)
    shutil.move(str(temp_path), destination)


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
            # **임베더도 넘긴다 — 안 넘기면 문서마다 KURE 2.27GB 를 다시 올린다.**
            # 실측(2026-08-26): 새 문서 업로드가 65초였고 그중 파싱은 0.8초였다.
            # 재색인은 임베딩 캐시가 전량 적중해 모델이 안 올라오므로 1.3초라,
            # **재색인만 재면 이 비용이 안 보인다** (rag_pipeline/ingestion/pipeline.py)
            embedder=embedder_for(request.app),
            shadow=_shadow(request),
        )
        # **색인이 성공한 뒤에만** 원본을 남긴다 (_keep_original 주석 참고).
        # 파일을 옮기는 것도 블로킹 I/O 라 스레드풀로 넘긴다
        await run_in_threadpool(_keep_original, temp_path, original_name, settings)
    finally:
        temp_path.unlink(missing_ok=True)

    # BM25 는 저장소를 전량 훑어 만든 **스냅샷**이라 새 문서가 들어오면 낡는다. None 으로
    # 되돌리면 다음 질의가 다시 짓는다 (routes/query.py 의 _bm25).
    request.app.state.bm25 = None

    # **답변 캐시는 객체를 갈아끼우지 않고 내용만 비운다.** 그쪽은 스냅샷이 아니라 store
    # 참조만 들고 질의마다 새로 읽으므로 새로 만들 이유가 없다(경고만 잃는다).
    #
    # 그런데 **비우는 것은 따로 필요하다** — 조회 시점 근거 대조는 *"이 답이 딛고 선 청크가
    # 사라지거나 바뀌었다"* 만 잡고, 지금처럼 **새 문서가 들어온 경우는 원리적으로 못 잡는다**
    # (사라진 것이 없어 옛 답의 근거는 여전히 멀쩡하다). 그 하나를 clear() 가 맡는다.
    # 캐시가 꺼져 있으면(기본값) None 이라 아무 일도 안 일어난다.
    # clear() 는 실패해도 안 던지므로 try/except 가 필요 없다 (rag_pipeline/answer_cache/)
    cache = getattr(request.app.state, "answer_cache", None)
    if cache is not None:
        cache.clear()

    # 목록 조각을 여기서 렌더하지 않는다 (모듈 docstring 참고). 사이드바가 이 이벤트를 듣고
    # GET /documents 를 다시 부른다
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Trigger": "documents-changed"})


# 색인된 문서의 원본 파일을 그대로 돌려준다. 사이드바 문서 이름과 답변의 출처 태그가
# 이 주소를 연다 (모듈 docstring 참고).
#
# **`{document_id}` 를 불투명 문자열로 받는다.** 지금은 원본 파일명(NFC)이지만 문서
# 레지스트리가 생기면 명시적 id 로 바뀐다 — 여기서 파일명이라고 전제하지 않으면 그때
# 공개 URL 이 안 깨진다 (.claude/wiki/plan_log/backend.md 7회차)
@router.get("/documents/{document_id}/original")
async def original_file(document_id: str) -> FileResponse:
    settings = get_settings()
    # 경로 파라미터는 사용자 입력이다. document_id_for() 가 경로를 떼고 NFC 로 맞추는 것이
    # 1차, _original_path() 의 resolve() 봉쇄가 2차다
    path = _original_path(document_id_for(document_id), settings)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="이 문서의 원본이 저장돼 있지 않습니다. 파일을 다시 업로드하면 저장됩니다.",
        )

    media_type, renderable = _MEDIA_TYPES.get(
        path.suffix.lower(), ("application/octet-stream", False)
    )
    return FileResponse(
        path,
        media_type=media_type,
        # **filename 을 줘야 Starlette 이 Content-Disposition 을 단다**(안 주면 헤더 자체가
        # 없다). 디스크 이름이 아니라 원래 이름을 주는 것이 요점이다 — 내려받았을 때
        # `안내서__a3f9c2.pdf` 가 아니라 `안내서.pdf` 로 저장된다
        filename=document_id_for(document_id),
        # 브라우저가 그릴 수 있는 것만 inline 이다. 나머지는 내려받기로 보낸다
        content_disposition_type="inline" if renderable else "attachment",
        # 위 표가 정한 타입을 브라우저가 다시 추측하지 못하게 막는다.
        # .svg·.html 이 화이트리스트에 없어 스크립트 경로는 이미 닫혀 있지만 한 겹 더 둔다
        headers={"X-Content-Type-Options": "nosniff"},
    )


# 이미 색인된 문서를 **보관해 둔 원본으로** 다시 색인한다.
#
# ## 왜 새 결합이 아니라 입구인가
#
# `index_document()` 는 이미 `delete_by_document_id()` -> `upsert()` 순서라, 같은 키로 다시
# 부르는 것이 곧 재색인이다. 7회차가 원본을 남기게 만들어 **입력도 이미 디스크에 있다.**
# 여기서 새로 만드는 것은 그 둘을 잇는 입구 하나뿐이다 (.claude/wiki/plan_log/backend.md 8회차).
#
# ## 왜 전량이 아니라 문서 하나인가
#
# 전량은 문서 10건에 분 단위라 동기로는 타임아웃이고, 백그라운드로 돌리면 작업
# 레지스트리·수명·동시성이 딸려 오며 **실패가 응답이 아니라 폴링으로 오게 되어** 5회차의
# 오류 표시 배선이 통째로 바뀐다. 그 덩어리는 "진짜 단계별 색인 진행률" 미결과 같은 것이다.
#
# ## `original_name` 에 무엇을 넘기나 — 여기가 제일 조용하게 틀릴 수 있는 자리다
#
# 디스크 이름은 `안내서__a3f9c2.pdf` 다(`_stored_name()`). 그것을 그대로 넘기면
# `document_id` 가 **새로 생겨** delete 가 옛 청크를 못 찾고 같은 문서가 두 벌이 된다.
# 넘길 것은 디스크 이름이 아니라 **문서 키**다
@router.post("/documents/{document_id}/reindex", status_code=status.HTTP_204_NO_CONTENT)
async def reindex_document(request: Request, document_id: str) -> Response:
    settings = get_settings()
    key = document_id_for(document_id)
    path = _original_path(key, settings)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="이 문서의 원본이 저장돼 있지 않아 다시 색인할 수 없습니다. 파일을 다시 업로드해 주세요.",
        )

    # 업로드와 **같은** 1차 검증을 다시 태운다. 화이트리스트가 좁아진 뒤에도 남아 있던 옛
    # 원본을 여기서 거른다 — 규칙이 한 곳에만 있는 것이 두 벌로 갈리는 것보다 낫다
    _reject_unsupported_extension(key)

    # `store` 주입은 색인 라우트와 **같은 이유로 필수**다 (index_upload 주석 참고).
    # `_keep_original()` 은 부르지 않는다 — 파일이 이미 제자리라 옮길 것이 없고,
    # shutil.move 가 자기 자신을 덮는다. 임시 파일이 없으니 정리할 것도 없다
    await run_in_threadpool(
        index_document,
        path,
        original_name=key,
        config=request.app.state.ingest_config,
        store=request.app.state.store,
        # 업로드 쪽과 같은 이유다 — 청크가 바뀌어 캐시를 빗나가면 여기서도 모델이 올라온다
        embedder=embedder_for(request.app),
        shadow=_shadow(request),
    )

    # 색인 라우트와 같은 계약이다. BM25 는 저장소를 전량 훑은 스냅샷이라 청크가 바뀌면 낡는다
    request.app.state.bm25 = None

    # **답변 캐시는 비운다 — 삭제 라우트와 갈리는 자리다.** 조회 시점 근거 대조는 *"이 답이
    # 딛고 선 청크가 사라지거나 바뀌었다"* 를 id·텍스트 해시로 잡는데, 파서를 고쳐 **청크가
    # 늘어난** 경우는 옛 답의 근거가 여전히 멀쩡해 **원리적으로 못 잡는다.** 색인 라우트가
    # clear() 를 부르는 이유(새 문서 유입)와 같은 축이다 (rag_pipeline/answer_cache/)
    cache = getattr(request.app.state, "answer_cache", None)
    if cache is not None:
        cache.clear()

    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Trigger": "documents-changed"})


# 문서 하나를 색인에서 지우고 원본도 함께 지운다.
#
# **둘을 함께 지우는 것이 계약이다** — 보이는 것과 실제가 어긋나면 사용자가 모르는 잔여물이
# 생긴다. 디스크를 정리할 수단이 화면에 있어야 한다는 것도 같은 이유다
# (.claude/wiki/plan_log/backend.md 7회차 — 상한·자동정리를 안 만든 대신 이 버튼이 있다)
@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(request: Request, document_id: str) -> Response:
    settings = get_settings()
    key = document_id_for(document_id)
    path = _original_path(key, settings)

    store = request.app.state.store
    # delete_by_document_id() 는 VectorStore Protocol 에 있다 — list_documents() 와 달리
    # getattr 로 봐줄 필요가 없다 (rag_pipeline/ingestion/indexing/base.py)
    await run_in_threadpool(store.delete_by_document_id, key)

    # **그림자 컬렉션에서도 지운다.** 안 지우면 화면에서 사라진 문서가 그림자에 남아,
    # 나중에 두 모델을 견줄 때 **한쪽에만 있는 문서**가 생긴다 — `overlap.py` 가 정확히
    # 그것을 경고하는 자리이고, 조용히 두면 비교가 검색이 아니라 문서 집합 차이를 잰다.
    # 색인 쪽이 두 컬렉션을 함께 채우므로 지우는 쪽도 함께여야 짝이 맞는다
    shadow_store = getattr(request.app.state, "shadow_store", None)
    if shadow_store is not None:
        await run_in_threadpool(shadow_store.delete_by_document_id, key)

    path.unlink(missing_ok=True)

    # 색인 라우트와 같은 계약이다. BM25 는 저장소를 전량 훑은 스냅샷이라 낡는다
    request.app.state.bm25 = None

    # **답변 캐시는 여기서 안 비운다.** 캐시는 조회 시점에 근거 청크를 되짚어 스스로
    # 걸러내는데, 삭제는 바로 그 청크가 사라지는 경우라 **원리적으로 잡히는 쪽**이다.
    # 색인 라우트가 clear() 를 부르는 이유(새 문서 유입은 사라진 것이 없어 못 잡는다)와
    # 정확히 반대다 (rag_pipeline/answer_cache/)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Trigger": "documents-changed"})


# 서비스 상태 확인용
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
