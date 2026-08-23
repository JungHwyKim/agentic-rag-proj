"""indexing 의 입출력 스키마. `Chunk` 와 나란히 두는 이유는 둘 다 단계 경계를 넘어서다 —
`VectorRecord` 는 indexing 의 입력이고 `SearchResult` 는 retriever 로 나간다.

## `id` 가 `chunk_id` 가 아닌 이유

**qdrant 는 point ID 로 unsigned int 또는 UUID 만 받는다.** `"안내서.pdf:0007"` 을 그대로
넣으면 `ValueError: Point id ... is not a valid UUID` 로 거절당한다(실측). 그래서
`uuid5(_NAMESPACE, chunk_id)` 로 파생한다 — 해시가 아니라 uuid5 인 것은 **결정론적이면서
qdrant 가 받는 형식**이라는 조건을 동시에 만족하는 것이 이것뿐이라서다. 같은 `chunk_id` 는
항상 같은 point 로 떨어지므로 재색인이 `upsert` 만으로 갱신이 된다.

사람이 읽는 `chunk_id` 는 payload 로 함께 저장한다. UUID 만 남기면 검색 결과에서 원본
청크를 눈으로 되짚을 수 없다.

## 왜 `StoredChunk` 와 `SearchResult` 가 갈려 있나

**`score` 때문이다.** 저장소를 전량 훑는 열거(`VectorStore.iter_chunks()`)에는 점수가 없는데,
`SearchResult` 를 그대로 쓰면 빈자리에 `0.0` 을 채우게 된다. 그것은 이 저장소가 두 번 겪은
손실과 같은 모양이다 — `RetrievedChunk` 의 경로별 점수를 `float | None` 로 둔 이유이고
(`schemas/retrieval.py`), PyMuPDF 의 `extract()` 만 보면 병합 셀과 빈 칸이 안 갈리던 이유다.
**"점수가 0"과 "점수라는 것이 없음"은 다르다.**

그래서 점수 없는 쪽을 상위에 두고 `SearchResult` 가 `score` 를 얹는다. 반대로 짜면
(`SearchResult` 를 두고 열거용을 따로) 필드 다섯이 두 벌이 되어 한쪽만 늘어난다.

## `metadata` 에 무엇이 들어가나

`Chunk` 의 **최상위 필드**(`page`·`heading_path`·`source_name`·`element_orders` 등)와
`Chunk.metadata`(표 `rows`·`part` 등)를 **함께** 담는다. 저쪽에서 최상위와 metadata 가
갈려 있는 것은 splitter 안에서의 구분이고, 저장소에는 "청크를 복원할 정보" 하나로 간다.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

__all__ = ["SearchResult", "StoredChunk", "VectorRecord", "point_id_for"]

# chunk_id -> point UUID 파생에 쓰는 고정 네임스페이스. **바꾸면 기존 색인 전체가 새 point 가
# 되어 재색인이 갱신이 아니라 중복이 된다.** 값 자체에 의미는 없고 고정이라는 점만 중요하다
_NAMESPACE = uuid.UUID("6f1c1d4e-9b3a-5f7c-8d2e-1a4b6c8e0f22")


# chunk_id 를 qdrant 가 받는 point ID 로 바꾼다. 결정론적이라 같은 청크는 항상 같은 자리다
def point_id_for(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


# 저장소에 넣을 벡터 한 건. 청크 하나와 그 벡터가 짝지어진 상태다
class VectorRecord(BaseModel):
    # uuid5(chunk_id). qdrant 가 임의 문자열을 안 받아 파생한 값이다 (모듈 docstring 참고)
    id: str
    vector: list[float]
    text: str
    # 문서 단위 삭제의 열쇠. 재색인이 옛 청크를 찾는 유일한 경로다
    document_id: str
    # 사람이 읽는 원래 id. payload 로 보존해 검색 결과에서 원본을 되짚을 수 있게 한다
    chunk_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# 저장소에 들어 있는 청크 하나. **점수가 없다** — 검색이 아니라 열거의 결과다
class StoredChunk(BaseModel):
    # uuid5(chunk_id). 저장소가 주는 신원이라 어느 경로로 얻어도 같은 값이다
    id: str
    text: str
    document_id: str
    chunk_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# 검색 결과 한 건. 열거 결과에 score 를 얹은 것이다 — threshold·rerank 가 붙을 자리다
class SearchResult(StoredChunk):
    score: float
