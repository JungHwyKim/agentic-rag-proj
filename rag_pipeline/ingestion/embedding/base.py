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

## LoadContext 를 재사용하지 않는 이유

`LoadContext` 는 `ContentRoutingConfig` 와 loader 전용 카운터(`vision_calls`·`ocr_applied`)
를 들고 있다. 끌어오면 loader 관심사가 임베딩으로 샌다. 반대로 `content_hash()` 와
`ResultCache` 는 그대로 재사용한다 — splitter 가 `content_hash` 에 대해 이미 같은 판단을
했고, 복제하면 해시 규칙과 비용 가시성이 두 곳으로 갈라진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
