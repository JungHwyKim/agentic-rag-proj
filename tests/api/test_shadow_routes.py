"""그림자 색인이 **화면 쪽에서** 지켜야 하는 셋을 잠근다.

1. 색인 라우트가 그림자를 실제로 넘기는가
2. 삭제 라우트가 **두 컬렉션을 다 지우는가** — 안 지우면 한쪽에만 있는 문서가 생겨
   나중 비교가 검색이 아니라 문서 집합 차이를 잰다
3. 그림자에 빠진 문서가 목록에 **드러나는가** — 실패를 삼키는 대가로 반드시 있어야 하는 자리다

## 파일 이름

`tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이다. 저장소 전체에서 유일해야 한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


# 주 저장소 흉내. 목록과 삭제만 쓰인다
class StubStore:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.collection = "primary"
        self._documents = documents or []
        self.deleted: list[str] = []
        self.closed = False

    def list_documents(self) -> list[dict]:
        return self._documents

    def delete_by_document_id(self, document_id: str) -> None:
        self.deleted.append(document_id)

    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]. 여기서는 늘 비어 있다
        return iter(())

    # 실제 QdrantStore 가 이것으로 그림자를 낸다. 대역도 같은 이음새를 가져야
    # lifespan 이 프로덕션과 같은 경로를 탄다
    def sibling_for(self, embedding: Any) -> StubStore:
        return self.sibling

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def wired(mocker):
    """주 저장소와 그림자를 붙인 앱 하나. 그림자는 주 저장소의 `sibling` 이다."""

    def _make(documents=None, shadowed=None):
        primary = StubStore(documents)
        primary.sibling = StubStore(shadowed if shadowed is not None else documents)
        mocker.patch("backend.app.main.get_store", return_value=primary)
        return primary

    return _make


def _files():
    return {"file": ("메모.txt", b"\xea\xb0\x80\xeb\x82\x98\xeb\x8b\xa4", "text/plain")}


# --- 1. 색인 라우트가 그림자를 넘긴다 ---


# **`shadow=` 가 실제로 실려 가는지 본다.** 라우트가 안 넘기면 그림자 컬렉션이 영원히
# 비는데 **아무 에러도 안 난다** — 목록 표시조차 "전부 빠짐" 으로만 보여 원인이 안 보인다
def test_index_route_passes_the_shadow(mocker, wired):
    wired()
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")
    mocker.patch("backend.app.api.routes.documents.get_embedder", return_value=object())

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/documents/index", files=_files())

    assert response.status_code == 204
    assert indexer.call_args.kwargs["shadow"] is not None


# 그림자가 꺼져 있으면 안 넘긴다. **켜고 끄는 것이 불리언 하나**라는 계약이다
def test_index_route_omits_the_shadow_when_disabled(mocker, wired, monkeypatch):
    monkeypatch.setenv("SHADOW_INDEX_ENABLED", "false")
    wired()
    indexer = mocker.patch("backend.app.api.routes.documents.index_document")

    with TestClient(create_app()) as client:
        client.post("/api/v1/documents/index", files=_files())

    assert indexer.call_args.kwargs["shadow"] is None


# --- 2. 삭제가 두 컬렉션을 다 지운다 ---


# **짝이 맞아야 한다.** 색인이 둘을 함께 채우므로 삭제도 함께여야 하고, 안 그러면
# 화면에서 사라진 문서가 그림자에 남아 비교 집합이 갈린다
def test_delete_clears_both_collections(wired):
    primary = wired()

    with TestClient(create_app()) as client:
        response = client.delete("/api/v1/documents/메모.txt")

    assert response.status_code == 204
    assert primary.deleted == ["메모.txt"]
    assert primary.sibling.deleted == ["메모.txt"]


# --- 3. 구멍이 목록에 드러난다 ---


# **그림자 실패를 삼키는 대가로 반드시 있어야 하는 자리다.** 이 표시가 없으면
# 무엇을 다시 올려야 할지 알 방법이 없다 (채우는 방법 = 재업로드, 색인이 멱등이다)
def test_missing_shadow_is_marked_in_the_list(wired):
    wired(
        documents=[
            {"document_id": "가.pdf", "source_name": "가.pdf", "chunk_count": 3},
            {"document_id": "나.pdf", "source_name": "나.pdf", "chunk_count": 2},
        ],
        shadowed=[{"document_id": "가.pdf", "source_name": "가.pdf", "chunk_count": 3}],
    )

    with TestClient(create_app()) as client:
        body = client.get("/api/v1/documents").text

    # 그림자에 없는 '나.pdf' 만 표시가 붙는다
    assert body.count("비교색인 없음") == 1


# 둘 다 들어 있으면 아무 표시도 안 붙는다 — 정상 상태가 조용해야 표시가 신호가 된다
def test_no_marker_when_the_shadow_is_complete(wired):
    wired(documents=[{"document_id": "가.pdf", "source_name": "가.pdf", "chunk_count": 3}])

    with TestClient(create_app()) as client:
        body = client.get("/api/v1/documents").text

    assert "비교색인 없음" not in body


# **그림자 조회가 실패해도 목록은 보여야 한다.** 여기서 죽으면 비교용 부가 기능 때문에
# 화면의 본 기능이 죽는다 — 그림자 실패를 삼키기로 한 판단과 같은 기준이다
def test_list_survives_a_broken_shadow(wired, mocker):
    primary = wired(documents=[{"document_id": "가.pdf", "source_name": "가.pdf", "chunk_count": 3}])
    mocker.patch.object(primary.sibling, "list_documents", side_effect=RuntimeError("잠금"))

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert "가.pdf" in response.text
