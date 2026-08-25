"""qdrant 구현체. 로컬 파일 모드로 쓴다.

## 왜 서버가 아니라 로컬 파일인가

*기본 `pytest` 는 API 키 없이 전부 통과해야 한다* 는 이 저장소의 규약을 도커 전제로는 지킬
수 없다. 로컬 파일 모드면 색인·검색 테스트가 아무 외부 의존 없이 돈다.

**대신 저장 디렉터리에 배타 잠금이 걸린다.** 같은 경로로 두 번째 클라이언트를 만들면
`RuntimeError: Storage folder ... is already accessed by another instance of Qdrant client`
가 난다(실측). 그래서 `close()` 가 선택이 아니고, 이 클래스는 `with` 로도 쓸 수 있다.
서버 모드로 옮길 때 이 제약은 사라지지만 그때 설정을 함께 더한다.

## 왜 컬렉션 이름에 모델과 차원이 들어가나

차원만 검사하면 **차원이 같고 모델이 다른 경우**를 못 잡는다 — `text-embedding-3-small` 의
기본 1536 과 `-large` 를 `dimensions=1536` 으로 줄인 것은 크기가 같고 벡터 공간이 다르다.
그대로 섞이면 에러 없이 검색 품질만 서서히 나빠진다. embedding 이 캐시 salt 를
`embed:{model}:{dim}` 으로 **둘 다** 넣은 것과 같은 판단이고, 이름에 넣으면 혼입이 검사 통과
여부에 기대지 않고 **구조적으로 불가능**해진다(모델이 다르면 아예 다른 컬렉션이다).

차원 부분이 `embedding_dimensions or "default"` 인 것도 salt 와 같은 규칙이다. `None` 은
"모델 기본값"이라는 뜻이지 차원을 아는 값이 아니라서, 실제 크기는 `ensure_collection()` 이
벡터에서 받는다.

## 왜 거리 척도가 설정이 아닌가

**Dot 으로 고정한다.** 유료 호출로 벡터가 단위 길이임을 확인했다(0.9998~1.0005,
`dimensions=256` 축소본도 0.9999 — plan_log/embedding.md). 단위 벡터에서는 Cosine 과 Dot 이
같은 순위를 주고 Dot 이 더 싸며 재정규화도 필요 없다. 두 값 중 하나만 쓸 설정은
`embedding.provider` 와 같은 죽은 설정이 된다. 정규화하지 않는 로컬 모델이 붙는 날 함께 더한다.

## 모킹 이음새

`_make_client()` 는 **모듈 레벨 함수**여야 한다. 인라인화하거나 메서드로 옮기면
`mocker.patch.object(qdrant_store, "_make_client", ...)` 가 깨진다.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from typing import Any

from qdrant_client import QdrantClient, models

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.exceptions import IndexingError
from rag_pipeline.schemas.vector import SearchResult, StoredChunk, VectorRecord

__all__ = ["QdrantStore", "collection_name_for"]

# 컬렉션 이름에 쓸 수 없는 문자를 밀어낸다. 모델명의 하이픈은 그대로 살아남는다
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")

# 재색인·삭제가 문서를 찾는 payload 키. 필터에서 이 이름으로 건다
_DOCUMENT_ID_KEY = "document_id"

# 문서 목록을 훑을 때 한 번에 받아올 point 수. 목록은 payload 만 받으므로(벡터 제외)
# upsert_batch_size 보다 크게 잡아도 메모리가 안 는다
_SCROLL_BATCH = 256


# 설정 두 개에서 실제 컬렉션 이름을 파생한다. 모델이 바뀌면 다른 컬렉션이 된다.
#
# `prefix` 는 **네임스페이스일 뿐 벡터를 가르는 축이 아니다** — 같은 클라이언트가 보는 다른
# 컬렉션(답변 캐시 등)이 모델·차원 파생을 **다시 구현하지 않고** 이 함수를 거치게 하려고
# 열어 뒀다. 캐시가 자기 이름 규칙을 따로 지어내면, 모델을 갈아탄 뒤 옛 모델 벡터가 든
# 캐시에 새 모델 질의 벡터로 유사도를 재는 사고가 난다 — 검색 품질만 조용히 나빠져 늦게 발각된다
def collection_name_for(
    indexing: IndexingConfig,
    embedding: EmbeddingConfig,
    *,
    prefix: str | None = None,
) -> str:
    dimensions = embedding.embedding_dimensions or "default"
    raw = f"{prefix or indexing.collection_name}_{embedding.embedding_model}_{dimensions}"
    return _UNSAFE.sub("_", raw)


# qdrant 클라이언트를 만든다.
# **모듈 레벨이어야 한다** — 테스트의 모킹 이음새다 (모듈 docstring 참고)
def _make_client(config: IndexingConfig) -> Any:
    path = config.qdrant_path
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


# 레코드를 qdrant point 로 바꾼다. metadata 는 payload 안에 중첩해 원본 모양을 지킨다
def _to_point(record: VectorRecord) -> models.PointStruct:
    return models.PointStruct(
        id=record.id,
        vector=record.vector,
        payload={
            _DOCUMENT_ID_KEY: record.document_id,
            "chunk_id": record.chunk_id,
            "text": record.text,
            "metadata": record.metadata,
        },
    )


# point 하나를 StoredChunk 로 바꾼다. payload 가 비어도 죽지 않게 기본값을 준다
def _to_stored(point: Any) -> StoredChunk:
    payload = getattr(point, "payload", None) or {}
    return StoredChunk(
        id=str(point.id),
        text=str(payload.get("text") or ""),
        document_id=str(payload.get(_DOCUMENT_ID_KEY) or ""),
        chunk_id=str(payload.get("chunk_id") or ""),
        metadata=payload.get("metadata") or {},
    )


# 검색 결과 한 건을 SearchResult 로 바꾼다. 열거와 같은 payload 해석을 쓰고 score 만 얹는다 —
# 두 벌로 두면 한쪽에만 필드가 늘어난다
def _to_result(point: Any) -> SearchResult:
    stored = _to_stored(point)
    return SearchResult(
        **stored.model_dump(),
        score=float(getattr(point, "score", 0.0) or 0.0),
    )


# qdrant 로컬 파일 모드 저장소. 컬렉션 이름은 설정에서 파생되므로 인자로 받지 않는다
class QdrantStore:
    # 클라이언트를 여기서 만들지 않는다. 파일 잠금을 실제로 쓸 때까지 늦추고,
    # _make_client() 를 호출 시점에 부르는 것이 테스트의 모킹 이음새다
    def __init__(
        self,
        indexing: IndexingConfig,
        embedding: EmbeddingConfig,
        *,
        shares_client_with: QdrantStore | None = None,
    ) -> None:
        self._config = indexing
        self._collection = collection_name_for(indexing, embedding)
        self._client: Any | None = None
        # 형제 store 는 클라이언트를 갖지 않고 주인에게 물어본다 (sibling_for 참고)
        self._owner = shares_client_with

    # 이 store 가 실제로 쓰는 컬렉션 이름
    @property
    def collection(self) -> str:
        return self._collection

    # 같은 저장 경로의 **다른 임베딩 컬렉션**을 보는 형제 store 를 만든다.
    #
    # **바로 아래 `client` 프로퍼티와 같은 이유로 있고, 소비자만 다르다** — 저쪽은 컬렉션을
    # 직접 다루는 소비자(답변 캐시)용이고, 이쪽은 **`VectorStore` 가 하나 더 필요한**
    # 소비자용이다. 지금 그 소비자는 그림자 색인이다(`pipeline.py:index_document(shadow=)`).
    #
    # **닫는 것은 여전히 주인 하나다.** 형제의 `close()` 는 아무것도 안 한다 — 소유권 규칙이
    # *"연 쪽이 닫는다"* 이고 형제는 연 적이 없다. 형제를 닫으면 주인이 다음 요청에서 죽는다.
    #
    # **게으름도 유지된다** — 형제가 주인의 `_qdrant` 를 그때 물어보므로, 만들어만 두고
    # 안 쓰면 파일이 안 열린다.
    #
    # **한 번 지웠다가 되살린 API 다.** 2026-08-24 다중 임베더 융합이 쓰던 것을 25일에
    # 되돌리며 지웠는데, 그때 근거가 *"호출부가 0개인 죽은 축"* 이었다. 그림자 색인이
    # 살아 있는 호출부를 만들어 사실이 바뀌었다 — 잣대는 그대로다
    def sibling_for(self, embedding: EmbeddingConfig) -> QdrantStore:
        return QdrantStore(self._config, embedding, shares_client_with=self)

    # 같은 저장 경로의 **다른 컬렉션**을 볼 소비자에게 클라이언트를 빌려준다.
    #
    # **두 번째 클라이언트를 만들면 안 되기 때문에 있는 것이다** — 로컬 파일 모드가 디렉터리에
    # 배타 잠금을 걸어, get_store() 를 한 번 더 부르면 같은 프로세스 안에서 자기 자신과
    # 충돌한다(backend 는 lifespan 에서 이미 하나 열어 들고 있다). 한 클라이언트가 여러
    # 컬렉션을 보는 것은 qdrant 의 정상 사용이고, 닫을 것도 여전히 이 store 하나뿐이다.
    #
    # **게으름이 유지된다** — 여기서 새로 여는 것이 아니라 _qdrant 를 그대로 통과시키므로,
    # 참조만 들고 안 쓰면 파일은 안 열린다. lifespan 이 지켜온 성질이다
    @property
    def client(self) -> Any:
        return self._qdrant

    # 클라이언트를 처음 쓸 때 만든다. 파일 잠금 실패를 IndexingError 로 바꿔 올린다 —
    # 날것의 RuntimeError 는 backend 매핑표에 없어 500 이 된다
    @property
    def _qdrant(self) -> Any:
        # 형제는 자기 클라이언트를 안 만들고 주인 것을 쓴다 (sibling_for 참고)
        if self._owner is not None:
            return self._owner._qdrant
        if self._client is None:
            try:
                self._client = _make_client(self._config)
            except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
                raise IndexingError(
                    f"벡터 저장소를 열지 못했습니다: {type(exc).__name__}: {exc}",
                    user_message="벡터 저장소를 열 수 없어 색인을 중단했습니다.",
                ) from exc
        return self._client

    # 컬렉션이 없으면 만들고, 있으면 차원이 맞는지 본다. 여러 번 불러도 안전하다
    def ensure_collection(self, vector_size: int) -> None:
        if vector_size <= 0:
            raise IndexingError(
                f"벡터 차원이 올바르지 않습니다: {vector_size}",
                user_message="임베딩 결과가 비어 있어 색인을 중단했습니다.",
            )

        client = self._qdrant
        try:
            if not client.collection_exists(self._collection):
                client.create_collection(
                    collection_name=self._collection,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.DOT,
                    ),
                )
                return
            existing = client.get_collection(self._collection).config.params.vectors.size
        except IndexingError:
            raise
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"컬렉션을 준비하지 못했습니다({self._collection}): {type(exc).__name__}: {exc}",
                user_message="벡터 저장소를 준비할 수 없어 색인을 중단했습니다.",
            ) from exc

        # 같은 모델·같은 설정인데 차원이 다르다. 컬렉션 이름이 모델을 이미 갈랐으므로
        # 여기까지 오는 경우는 dimensions 설정만 바꿨을 때다 — 섞이면 검색이 조용히 망가진다
        if existing != vector_size:
            raise IndexingError(
                f"컬렉션 차원이 다릅니다({self._collection}): 기존 {existing} / 이번 {vector_size}",
                user_message=(
                    "저장된 벡터와 차원이 달라 색인할 수 없습니다. "
                    "임베딩 설정을 되돌리거나 컬렉션을 새로 만드세요."
                ),
            )

    # 레코드를 저장한다. 같은 id 는 갱신된다. 저장한 개수를 돌려준다
    def upsert(self, records: list[VectorRecord]) -> int:
        if not records:
            return 0

        client = self._qdrant
        size = max(self._config.upsert_batch_size, 1)
        try:
            for start in range(0, len(records), size):
                client.upsert(
                    collection_name=self._collection,
                    points=[_to_point(record) for record in records[start : start + size]],
                )
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"벡터를 저장하지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="벡터 저장에 실패해 색인을 중단했습니다.",
            ) from exc
        return len(records)

    # point id 로 지운다. 컬렉션이 아직 없으면 지울 것도 없으므로 조용히 넘어간다
    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._delete(models.PointIdsList(points=list(ids)))

    # 문서 하나에 딸린 것을 전부 지운다. 재색인이 옛 청크를 없애는 경로다
    def delete_by_document_id(self, document_id: str) -> None:
        self._delete(
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key=_DOCUMENT_ID_KEY,
                            match=models.MatchValue(value=document_id),
                        )
                    ]
                )
            )
        )

    # 삭제 두 갈래의 공통부. **컬렉션이 없는 것은 오류가 아니다** —
    # 첫 색인에서 delete -> upsert 순서로 부르는 것이 정상 흐름이라 여기서 막으면 안 된다
    def _delete(self, selector: Any) -> None:
        client = self._qdrant
        try:
            if not client.collection_exists(self._collection):
                return
            client.delete(collection_name=self._collection, points_selector=selector)
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"벡터를 지우지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="기존 벡터를 정리하지 못해 색인을 중단했습니다.",
            ) from exc

    # 질의 벡터와 가까운 것부터 top_k 개
    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        if top_k <= 0:
            raise IndexingError(
                f"top_k 는 1 이상이어야 합니다: {top_k}",
                user_message="검색 개수가 올바르지 않습니다.",
            )
        if not query_vector:
            raise IndexingError(
                "질의 벡터가 비어 있습니다",
                user_message="검색할 질의 벡터가 비어 있습니다.",
            )

        client = self._qdrant
        # 없는 컬렉션은 "결과 0건"이 아니라 오류다 — 색인을 한 적이 없는 것과 질의가 안 맞는
        # 것은 사용자가 할 일이 서로 다르다. 빈 컬렉션에서 0건이 나오는 것과도 구분된다
        try:
            exists = client.collection_exists(self._collection)
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"컬렉션을 확인하지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="벡터 저장소를 확인할 수 없어 검색에 실패했습니다.",
            ) from exc

        if not exists:
            raise IndexingError(
                f"컬렉션이 없습니다: {self._collection}",
                user_message="아직 색인된 문서가 없어 검색할 수 없습니다.",
            )

        try:
            response = client.query_points(
                collection_name=self._collection,
                query=list(query_vector),
                limit=top_k,
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"검색에 실패했습니다: {type(exc).__name__}: {exc}",
                user_message="벡터 검색에 실패했습니다.",
            ) from exc

        return [_to_result(point) for point in getattr(response, "points", [])]

    # 색인된 문서 목록. 문서당 (document_id, source_name, chunk_count) 하나로 접는다.
    #
    # **Protocol(base.py)에는 안 올린다.** 저장소 중립을 지키는 쪽이 그것이고, 두 번째
    # 구현체가 실재할 때 올린다 (plan_log/검색-생성.md 의 "새 추상화를 안 만든다"와 같은 잣대).
    # 그래서 이 메서드는 backend 가 QdrantStore 를 구체 타입으로 아는 유일한 자리다.
    #
    # **컬렉션이 없으면 search() 와 달리 빈 리스트다.** 저쪽은 "색인한 적 없음"과 "질의가 안
    # 맞음"이 사용자가 할 일이 달라 갈랐지만, 목록은 둘 다 "보여줄 문서가 없다" 하나다 —
    # 첫 실행에서 사이드바가 비는 것은 정상이지 오류가 아니다
    def list_documents(self) -> list[dict[str, Any]]:
        client = self._qdrant
        try:
            if not client.collection_exists(self._collection):
                return []
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"컬렉션을 확인하지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="벡터 저장소를 확인할 수 없습니다.",
            ) from exc

        # dict 를 쓰는 것은 **삽입 순서가 곧 첫 색인 순서**라서다. 정렬하면 그 정보를 잃는다
        found: dict[str, dict[str, Any]] = {}
        offset: Any = None
        try:
            while True:
                # 벡터는 안 받는다 — 청크 수백 개 × 1536차원을 목록 하나 만들자고 실어 오면
                # 사이드바 갱신이 색인만큼 무거워진다
                points, offset = client.scroll(
                    collection_name=self._collection,
                    limit=_SCROLL_BATCH,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    payload = getattr(point, "payload", None) or {}
                    document_id = payload.get(_DOCUMENT_ID_KEY)
                    if not document_id:
                        continue
                    entry = found.get(document_id)
                    if entry is None:
                        metadata = payload.get("metadata") or {}
                        found[document_id] = {
                            "document_id": document_id,
                            # source_name 이 없으면 document_id 로 떨어진다. 문서 키가 원본
                            # 파일명 기반(NFC)이라 사람이 읽을 수 있는 값이다
                            "source_name": metadata.get("source_name") or document_id,
                            "chunk_count": 1,
                        }
                    else:
                        entry["chunk_count"] += 1
                if offset is None:
                    break
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"문서 목록을 읽지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="색인된 문서 목록을 읽을 수 없습니다.",
            ) from exc

        return list(found.values())

    # 저장된 청크를 전부 훑는다. BM25 인덱스를 짓는 쪽이 소비자다.
    #
    # **컬렉션이 없으면 search() 와 달리 빈 이터레이터다.** 저쪽은 "색인한 적 없음"과 "질의가
    # 안 맞음"이 사용자가 할 일이 달라 갈랐지만, 열거에서 첫 실행이 비는 것은 정상이다 —
    # backend 가 첫 질의에 이걸 부르는데 여기서 던지면 색인 전 서버가 질의마다 죽는다.
    # list_documents() 가 같은 판단을 이미 했다.
    #
    # 벡터는 안 받는다. 청크 수백 개 × 1536차원을 BM25 하나 짓자고 실어 오면 첫 질의가
    # 색인만큼 무거워진다 (list_documents() 와 같은 이유)
    def iter_chunks(self) -> Iterator[StoredChunk]:
        client = self._qdrant
        try:
            if not client.collection_exists(self._collection):
                return
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"컬렉션을 확인하지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="벡터 저장소를 확인할 수 없습니다.",
            ) from exc

        offset: Any = None
        while True:
            try:
                points, offset = client.scroll(
                    collection_name=self._collection,
                    limit=_SCROLL_BATCH,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
                raise IndexingError(
                    f"저장된 청크를 읽지 못했습니다: {type(exc).__name__}: {exc}",
                    user_message="색인된 청크를 읽을 수 없습니다.",
                ) from exc

            for point in points:
                yield _to_stored(point)
            if offset is None:
                return

    # point id 로 골라 온다. **찾은 것만 돌려주므로 길이가 입력보다 짧을 수 있다** —
    # 그 차이가 곧 "그 사이 지워졌다"는 신호라, 없는 것을 빈 자리로 채우면 그 신호가 사라진다.
    #
    # **Protocol(base.py)에는 안 올린다** — list_documents() 와 같은 잣대이고, 소비자가
    # retriever 가 아니라서다(그쪽은 *"VectorStore Protocol 만 받는다"* 가 확정이다).
    # 벡터는 안 받는다. 이 메서드의 소비자는 텍스트만 본다
    def fetch_by_ids(self, ids: list[str]) -> list[StoredChunk]:
        if not ids:
            return []

        client = self._qdrant
        try:
            if not client.collection_exists(self._collection):
                return []
            points = client.retrieve(
                collection_name=self._collection,
                ids=list(ids),
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"청크를 id 로 읽지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="저장된 청크를 확인할 수 없습니다.",
            ) from exc

        return [_to_stored(point) for point in points]

    # 컬렉션에 든 point 수. 컬렉션이 없으면 0
    def count(self) -> int:
        client = self._qdrant
        try:
            if not client.collection_exists(self._collection):
                return 0
            return int(client.count(collection_name=self._collection).count)
        except Exception as exc:  # noqa: BLE001 - 원인을 IndexingError로 바꿔 올린다
            raise IndexingError(
                f"저장된 개수를 세지 못했습니다: {type(exc).__name__}: {exc}",
                user_message="벡터 저장소를 확인할 수 없습니다.",
            ) from exc

    # 저장소를 닫아 파일 잠금을 푼다. 안 닫으면 다음 색인이 통째로 실패한다
    def close(self) -> None:
        # 형제는 연 적이 없으므로 닫지 않는다. 닫으면 주인이 다음 호출에서 죽는다
        if self._owner is not None:
            return
        client, self._client = self._client, None
        if client is None:
            return
        # 닫기가 실패해도 호출부를 죽이지 않는다. close() 는 대개 finally 에서 불리므로
        # 여기서 예외를 올리면 진짜 원인이 된 예외를 덮어버린다
        with contextlib.suppress(Exception):
            client.close()

    # with 문으로 쓸 수 있게 한다. 파일 잠금 때문에 닫기를 잊는 것이 실제 사고가 된다
    def __enter__(self) -> QdrantStore:
        return self

    # 예외로 빠져나가도 잠금을 푼다
    def __exit__(self, *_exc: object) -> None:
        self.close()
