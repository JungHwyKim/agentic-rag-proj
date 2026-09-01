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
# **provider 는 안 들어간다 — 미결이 아니라 결정이다**(3회차, .claude/wiki/plan_log/embedding.md).
# 로컬 구현체가 생겨 축은 살아났지만, provider 는 **모델 이름을 통해서만** 벡터를 바꾼다:
# OpenAI 는 `text-embedding-*`, 로컬은 `org/name` 꼴이라 두 이름 공간이 안 겹쳐 같은 자리를
# 볼 수 없다. 넣었다면 얻는 것 없이 `.cache/embedding` 이 전량 미스가 되고 개발자 `.qdrant`
# 컬렉션이 고아가 됐다. 이름 공간이 겹치게 되는 날 다시 본다.
#
# **질의 접두사는 여전히 죽은 축이다** — 지금 붙은 로컬 모델(KURE-v1)이 접두사를 안 쓴다
# (`prompts: {}`). E5·arctic 계열처럼 접두사를 요구하는 모델을 붙이는 날 **이 함수 한 곳에**
# 더한다. 같은 모델 이름이어도 접두사나 풀링이 다르면 벡터가 달라지기 때문이다
def cache_salt_for(embedding: EmbeddingConfig) -> str:
    dimensions = embedding.embedding_dimensions or "default"
    return f"embed:{embedding.embedding_model}:{dimensions}"


# 임베딩을 만드는 동안 쌓이는 비용. 문서당으로 읽으려면 호출부가 reset() 한다
@dataclass
class EmbeddingUsage:
    # 실제로 나간 API 호출 수. **성공한 뒤에만** 오른다
    api_calls: int = 0
    # 그 호출들이 쓴 토큰. 배치라 호출 수보다 이쪽이 비용을 대변한다.
    #
    # **입력만 찬다.** 임베딩 API 에는 출력 토큰이 없어 `output_tokens` 는 늘 0이고, 그
    # 0 은 *"안 썼다"* 가 아니라 *"이 API 에는 그 칸이 없다"* 는 뜻이다. 칸을 아예 안 두면
    # 다섯 usage 의 모양이 갈려 비용표를 손으로 맞춰야 한다 — `RerankUsage` 가 토큰을 안
    # 쓰는 Cohere 갈래에서도 같은 칸을 지키는 것과 같은 판단이다
    input_tokens: int = 0
    output_tokens: int = 0
    # 캐시로 아낀 텍스트 수
    cache_hits: int = 0

    # 합계는 **파생이지 필드가 아니다** (`RerankUsage.total_tokens` 참고).
    # `openai_api.py` 의 `max_embedding_tokens_per_doc` 상한 검사가 이 값을 읽는다
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    # 문서 하나를 끝내고 다음 문서로 넘어갈 때 부른다
    def reset(self) -> None:
        self.api_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
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
