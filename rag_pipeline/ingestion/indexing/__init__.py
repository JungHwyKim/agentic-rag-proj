"""적재 파이프라인 5단계 — 벡터를 저장하고 찾는다.

`list[Chunk]` 와 그에 대응하는 벡터를 받아 저장소에 넣는다. 앞 단계들과 달리 반환값이 아니라
**부수 효과**가 목적인 첫 단계다.

## 실패하면 예외를 던진다 — embedding 과 같은 이유

contracts 2절("외부 API 는 예외를 안 던지고 빈 결과 + `warn()`")을 **의도적으로 따르지
않는다.** 그 규약은 내려갈 층이 있어서 성립한다(LlamaParse -> PyMuPDF, Vision -> OCR).
여기에는 없다 — 저장에 실패한 청크를 조용히 넘기면 그 청크는 영원히 검색되지 않는데 아무도
모른다. embedding 이 한 칸 앞에서 같은 판단을 했고 근거도 같다
(`plan_log/embedding.md` · `plan_log/indexing.md` 의 "실패 계약").

## 비용 카운터가 없는 이유

contracts 3절의 카운터·호출 상한·캐시 salt 셋은 **유료** 외부 API 규약이다. 로컬 파일
모드에는 호출당 과금이 없어서 형식만 베끼면 아무것도 안 지키는 카운터가 남는다.
관리형 qdrant 로 옮기는 날 함께 더한다. 같은 이유로 `ResultCache` 도 안 쓴다 —
qdrant 자체가 저장소라 캐시를 얹으면 진실이 두 곳이 된다.

## provider 설정이 없는 이유

embedding 과 같다. 구현체가 하나면 `indexing.provider` 는 합법값이 하나뿐인 죽은 설정이다
(`table_pdf_handler` 에 두 값이 있는 것은 두 경로가 **실재해서**다). 추상화
(`VectorStore` Protocol + 이 팩토리)만 두고 설정은 두 번째 구현체가 생길 때 더한다.

## 이 단계의 규약이 어디 있나

  records.py         `document_id` 를 내용 해시가 아니라 **원본 파일명**에서 뽑는 이유 ·
                     `unicodedata.normalize("NFC", ...)` 가 필수인 이유(macOS 는 NFD,
                     Windows 는 NFC 로 저장해 눈에 같은 이름이 다른 키가 된다) ·
                     `to_records()` 의 길이 검사 — `zip` 이 일어나는 유일한 자리다
  qdrant_store.py    컬렉션 이름에 모델·차원이 들어가는 이유 · 거리 척도가 Dot 고정이고
                     설정이 아닌 이유 · 로컬 파일 모드의 배타 잠금
  base.py            `close()` 가 왜 인터페이스에 있나
  schemas/vector.py  `VectorRecord.id` 가 `chunk_id` 가 아니라 `uuid5(chunk_id)` 인 이유
                     (qdrant 는 point ID 로 uint 또는 UUID 만 받는다) · `_NAMESPACE` 를
                     바꾸면 기존 색인 전체가 새 point 가 된다

재색인은 `delete_by_document_id()` → `upsert()` 순서다. upsert 만으로는 문서가 `A B C D E`
에서 `A B D` 로 줄었을 때 C·E 가 남아 계속 검색된다. **레코드가 0개여도 delete 는 부른다** —
빈 문서로 바뀐 경우를 건너뛰면 옛 청크가 통째로 남는다. 두 규약 모두
`pipeline.py:index_document()` 가 지키고, 같은 함수의 `try/finally` 가 `close()` 를 푼다.

`search(filters=...)` 와 backend 색인 엔드포인트는 **일부러 없다.** 필터로 쓸 축이
`document_id` 뿐이고 `page` 는 못 쓴다(HWPX 쪽 수가 문서에 따라 부풀어 있다 — CLAUDE.md 의
함정 절). 근거는 `plan_log/indexing.md`.
"""

from __future__ import annotations

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.ingestion.indexing.qdrant_store import QdrantStore, collection_name_for
from rag_pipeline.ingestion.indexing.records import document_id_for, to_records

__all__ = [
    "VectorStore",
    "collection_name_for",
    "document_id_for",
    "get_store",
    "to_records",
]


# 설정에 맞는 저장소 구현체를 만든다. 공개 진입점이고 HTTP 를 모른다.
# EmbeddingConfig 를 함께 받는 이유: 컬렉션 정체성이 모델·차원에 걸려 있다 (qdrant_store.py)
def get_store(
    indexing: IndexingConfig | None = None,
    embedding: EmbeddingConfig | None = None,
) -> VectorStore:
    return QdrantStore(indexing or IndexingConfig(), embedding or EmbeddingConfig())
