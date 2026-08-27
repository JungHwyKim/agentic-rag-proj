"""hybrid 검색 배선. 저장소·임베더·BM25 가 전부 가짜라 키 없이 돈다.

제일 중요한 것은 **`bm25=None` 이면 예전 dense 결과와 글자 그대로 같다** 는 것이다.
이게 깨지면 hybrid 비교의 기준선이 함께 움직여 무엇이 달라졌는지 못 가른다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.config import RetrievalConfig
from rag_pipeline.exceptions import IndexingError
from rag_pipeline.retrieval import build_bm25_index, retrieve, uses_bm25
from rag_pipeline.retrieval.bm25 import Bm25Index
from rag_pipeline.schemas.vector import SearchResult, StoredChunk


# 어떤 질의든 같은 벡터를 준다. 실제 값은 가짜 저장소가 안 보므로 상관없다
class FakeEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


# 받은 top_k 를 기록하고, 저장된 청크를 열거해 준다
class FakeStore:
    def __init__(self, results=None, stored=None, search_error=None, iter_error=None) -> None:
        self.results = results or []
        self.stored = stored or []
        self.search_error = search_error
        self.iter_error = iter_error
        self.top_ks: list[int] = []
        self.iter_calls = 0

    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        if self.search_error is not None:
            raise self.search_error
        self.top_ks.append(top_k)
        return self.results

    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]
        self.iter_calls += 1
        if self.iter_error is not None:
            raise self.iter_error
        return iter(self.stored)


def _stored(chunk_id: str, text: str) -> StoredChunk:
    return StoredChunk(
        id=f"uuid-{chunk_id}",
        text=text,
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"source_name": "안내서.pdf"},
    )


def _result(chunk_id: str, score: float, text: str = "본문") -> SearchResult:
    return SearchResult(
        id=f"uuid-{chunk_id}",
        score=score,
        text=text,
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"source_name": "안내서.pdf"},
    )


# ---------------------------------------------------------------- 동등성

# **이 파일에서 제일 중요한 테스트다.** bm25 를 안 넘기면 dense 한 갈래로 돌고,
# 결과가 저장소 점수를 그대로 단 예전 모양이어야 한다
def test_no_bm25_is_exactly_dense():
    store = FakeStore([_result("a", 0.82), _result("b", 0.71)])
    chunks = retrieve("위약금", store=store, embedder=FakeEmbedder(), top_k=5)

    assert [c.chunk_id for c in chunks] == ["a", "b"]
    assert [c.dense_score for c in chunks] == [pytest.approx(0.82), pytest.approx(0.71)]
    # RRF 를 안 태운다 — 경로가 하나면 순위 변환이 점수 간격을 잃기만 한다
    assert [c.fused_score for c in chunks] == [pytest.approx(0.82), pytest.approx(0.71)]
    assert all(c.bm25_score is None for c in chunks)


# 빈 인덱스는 없는 것과 같다. 색인 전에도 질의가 dense 로 살아 있어야 한다
def test_empty_bm25_falls_back_to_dense():
    store = FakeStore([_result("a", 0.82)])
    chunk = retrieve("질문", store=store, embedder=FakeEmbedder(), bm25=Bm25Index([]))[0]
    assert chunk.fused_score == pytest.approx(0.82)


# dense 만 돌 때는 후보를 넓히지 않는다. 넓혀 봐야 버리는데 저장소는 이미 훑는다
def test_dense_only_does_not_widen():
    store = FakeStore()
    retrieve("질문", store=store, embedder=FakeEmbedder(), top_k=5)
    assert store.top_ks == [5]


# ---------------------------------------------------------------- 합류

# 두 경로가 실제로 합쳐진다. BM25 에만 걸린 청크가 결과에 들어온다
def test_bm25_only_candidate_appears():
    store = FakeStore(
        results=[_result("a", 0.82)],
        stored=[_stored("a", "위약금 조항"), _stored("b", "손해배상 예정액")],
    )
    bm25 = build_bm25_index(store)
    chunks = retrieve("손해배상", store=store, embedder=FakeEmbedder(), top_k=5, bm25=bm25)

    ids = [c.chunk_id for c in chunks]
    assert "b" in ids
    by_id = {c.chunk_id: c for c in chunks}
    assert by_id["b"].bm25_score is not None
    assert by_id["b"].dense_score is None
    # BM25 에만 걸려도 출처 인용의 근거는 남아 있어야 한다
    assert by_id["b"].metadata["source_name"] == "안내서.pdf"


# BM25 를 쓸 때는 후보를 candidate_k 까지 넓힌다 — 미결이 적어둔 "후보 자체를 늘린다" 가 이것이다
def test_candidate_k_widens_dense_fetch():
    store = FakeStore(results=[_result("a", 0.8)], stored=[_stored("a", "위약금 조항")])
    bm25 = build_bm25_index(store)
    retrieve(
        "위약금",
        store=store,
        embedder=FakeEmbedder(),
        top_k=5,
        bm25=bm25,
        config=RetrievalConfig(candidate_k=37),
    )
    assert store.top_ks == [37]


# candidate_k 가 top_k 보다 작아도 top_k 만큼은 가져온다
def test_candidate_k_never_below_top_k():
    store = FakeStore(results=[_result("a", 0.8)], stored=[_stored("a", "위약금")])
    bm25 = build_bm25_index(store)
    retrieve(
        "위약금",
        store=store,
        embedder=FakeEmbedder(),
        top_k=20,
        bm25=bm25,
        config=RetrievalConfig(candidate_k=5),
    )
    assert store.top_ks == [20]


# ---------------------------------------------------------------- 실패 계약

# 열거가 죽으면 **예외가 아니라 빈 인덱스 + 경고**다. 여기서 던지면 색인 전 서버가
# 질의마다 죽는다 (bm25.py docstring)
def test_build_failure_becomes_warning():
    store = FakeStore(iter_error=IndexingError("저장소가 열리지 않습니다"))
    index = build_bm25_index(store)

    assert len(index) == 0
    assert len(index.warnings) == 1
    assert "dense" in index.warnings[0]


# BM25 검색이 죽으면 dense 로 내려가고 경고가 한 줄 남는다. 결과는 여전히 온다
def test_search_failure_falls_back_with_warning():
    store = FakeStore(results=[_result("a", 0.82)], stored=[_stored("a", "위약금 조항")])
    bm25 = build_bm25_index(store)

    def boom(query: str, top_k: int):
        raise RuntimeError("모델이 죽었다")

    bm25.search = boom
    chunks = retrieve("위약금", store=store, embedder=FakeEmbedder(), top_k=5, bm25=bm25)

    assert [c.chunk_id for c in chunks] == ["a"]
    assert chunks[0].fused_score == pytest.approx(0.82)
    assert len(bm25.warnings) == 1
    assert "dense" in bm25.warnings[0]


# **dense 의 예외는 안 삼킨다.** BM25 를 먼저 부르면 "컬렉션이 없다" 가 경고로 삼켜져,
# 색인을 한 적 없는 상태가 "BM25 만 실패했네" 로 보인다
def test_dense_error_propagates():
    store = FakeStore(
        stored=[_stored("a", "위약금 조항")],
        search_error=IndexingError("컬렉션이 없습니다"),
    )
    bm25 = build_bm25_index(store)

    with pytest.raises(IndexingError):
        retrieve("위약금", store=store, embedder=FakeEmbedder(), bm25=bm25)


# 빈 질의는 저장소도 임베더도 안 부른다. bm25 가 있어도 마찬가지다
@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_blank_query_short_circuits(query: str):
    store, embedder = FakeStore(), FakeEmbedder()
    assert retrieve(query, store=store, embedder=embedder, bm25=Bm25Index([])) == []
    assert store.top_ks == []
    assert embedder.queries == []


# ---------------------------------------------------------------- 인덱스 수명

# 인덱스는 한 번만 지어진다. 질의마다 저장소를 다시 훑으면 첫 질의가 색인만큼 무거워진다
def test_index_built_once_and_reused():
    store = FakeStore(results=[_result("a", 0.8)], stored=[_stored("a", "위약금 조항")])
    bm25 = build_bm25_index(store)
    assert store.iter_calls == 1

    for _ in range(3):
        retrieve("위약금", store=store, embedder=FakeEmbedder(), bm25=bm25)
    assert store.iter_calls == 1


# 토큰이 하나도 없는 청크는 색인에서 뺀다. 안 빼면 문서 길이 평균이 0 쪽으로 끌린다
def test_untokenizable_chunks_excluded():
    store = FakeStore(stored=[_stored("a", "위약금 조항"), _stored("b", "!!! ???")])
    assert len(build_bm25_index(store)) == 1


# ---------------------------------------------------------------- BM25 사용 판정


# **`uses_bm25()` 가 `retrieve()` 의 판정과 같아야 한다.** 답변 캐시의 파이프라인 지문이
# 이 함수를 보고 «BM25 갈래가 돌았나» 를 적는데, 판정이 두 벌이면 한쪽만 고쳐졌을 때
# 지문이 조용히 어긋나 낡은 답이 히트한다 (rag_pipeline/answer_cache/fingerprint.py)
@pytest.mark.parametrize("empty", [True, False])
def test_uses_bm25_matches_the_widening_decision(empty: bool):
    store = FakeStore(results=[_result("a", 0.8)], stored=[_stored("a", "위약금 조항")])
    bm25 = Bm25Index([]) if empty else build_bm25_index(store)

    retrieve(
        "위약금",
        store=store,
        embedder=FakeEmbedder(),
        top_k=5,
        bm25=bm25,
        config=RetrievalConfig(candidate_k=37),
    )

    widened = store.top_ks == [37]
    assert uses_bm25(bm25) is widened


# 인덱스를 안 넘기는 것이 곧 끄는 스위치다 — 그 자리에서도 판정이 같아야 한다
def test_uses_bm25_is_false_without_an_index():
    assert uses_bm25(None) is False
