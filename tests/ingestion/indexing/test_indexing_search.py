"""검색 — 순위, 돌려주는 것, 그리고 경계 입력.

`SearchResult` 가 **score·text·metadata 를 전부** 실어야 하는 이유는 각각 다르다.
score 는 이후 threshold·rerank 가 붙을 자리이고, text 와 metadata 는 검색 결과에서 원본
청크를 되짚는 유일한 경로다. 셋 중 하나라도 빠지면 상위 계층이 저장소에 두 번 묻게 된다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.exceptions import IndexingError
from rag_pipeline.ingestion.indexing import to_records


# 축이 서로 다른 청크 넷. Dot 거리에서 어느 것이 1등인지 손으로 계산할 수 있다
def _seed(store, make_chunk, unit_vector, count=4):
    store.ensure_collection(4)
    chunks = [
        make_chunk(index, f"본문 {index}", page=index + 1, heading=f"제{index}장")
        for index in range(count)
    ]
    vectors = [unit_vector(index) for index in range(count)]
    store.upsert(to_records(chunks, vectors, document_id="안내서.pdf"))
    return chunks


def test_nearest_vector_comes_first(store, make_chunk, unit_vector):
    _seed(store, make_chunk, unit_vector)

    results = store.search(unit_vector(2), top_k=4)

    assert results[0].chunk_id == "안내서.pdf:0002"


def test_top_k_limits_the_result_count(store, make_chunk, unit_vector):
    _seed(store, make_chunk, unit_vector)

    assert len(store.search(unit_vector(0), top_k=2)) == 2


def test_scores_are_returned_and_ordered(store, make_chunk, unit_vector):
    """score 가 없으면 threshold 도 rerank 도 나중에 못 붙인다."""
    _seed(store, make_chunk, unit_vector)

    scores = [result.score for result in store.search(unit_vector(0), top_k=4)]

    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0)


def test_text_and_metadata_come_back(store, make_chunk, unit_vector):
    _seed(store, make_chunk, unit_vector)

    found = store.search(unit_vector(1), top_k=1)[0]

    assert found.text == "본문 1"
    assert found.document_id == "안내서.pdf"
    assert found.metadata["page"] == 2
    assert found.metadata["heading"] == "제1장"
    assert found.metadata["chunk_index"] == 1


def test_empty_collection_returns_no_results(store, unit_vector):
    """빈 컬렉션은 오류가 아니다 — 색인은 했는데 아직 아무것도 안 넣은 상태다."""
    store.ensure_collection(4)

    assert store.search(unit_vector(0), top_k=5) == []


def test_missing_collection_is_an_error(store, unit_vector):
    """'색인한 적이 없다'와 '질의가 안 맞는다'는 사용자가 할 일이 서로 다르다."""
    with pytest.raises(IndexingError, match="컬렉션이 없습니다"):
        store.search(unit_vector(0), top_k=5)


@pytest.mark.parametrize("top_k", [0, -1])
def test_bad_top_k_is_refused(store, make_chunk, unit_vector, top_k):
    _seed(store, make_chunk, unit_vector)

    with pytest.raises(IndexingError, match="top_k"):
        store.search(unit_vector(0), top_k=top_k)


def test_empty_query_vector_is_refused(store, make_chunk, unit_vector):
    _seed(store, make_chunk, unit_vector)

    with pytest.raises(IndexingError, match="질의 벡터"):
        store.search([], top_k=5)


def test_dimension_mismatch_in_query_is_an_error(store, make_chunk, unit_vector):
    """차원이 다른 질의는 조용히 이상한 순위를 주는 대신 실패해야 한다."""
    _seed(store, make_chunk, unit_vector)

    with pytest.raises(IndexingError):
        store.search(unit_vector(0, dim=8), top_k=5)


def test_top_k_larger_than_stored_is_fine(store, make_chunk, unit_vector):
    _seed(store, make_chunk, unit_vector, count=2)

    assert len(store.search(unit_vector(0), top_k=50)) == 2
