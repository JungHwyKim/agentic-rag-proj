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
근거는 .claude/wiki/plan_log/embedding.md 의 "합성 지점" 절.

## provider 설정이 왜 이제 있나

2회차까지는 없었다 — 구현체가 하나뿐이면 `embedding_provider` 는 합법값이 하나뿐인 죽은
설정이기 때문이다(`table_pdf_handler` 에 두 값이 있는 것은 두 경로가 **실재해서**다).
**3회차에 `LocalEmbedder` 가 생기며 그 전제가 사라졌다.** 추상화를 먼저 두고 설정을 나중에
연 것이 실제로 값을 했다 — 호출부는 한 줄도 안 바뀐다.

**로컬 갈래는 키가 없어도 돈다.** 그래서 기본 `pytest` 방어가 *"키를 지운다"* 로는 부족하다.
전에는 두 겹이었는데(provider 핀 + `torch` 부재), **2026-08-25 에 기본값이 `local` 로 가고
`torch` 가 본체 의존성이 되면서 뒤엣것이 사라졌다.** 지금 막는 것은 `tests/conftest.py` 의
`offline_config` 가 provider 를 `openai` 로 못 박는 **한 겹뿐**이다 — 자세한 것과 그 겹이
실제로 뚫렸던 사고는 `local_st.py` 모듈 docstring 에 있다.

## 이 단계가 잰 것 — 뒤 단계가 쓰고 있다

**벡터는 단위 길이다.** 유료 호출로 확인했고 실측이 0.9998~1.0005 였다 (`dimensions=256`
으로 줄인 축소본도 0.9999). 그래서 indexing 이 Cosine 대신 더 싼 Dot 을 쓰고 재정규화도
안 한다 — `indexing/qdrant_store.py` 의 "왜 거리 척도가 설정이 아닌가" 가 이 실측을 근거로
든다. 정규화하지 않는 임베딩 구현체를 붙이는 날 그쪽도 함께 바꿔야 한다.

**두 벡터를 원소별로 비교하지 않는다.** 호출 간 비트 재현성은 보장되지 않는다 — 같은
텍스트가 한 번은 1e-6 넘게 달랐고 한 번은 정확히 같았다(배치 구성이 다르면 ~1e-4 로
흔들린다). 판정은 코사인 유사도로 한다. 특히 **순서 검증을 값 비교로 쓰면 자리 밀림과
잡음을 구분하지 못한다** — 자기 짝과의 유사도가 남과의 유사도보다 높은가로 본다.

## 이 단계의 규약이 어디 있나

  openai_api.py   `len(반환)==len(입력)` 이 아니면 `EmbeddingError` · 응답을 자리로 믿지
                  않고 `data[i].index` 로 재정렬 · 상한은 호출 수가 아니라 토큰이라
                  `tiktoken` 으로 보내기 전에 센다 · 캐시 salt 가 `embed:{model}:{dim}` 인
                  이유 · `_make_client()` 는 모듈 레벨이어야 한다(테스트 모킹 이음새)
  config.py       차원이 모델 고정이 아니라 **설정값**인 이유(`dimensions` 로 줄일 수 있다)
"""

from __future__ import annotations

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.ingestion.embedding.base import Embedder, EmbeddingUsage, cache_salt_for
from rag_pipeline.ingestion.embedding.local_st import LocalEmbedder
from rag_pipeline.ingestion.embedding.openai_api import OpenAIEmbedder

__all__ = [
    "Embedder",
    "EmbeddingUsage",
    "LocalEmbedder",
    "OpenAIEmbedder",
    "cache_salt_for",
    "get_embedder",
]


# 설정에 맞는 임베딩 구현체를 만든다. 공개 진입점이고 HTTP 를 모른다.
# **분기는 여기 하나뿐이다** — 두 구현체가 같은 Protocol 을 만족하므로 호출부는 모른다
def get_embedder(config: EmbeddingConfig | None = None) -> Embedder:
    config = config or EmbeddingConfig()
    if config.embedding_provider == "local":
        return LocalEmbedder(config)
    return OpenAIEmbedder(config)
