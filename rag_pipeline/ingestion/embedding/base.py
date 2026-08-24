"""임베딩 구현체들이 공유하는 인터페이스와 비용 누적기.

## 왜 문서와 질의를 가르나

`text-embedding-3-*` 는 둘을 구분하지 않는다. **지금은 `embed_query(t)` 가
`embed_documents([t])[0]` 과 글자 그대로 같은 값이다.** 그런데도 메서드를 둘로 두는 이유는
하나뿐이다 — 로컬 모델 대비다. BGE·E5 계열은 질의에 접두사를 요구하므로, 나중에 그런 모델을
붙일 때 호출부를 전부 고치지 않으려면 지금 갈라놔야 한다. "임베딩 공간이 달라서"가 아니다.

## 왜 usage 가 인스턴스에 있나

비용 카운터는 보여야 한다(rag-code-review references/contracts.md 3절). 로더는 `LoadContext`
를 핸들러에 넘겨 `DocumentMetadata` 로 내보내지만, 임베딩은 `ingest()` 밖에 있어 그 경로가
없다. 그렇다고 `embed_documents(texts, ctx)` 로 인자를 늘리면 확정된 시그니처를 벗어나므로
인스턴스에 둔다. **누적되므로 문서당으로 읽으려면 호출부가 `reset()` 한다.**

## 캐시 salt 가 왜 구현체 밖에 있나

`cache_salt_for()` 를 보라. 원래 이것은 `OpenAIEmbedder` 의 사설 프로퍼티였는데, 그러면
**두 번째 구현체가 salt 를 자기가 다시 짜야 하고 빠뜨려도 아무 에러가 안 난다** — 모델을
바꿨는데 옛 벡터가 나오는 상황이 조용히 만들어진다. `collection_name_for()` 는 처음부터
모듈 수준 공개 함수라 그 위험이 없었고, 이쪽에만 그 대칭이 없었다.

## LoadContext 를 재사용하지 않는 이유

`LoadContext` 는 `ContentRoutingConfig` 와 loader 전용 카운터(`vision_calls`·`ocr_applied`)
를 들고 있다. 끌어오면 loader 관심사가 임베딩으로 샌다. 반대로 `content_hash()` 와
`ResultCache` 는 그대로 재사용한다 — splitter 가 `content_hash` 에 대해 이미 같은 판단을
했고, 복제하면 해시 규칙과 비용 가시성이 두 곳으로 갈라진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rag_pipeline.config import EmbeddingConfig


# 임베딩 캐시 키의 salt. **`indexing.collection_name_for()` 의 거울이다** — 저쪽은 벡터가
# *저장되는* 자리를, 이쪽은 벡터가 *캐시되는* 자리를 가른다. 둘이 갈리면 한쪽만 새 모델을
# 알아보는데, 그 상태는 에러가 아니라 나쁜 검색 결과로만 드러난다.
#
# **여기 들어가는 것은 벡터를 바꾸는 설정 전부다.** 지금 실재하는 축은 (모델, 차원) 둘뿐이고
# 나머지 필드(배치 크기·토큰 상한·캐시 on/off·키)는 같은 입력에 같은 벡터를 준다.
#
# **provider 와 질의 접두사 정책은 아직 안 넣는다** — 구현체가 하나라 합법값이 하나뿐인 죽은
# 축이 되고, 그것은 `embedding.provider` 설정을 기각한 그 잣대다(plan_log/embedding.md).
# 로컬 구현체가 생기면 **이 함수 한 곳에** 더한다. 같은 모델 이름이어도 질의 접두사나 풀링이
# 다르면 벡터가 달라지므로, 그때는 그것들이 곧 "벡터를 바꾸는 설정" 이 된다
def cache_salt_for(embedding: EmbeddingConfig) -> str:
    dimensions = embedding.embedding_dimensions or "default"
    return f"embed:{embedding.embedding_model}:{dimensions}"


# 임베딩을 만드는 동안 쌓이는 비용. 문서당으로 읽으려면 호출부가 reset() 한다
@dataclass
class EmbeddingUsage:
    # 실제로 나간 API 호출 수. **성공한 뒤에만** 오른다
    api_calls: int = 0
    # 그 호출들이 쓴 토큰. 배치라 호출 수보다 이쪽이 비용을 대변한다
    total_tokens: int = 0
    # 캐시로 아낀 텍스트 수
    cache_hits: int = 0

    # 문서 하나를 끝내고 다음 문서로 넘어갈 때 부른다
    def reset(self) -> None:
        self.api_calls = 0
        self.total_tokens = 0
        self.cache_hits = 0


# 텍스트를 벡터로 만드는 구현체의 인터페이스. 상위 계층은 이것만 알면 된다
class Embedder(Protocol):
    # 문서 텍스트 여러 개를 한 번에. **반환 길이는 입력 길이와 반드시 같다**
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    # 질의 하나를 벡터로. 문서와 같은 임베딩 공간이어야 한다
    def embed_query(self, text: str) -> list[float]: ...

    # 지금까지 쌓인 비용
    @property
    def usage(self) -> EmbeddingUsage: ...
