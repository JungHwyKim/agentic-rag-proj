"""답변 캐시. 히트·미스·임계값 경계·무효화·실패 계약을 본다.

qdrant 클라이언트를 가짜로 세운다 — 실제 저장소를 쓰면 이 파일이 재는 것이 캐시 논리가
아니라 qdrant 동작이 되고, 무효화 시나리오(근거가 그 사이 지워졌다)를 만들기가 어렵다.
가짜는 `QdrantStore.client` 를 통해서만 들어오므로 그 이음새가 곧 계약이다.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from rag_pipeline.answer_cache import get_answer_cache, pipeline_fingerprint, qdrant_cache
from rag_pipeline.answer_cache.qdrant_cache import QdrantAnswerCache, cache_point_id_for
from rag_pipeline.config import (
    AnswerCacheConfig,
    EmbeddingConfig,
    GenerationConfig,
    IndexingConfig,
    IngestConfig,
    RerankConfig,
    RetrievalConfig,
    RewriteConfig,
)
from rag_pipeline.schemas.answer import Answer
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import StoredChunk, point_id_for

_VECTOR = [0.1, 0.2, 0.3]

# 이 파일이 쓰는 기본 지문. **값이 무엇인지는 안 중요하다** — 저장과 조회가 같은 값을
# 쓰는지, 다르면 미스가 나는지가 여기서 재는 것이다. 지문을 *만드는* 쪽은 아래
# 「파이프라인 지문」 절이 pipeline_fingerprint() 로 직접 잰다
_FP = "v1|bm25=0|top_k=5|테스트"


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
    def __init__(
        self, *, exists: bool = True, point: _Point | None = None, dimensions: int = len(_VECTOR)
    ) -> None:
        self._exists = exists
        self._point = point
        self._dimensions = dimensions
        self.upserted: list[Any] = []
        self.deleted: list[Any] = []
        self.created: list[str] = []

    def collection_exists(self, collection_name: str) -> bool:
        return self._exists

    def create_collection(self, collection_name: str, **_: Any) -> None:
        self._exists = True
        self.created.append(collection_name)

    # 기존 컬렉션의 차원. **`QdrantStore.ensure_collection()` 과 같은 모양으로 중첩한다** —
    # 평평하게 만들면 실제 클라이언트가 바뀌었을 때 이 가짜가 그것을 안 알려준다
    def get_collection(self, collection_name: str) -> Any:
        vectors = type("_Vectors", (), {"size": self._dimensions})()
        params = type("_Params", (), {"vectors": vectors})()
        return type("_Info", (), {"config": type("_Config", (), {"params": params})()})()

    def query_points(self, **_: Any) -> _Response:
        return _Response([self._point] if self._point is not None else [])

    # exact 갈래가 쓰는 id 조회. **id 가 맞을 때만 돌려주는 것이 이 가짜의 요점이다** —
    # query_points 처럼 무조건 주면 "다른 문자열은 미스"라는 계약을 못 잰다.
    # 실제 클라이언트와 마찬가지로 점수 필드가 없는 레코드를 흉내 내지 않고 같은 객체를 준다
    def retrieve(self, collection_name: str, ids: list[Any], **_: Any) -> list[_Point]:
        if self._point is None or str(self._point.id) not in {str(i) for i in ids}:
            return []
        return [self._point]

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


# 캐시 하나를 세운다. threshold 기본 0.9 · 갈래 기본 semantic
def _cache(
    store: Any, threshold: float | None = 0.9, mode: str = "semantic"
) -> QdrantAnswerCache:
    return QdrantAnswerCache(
        store,
        indexing=IndexingConfig(),
        embedding=EmbeddingConfig(),
        config=AnswerCacheConfig(),
        mode=mode,
        threshold=threshold,
    )


# 실제로 저장되는 payload 를 얻는다 — 히트 시나리오의 입력을 손으로 짓지 않기 위해서다
def _payload_of(answer: Answer) -> dict[str, Any]:
    client = _FakeClient(exists=True)
    cache = _cache(_FakeStore(client))
    cache.put(answer, embedder=_FakeEmbedder(), fingerprint=_FP)
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

    assert cache.put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP) is True

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
        embedder=_FakeEmbedder(), fingerprint=_FP,
    )

    assert stored is False
    assert client.upserted == []


# 캐시에서 온 답을 다시 저장하면 배지의 "원래 질문"이 회를 거듭할수록 원본에서 멀어진다
def test_put_skips_answer_that_came_from_cache():
    client = _FakeClient(exists=True)
    cache = _cache(_FakeStore(client))
    hit = _cache(_FakeStore(_FakeClient(point=_Point(_payload_of(_answer()), score=0.95)),
                            [_stored()])).lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP)
    assert hit is not None

    assert cache.put(hit, embedder=_FakeEmbedder(), fingerprint=_FP) is False
    assert client.upserted == []


# 컬렉션이 없으면 첫 저장에서 만든다
def test_put_creates_collection_when_missing():
    client = _FakeClient(exists=False)
    cache = _cache(_FakeStore(client))

    cache.put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP)

    assert client.created == [cache.collection]


# --- 조회와 임계값 -------------------------------------------------------------


def test_lookup_returns_cached_answer_with_origin():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.97)), [_stored()])
    cache = _cache(store)

    hit = cache.lookup("유예기간 언제까지죠?", embedder=_FakeEmbedder(), fingerprint=_FP)

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

    assert _cache(store, threshold=0.9).lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None


# 임계값과 **같으면** 히트다. `<` 로 판정한다는 계약을 잠근다
def test_lookup_hits_exactly_at_threshold():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.9)), [_stored()])

    assert _cache(store, threshold=0.9).lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is not None


# 캐시 컬렉션이 아직 없는 것은 오류가 아니라 정상 첫 질의다
def test_lookup_on_missing_collection_is_a_miss():
    store = _FakeStore(_FakeClient(exists=False))

    assert _cache(store).lookup("첫 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None


def test_lookup_ignores_blank_query():
    embedder = _FakeEmbedder()

    assert _cache(_FakeStore(_FakeClient())).lookup("   ", embedder=embedder, fingerprint=_FP) is None
    # 빈 질문으로 임베딩을 부르면 EmbeddingError 가 난다. 그 앞에서 끊는다
    assert embedder.calls == []


# --- 무효화 -------------------------------------------------------------------


# 근거 청크가 지워졌으면 미스이고, 그 캐시 항목도 함께 지운다
def test_lookup_misses_when_source_chunk_is_gone():
    payload = _payload_of(_answer())
    client = _FakeClient(point=_Point(payload, score=0.99))
    store = _FakeStore(client, [])  # 근거가 저장소에 없다
    cache = _cache(store)

    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None
    assert client.deleted, "낡은 캐시 항목은 지워져야 한다"


# **id 는 그대로인데 내용만 바뀐 경우** — 재색인이 같은 point 를 덮어쓰면 이렇게 된다
def test_lookup_misses_when_source_text_changed():
    payload = _payload_of(_answer())
    client = _FakeClient(point=_Point(payload, score=0.99))
    store = _FakeStore(client, [_stored(text="제12조 (개정) …")])
    cache = _cache(store)

    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None
    assert client.deleted


# 무효화가 되짚는 열쇠는 point_id_for(chunk_id) 다 — RetrievedChunk 에 point id 가 없어서
# 저장 시점에 되만든다. 이 파생이 어긋나면 모든 히트가 조용히 미스가 된다
def test_invalidation_looks_up_derived_point_ids():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    _cache(store).lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP)

    assert store.fetch_calls == [[point_id_for("안내서.pdf:0001")]]


# 짝이 안 맞는 옛 형식 항목은 판정할 수 없다. **믿지 않는 쪽으로 떨어뜨린다**
def test_lookup_misses_on_payload_without_invalidation_keys():
    payload = _payload_of(_answer())
    payload.pop("source_hashes")
    client = _FakeClient(point=_Point(payload, score=0.99))

    assert _cache(_FakeStore(client, [_stored()])).lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None
    assert client.deleted


def test_lookup_misses_when_created_at_is_missing():
    payload = _payload_of(_answer())
    payload.pop("created_at")
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    assert _cache(store).lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None


def test_created_at_round_trips_as_datetime():
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    hit = _cache(store).lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP)

    assert hit is not None
    assert datetime.now(UTC) - hit.cached_from.created_at < timedelta(minutes=5)


# --- 실패 계약 ----------------------------------------------------------------


# **캐시 실패는 예외가 아니라 미스다.** 내려갈 층(정상 검색·생성)이 있기 때문이다
def test_store_failure_during_lookup_does_not_raise():
    class _Boom(_FakeClient):
        def query_points(self, **_: Any):
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom()))

    assert cache.lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None
    assert cache.warnings


def test_store_failure_during_put_does_not_raise():
    class _Boom(_FakeClient):
        def upsert(self, collection_name: str, points: list[Any]) -> None:
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom()))

    assert cache.put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP) is False
    assert cache.warnings


def test_embedding_failure_does_not_raise():
    embedder = _FakeEmbedder(error=RuntimeError("키가 없다"))
    cache = _cache(_FakeStore(_FakeClient()))

    assert cache.lookup("질문", embedder=embedder, fingerprint=_FP) is None
    assert cache.put(_answer(), embedder=embedder, fingerprint=_FP) is False
    assert len(cache.warnings) == 2


def test_invalidation_failure_does_not_raise():
    class _BoomStore(_FakeStore):
        def fetch_by_ids(self, ids: list[str]) -> list[StoredChunk]:
            raise RuntimeError("저장소가 죽었다")

    payload = _payload_of(_answer())
    cache = _cache(_BoomStore(_FakeClient(point=_Point(payload, score=0.99))))

    assert cache.lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None
    assert cache.warnings


# --- 팩토리 -------------------------------------------------------------------


# **임계값이 없으면 캐시가 통째로 꺼진다.** 근거 없는 수가 코드에 존재할 수 없는 모양이다.
#
# **`answer_cache_mode` 를 함께 못 박는 것이 이 단언의 뜻을 지킨다** — 꺼짐 조건이 갈래마다
# 달라(`exact` 는 임계값을 아예 안 본다) 모드를 안 박으면 이 테스트가 재는 것이 개발자 `.env`
# 에 딸린다. **실제로 밟았다**(2026-08-28): `.env` 에 `ANSWER_CACHE_MODE=exact` 를 적은
# 순간 이 파일이 빨개졌고, 그때까지 통과하던 근거는 코드가 아니라 `.env` 의 침묵이었다
def test_get_answer_cache_returns_none_without_threshold():
    config = IngestConfig()
    object.__setattr__(
        config,
        "_answer_cache",
        AnswerCacheConfig(answer_cache_mode="semantic", answer_cache_threshold=None),
    )

    assert get_answer_cache(_FakeStore(_FakeClient()), config) is None


# 모드를 박는 이유는 위와 같다 — 재는 것이 **semantic 갈래의 진리표**다
def test_get_answer_cache_builds_cache_when_threshold_is_set():
    config = IngestConfig()
    object.__setattr__(
        config,
        "_answer_cache",
        AnswerCacheConfig(answer_cache_mode="semantic", answer_cache_threshold=0.93),
    )

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
        cache.lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP)

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
    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is not None

    client._exists = False  # delete_collection 뒤의 상태
    assert cache.lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None


# **청소 실패가 색인을 막으면 안 된다.** 이 모듈의 실패 계약 그대로 경고 + False 다
def test_clear_failure_does_not_raise():
    class _Boom(_FakeClient):
        def delete_collection(self, collection_name: str) -> None:
            raise RuntimeError("저장소가 죽었다")

    cache = _cache(_FakeStore(_Boom(exists=True)))

    assert cache.clear() is False
    assert cache.warnings


# --- exact 갈래 --------------------------------------------------------------
#
# 임계값이 아니라 **정규화한 문자열**로 판정하는 갈래다. 여기서 재는 것은 넷이다 —
# 임베딩을 안 부른다 / 표기 차이를 흡수한다 / 다른 문자열은 아무리 비슷해도 미스다 /
# 무효화는 그대로 문다. 근거는 plan_log/시맨틱-캐시.md 6회차.


# **이 갈래의 존재 이유가 이것이다** — 조회에 임베딩이 안 든다. 수가 아니라 계약이라
# 호출 횟수를 직접 센다
def test_exact_mode_looks_up_without_embedding() -> None:
    query = "유예기간이 언제까지인가요?"
    point = _Point(_payload_of(_answer(query)), score=0.0, point_id=cache_point_id_for(query))
    store = _FakeStore(_FakeClient(point=point), chunks=[_stored()])
    embedder = _FakeEmbedder()

    hit = _cache(store, threshold=None, mode="exact").lookup(query, embedder=embedder, fingerprint=_FP)

    assert hit is not None
    assert embedder.calls == []


# 눈에 같은 질문은 같은 자리다. `cache_point_id_for()` 가 NFC + 공백 정규화라 표기 차이를
# 흡수한다 — 사용자가 같은 질문을 두 번 칠 때 띄어쓰기까지 같으리라 기대할 수 없다
def test_exact_mode_absorbs_spacing_and_nfc() -> None:
    query = "유예기간이 언제까지인가요?"
    point = _Point(_payload_of(_answer(query)), score=0.0, point_id=cache_point_id_for(query))
    store = _FakeStore(_FakeClient(point=point), chunks=[_stored()])

    again = unicodedata.normalize("NFD", "  유예기간이   언제까지인가요?  ")
    hit = _cache(store, threshold=None, mode="exact").lookup(again, embedder=_FakeEmbedder(), fingerprint=_FP)

    assert hit is not None
    assert hit.cached_from is not None
    assert hit.cached_from.query == query


# **완전 일치의 정의이자 시맨틱과 갈리는 지점이다.** 뜻이 같아도 문자열이 다르면 미스다 —
# 이 갈래가 "틀릴 수가 없는" 근거가 바로 이것이고, 동시에 1회차가 기각했던 사유이기도 하다
def test_exact_mode_misses_a_paraphrase() -> None:
    stored_query = "유예기간이 언제까지인가요?"
    point = _Point(
        _payload_of(_answer(stored_query)), score=0.99, point_id=cache_point_id_for(stored_query)
    )
    store = _FakeStore(_FakeClient(point=point), chunks=[_stored()])
    embedder = _FakeEmbedder()

    miss = _cache(store, threshold=None, mode="exact").lookup(
        "유예 기간은 언제까지죠?", embedder=embedder, fingerprint=_FP
    )

    assert miss is None
    assert embedder.calls == []


# 점수는 1.0 이다. 같은 정규화 문자열이면 같은 벡터이고 이 컬렉션의 벡터는 단위 길이라,
# 화면 배지가 보여주는 유사도가 거짓이 아니다
def test_exact_mode_reports_similarity_one() -> None:
    query = "유예기간이 언제까지인가요?"
    point = _Point(_payload_of(_answer(query)), score=0.0, point_id=cache_point_id_for(query))
    store = _FakeStore(_FakeClient(point=point), chunks=[_stored()])

    hit = _cache(store, threshold=None, mode="exact").lookup(query, embedder=_FakeEmbedder(), fingerprint=_FP)

    assert hit is not None
    assert hit.cached_from is not None
    assert hit.cached_from.score == pytest.approx(1.0)


# **무효화는 갈래를 안 탄다.** 근거 청크가 사라졌으면 문자열이 같아도 미스이고 항목이 지워진다 —
# 낡은 답을 주는 것이 이 기능의 위험이라, 판정 방식이 바뀌었다고 그 방어가 빠지면 안 된다
def test_exact_mode_still_drops_stale_entries() -> None:
    query = "유예기간이 언제까지인가요?"
    point = _Point(_payload_of(_answer(query)), score=0.0, point_id=cache_point_id_for(query))
    client = _FakeClient(point=point)
    store = _FakeStore(client, chunks=[])  # 근거가 저장소에서 사라졌다

    miss = _cache(store, threshold=None, mode="exact").lookup(query, embedder=_FakeEmbedder(), fingerprint=_FP)

    assert miss is None
    assert client.deleted


# **저장은 두 갈래가 똑같고 진짜 벡터를 넣는다.** 더미를 넣으면 나중에 semantic 으로 돌렸을 때
# 조용히 오염된 캐시를 검색하게 된다 — 컬렉션 이름이 모델 교체는 막지만 이건 못 막는다
def test_exact_mode_still_stores_a_real_vector() -> None:
    client = _FakeClient(exists=True)
    embedder = _FakeEmbedder()

    assert _cache(_FakeStore(client), threshold=None, mode="exact").put(
        _answer(), embedder=embedder, fingerprint=_FP
    )
    assert list(client.upserted[0].vector) == _VECTOR
    assert embedder.calls == ["유예기간이 언제까지인가요?"]


# --- 켜짐 진리표 -------------------------------------------------------------


# 저장소 커밋본의 조합이다. **모드를 박지 않으면 이 테스트가 `.env` 를 잰다** — 이름은
# `semantic` 이라고 말하는데 실제로 무엇을 쟀는지는 그 머신의 `.env` 가 정하게 된다
def test_semantic_without_threshold_stays_off() -> None:
    config = IngestConfig()
    object.__setattr__(
        config,
        "_answer_cache",
        AnswerCacheConfig(answer_cache_mode="semantic", answer_cache_threshold=None),
    )

    assert get_answer_cache(_FakeStore(_FakeClient()), config) is None


# **`exact` 는 임계값 없이 켜진다** — 이 축에는 정할 값이 없기 때문이다. 임계값을 요구하면
# 근거 없는 수를 만들어야 하는데, 그것이 1회차가 기본값 자체를 없앤 이유였다
def test_exact_turns_on_without_a_threshold() -> None:
    config = IngestConfig()
    object.__setattr__(
        config,
        "_answer_cache",
        AnswerCacheConfig(answer_cache_mode="exact", answer_cache_threshold=None),
    )

    cache = get_answer_cache(_FakeStore(_FakeClient()), config)

    assert cache is not None
    assert cache.mode == "exact"
    assert cache.threshold is None


# --- 파이프라인 지문 -----------------------------------------------------------


# 리랭커 설정 하나. **키를 명시로 지우는 것이 `.env` 차단이다** — 각 설정 객체가 `.env` 를
# 직접 읽어서, 안 꽂으면 개발자 머신의 `RERANK_PROVIDER` 가 지문에 실려 이 파일이 머신마다
# 다른 값을 잰다(offline_config 가 실키를 지우는 것과 같은 이유다)
def _rerank_config(**overrides: Any) -> RerankConfig:
    values: dict[str, Any] = {
        "rerank_provider": "local",
        "rerank_model": "cross-encoder",
        "rerank_device": "cpu",
        "openai_api_key": None,
        "cohere_api_key": None,
    }
    values.update(overrides)
    return RerankConfig(**values)


# 재작성 설정 하나. 같은 이유로 `.env` 를 못 타게 못 박는다
def _rewrite_config(**overrides: Any) -> RewriteConfig:
    values: dict[str, Any] = {
        "rewrite_model": "gpt-4.1-mini",
        "rewrite_mode": "fuse",
        "openai_api_key": None,
    }
    values.update(overrides)
    return RewriteConfig(**values)


# 생성 설정 하나
def _generation_config(**overrides: Any) -> GenerationConfig:
    values: dict[str, Any] = {
        "generation_model": "gpt-4.1-mini",
        "generation_temperature": 0.2,
        "generation_top_k": 5,
        "openai_api_key": None,
    }
    values.update(overrides)
    return GenerationConfig(**values)


# 지문을 만들 설정 하나. 하위 설정을 전부 손으로 꽂는다
def _fp_config(**parts: Any) -> IngestConfig:
    defaults: dict[str, Any] = {
        "_retrieval": RetrievalConfig(candidate_k=50, rrf_k=60),
        "_rerank": _rerank_config(),
        "_rewrite": _rewrite_config(),
        "_generation": _generation_config(),
    }
    defaults.update(parts)
    config = IngestConfig()
    for name, value in defaults.items():
        object.__setattr__(config, name, value)
    return config


# 지문 하나. 인자를 안 주면 세 단계가 다 켜진 조합이다
def _fp(config: IngestConfig | None = None, **flags: Any) -> str:
    settings: dict[str, Any] = {
        "use_bm25": True,
        "reranker_on": True,
        "rewriter_on": True,
        "top_k": 5,
    }
    settings.update(flags)
    return pipeline_fingerprint(config or _fp_config(), **settings)


# 같은 설정은 같은 지문이다. **안 그러면 방금 저장한 답이 다음 질의에서 자기 지문과 안 맞아
# 영원히 미스가 나는데 에러가 안 난다** — 캐시가 켜져 있는데 아무 일도 안 하는 상태다
def test_fingerprint_is_stable_for_the_same_settings() -> None:
    assert _fp() == _fp()


# 출력을 가르는 설정은 전부 지문을 갈라야 한다. **빠뜨리면 낡은 순서를 조용히 서빙한다**
@pytest.mark.parametrize(
    ("label", "parts"),
    [
        ("candidate_k", {"_retrieval": RetrievalConfig(candidate_k=30, rrf_k=60)}),
        ("rrf_k", {"_retrieval": RetrievalConfig(candidate_k=50, rrf_k=10)}),
        ("rerank_provider", {"_rerank": _rerank_config(rerank_provider="cohere")}),
        ("rerank_model", {"_rerank": _rerank_config(rerank_model="rerank-v3.5")}),
        ("rerank_device", {"_rerank": _rerank_config(rerank_device="cuda")}),
        ("rewrite_model", {"_rewrite": _rewrite_config(rewrite_model="gpt-4.1")}),
        ("generation_model", {"_generation": _generation_config(generation_model="gpt-4.1")}),
        ("generation_temp", {"_generation": _generation_config(generation_temperature=0.7)}),
    ],
)
def test_fingerprint_splits_on_output_deciding_settings(label: str, parts: dict[str, Any]) -> None:
    assert _fp(_fp_config(**parts)) != _fp(), label


# **`rewrite_mode` 는 지문을 가른다 — `retrieval/rewrite.py` 의 캐시 salt 와 반대다.**
# 잣대는 같다(*출력을 가르는 것만 넣는다*): 재작성 **호출** 은 `(질의, 1차 pool)` 만 보고
# mode 는 그 뒤에서 갈리므로 저쪽 출력을 안 가르지만, 여기가 가리는 것은 *검색 결과와 답
# 본문* 이라 `fuse`/`replace` 가 실제로 가른다.
# 짝은 `tests/retrieval/test_rewrite.py::test_mode_does_not_split_the_cache` 다 —
# 둘을 「통일하자」로 뭉개면 한쪽이 반드시 틀린다
def test_fingerprint_splits_on_rewrite_mode() -> None:
    replaced = _fp_config(_rewrite=_rewrite_config(rewrite_mode="replace"))

    assert _fp(replaced) != _fp()


# 단계를 켜고 끄는 것 자체도 갈라야 한다 — 리랭커를 껐는데 옛 순서가 계속 나오면 안 된다
@pytest.mark.parametrize("flag", ["use_bm25", "reranker_on", "rewriter_on"])
def test_fingerprint_splits_on_each_stage_switch(flag: str) -> None:
    assert _fp(**{flag: False}) != _fp(), flag


def test_fingerprint_splits_on_top_k() -> None:
    assert _fp(top_k=10) != _fp()


# **재시도 루프가 꺼진 지문은 이 인자가 생기기 전과 글자 그대로 같아야 한다.**
# 여기가 이 축에서 제일 조용한 자리다 — 조각을 늘 붙이면 `.qdrant/qa_cache_*` 의 기존 항목이
# **전부 한 번에 죽고**, 그러면 `exact` 캐시의 «50줄에서 5% 미만이면 끈다» 표본이 미스 쪽으로
# 밀린다(`evaluation/README.md` 8절). 에러가 안 나고 **수치로만** 드러나는 종류다.
#
# 인자를 아예 안 준 것과도 같아야 한다 — 옛 호출부(`answer_question()`)가 그대로 산다
def test_fingerprint_is_unchanged_when_the_loop_is_off() -> None:
    assert _fp(max_retries=0) == _fp()


# 반대쪽. 켜지면 실제로 파이프라인이 다르므로(검색이 여러 번 돈다) 지문이 갈려야 한다 —
# 안 갈리면 **루프가 만든 답을 단발 질의에 서빙**하고 그 반대도 된다
def test_fingerprint_splits_when_the_loop_is_on() -> None:
    assert _fp(max_retries=2) != _fp()


# 회차 수까지 갈린다. 2바퀴와 3바퀴는 근거가 다를 수 있어 같은 답으로 접으면 안 된다
def test_fingerprint_splits_on_the_retry_budget() -> None:
    assert _fp(max_retries=3) != _fp(max_retries=2)


# **꺼진 단계의 설정은 지문에 안 든다.** 리랭커가 꺼져 있는데 `RERANK_DEVICE` 를 바꿨다고
# 캐시가 죽으면, 그때 아무것도 안 하는 설정이 캐시를 버리는 것이 된다
def test_fingerprint_ignores_settings_of_a_disabled_stage() -> None:
    moved = _fp_config(_rerank=_rerank_config(rerank_device="cuda", rerank_provider="cohere"))

    assert _fp(moved, reranker_on=False) == _fp(reranker_on=False)


def test_fingerprint_ignores_rewrite_settings_when_rewriter_is_off() -> None:
    moved = _fp_config(_rewrite=_rewrite_config(rewrite_mode="replace"))

    assert _fp(moved, rewriter_on=False) == _fp(rewriter_on=False)


# **키는 지문에 안 든다.** 출력을 안 가르고, payload 에 남길 값도 아니다
def test_fingerprint_carries_no_api_key() -> None:
    config = _fp_config(
        _rerank=_rerank_config(openai_api_key="sk-비밀", cohere_api_key="co-비밀"),
        _rewrite=_rewrite_config(openai_api_key="sk-비밀"),
        _generation=_generation_config(openai_api_key="sk-비밀"),
    )

    fingerprint = _fp(config)

    assert "비밀" not in fingerprint
    assert "api_key" not in fingerprint


# **`generation_top_k` 는 안 싣는다 — 유효 `top_k` 가 이미 그 값이다.** 두 번 실으면
# `top_k` 를 덮어쓴 호출에서만 엉뚱하게 미스가 난다(덮어쓴 순간 저쪽은 안 쓰이는 값이다)
def test_fingerprint_ignores_generation_top_k_when_top_k_is_explicit() -> None:
    other = _fp_config(_generation=_generation_config(generation_top_k=9))

    assert _fp(other, top_k=5) == _fp(top_k=5)


# 지문은 읽혀야 한다 — payload 를 열었을 때 *어느 손잡이가 바뀌어 미스가 났나* 가 값 자체로
# 보여야 하기 때문이다. 해시로 접으면 그것을 잃는다 (`rerank_cache_salt_for()` 와 같은 모양)
def test_fingerprint_is_readable_not_a_hash() -> None:
    fingerprint = _fp()

    assert "cross-encoder" in fingerprint
    assert "rewrite_mode=fuse" in fingerprint


# **호출 캐시 설정은 지문에 안 든다.** 리랭커·재작성기가 점수를 디스크에서 읽어오느냐 다시
# 부르느냐는 파이프라인이 *무엇인지* 를 안 바꾼다. 그리고 `cache_dir` 은 **절대 경로**라
# 실으면 payload 에 머신의 파일 배치가 박히고 같은 설정이 머신마다 다른 지문을 낸다 —
# *넘쳐도 미스 한 번* 이라는 이 모듈의 비대칭이 여기서만 성립하지 않는다(늘 미스다)
def test_fingerprint_ignores_call_cache_settings(tmp_path: Path) -> None:
    moved = _fp_config(
        _rerank=_rerank_config(rerank_cache_dir=tmp_path / "r", rerank_cache_enabled=False),
        _rewrite=_rewrite_config(rewrite_cache_dir=tmp_path / "w", rewrite_cache_enabled=False),
    )

    assert _fp(moved) == _fp()


# 그리고 절대 경로가 값 자체로 안 새어야 한다 — payload 에 남는 문자열이다
def test_fingerprint_carries_no_absolute_path() -> None:
    fingerprint = _fp(_fp_config())

    assert ":\\" not in fingerprint
    assert "/" not in fingerprint


# --- 지문과 캐시 --------------------------------------------------------------


# 저장할 때의 지문이 payload 에 실린다 — 그것이 조회 때 대조할 유일한 근거다
def test_put_stores_the_fingerprint() -> None:
    client = _FakeClient(exists=True)
    _cache(_FakeStore(client)).put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP)

    assert client.upserted[0].payload["pipeline"] == _FP


def test_lookup_hits_when_the_fingerprint_matches() -> None:
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    hit = _cache(store).lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint=_FP)

    assert hit is not None


# **설정이 바뀌면 미스다.** 이것이 없으면 리랭커를 켠 뒤에도 히트한 질의가 옛 순서로
# 서빙되는데 에러가 안 나고 답도 그럴듯하다
def test_lookup_misses_when_the_pipeline_changed() -> None:
    payload = _payload_of(_answer())
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    miss = _cache(store).lookup("비슷한 질문", embedder=_FakeEmbedder(), fingerprint="v1|다른설정")

    assert miss is None


# 지문 칸이 없는 옛 항목은 **마이그레이션 없이** 걸러진다 — None 은 어느 지문과도 다르다.
# `_sources_still_valid()` 의 *"짝이 안 맞으면 믿지 않는 쪽으로 떨어뜨린다"* 와 같은 대우다
def test_lookup_misses_on_payload_without_pipeline_key() -> None:
    payload = _payload_of(_answer())
    del payload["pipeline"]
    store = _FakeStore(_FakeClient(point=_Point(payload, score=0.99)), [_stored()])

    assert _cache(store).lookup("질문", embedder=_FakeEmbedder(), fingerprint=_FP) is None


# **지문 불일치는 항목을 안 지운다.** 근거가 사라진 것은 확정 사망이지만 설정 변경은
# *되돌리면 다시 유효해지는* 조건부 사망이라, 같은 대우를 하면 손잡이를 왔다 갔다 할 때마다
# 캐시를 통째로 버린다
def test_pipeline_mismatch_does_not_drop_the_entry() -> None:
    payload = _payload_of(_answer())
    client = _FakeClient(point=_Point(payload, score=0.99))
    store = _FakeStore(client, [_stored()])

    _cache(store).lookup("질문", embedder=_FakeEmbedder(), fingerprint="v1|다른설정")

    assert client.deleted == []


# --- 컬렉션 차원 --------------------------------------------------------------


# **차원이 어긋나면 저장을 접고 사유를 적는다.** 확인하지 않으면 upsert 가 qdrant 에서
# 실패하고 put() 의 except 가 삼켜 *"저장하지 못했습니다"* 한 줄로 끝난다 —
# 캐시가 조용히 아무것도 안 쌓는 상태가 된다
def test_put_skips_when_existing_dimensions_differ() -> None:
    client = _FakeClient(exists=True, dimensions=len(_VECTOR) + 1)
    cache = _cache(_FakeStore(client))

    stored = cache.put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP)

    assert stored is False
    assert client.upserted == []
    # 사유가 *차원* 이라고 적혀 있어야 한다. 없으면 원인을 못 짚는다
    assert any("차원" in warning for warning in cache.warnings)


# **예외는 못 던진다** — 캐시 때문에 질의가 죽으면 최적화가 신뢰성을 깎은 것이 된다
def test_dimension_mismatch_does_not_raise() -> None:
    cache = _cache(_FakeStore(_FakeClient(exists=True, dimensions=999)))

    assert cache.put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP) is False


# 차원이 맞으면 평소대로 저장한다 — 검사가 정상 경로를 막으면 안 된다
def test_put_proceeds_when_dimensions_match() -> None:
    client = _FakeClient(exists=True, dimensions=len(_VECTOR))
    cache = _cache(_FakeStore(client))

    assert cache.put(_answer(), embedder=_FakeEmbedder(), fingerprint=_FP) is True
    assert cache.warnings == []
