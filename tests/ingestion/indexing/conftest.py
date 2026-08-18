"""색인 테스트용 픽스처.

**여기서는 qdrant 를 모킹하지 않는다.** 로컬 파일 모드라 서버도 키도 과금도 없고, 진짜
저장소를 쓰는 편이 "upsert 가 정말 갱신인가" 같은 것을 실제로 검증한다. 모킹이 필요한 곳은
저장소가 **고장났을 때**뿐이라 그 테스트만 `_make_client()` 를 갈아끼운다.

**모든 store 는 반드시 닫아야 한다.** qdrant 로컬 모드는 저장 디렉터리에 배타 잠금을 걸어,
안 닫으면 같은 경로를 쓰는 다음 테스트가 `RuntimeError` 로 죽는다. 그래서 픽스처가 열고
닫는 책임을 가져간다.

차원을 4로 두는 이유: 실제 1536 은 테스트에서 읽을 수 없고, 검증하려는 것은 차원 값이
아니라 순위·자리 대응이라 작을수록 눈으로 확인된다.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.embedding import openai_api
from rag_pipeline.ingestion.indexing import get_store
from rag_pipeline.schemas.chunk import Chunk, ChunkType

# 테스트 벡터의 차원. 작게 두어야 기대값을 손으로 적을 수 있다
DIM = 4


# 저장 경로를 tmp_path 로 돌린 색인 설정. 개발자의 실제 .qdrant 를 절대 건드리지 않는다
@pytest.fixture
def indexing_config(tmp_path: Path) -> IndexingConfig:
    return IndexingConfig(qdrant_path=tmp_path / "qdrant", collection_name="테스트")


# 컬렉션 이름 파생에 쓰는 임베딩 설정. 키는 지운다 — 색인 테스트는 임베딩을 부르지 않는다
@pytest.fixture
def embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        openai_api_key=None,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=DIM,
    )


# 열린 저장소 하나. 테스트가 끝나면 반드시 닫아 파일 잠금을 푼다
@pytest.fixture
def store(indexing_config, embedding_config):
    opened = get_store(indexing_config, embedding_config)
    try:
        yield opened
    finally:
        opened.close()


# 같은 경로에 저장소를 다시 여는 factory. 닫고 다시 열어도 남아 있는지, 임베딩 설정이
# 다르면 서로의 벡터가 안 보이는지 볼 때 쓴다. 열어준 것은 전부 여기서 닫는다 —
# 하나라도 열려 있으면 다음 open 이 파일 잠금 때문에 죽는다
@pytest.fixture
def open_store(indexing_config, embedding_config):
    opened = []

    def _open(embedding: EmbeddingConfig | None = None):
        store = get_store(indexing_config, embedding or embedding_config)
        opened.append(store)
        return store

    try:
        yield _open
    finally:
        for store in opened:
            store.close()


# 청크 하나를 만든다. chunk_id 는 splitter 와 같은 규칙으로 맞춘다
@pytest.fixture
def make_chunk():
    def _make(
        index: int,
        text: str = "본문",
        *,
        document_key: str = "안내서.pdf",
        **overrides,
    ) -> Chunk:
        params = {
            "chunk_id": f"{document_key}:{index:04d}",
            "document_key": document_key,
            "chunk_index": index,
            "type": ChunkType.TEXT,
            "text": text,
            "source_name": document_key,
        }
        params.update(overrides)
        return Chunk(**params)

    return _make


# 한 축만 1인 단위 벡터. Dot 거리에서 어느 것이 뽑힐지 손으로 계산할 수 있다
@pytest.fixture
def unit_vector():
    def _make(axis: int, dim: int = DIM) -> list[float]:
        return [1.0 if position == axis else 0.0 for position in range(dim)]

    return _make


# 텍스트에서 결정론적으로 만드는 단위 벡터. **같은 글자면 항상 같은 값**이라야
# "자기 청크가 자기 질의에 1등인가"를 검증할 수 있다. 단위 길이인 것도 일부러다 —
# 실제 임베딩이 단위 길이라서 Dot 을 쓰기로 했고, 가짜가 그 전제를 깨면 검증이 헛돈다
def _text_vector(text: str, dim: int = 8) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = (digest * (dim // len(digest) + 1))[:dim]
    values = [byte / 255 + 0.01 for byte in raw]
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


# client.embeddings 자리. 실제 SDK 와 같은 모양만 흉내낸다
class _FakeEmbeddings:
    def __init__(self, owner: _FakeEmbeddingClient) -> None:
        self._owner = owner

    # 실제 SDK 와 같은 시그니처. 요청을 기록해 두고 결정론적 벡터를 돌려준다
    def create(self, **params: Any):
        texts = list(params["input"])
        self._owner.calls.append(texts)
        data = [
            SimpleNamespace(index=index, embedding=_text_vector(text))
            for index, text in enumerate(texts)
        ]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(texts) * 10))


# embedding 의 _make_client() 가 돌려주는 객체 자리. 과금도 네트워크도 없다
class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.embeddings = _FakeEmbeddings(self)


# 임베딩 API 를 가짜로 갈아끼운다. **_make_client() 가 모듈 레벨이라 가능한 것이다**.
# 돌려주는 객체의 `calls` 로 "정말 안 불렸는가"까지 볼 수 있다
@pytest.fixture
def fake_embedding_api(mocker):
    client = _FakeEmbeddingClient()
    mocker.patch.object(openai_api, "_make_client", return_value=client)
    return client


# 질의 텍스트를 그 청크와 같은 규칙으로 벡터화한다. 검색 검증에 쓴다
@pytest.fixture
def text_vector():
    return _text_vector
