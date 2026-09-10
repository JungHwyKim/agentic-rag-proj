"""저장소 구현체가 지켜야 할 인터페이스.

## 왜 메서드가 `collection` 인자를 안 받나

컬렉션은 설정(`IndexingConfig.collection_name`)에서 오고 store 인스턴스가 들고 있다.
메서드마다 인자로 받으면 **합법값이 하나뿐인 인자**가 여섯 개 생긴다. embedding 이
`provider` 설정을 "구현체가 하나면 합법값이 하나뿐인 죽은 설정"이라며 안 만든 것과 같은
기준이다. 컬렉션을 실제로 여럿 쓸 요구가 생기면 그때 인자를 더한다.

같은 이유로 `create_collection` / `delete_collection` / `collection_exists` 셋을 두지 않고
`ensure_collection()` 하나만 둔다. 나머지 둘은 지금 부를 곳이 없다.

## 왜 `ensure_collection()` 이 차원을 인자로 받나

`EmbeddingConfig.embedding_dimensions` 는 `None` 일 수 있고 그 뜻은 "모델 기본값"이다.
즉 **설정만 봐서는 벡터 크기를 모른다.** 모델→차원 표를 코드에 박으면 모델이 늘 때마다
거짓이 되므로, 실제 벡터에서 얻은 크기를 넘겨받는다.

## 왜 `iter_chunks()` 는 Protocol 에 있고 `list_documents()` 는 없나

`QdrantStore.list_documents()` 는 **일부러 여기 안 올렸다**(그쪽 메서드 주석에 이유가 있다) —
소비자인 backend 는 구체 타입을 알아도 되기 때문이다. 열거는 다르다. 소비자가 retriever 이고,
*"retriever 는 `VectorStore` Protocol 만 받는다"* 가 `.claude/wiki/plan_log/검색-생성.md` 1회차 확정이다.
BM25 인덱스를 지으려면 저장된 청크를 전부 읽어야 하는데, Protocol 에 없으면 retriever 가
`QdrantStore` 를 구상 타입으로 잡아 그 확정이 곧바로 깨진다.

**가르는 축은 "몇 명이 쓰나"가 아니라 "쓰는 쪽이 저장소 중립이어야 하나"다.**

## 왜 `close()` 가 인터페이스에 있나

qdrant 로컬 파일 모드는 **저장 디렉터리에 배타 잠금을 건다.** 같은 경로로 두 번째
클라이언트를 만들면 `RuntimeError: Storage folder ... is already accessed by another
instance` 가 난다(실측). 안 닫으면 다음 색인이 통째로 실패하므로 닫는 것이 선택이 아니다.
서버 모드 구현체에서는 사실상 no-op 이 되겠지만, 그건 그쪽 사정이다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from rag_pipeline.schemas.vector import SearchResult, StoredChunk, VectorRecord

__all__ = ["VectorStore"]


# 벡터를 저장하고 찾아주는 구현체의 인터페이스. 상위 계층은 이것만 알면 된다
class VectorStore(Protocol):
    # 이 store 가 실제로 쓰는 컬렉션 이름. 모델·차원이 덧붙은 파생 이름이다
    @property
    def collection(self) -> str: ...

    # 컬렉션이 없으면 만들고, 있으면 차원이 맞는지 확인한다. 여러 번 불러도 안전하다
    def ensure_collection(self, vector_size: int) -> None: ...

    # 레코드들을 저장한다. 같은 id 는 갱신된다. 실제로 저장된 개수를 돌려준다
    def upsert(self, records: list[VectorRecord]) -> int: ...

    # point id 로 지운다. 없는 id 가 섞여 있어도 괜찮다
    def delete(self, ids: list[str]) -> None: ...

    # 문서 하나에 딸린 것을 전부 지운다. 재색인이 옛 청크를 없애는 유일한 경로다
    def delete_by_document_id(self, document_id: str) -> None: ...

    # 질의 벡터와 가까운 것부터 top_k 개. score 를 반드시 실어 돌려준다
    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]: ...

    # 저장된 청크를 전부 훑는다. 벡터는 안 싣는다 — BM25 인덱스를 짓는 것이 소비자다.
    # **컬렉션이 없으면 예외가 아니라 빈 이터레이터다** (구현체 주석에 이유가 있다).
    # 배치 크기를 인자로 받지 않는 것은 위 "왜 메서드가 collection 인자를 안 받나"와 같은 기준이다
    def iter_chunks(self) -> Iterator[StoredChunk]: ...

    # 컬렉션에 든 point 수. 컬렉션이 없으면 0
    def count(self) -> int: ...

    # 저장소를 닫는다. **로컬 파일 모드에서는 필수다** (모듈 docstring 참고)
    def close(self) -> None: ...
