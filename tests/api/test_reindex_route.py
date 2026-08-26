"""재색인 엔드포인트 — 보관해 둔 원본으로 다시 색인하는 자리.

**여기서 확인하는 것은 라우트의 계약이지 색인 자체가 아니다.** `index_document()` 를
갈아끼워 무엇이 넘어가고 무엇이 뒤따르는지만 본다. 색인의 의미는
`tests/ingestion/indexing/test_indexing_pipeline.py` 가 덮는다.

잠그는 것 둘이 특히 조용하다.

1. **`original_name` 이 디스크 이름이 아니라 문서 키인가.** 디스크 이름은
   `안내서__a3f9c2.pdf` 라(`_stored_name()`) 그대로 넘기면 `document_id` 가 새로 생겨
   delete 가 옛 청크를 못 찾고 같은 문서가 두 벌이 된다 — **아무 에러도 안 난다**
2. **`store` 를 넘기는가.** 안 넘기면 `index_document()` 가 자기 클라이언트를 열어 같은
   uvicorn 안에서 startup 싱글턴의 배타 잠금과 부딪힌다. 운영에서만 죽는 종류다

`app.state` 는 가짜를 직접 꽂고 lifespan 은 일부러 안 돌린다 — `test_index_route.py` 와
같은 이유다(진짜 저장소를 열면 개발자의 실제 `.qdrant` 를 건드린다).

## 파일 이름

`tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이다. 저장소 전체에서 유일해야 한다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.routes.documents import _stored_name
from backend.app.main import create_app
from rag_pipeline.config import IngestConfig


# 저장소 자리. 재색인 라우트는 목록만 읽고 색인은 `index_document()` 가 하므로
# **같은 객체가 넘어왔는지**만 보면 된다
class FakeStore:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self._documents = documents or []

    def list_documents(self) -> list[dict]:
        return self._documents


# 답변 캐시 자리. `clear()` 가 불렸는지만 본다 — 비우는 의미는 tests/answer_cache/ 가 덮는다
class FakeCache:
    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> bool:
        self.cleared += 1
        return True


# 원본을 보관함에 만들어 둔다. 이름은 라우트와 **같은 함수**로 계산한다 — 손으로 적으면
# 저장 이름 규칙이 바뀌었을 때 테스트만 통과한다
def _place_original(originals_dir: Path, document_id: str) -> Path:
    originals_dir.mkdir(parents=True, exist_ok=True)
    path = originals_dir / _stored_name(document_id)
    path.write_bytes(b"PK\x03\x04 not really a document")
    return path


# 앱을 만들고 가짜 상태를 꽂는다. lifespan 은 일부러 안 돌린다 (모듈 docstring 참고)
def _client(*, documents=None, answer_cache=None, shadow_store=None):
    app = create_app()
    app.state.ingest_config = IngestConfig()
    app.state.store = FakeStore(documents)
    app.state.embedder = object()
    app.state.bm25 = object()
    app.state.answer_cache = answer_cache
    if shadow_store is not None:
        app.state.shadow_store = shadow_store
        # 안 꽂으면 `_shadow()` 가 진짜 임베더를 만든다. 여기서 볼 것은 그것이 아니다
        app.state.shadow_embedder = object()
    return TestClient(app), app


# 재색인이 끝나면 204 와 목록 갱신 신호를 보낸다. 목록 조각을 여기서 렌더하지 않는 것이
# 계약이다 — 렌더는 GET /documents 한 곳뿐이다 (색인 라우트와 같다)
def test_reindex_returns_204_with_refresh_trigger(mocker, originals_dir: Path):
    mocker.patch("backend.app.api.routes.documents.index_document")
    _place_original(originals_dir, "안내서.pdf")
    client, _ = _client()

    response = client.post("/api/v1/documents/안내서.pdf/reindex")

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "documents-changed"
    assert response.content == b""


# **디스크 이름이 아니라 문서 키를 넘긴다** (모듈 docstring 1번). 그리고 store 를 주입한다(2번)
def test_reindex_passes_document_key_and_injected_store(mocker, originals_dir: Path):
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")
    original = _place_original(originals_dir, "안내서.pdf")
    client, app = _client()

    client.post("/api/v1/documents/안내서.pdf/reindex")

    args, kwargs = indexer.call_args
    # 읽는 것은 보관된 원본이고
    assert args[0] == original
    # 넘기는 것은 그 파일의 디스크 이름이 아니라 문서 키다
    assert kwargs["original_name"] == "안내서.pdf"
    assert "__" not in kwargs["original_name"]
    assert kwargs["store"] is app.state.store
    assert kwargs["config"] is app.state.ingest_config


# 원본이 없으면 404 다. **색인은 손대지 않는다** — 있던 문서가 조용히 비면 안 된다
def test_reindex_without_original_returns_404(mocker, originals_dir: Path):
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")
    client, _ = _client()

    response = client.post("/api/v1/documents/없는문서.pdf/reindex")

    assert response.status_code == 404
    assert "업로드" in response.json()["detail"]
    indexer.assert_not_called()


# 화이트리스트 밖 확장자는 415 다. 업로드가 통과시킨 뒤 목록이 좁아졌더라도 여기서 걸린다
def test_reindex_rejects_unsupported_extension(mocker, originals_dir: Path):
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")
    _place_original(originals_dir, "메모.zip")
    client, _ = _client()

    response = client.post("/api/v1/documents/메모.zip/reindex")

    assert response.status_code == 415
    indexer.assert_not_called()


# BM25 는 스냅샷이라 **버리고**, 답변 캐시는 객체를 두고 **내용만 비운다.**
# 둘의 대우가 다른 이유는 `index_upload` 주석에 있다
def test_reindex_invalidates_bm25_and_clears_cache(mocker, originals_dir: Path):
    mocker.patch("backend.app.api.routes.documents.index_document")
    _place_original(originals_dir, "안내서.pdf")
    cache = FakeCache()
    client, app = _client(answer_cache=cache)

    client.post("/api/v1/documents/안내서.pdf/reindex")

    assert app.state.bm25 is None
    assert cache.cleared == 1
    assert app.state.answer_cache is cache


# **그림자도 함께 다시 색인한다.** 한쪽만 다시 하면 두 컬렉션이 서로 다른 청크를 들어
# 나중 비교가 검색이 아니라 청크 집합 차이를 잰다 — 그 어긋남은 화면에 안 보인다
def test_reindex_passes_shadow(mocker, originals_dir: Path):
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")
    _place_original(originals_dir, "안내서.pdf")
    shadow = FakeStore()
    client, app = _client(shadow_store=shadow)

    client.post("/api/v1/documents/안내서.pdf/reindex")

    assert indexer.call_args.kwargs["shadow"] == (shadow, app.state.shadow_embedder)


# 목록이 원본 유무를 싣는다. **원본이 없는 문서에는 버튼을 안 그린다** — 그리면 눌러야
# 404 를 안다(6회차에 사용자가 물고 온 것이 그 종류다)
def test_document_list_hides_reindex_when_original_missing(originals_dir: Path):
    _place_original(originals_dir, "있는문서.pdf")
    client, _ = _client(
        documents=[
            {"document_id": "있는문서.pdf", "source_name": "있는문서.pdf", "chunk_count": 3},
            {"document_id": "없는문서.pdf", "source_name": "없는문서.pdf", "chunk_count": 5},
        ]
    )

    html = client.get("/api/v1/documents").text

    # 버튼은 원본이 있는 하나에만 그려진다
    assert html.count("/reindex") == 1
    assert html.count("원본 없음") == 1
