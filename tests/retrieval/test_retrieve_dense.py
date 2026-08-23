"""dense 검색 배선. 저장소·임베더는 가짜라 키 없이 돈다."""

from __future__ import annotations

import pytest

from rag_pipeline.retrieval import retrieve
from rag_pipeline.schemas.vector import SearchResult


# 호출 인자를 기록하는 가짜 임베더. usage 는 안 쓰므로 두지 않는다
class FakeEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


# 받은 top_k 를 기록하는 가짜 저장소
class FakeStore:
    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = results or []
        self.top_ks: list[int] = []
        self.vectors: list[list[float]] = []

    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        self.vectors.append(query_vector)
        self.top_ks.append(top_k)
        return self.results


def _result(chunk_id: str, score: float, **metadata: object) -> SearchResult:
    return SearchResult(
        id=f"uuid-{chunk_id}",
        score=score,
        text=f"{chunk_id} 본문",
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"source_name": "안내서.pdf", **metadata},
    )


# SearchResult 가 RetrievedChunk 로 승격되며 metadata 를 잃지 않는다 —
# 출처 인용의 유일한 근거라 여기서 빠지면 화면이 소스 태그를 못 그린다
def test_search_result_promoted_with_metadata():
    store = FakeStore([_result("안내서.pdf:0007", 0.82, page=4, heading_path=["Ⅱ", "세부"])])
    chunks = retrieve("위약금", store=store, embedder=FakeEmbedder())

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "안내서.pdf:0007"
    assert chunk.document_id == "안내서.pdf"
    assert chunk.text == "안내서.pdf:0007 본문"
    assert chunk.metadata["page"] == 4
    assert chunk.metadata["heading_path"] == ["Ⅱ", "세부"]


# 저장소 점수가 dense_score 로 실린다. 임계값 논의를 나중에 실측으로 하려면 이 값이 필요하다
def test_dense_score_carried():
    store = FakeStore([_result("a:0001", 0.73)])
    chunk = retrieve("질문", store=store, embedder=FakeEmbedder())[0]

    assert chunk.dense_score == pytest.approx(0.73)
    # 경로가 하나뿐이어도 채운다 — 정렬 기준이 늘 같은 자리에 있어야 한다
    assert chunk.fused_score == pytest.approx(0.73)
    assert chunk.ranking_score == pytest.approx(0.73)
    # dense 만 돌았으므로 BM25 는 "0점"이 아니라 "후보가 아님"이다
    assert chunk.bm25_score is None
    assert chunk.rerank_score is None


# top_k 를 그대로 저장소에 넘긴다. **run_eval 이 @10·@20 을 채점하는 근거다** —
# 기본값 5 가 실리면 그 두 축만 조용히 낮아진다
def test_top_k_passed_through():
    store = FakeStore()
    retrieve("질문", store=store, embedder=FakeEmbedder(), top_k=20)
    assert store.top_ks == [20]


# 기본값은 5 다. 바뀌면 baseline 재현이 깨지므로 못 박는다
def test_default_top_k_is_five():
    store = FakeStore()
    retrieve("질문", store=store, embedder=FakeEmbedder())
    assert store.top_ks == [5]


# 빈 질의는 저장소도 임베더도 안 부른다 — 빈 벡터로 검색하면 IndexingError 가 난다
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_blank_query_short_circuits(query: str):
    store, embedder = FakeStore(), FakeEmbedder()
    assert retrieve(query, store=store, embedder=embedder) == []
    assert store.top_ks == []
    assert embedder.queries == []


# 앞뒤 공백을 떼고 임베딩한다. 같은 질문이 캐시에서 갈리지 않게 한다
def test_query_is_stripped():
    embedder = FakeEmbedder()
    retrieve("  위약금 조항  ", store=FakeStore(), embedder=embedder)
    assert embedder.queries == ["위약금 조항"]
