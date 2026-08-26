"""색인 엔드포인트 — 업로드가 실제로 검색 가능한 벡터가 되는 자리.

**여기서 확인하는 것은 라우트의 계약이지 색인 자체가 아니다.** `index_document()` 를 갈아끼워
넷을 본다 — store 를 넘기는가 · BM25 를 무효화하는가 · 답변 캐시를 **비우되 갈아끼우지는
않는가** · 목록 갱신 신호를 보내는가.

**BM25 와 캐시가 대우가 다른 것이 요점이다.** BM25 는 스냅샷이라 객체를 버리고(`None`),
캐시는 store 참조만 들고 질의마다 새로 읽으므로 **객체는 그대로 두고 내용만 비운다.**
둘을 같게 다루면 캐시는 경고만 잃고 얻는 것이 없다. 색인의 의미(주입한 store 를 안 닫는다 등)는
`tests/ingestion/indexing/test_indexing_pipeline.py` 가 덮는다.

`app.state` 는 가짜를 직접 꽂는다. `TestClient` 를 `with` 없이 쓰면 lifespan 이 안 돌아
진짜 저장소가 안 열리는데, 그것이 여기서는 의도다 — 안 그러면 테스트가
`PROJECT_ROOT/.qdrant` 를 열어 개발자의 실제 색인을 건드린다 (test_query_route.py 와 같다).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from rag_pipeline.config import IngestConfig


# 색인 실패를 흉내내는 전용 예외. **흔한 타입을 쓰면 안 되는 이유는 아래 테스트 주석에 있다**
class _IndexingBoom(Exception):
    pass


# 색인 라우트가 넘겨받아야 하는 저장소. 실제로 쓰이지는 않고 **같은 객체가 넘어왔는지**만 본다
class FakeStore:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


# 답변 캐시 자리. **clear() 가 불렸는지만 본다** — 비우는 의미는 tests/answer_cache/ 가 덮는다
class FakeCache:
    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> bool:
        self.cleared += 1
        return True


# 앱을 만들고 가짜 상태를 꽂는다. lifespan 은 일부러 안 돌린다 (모듈 docstring 참고)
def _client(bm25=None, answer_cache=None) -> tuple[TestClient, object]:
    app = create_app()
    app.state.ingest_config = IngestConfig()
    app.state.store = FakeStore()
    app.state.embedder = object()
    app.state.bm25 = bm25
    app.state.answer_cache = answer_cache
    return TestClient(app), app


# 업로드 파일 한 건을 만드는 헬퍼. 색인은 갈아끼우므로 내용은 아무래도 좋다
def _files(name: str = "안내서.docx") -> dict:
    return {"file": (name, b"PK\x03\x04 not really a docx", "application/octet-stream")}


# 색인이 끝나면 204 와 함께 목록 갱신 신호를 보낸다. 목록 조각을 여기서 렌더하지 않는 것이
# 계약이다 — 렌더는 GET /documents 한 곳뿐이다
def test_index_returns_204_with_refresh_trigger(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, _ = _client()

    response = client.post("/api/v1/documents/index", files=_files())

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "documents-changed"
    assert response.content == b""


# **store 를 인자로 넘겨야 한다.** 안 넘기면 index_document() 가 자기 클라이언트를 열어
# startup 싱글턴과 같은 프로세스 안에서 배타 잠금으로 부딪힌다
def test_index_passes_the_singleton_store(mocker):
    spy = mocker.patch("backend.app.api.routes.documents.index_document")
    client, app = _client()

    client.post("/api/v1/documents/index", files=_files())

    kwargs = spy.call_args.kwargs
    assert kwargs["store"] is app.state.store
    assert kwargs["config"] is app.state.ingest_config
    assert kwargs["original_name"] == "안내서.docx"


# 넘긴 store 를 라우트가 닫으면 다음 요청이 죽는다. 수명은 lifespan 이 쥔다
def test_index_does_not_close_the_singleton_store(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, app = _client()

    client.post("/api/v1/documents/index", files=_files())

    assert app.state.store.closed is False


# BM25 는 저장소를 전량 훑은 **스냅샷**이라 새 문서가 들어오면 낡는다. None 이면 다음 질의가
# 다시 짓는다
def test_index_invalidates_bm25(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, app = _client(bm25=object())

    client.post("/api/v1/documents/index", files=_files())

    assert app.state.bm25 is None


# **답변 캐시는 건드리지 않는다.** 스냅샷이 아니라 store 참조만 들고 질의마다 새로 읽으며,
# 낡은 항목은 조회 시점에 근거 청크를 되짚어 스스로 걸러낸다. 리셋하면 warnings 만 잃는다
def test_index_keeps_the_same_cache_object(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")
    cache = FakeCache()
    client, app = _client(answer_cache=cache)

    client.post("/api/v1/documents/index", files=_files())

    # BM25 와 달리 갈아끼우지 않는다 — 스냅샷이 아니라 새로 만들 이유가 없고 경고만 잃는다
    assert app.state.answer_cache is cache


# **비우는 것은 따로 필요하다.** 조회 시점 근거 대조는 *"딛고 선 청크가 사라지거나 바뀌었다"*
# 만 잡고, 새 문서가 들어온 경우는 원리적으로 못 잡는다 — 사라진 것이 없어 옛 답의 근거는
# 여전히 멀쩡하다. 그 하나를 clear() 가 맡는다
def test_index_clears_the_answer_cache(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")
    cache = FakeCache()
    client, _ = _client(answer_cache=cache)

    client.post("/api/v1/documents/index", files=_files())

    assert cache.cleared == 1


# 캐시가 꺼져 있으면(기본값) None 이라 아무 일도 안 일어나야 한다 — 여기서 죽으면
# 임계값을 안 정한 개발 환경에서 업로드가 통째로 실패한다
def test_index_works_when_the_cache_is_off(mocker):
    mocker.patch("backend.app.api.routes.documents.index_document")
    client, _ = _client(answer_cache=None)

    assert client.post("/api/v1/documents/index", files=_files()).status_code == 204


# 색인이 실패하면 캐시도 안 비운다 — 저장소가 안 바뀌었는데 비우면 헛되이 버린다
def test_failed_index_does_not_clear_the_cache(mocker):
    mocker.patch(
        "backend.app.api.routes.documents.index_document", side_effect=_IndexingBoom("실패")
    )
    cache = FakeCache()
    client, _ = _client(answer_cache=cache)

    with pytest.raises(_IndexingBoom):
        client.post("/api/v1/documents/index", files=_files())
    assert cache.cleared == 0


# 1차 검증은 색인 라우트도 똑같이 받는다. 확장자가 아니면 파싱까지 가지 않는다
def test_index_rejects_unlisted_extension(mocker, tmp_path: Path):
    spy = mocker.patch("backend.app.api.routes.documents.index_document")
    client, _ = _client()

    response = client.post(
        "/api/v1/documents/index",
        files={"file": ("보고서.exe", b"MZ\x00\x00", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert ".exe" in response.json()["detail"]
    spy.assert_not_called()


# 색인이 실패하면 BM25 를 되돌리지 않는다 — 저장소가 안 바뀌었으므로 스냅샷도 안 낡았다.
#
# **실패 신호를 전용 예외로 만드는 이유**: 흔한 타입(RuntimeError 등)을 쓰면 qdrant 로컬
# 파일 모드의 배타 잠금 오류와 구분되지 않는다. 그것도 RuntimeError 다
# ("Storage folder ... is already accessed by another instance"). 삼키고 뒤 상태만 단언하면
# **주입이 동작해서 통과한 것**과 **잠금으로 죽었는데 단언이 우연히 맞는 것**이 안 갈린다.
# 전용 예외를 pytest.raises 로 받으면 잠금 오류는 잡히지 않고 그대로 터진다
def test_failed_index_keeps_bm25(mocker):
    mocker.patch(
        "backend.app.api.routes.documents.index_document",
        side_effect=_IndexingBoom("색인 실패"),
    )
    index = object()
    client, app = _client(bm25=index)

    with pytest.raises(_IndexingBoom):
        client.post("/api/v1/documents/index", files=_files())

    assert app.state.bm25 is index


# **임베더도 넘긴다 — 안 넘기면 문서마다 모델을 다시 올린다.** `get_embedder()` 는
# 메모이즈하지 않고, 로컬 임베더는 임베딩 캐시가 빗나갈 때 가중치를 올리므로 *새 문서*
# 하나마다 KURE-v1 2.27GB 가 처음부터 적재된다(실측 2026-08-26: 업로드 65초).
# 넘길 것은 `core/singletons.py` 가 들고 있는 **앱 하나짜리 인스턴스**다
def test_index_passes_the_app_wide_embedder(mocker):
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")
    client, app = _client()

    client.post("/api/v1/documents/index", files=_files())

    assert indexer.call_args.kwargs["embedder"] is app.state.embedder
