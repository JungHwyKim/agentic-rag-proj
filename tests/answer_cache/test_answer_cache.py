"""답변 캐시. 히트·미스·임계값 경계·무효화·실패 계약을 본다.

qdrant 클라이언트를 가짜로 세운다 — 실제 저장소를 쓰면 이 파일이 재는 것이 캐시 논리가
아니라 qdrant 동작이 되고, 무효화 시나리오(근거가 그 사이 지워졌다)를 만들기가 어렵다.
가짜는 `QdrantStore.client` 를 통해서만 들어오므로 그 이음새가 곧 계약이다.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from rag_pipeline.answer_cache import get_answer_cache, qdrant_cache
from rag_pipeline.answer_cache.qdrant_cache import QdrantAnswerCache, cache_point_id_for
from rag_pipeline.config import AnswerCacheConfig, EmbeddingConfig, IndexingConfig, IngestConfig
from rag_pipeline.schemas.answer import Answer
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import StoredChunk, point_id_for

_VECTOR = [0.1, 0.2, 0.3]


# 검색으로 돌아온 청크 하나
def _chunk(chunk_id: str = "안내서.pdf:0001", text: str = "제12조 …") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="안내서.pdf",
        text=text,
        metadata={"source_name": "안내서.pdf", "page": 4},
        dense_score=0.9,
    )


# 근거가 달린 정상 답변
def _answer(query: str = "유예기간이 언제까지인가요?") -> Answer:
    return Answer(query=query, text="6개월입니다.", sources=[_chunk()])


# query_points() 가 돌려주는 point 한 건
class _Point:
    def __init__(self, payload: dict[str, Any], score: float, point_id: str = "p1") -> None:
        self.payload = payload
        self.score = score
        self.id = point_id


class _Response:
    def __init__(self, points: list[_Point]) -> None:
        self.points = points


# qdrant 클라이언트 자리. 부른 것을 기록해 두어 테스트가 확인한다
class _FakeClient:
    def __init__(self, *, exists: bool = True, point: _Point | None = None) -> None:
        self._exists = exists
        self._point = point
        self.upserted: list[Any] = []
        self.deleted: list[Any] = []
        self.created: list[str] = []

    def collection_exists(self, collection_name: str) -> bool:
        return self._exists

    def create_collection(self, collection_name: str, **_: Any) -> None:
        self._exists = True
        self.created.append(collection_name)

    def query_points(self, **_: Any) -> _Response:
        return _Response([self._point] if self._point is not None else [])

    def upsert(self, collection_name: str, points: list[Any]) -> None:
        self.upserted.extend(points)

    def delete(self, collection_name: str, points_selector: Any) -> None:
        self.deleted.append(points_selector)


# QdrantStore 자리. client 를 빌려주고 fetch_by_ids 로 무효화 판정에 답한다
class _FakeStore:
    def __init__(self, client: _FakeClient, chunks: list[StoredChunk] | None = None) -> None:
        self.client = client
        self._chunks = {chunk.id: chunk for chunk in (chunks or [])}
        self.fetch_calls: list[list[str]] = []

    def fetch_by_ids(self, ids: list[str]) -> list[StoredChunk]:
        self.fetch_calls.append(list(ids))
        return [self._chunks[i] for i in ids if i in self._chunks]


# 벡터를 늘 같은 값으로 돌려주는 embedder 자리
class _FakeEmbedder:
    def __init__(self, vector: list[float] | None = _VECTOR, error: Exception | None = None) -> None:
        self._vector = vector
        self._error = error
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        return list(self._vector or [])


# 저장된 근거 청크. 캐시 payload 의 해시와 맞아야 히트가 산다
def _stored(text: str = "제12조 …") -> StoredChunk:
    return StoredChunk(
        id=point_id_for("안내서.pdf:0001"),
        text=text,
        document_id="안내서.pdf",
        chunk_id="안내서.pdf:0001",
    )


# 캐시 하나를 세운다. threshold 기본 0.9
def _cache(store: Any, threshold: float = 0.9) -> QdrantAnswerCache:
    return QdrantAnswerCache(
        store,
        indexing=IndexingConfig(),
        embedding=EmbeddingConfig(),
        config=AnswerCacheConfig(),
        threshold=threshold,
    )


# 실제로 저장되는 payload 를 얻는다 — 히트 시나리오의 입력을 손으로 짓지 않기 위해서다
def _payload_of(answer: Answer) -> dict[str, Any]:
    client = _FakeClient(exists=True)
    cache = _cache(_FakeStore(client))
    cache.put(answer, embedder=_FakeEmbedder())
    return dict(client.upserted[0].payload)


# --- 이름과 신원 -------------------------------------------------------------


# **캐시 컬렉션은 문서 컬렉션과 이름이 달라야 한다.** 같으면 BM25 전량 훑기와 문서 목록에
# 캐시 항목이 문서로 섞여 든다
def test_collection_differs_from_documents():
    cache = _cache(_FakeStore(_FakeClient()))
    indexing = IndexingConfig()

    assert cache.collection.startswith("qa_cache")
    assert indexing.collection_name not in cache.collection.split("_")[0]


# 이름에 모델·차원이 박혀 있어야 모델을 갈아탄 뒤 옛 벡터에 새 질의를 재는 사고가 안 난다
def test_collection_carries_model_and_dimensions():
    cache = QdrantAnswerCache(
        _FakeStore(_FakeClient()),
        indexing=IndexingConfig(),
        embedding=EmbeddingConfig(embedding_model="text-embedding-3-large"),
        config=AnswerCacheConfig(),
        threshold=0.9,
    )

    assert "text-embedding-3-large" in cache.collection


# 같은 질문은 늘 같은 point 다 — 재질의가 중복이 아니라 갱신이어야 한다
def test_point_id_is_stable_across_spacing_and_nfc():
    base = cache_point_id_for("유예기간이 언제까지인가요?")

    assert cache_point_id_for("  유예기간이   언제까지인가요?  ") == base
    # 자모 분리된 같은 글자(NFD)도 같은 자리로 떨어져야 한다 — macOS 가 그 형태로 넘긴다.
    # **NFD 문자열이 원본과 실제로 다른지 먼저 확인한다.** 같으면 이 단언이 아무것도 안 잰다
    nfd = unicodedata.normalize("NFD", "유예기간이 언제까지인가요?")
    assert nfd != "유예기간이 언제까지인가요?"
    assert cache_point_id_for(nfd) == base


def test_point_id_differs_for_different_questions():
    assert cache_point_id_for("질문 하나") != cache_point_id_for("질문 둘")


# --- 저장 ---------------------------------------------------------------------


def test_put_stores_question_and_answer_in_one_point():
    client = _FakeClient(exists=True)
    cache = _cache(_FakeStore(client))

    assert cache.put(_answer(), embedder=_FakeEmbedder()) is True

    point = client.upserted[0]
    # **한 point 에 질문 벡터와 답이 함께 있다** — 갈라 두면 반쪽 저장이 생긴다
    assert point.vector == _VECTOR
    assert point.payload["answer"] == "6개월입니다."
    assert point.payload["query"] == "유예기간이 언제까지인가요?"
    assert point.id == cache_point_id_for("유예기간이 언제까지인가요?")


# **근거가 없는 답은 저장하지 않는다** — 무효화가 걸 축이 없어 영원히 낡지 않는다
def test_put_skips_answer_without_sources():
    client = _FakeClient(exists=True)
    cache = _cache(_FakeStore(client))

    stored = cache.put(
        Answer(query="아무거나", text="문서에서 찾지 못했습니다.", sources=[]),
        embedder=_FakeEmbedder(),
    )

    assert stored is False
    assert client.upserted == []


# 캐시에서 온 답을 다시 저장하면 배지의 "원래 질문"이 회를 거듭할수록 원본에서 멀어진다
def test_put_skips_answer_that_came_from_cache():
    client = _FakeClient(exists=True)
    cache = _cache(_FakeStore(client))
    hit = _cache(_FakeStore(_FakeClient(point=_Point(_payload_of(_answer()), score=0.95)),
                            [_stored()])).lookup("비슷한 질문", embedder=_FakeEmbedder())
    assert hit is not None

    assert cache.put(hit, embedder=_FakeEmbedder()) is False
    assert client.upserted == []


# 컬렉션이 없으면 첫 저장에서 만든다
def test_put_creates_collection_when_missing():
    client = _FakeClient(exists=False)
    cache = _cache(_FakeStore(client))

    cache.put(_answer(), embedder=_FakeEmbedder())

    assert client.created == [cache.collection]


# --- 조회와 임계값 -------------------------------------------------------------


def test_lookup_returns_cached_answer_with_origin():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.97)), [_stored()])
    cache = _cache(store)

    hit = cache.lookup("유예기간 언제까지죠?", embedder=_FakeEmbedder())

    assert hit is not None
    assert hit.text == "6개월입니다."
    # **query 는 지금 질문이고 원 질문은 cached_from 이다** — 화면이 둘을 나란히 보여준다
    assert hit.query == "유예기간 언제까지죠?"
    assert hit.cached_from is not None
    assert hit.cached_from.query == "유예기간이 언제까지인가요?"
    assert hit.cached_from.score == pytest.approx(0.97)
    # 인용을 잃지 않는다 — metadata 의 page 가 출처 표시의 유일한 근거다
    assert hit.sources[0].metadata["page"] == 4


# 임계값 **바로 아래**는 미스다. 경계를 재는 것이 이 기능의 유일한 위험을 재는 것이다
def test_lookup_misses_just_below_threshold():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.8999)), [_stored()])

    assert _cache(store, threshold=0.9).lookup("비슷한 질문", embedder=_FakeEmbedder()) is None


# 임계값과 **같으면** 히트다. `<` 로 판정한다는 계약을 잠근다
def test_lookup_hits_exactly_at_threshold():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.9)), [_stored()])

    assert _cache(store, threshold=0.9).lookup("비슷한 질문", embedder=_FakeEmbedder()) is not None


# 캐시 컬렉션이 아직 없는 것은 오류가 아니라 정상 첫 질의다
def test_lookup_on_missing_collection_is_a_miss():
    store = _FakeStore(_FakeClient(exists=False))

    assert _cache(store).lookup("첫 질문", embedder=_FakeEmbedder()) is None


def test_lookup_ignores_blank_query():
    embedder = _FakeEmbedder()

    assert _cache(_FakeStore(_FakeClient())).lookup("   ", embedder=embedder) is None
    # 빈 질문으로 임베딩을 부르면 EmbeddingError 가 난다. 그 앞에서 끊는다
    assert embedder.calls == []


# --- 무효화 -------------------------------------------------------------------


# 근거 청크가 지워졌으면 미스이고, 그 캐시 항목도 함께 지운다
def test_lookup_misses_when_source_chunk_is_gone():
    payload = _payload_of(_answer())
    client = _FakeClient(point=_Point(payload, score=0.99))
    store = _FakeStore(client, [])  # 근거가 저장소에 없다
    cache = _cache(store)

    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder()) is None
    assert client.deleted, "낡은 캐시 항목은 지워져야 한다"


# **id 는 그대로인데 내용만 바뀐 경우** — 재색인이 같은 point 를 덮어쓰면 이렇게 된다
def test_lookup_misses_when_source_text_changed():
    payload = _payload_of(_answer())
    client = _FakeClient(point=_Point(payload, score=0.99))
    store = _FakeStore(client, [_stored(text="제12조 (개정) …")])
    cache = _cache(store)

    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder()) is None
    assert client.deleted


# 무효화가 되짚는 열쇠는 point_id_for(chunk_id) 다 — RetrievedChunk 에 point id 가 없어서
# 저장 시점에 되만든다. 이 파생이 어긋나면 모든 히트가 조용히 미스가 된다
def test_invalidation_looks_up_derived_point_ids():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    _cache(store).lookup("비슷한 질문", embedder=_FakeEmbedder())

    assert store.fetch_calls == [[point_id_for("안내서.pdf:0001")]]


# 짝이 안 맞는 옛 형식 항목은 판정할 수 없다. **믿지 않는 쪽으로 떨어뜨린다**
def test_lookup_misses_on_payload_without_invalidation_keys():
    payload = _payload_of(_answer())
    payload.pop("source_hashes")
    client = _FakeClient(point=_Point(payload, score=0.99))

    assert _cache(_FakeStore(client, [_stored()])).lookup("질문", embedder=_FakeEmbedder()) is None
    assert client.deleted


def test_lookup_misses_when_created_at_is_missing():
    payload = _payload_of(_answer())
    payload.pop("created_at")
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    assert _cache(store).lookup("질문", embedder=_FakeEmbedder()) is None


def test_created_at_round_trips_as_datetime():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    hit = _cache(store).lookup("질문", embedder=_FakeEmbedder())

    assert hit is not None
    assert datetime.now(UTC) - hit.cached_from.created_at < timedelta(minutes=5)


# --- 실패 계약 ----------------------------------------------------------------


# **캐시 실패는 예외가 아니라 미스다.** 내려갈 층(정상 검색·생성)이 있기 때문이다
def test_store_failure_during_lookup_does_not_raise():
    class _Boom(_FakeClient):
        def query_points(self, **_: Any):
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom()))

    assert cache.lookup("질문", embedder=_FakeEmbedder()) is None
    assert cache.warnings


def test_store_failure_during_put_does_not_raise():
    class _Boom(_FakeClient):
        def upsert(self, collection_name: str, points: list[Any]) -> None:
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom()))

    assert cache.put(_answer(), embedder=_FakeEmbedder()) is False
    assert cache.warnings


def test_embedding_failure_does_not_raise():
    embedder = _FakeEmbedder(error=RuntimeError("키가 없다"))
    cache = _cache(_FakeStore(_FakeClient()))

    assert cache.lookup("질문", embedder=embedder) is None
    assert cache.put(_answer(), embedder=embedder) is False
    assert len(cache.warnings) == 2


def test_invalidation_failure_does_not_raise():
    class _BoomStore(_FakeStore):
        def fetch_by_ids(self, ids: list[str]) -> list[StoredChunk]:
            raise RuntimeError("저장소가 죽었다")

    payload = _payload_of(_answer())
    cache = _cache(_BoomStore(_FakeClient(point=_Point(payload, score=0.99))))

    assert cache.lookup("질문", embedder=_FakeEmbedder()) is None
    assert cache.warnings


# --- 팩토리 -------------------------------------------------------------------


# **임계값이 없으면 캐시가 통째로 꺼진다.** 근거 없는 수가 코드에 존재할 수 없는 모양이다
def test_get_answer_cache_returns_none_without_threshold():
    config = IngestConfig()
    object.__setattr__(config, "_answer_cache", AnswerCacheConfig(answer_cache_threshold=None))

    assert get_answer_cache(_FakeStore(_FakeClient()), config) is None


def test_get_answer_cache_builds_cache_when_threshold_is_set():
    config = IngestConfig()
    object.__setattr__(config, "_answer_cache", AnswerCacheConfig(answer_cache_threshold=0.93))

    cache = get_answer_cache(_FakeStore(_FakeClient()), config)

    assert cache is not None
    assert cache.threshold == pytest.approx(0.93)


# 캐시를 만드는 것만으로 저장소를 열면 backend lifespan 의 게으름이 깨진다
def test_building_cache_does_not_touch_the_client():
    class _Tripwire:
        @property
        def client(self):  # noqa: ANN202 - 접근하면 실패한다는 것이 이 자리의 전부다
            raise AssertionError("캐시를 만드는 것만으로 저장소를 열면 안 된다")

    config = IngestConfig()
    object.__setattr__(config, "_answer_cache", AnswerCacheConfig(answer_cache_threshold=0.9))

    assert get_answer_cache(_Tripwire(), config) is not None


# --- 경고 상한 ---------------------------------------------------------------


# **warnings 는 무한히 자라면 안 된다.** 이 객체는 backend lifespan 이 앱 수명 내내 들고
# 있어서, 저장소가 계속 실패하면 상한이 없을 때 메모리가 는다.
# Bm25Index 는 같은 이름인데 안 물린다 — 저쪽은 다시 지을 때마다 새 객체다
def test_warnings_stop_growing_at_the_cap():
    class _Boom(_FakeClient):
        def query_points(self, **_: Any):
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom()))
    for _ in range(qdrant_cache._MAX_WARNINGS + 50):
        cache.lookup("질문", embedder=_FakeEmbedder())

    assert len(cache.warnings) == qdrant_cache._MAX_WARNINGS


# **버리는 쪽은 앞이다.** 방금 실패를 읽으려고 있는 것이라 최근 것이 남아야 한다
def test_warnings_keep_the_most_recent():
    cache = _cache(_FakeStore(_FakeClient()))
    for i in range(qdrant_cache._MAX_WARNINGS + 5):
        cache._warn(f"경고 {i}")

    assert cache.warnings[-1] == f"경고 {qdrant_cache._MAX_WARNINGS + 4}"
    assert "경고 0" not in cache.warnings


# --- clear() ------------------------------------------------------------------


# 새 문서가 색인되면 통째로 비운다. **근거 대조로는 원리적으로 못 잡는 자리**라서다 —
# 옛 답의 근거는 멀쩡한데 새 문서에 더 나은 답이 있을 수 있다
def test_clear_deletes_the_cache_collection():
    class _Deletable(_FakeClient):
        def __init__(self, **kw: Any) -> None:
            super().__init__(**kw)
            self.dropped: list[str] = []

        def delete_collection(self, collection_name: str) -> None:
            self.dropped.append(collection_name)
            self._exists = False

    client = _Deletable(exists=True)
    cache = _cache(_FakeStore(client))

    assert cache.clear() is True
    assert client.dropped == [cache.collection]


# 컬렉션이 없으면 이미 비어 있는 것이다. 만들었다 지우지 않는다
def test_clear_on_missing_collection_is_a_noop():
    client = _FakeClient(exists=False)

    assert _cache(_FakeStore(client)).clear() is True
    assert client.created == []


# **비운 뒤에는 미스여야 한다.** 이것이 clear() 가 실제로 맡은 일이다
def test_lookup_after_clear_is_a_miss():
    payload = _payload_of(_answer())
    client = _FakeClient(point=_Point(payload, score=0.99))
    cache = _cache(_FakeStore(client, [_stored()]))
    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder()) is not None

    client._exists = False  # delete_collection 뒤의 상태
    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder()) is None


# **청소 실패가 색인을 막으면 안 된다.** 이 모듈의 실패 계약 그대로 경고 + False 다
def test_clear_failure_does_not_raise():
    class _Boom(_FakeClient):
        def delete_collection(self, collection_name: str) -> None:
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom(exists=True)))

    assert cache.clear() is False
    assert cache.warnings
