"""RRF 병합. 점수가 아니라 **순위**만 쓴다는 것이 여기서 지켜야 할 전부다."""

from __future__ import annotations

import pytest

from rag_pipeline.retrieval.fusion import fuse
from rag_pipeline.schemas.vector import SearchResult, StoredChunk


def _dense(chunk_id: str, score: float) -> SearchResult:
    return SearchResult(
        id=f"uuid-{chunk_id}",
        score=score,
        text=f"{chunk_id} 본문",
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"source_name": "안내서.pdf"},
    )


def _sparse(chunk_id: str, score: float) -> tuple[StoredChunk, float]:
    return (
        StoredChunk(
            id=f"uuid-{chunk_id}",
            text=f"{chunk_id} 본문",
            document_id="안내서.pdf",
            chunk_id=chunk_id,
            metadata={"source_name": "안내서.pdf"},
        ),
        score,
    )


# 두 경로에 다 걸린 청크가 이긴다. 한 번씩만 걸린 것보다 기여가 두 배다
def test_both_paths_wins():
    chunks = fuse([_dense("a", 0.9), _dense("b", 0.8)], [_sparse("b", 4.2)], rrf_k=60, top_k=5)
    assert chunks[0].chunk_id == "b"


# **점수 크기가 아니라 순위만 본다.** BM25 점수가 dense 보다 수백 배 커도 순위가 같으면
# 기여가 같다 — 이것이 정규화 없이 두 단위를 섞을 수 있는 이유다
def test_score_magnitude_is_ignored():
    small = fuse([_dense("a", 0.9)], [_sparse("b", 0.001)], rrf_k=60, top_k=5)
    huge = fuse([_dense("a", 0.9)], [_sparse("b", 9999.0)], rrf_k=60, top_k=5)
    assert [c.fused_score for c in small] == [c.fused_score for c in huge]


# RRF 공식 자체를 못 박는다. rrf_k 를 바꾸면 순위 간 간격이 달라진다
def test_rrf_formula():
    chunks = fuse([_dense("a", 0.9), _dense("b", 0.5)], [], rrf_k=60, top_k=5)
    assert chunks[0].fused_score == pytest.approx(1 / 61)
    assert chunks[1].fused_score == pytest.approx(1 / 62)


# 한쪽 경로에만 걸린 후보의 반대쪽 점수는 **None 이다.** 0.0 을 채우면 "점수 0" 과
# "후보가 아니었음" 이 안 갈려, 나중에 "BM25 가 이걸 못 찾았다" 를 셀 수 없다
def test_missing_path_stays_none():
    by_id = {c.chunk_id: c for c in fuse([_dense("a", 0.9)], [_sparse("b", 4.2)], rrf_k=60, top_k=5)}

    assert by_id["a"].dense_score == pytest.approx(0.9)
    assert by_id["a"].bm25_score is None
    assert by_id["b"].bm25_score == pytest.approx(4.2)
    assert by_id["b"].dense_score is None


# 두 경로에 다 걸리면 양쪽 점수가 **둘 다** 남는다. 나중에 원인을 되짚는 근거다
def test_both_scores_kept():
    chunk = fuse([_dense("b", 0.8)], [_sparse("b", 4.2)], rrf_k=60, top_k=5)[0]
    assert chunk.dense_score == pytest.approx(0.8)
    assert chunk.bm25_score == pytest.approx(4.2)


# 동점은 dense 가 이긴다. dense 는 저장소가 매긴 점수를 갖고 있어 근거가 있고,
# 순서를 임의로 흔들 이유가 없다 (fusion.py docstring)
def test_tie_goes_to_dense():
    chunks = fuse([_dense("a", 0.9)], [_sparse("b", 4.2)], rrf_k=60, top_k=5)
    assert chunks[0].fused_score == pytest.approx(chunks[1].fused_score)
    assert [c.chunk_id for c in chunks] == ["a", "b"]


# 합류 열쇠는 point id 다. 같은 청크가 두 경로에서 와도 하나로 접힌다
def test_merged_by_point_id():
    chunks = fuse([_dense("a", 0.9)], [_sparse("a", 4.2)], rrf_k=60, top_k=5)
    assert len(chunks) == 1


# metadata 를 잃지 않는다 — BM25 에만 걸린 후보도 출처 인용을 만들 수 있어야 한다
def test_sparse_only_candidate_keeps_metadata():
    chunk = fuse([], [_sparse("b", 4.2)], rrf_k=60, top_k=5)[0]
    assert chunk.metadata["source_name"] == "안내서.pdf"
    assert chunk.text == "b 본문"


# top_k 로 자른다. 후보 폭(candidate_k)과 반환 수는 다른 값이다
def test_top_k_truncates():
    dense = [_dense(str(i), 1.0 - i / 100) for i in range(10)]
    assert len(fuse(dense, [], rrf_k=60, top_k=3)) == 3


# 정렬 기준은 늘 ranking_score 다. 리랭커가 없으면 fused_score 가 그 값이다
def test_ranking_score_follows_fused():
    chunks = fuse([_dense("a", 0.9), _dense("b", 0.5)], [], rrf_k=60, top_k=5)
    assert [c.ranking_score for c in chunks] == [c.fused_score for c in chunks]


# 양쪽이 다 비면 빈 목록이다. 예외가 아니다
def test_empty_inputs():
    assert fuse([], [], rrf_k=60, top_k=5) == []
