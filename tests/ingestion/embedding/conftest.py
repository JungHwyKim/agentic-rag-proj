"""임베딩 테스트용 가짜 OpenAI 클라이언트.

실제 SDK 를 부르지 않는다. `_make_client()` 를 이 객체로 갈아끼우는 것이 유일한 모킹
이음새이므로(`rag-code-review` references/contracts.md 6절), 여기서 만드는 것은 그 함수가
돌려주는 객체의 모양뿐이다 — `client.embeddings.create(...)` 하나.

벡터는 텍스트 해시에서 만든다. **같은 텍스트면 항상 같은 벡터**라야 캐시 적중과
`embed_query == embed_documents([t])[0]` 을 검증할 수 있다.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import Any

import pytest

from rag_pipeline.config import EmbeddingConfig


# 텍스트에서 결정론적으로 만드는 벡터. 같은 글자면 항상 같은 값이다
def fake_vector(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # 4바이트씩 읽으므로 dim*4 바이트가 필요하다. 해시를 그만큼 이어붙인다
    raw = (digest * (dim * 4 // len(digest) + 1))[: dim * 4]
    # 4바이트씩 부호 없는 정수로 읽어 0~1 로 정규화한다
    return [value / 0xFFFFFFFF for value in struct.unpack(f">{dim}I", raw)]


# 한 번의 create() 호출에 실제로 넘어간 인자
@dataclass
class FakeCall:
    model: str
    input: list[str]
    dimensions: int | None


# 응답 항목 하나. 실제 SDK 와 같은 속성 이름을 쓴다.
# embedding 이 None 일 수 있는 이유는 손상된 응답을 흉내내기 위해서다
@dataclass
class _FakeItem:
    index: int
    embedding: list[float] | None


# 응답이 알려주는 과금 토큰. 실제 SDK 는 response.usage.total_tokens 로 준다
@dataclass
class _FakeUsage:
    total_tokens: int


# create() 가 돌려주는 응답 자리
@dataclass
class _FakeResponse:
    data: list[_FakeItem]
    usage: Any


# client.embeddings 자리
class _FakeEmbeddings:
    # 어느 FakeClient 의 것인지 들고 있다가 호출을 그쪽으로 넘긴다
    def __init__(self, owner: FakeClient) -> None:
        self._owner = owner

    # 실제 SDK 와 같은 시그니처. 키워드로만 받는다
    def create(self, **params: Any) -> _FakeResponse:
        return self._owner._create(**params)


# _make_client() 가 돌려주는 객체 자리. 실패·순서뒤섞임·개수부족을 흉내낼 수 있다
@dataclass
class FakeClient:
    dim: int = 8
    # 던질 예외. 주면 create() 가 항상 이걸 던진다
    error: Exception | None = None
    # 응답을 뒤집어 돌려준다. index 로 제자리를 찾는지 보는 용도
    shuffle: bool = False
    # 응답에서 이 개수만큼 빼고 돌려준다. 길이 계약 위반 감지용
    drop: int = 0
    # 응답 벡터의 차원을 항목마다 다르게 만든다
    ragged: bool = False
    # embedding 필드를 숫자 목록이 아닌 것으로 채운다. 손상된 응답을 흉내낸다
    broken_vector: bool = False
    # usage 를 통째로 빼서 폴백 토큰 계산 경로를 태운다
    report_usage: bool = True
    calls: list[FakeCall] = field(default_factory=list)

    # 실제 SDK 처럼 client.embeddings 를 달아준다
    def __post_init__(self) -> None:
        self.embeddings = _FakeEmbeddings(self)

    # 지금까지 나간 호출 수. 캐시가 먹었는지 보는 데 쓴다
    @property
    def call_count(self) -> int:
        return len(self.calls)

    # 실제 요청을 흉내낸다. 인자를 기록해 두고 설정대로 응답을 만든다
    def _create(self, **params: Any) -> _FakeResponse:
        texts = list(params["input"])
        self.calls.append(
            FakeCall(
                model=params["model"],
                input=texts,
                dimensions=params.get("dimensions"),
            )
        )
        if self.error is not None:
            raise self.error

        dim = self.dimensions_for_call(params)
        items = [
            _FakeItem(
                index=index,
                embedding=(
                    None
                    if self.broken_vector
                    else fake_vector(text, dim + (1 if self.ragged and index else 0))
                ),
            )
            for index, text in enumerate(texts)
        ]
        if self.drop:
            items = items[: -self.drop]
        if self.shuffle:
            items = list(reversed(items))

        usage = _FakeUsage(total_tokens=len(texts) * 10) if self.report_usage else None
        return _FakeResponse(data=items, usage=usage)

    # 요청에 dimensions 가 있으면 그것을, 없으면 기본 차원을 쓴다
    def dimensions_for_call(self, params: dict[str, Any]) -> int:
        requested = params.get("dimensions")
        return requested if isinstance(requested, int) else self.dim


# FakeClient 를 만드는 팩토리. tests/ 에 __init__.py 가 없어 테스트끼리 import 할 수 없으므로
# 픽스처로 건넨다 — 클래스를 그대로 돌려주니 make_client(dim=4) 처럼 쓴다
@pytest.fixture
def make_client():
    return FakeClient


# 기대 벡터를 만드는 함수. 같은 이유로 픽스처로 건넨다
@pytest.fixture
def vector_of():
    return fake_vector


# 캐시를 끈 기본 설정. 키는 가짜지만 _make_client 를 갈아끼우므로 밖으로 안 나간다
@pytest.fixture
def embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        openai_api_key="test-key",
        embedding_batch_size=100,
        embedding_cache_enabled=False,
    )


# 캐시를 켠 설정. tmp_path 에 두어 테스트끼리 결과를 물려받지 않는다
@pytest.fixture
def cached_config(tmp_path):
    def _make(**overrides) -> EmbeddingConfig:
        params = {
            "openai_api_key": "test-key",
            "embedding_batch_size": 100,
            "embedding_cache_enabled": True,
            "embedding_cache_dir": tmp_path / "embedding-cache",
        }
        params.update(overrides)
        return EmbeddingConfig(**params)

    return _make
