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
