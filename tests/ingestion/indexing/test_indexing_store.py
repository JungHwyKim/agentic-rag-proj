"""저장·삭제의 기본 동작과 실패 경로.

`upsert` 가 정말 **갱신**인지가 이 파일의 중심이다. insert 로 동작하면 재색인마다 청크가
불어나는데, 검색은 여전히 결과를 주므로 눈치채기 어렵다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.exceptions import IndexingError
from rag_pipeline.ingestion.indexing import qdrant_store, to_records


# 청크 여러 개를 레코드로. 축을 하나씩 다르게 줘서 나중에 순위를 손으로 계산할 수 있다
def _records(make_chunk, unit_vector, count, *, document_id="안내서.pdf", text="본문"):
    chunks = [
        make_chunk(index, f"{text} {index}", document_key=document_id) for index in range(count)
    ]
    vectors = [unit_vector(index) for index in range(count)]
    return to_records(chunks, vectors, document_id=document_id)


def test_single_record_round_trips(store, make_chunk, unit_vector):
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, 1))

    found = store.search(unit_vector(0), top_k=1)[0]

    assert found.chunk_id == "안내서.pdf:0000"
    assert found.document_id == "안내서.pdf"
    assert found.text == "본문 0"


def test_many_records_are_all_stored(store, make_chunk, unit_vector):
    store.ensure_collection(4)
    stored = store.upsert(_records(make_chunk, unit_vector, 4))

    assert stored == 4
    assert store.count() == 4


def test_batching_does_not_lose_records(store, indexing_config, make_chunk, unit_vector):
    """배치 경계에서 조용히 빠지는 것이 없어야 한다."""
    object.__setattr__(indexing_config, "upsert_batch_size", 1)
    store.ensure_collection(4)

    store.upsert(_records(make_chunk, unit_vector, 4))

    assert store.count() == 4


def test_same_id_updates_instead_of_duplicating(store, make_chunk, unit_vector):
    """이 파일의 중심. insert 로 동작하면 재색인마다 청크가 불어난다."""
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, 1, text="처음"))
    store.upsert(_records(make_chunk, unit_vector, 1, text="고친 뒤"))

    assert store.count() == 1
    assert store.search(unit_vector(0), top_k=1)[0].text == "고친 뒤 0"


def test_empty_upsert_is_a_no_op(store):
    store.ensure_collection(4)

    assert store.upsert([]) == 0
    assert store.count() == 0


def test_delete_by_ids(store, make_chunk, unit_vector):
    store.ensure_collection(4)
    records = _records(make_chunk, unit_vector, 3)
    store.upsert(records)

    store.delete([records[0].id])

    assert store.count() == 2


def test_deleting_an_unknown_id_is_not_an_error(store, make_chunk, unit_vector):
    store.ensure_collection(4)
    store.upsert(_records(make_chunk, unit_vector, 1))

    store.delete(["00000000-0000-5000-8000-999999999999"])

    assert store.count() == 1


def test_delete_before_any_collection_exists_is_silent(store):
    """첫 색인은 delete -> upsert 순서로 부른다. 여기서 막으면 정상 흐름이 죽는다."""
    store.delete_by_document_id("없는문서.pdf")

    assert store.count() == 0


def test_data_survives_close_and_reopen(open_store, make_chunk, unit_vector):
    first = open_store()
    first.ensure_collection(4)
    first.upsert(_records(make_chunk, unit_vector, 2))
    first.close()

    assert open_store().count() == 2


def test_close_is_safe_to_call_twice(store):
    store.ensure_collection(4)
    store.close()
    store.close()


# --- 실패 경로 ---


def test_storage_open_failure_becomes_indexing_error(mocker, store):
    """날것의 RuntimeError 는 backend 매핑표에 없어 500 이 된다."""
    mocker.patch.object(
        qdrant_store,
        "_make_client",
        side_effect=RuntimeError("Storage folder is already accessed"),
    )

    with pytest.raises(IndexingError, match="열지 못했습니다"):
        store.ensure_collection(4)


def test_upsert_failure_becomes_indexing_error(mocker, store, make_chunk, unit_vector):
    """저장이 실패했는데 조용히 넘어가면 그 청크는 영원히 검색되지 않는다."""

    # 저장만 고장난 클라이언트. _make_client() 를 갈아끼우는 것이 이 저장소의 모킹 이음새다
    class _BrokenOnUpsert:
        def collection_exists(self, *_a, **_k):
            return True

        def upsert(self, *_a, **_k):
            raise ValueError("boom")

        def close(self):
            pass

    mocker.patch.object(qdrant_store, "_make_client", return_value=_BrokenOnUpsert())
    records = _records(make_chunk, unit_vector, 1)

    with pytest.raises(IndexingError, match="저장하지 못했습니다"):
        store.upsert(records)
