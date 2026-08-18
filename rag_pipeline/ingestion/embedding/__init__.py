"""적재 파이프라인 4단계 — 청크 텍스트를 검색용 벡터로 만든다.

`list[str]` 을 받아 `list[list[float]]` 를 돌려준다. `Chunk` 를 받지 않는 이유는 질의
임베딩과 대칭을 지키기 위해서다 — 상위가 `[c.text for c in chunks]` 를 넘긴다. Chunk 를
다시 자르거나 고치지 않고, metadata 를 벡터에 섞지 않고, Vector DB 를 모른다.

## ingest() 에 끼우지 않는 이유

splitter 와 **같은 이유이고 한 칸 밀린 같은 상황**이다. 벡터를 소비할 곳(indexing)이 아직
없다. `ingest()` 의 반환 타입은 곧 `backend` 의 `response_model` 이라, 벡터를 내보내는 것은
embedding 결정이 아니라 API 결정이다. 청크 285~721개 × 1536차원을 업로드 응답에 실으면
수 MB 이기도 하다.

부수 효과 하나가 중요하다 — **기본 `pytest` 가 이 단계를 아예 안 부른다.** 그래서 키가
없을 때 예외를 던지는 설계가 기본 테스트를 깨지 않는다. 두 결정이 서로를 지탱한다.
근거는 plan_log/embedding.md 의 "합성 지점" 절.

## provider 설정이 없는 이유

구현체가 하나뿐이라 `embedding.provider` 는 합법값이 하나뿐인 죽은 설정이 된다
(`table_pdf_handler` 에 두 값이 있는 것은 두 경로가 **실재해서**다). 그래서 추상화
(`Embedder` Protocol + 이 팩토리)는 두되 설정은 만들지 않았다. 로컬 구현체가 생기는 날
아래 분기와 설정을 함께 더하면 된다.
"""

from __future__ import annotations

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.ingestion.embedding.base import Embedder, EmbeddingUsage
from rag_pipeline.ingestion.embedding.openai_api import OpenAIEmbedder

__all__ = ["Embedder", "EmbeddingUsage", "get_embedder"]


# 설정에 맞는 임베딩 구현체를 만든다. 공개 진입점이고 HTTP 를 모른다.
# 지금은 분기가 없다 — 로컬 구현체가 생길 때 여기에 붙는다 (모듈 docstring 참고)
def get_embedder(config: EmbeddingConfig | None = None) -> Embedder:
    return OpenAIEmbedder(config or EmbeddingConfig())
