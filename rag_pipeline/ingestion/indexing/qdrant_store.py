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
from typing import Any

from qdrant_client import QdrantClient, models

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.exceptions import IndexingError
from rag_pipeline.schemas.vector import SearchResult, VectorRecord

__all__ = ["QdrantStore", "collection_name_for"]

# 컬렉션 이름에 쓸 수 없는 문자를 밀어낸다. 모델명의 하이픈은 그대로 살아남는다
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")

# 재색인·삭제가 문서를 찾는 payload 키. 필터에서 이 이름으로 건다
_DOCUMENT_ID_KEY = "document_id"


# 설정 두 개에서 실제 컬렉션 이름을 파생한다. 모델이 바뀌면 다른 컬렉션이 된다
def collection_name_for(indexing: IndexingConfig, embedding: EmbeddingConfig) -> str:
    dimensions = embedding.embedding_dimensions or "default"
    raw = f"{indexing.collection_name}_{embedding.embedding_model}_{dimensions}"
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


# 검색 결과 한 건을 SearchResult 로 바꾼다. payload 가 비어도 죽지 않게 기본값을 준다
def _to_result(point: Any) -> SearchResult:
    payload = getattr(point, "payload", None) or {}
    return SearchResult(
        id=str(point.id),
        score=float(getattr(point, "score", 0.0) or 0.0),
        text=str(payload.get("text") or ""),
        document_id=str(payload.get(_DOCUMENT_ID_KEY) or ""),
        chunk_id=str(payload.get("chunk_id") or ""),
        metadata=payload.get("metadata") or {},
    )


# qdrant 로컬 파일 모드 저장소. 컬렉션 이름은 설정에서 파생되므로 인자로 받지 않는다
class QdrantStore:
    # 클라이언트를 여기서 만들지 않는다. 파일 잠금을 실제로 쓸 때까지 늦추고,
    # _make_client() 를 호출 시점에 부르는 것이 테스트의 모킹 이음새다
    def __init__(self, indexing: IndexingConfig, embedding: EmbeddingConfig) -> None:
        self._config = indexing
        self._collection = collection_name_for(indexing, embedding)
        self._client: Any | None = None

    # 이 store 가 실제로 쓰는 컬렉션 이름
    @property
    def collection(self) -> str:
        return self._collection

    # 클라이언트를 처음 쓸 때 만든다. 파일 잠금 실패를 IndexingError 로 바꿔 올린다 —
    # 날것의 RuntimeError 는 backend 매핑표에 없어 500 이 된다
    @property
    def _qdrant(self) -> Any:
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
