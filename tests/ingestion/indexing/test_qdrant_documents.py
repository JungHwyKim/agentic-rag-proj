"""QdrantStore.list_documents(). 사이드바 문서 목록의 유일한 출처다.

**Protocol 에는 없는 메서드다** — 저장소 중립을 지키려고 구체 클래스에만 뒀다
(plan_log/backend.md). 그래서 여기서 계약을 못 박아 두지 않으면 아무도 안 지킨다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.exceptions import IndexingError
from rag_pipeline.ingestion.indexing import qdrant_store as qdrant_module
from rag_pipeline.ingestion.indexing.qdrant_store import QdrantStore
from rag_pipeline.schemas.vector import VectorRecord, point_id_for


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


# 청크 하나를 레코드로 만든다
def _record(document_id: str, index: int, source_name: str | None = None) -> VectorRecord:
    chunk_id = f"{document_id}:{index:04d}"
    metadata: dict[str, object] = {"chunk_index": index, "page": index + 1}
    if source_name is not None:
        metadata["source_name"] = source_name
    return VectorRecord(
        id=point_id_for(chunk_id),
        vector=[0.1, 0.2, 0.3],
        text=f"{document_id} 청크 {index}",
        document_id=document_id,
        chunk_id=chunk_id,
        metadata=metadata,
    )


# **컬렉션이 없으면 빈 리스트다.** search() 는 같은 상황에서 예외인데 여기는 아니다 —
# 첫 실행에서 사이드바가 비는 것은 정상이지 오류가 아니다
def test_missing_collection_returns_empty(store):
    assert store.list_documents() == []


# 문서당 한 줄로 접고 청크 수를 센다
def test_documents_folded_with_chunk_counts(store):
    store.ensure_collection(3)
    store.upsert(
        [
            _record("안내서.pdf", 0, "안내서.pdf"),
            _record("안내서.pdf", 1, "안내서.pdf"),
            _record("시행령.hwpx", 0, "시행령.hwpx"),
        ]
    )

    documents = store.list_documents()

    by_id = {doc["document_id"]: doc for doc in documents}
    assert set(by_id) == {"안내서.pdf", "시행령.hwpx"}
    assert by_id["안내서.pdf"]["chunk_count"] == 2
    assert by_id["시행령.hwpx"]["chunk_count"] == 1
    assert by_id["안내서.pdf"]["source_name"] == "안내서.pdf"


# source_name 이 없으면 document_id 로 떨어진다. 문서 키가 원본 파일명 기반(NFC)이라
# 사람이 읽을 수 있는 값이다 — 화면에 빈 칸이 뜨는 것보다 낫다
def test_source_name_falls_back_to_document_id(store):
    store.ensure_collection(3)
    store.upsert([_record("이름없음.pdf", 0, None)])

    assert store.list_documents()[0]["source_name"] == "이름없음.pdf"


# 배치 경계를 넘어도 다 센다. scroll 이 한 번에 _SCROLL_BATCH 개씩만 주기 때문에,
# 페이징을 안 돌면 큰 문서가 조용히 잘린 수로 나온다
def test_paging_covers_all_points(store, monkeypatch):
    monkeypatch.setattr(qdrant_module, "_SCROLL_BATCH", 2)
    store.ensure_collection(3)
    store.upsert([_record("큰문서.pdf", i, "큰문서.pdf") for i in range(5)])

    documents = store.list_documents()

    assert len(documents) == 1
    assert documents[0]["chunk_count"] == 5


# 재색인해도 문서가 두 벌로 안 늘어난다. point id 가 chunk_id 에서 파생돼 upsert 가 갱신이다
def test_reindex_does_not_duplicate(store):
    store.ensure_collection(3)
    records = [_record("안내서.pdf", 0, "안내서.pdf")]
    store.upsert(records)
    store.upsert(records)

    documents = store.list_documents()

    assert len(documents) == 1
    assert documents[0]["chunk_count"] == 1


# 저장소가 터지면 날것의 예외가 아니라 IndexingError 다 — 날것은 매핑표에 없어 500 이 된다
def test_scroll_failure_becomes_indexing_error(store, mocker):
    store.ensure_collection(3)
    store.upsert([_record("안내서.pdf", 0, "안내서.pdf")])
    mocker.patch.object(store._qdrant, "scroll", side_effect=RuntimeError("boom"))

    with pytest.raises(IndexingError):
        store.list_documents()
