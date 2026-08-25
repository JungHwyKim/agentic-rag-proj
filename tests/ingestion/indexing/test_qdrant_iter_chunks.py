"""QdrantStore.iter_chunks(). BM25 색인이 저장소를 훑는 유일한 경로다.

**Protocol 에 있는 메서드다** — `list_documents()` 와 갈리는 자리이고, 그래서 여기서
계약을 못 박는다. 소비자인 retriever 가 저장소 중립이라 구체 타입을 못 잡기 때문이다
(`ingestion/indexing/base.py` docstring).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.indexing.qdrant_store import QdrantStore
from rag_pipeline.schemas.vector import StoredChunk, VectorRecord, point_id_for


# 테스트 전용 저장 경로를 쓰는 store. 안 가르면 개발자의 실제 색인을 건드린다
@pytest.fixture
def store(tmp_path: Path):
    store = QdrantStore(
        IndexingConfig(qdrant_path=tmp_path / "qdrant"),
        EmbeddingConfig(openai_api_key=None),
    )
    yield store
    # 안 닫으면 같은 경로의 다음 테스트가 배타 잠금으로 죽는다
    store.close()


def _record(index: int) -> VectorRecord:
    chunk_id = f"안내서.pdf:{index:04d}"
    return VectorRecord(
        id=point_id_for(chunk_id),
        vector=[0.1, 0.2, 0.3],
        text=f"{index}번 청크 본문",
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"chunk_index": index, "source_name": "안내서.pdf"},
    )


# **컬렉션이 없으면 예외가 아니라 빈 이터레이터다.** search() 와 일부러 다르다 —
# backend 가 첫 질의에 이걸 부르는데 던지면 색인 전 서버가 질의마다 죽는다
def test_missing_collection_yields_nothing(store: QdrantStore):
    assert list(store.iter_chunks()) == []


# 넣은 청크가 그대로 나온다. 텍스트와 metadata 를 잃으면 BM25 도 출처 인용도 못 만든다
def test_round_trip(store: QdrantStore):
    store.ensure_collection(3)
    store.upsert([_record(0), _record(1)])

    chunks = sorted(store.iter_chunks(), key=lambda c: c.chunk_id)
    assert [c.chunk_id for c in chunks] == ["안내서.pdf:0000", "안내서.pdf:0001"]
    assert chunks[0].text == "0번 청크 본문"
    assert chunks[0].document_id == "안내서.pdf"
    assert chunks[0].metadata["source_name"] == "안내서.pdf"
    assert chunks[0].id == point_id_for("안내서.pdf:0000")


# 돌려주는 것은 StoredChunk 다 — **score 가 없다.** SearchResult 를 쓰면 열거에
# 0.0 을 지어내야 하고, 그러면 "점수 0" 과 "점수가 없음" 이 안 갈린다
def test_yields_stored_chunk_without_score(store: QdrantStore):
    store.ensure_collection(3)
    store.upsert([_record(0)])

    chunk = next(iter(store.iter_chunks()))
    assert isinstance(chunk, StoredChunk)
    assert not hasattr(chunk, "score")


# 한 배치(_SCROLL_BATCH=256)를 넘겨도 전부 나온다. 페이지 넘김이 실제로 도는지가 요점이다
def test_pages_past_one_scroll_batch(store: QdrantStore):
    store.ensure_collection(3)
    records = [_record(i) for i in range(300)]
    store.upsert(records)

    assert len({c.chunk_id for c in store.iter_chunks()}) == 300


# 빈 컬렉션은 빈 이터레이터다. "컬렉션이 없다" 와 결과가 같고, 둘 다 정상이다
def test_empty_collection(store: QdrantStore):
    store.ensure_collection(3)
    assert list(store.iter_chunks()) == []


# 문서를 지우면 열거에서도 사라진다 — BM25 를 질의 시점에 짓기로 한 근거가 이것이다.
# 색인 시점에 파일로 떨궈 두면 이 삭제가 저쪽에 안 반영돼 지운 청크가 계속 검색된다
def test_deleted_document_disappears(store: QdrantStore):
    store.ensure_collection(3)
    store.upsert([_record(0), _record(1)])
    store.delete_by_document_id("안내서.pdf")

    assert list(store.iter_chunks()) == []
