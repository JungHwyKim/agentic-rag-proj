"""컬렉션 정체성과 준비 — 모델이 다른 벡터가 섞이지 않는가.

핵심은 하나다. **차원만 검사하면 `-small`(기본 1536)과 `dimensions=1536` 으로 줄인
`-large` 를 못 가른다.** 크기가 같고 벡터 공간이 다르면 에러 없이 검색 품질만 서서히
나빠진다 — 제일 늦게 발각되는 종류다. 그래서 모델을 컬렉션 이름에 넣어 아예 다른 컬렉션이
되게 했고, 이 파일이 그것을 지킨다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.exceptions import IndexingError
from rag_pipeline.ingestion.indexing import collection_name_for

# --- 이름 파생 ---


def test_model_and_dimensions_are_in_the_name(indexing_config):
    embedding = EmbeddingConfig(embedding_model="text-embedding-3-small", embedding_dimensions=256)

    name = collection_name_for(indexing_config, embedding)

    assert "text-embedding-3-small" in name
    assert "256" in name


def test_same_size_different_model_gives_different_collection(indexing_config):
    """이 파일의 존재 이유. 1536 끼리도 모델이 다르면 섞이면 안 된다."""
    small = EmbeddingConfig(embedding_model="text-embedding-3-small", embedding_dimensions=1536)
    large = EmbeddingConfig(embedding_model="text-embedding-3-large", embedding_dimensions=1536)

    assert collection_name_for(indexing_config, small) != collection_name_for(
        indexing_config, large
    )


def test_none_dimensions_matches_the_cache_salt_convention(indexing_config):
    """embedding 의 salt 가 `dimensions or 'default'` 라 여기도 같아야 한다."""
    embedding = EmbeddingConfig(embedding_model="text-embedding-3-small", embedding_dimensions=None)

    assert collection_name_for(indexing_config, embedding).endswith("_default")


def test_unsafe_characters_are_replaced(embedding_config):
    """한국어 collection_name 을 그대로 쓰면 qdrant 가 거절한다."""
    config = IndexingConfig(collection_name="문서 모음")

    name = collection_name_for(config, embedding_config)

    assert all(char.isascii() for char in name)
    assert " " not in name


# --- ensure_collection ---


def test_ensure_creates_and_is_idempotent(store):
    store.ensure_collection(4)
    store.ensure_collection(4)

    assert store.count() == 0


def test_dimension_change_is_refused(store):
    """모델은 그대로 두고 dimensions 만 바꾼 경우가 여기로 온다."""
    store.ensure_collection(4)

    with pytest.raises(IndexingError, match="차원이 다릅니다"):
        store.ensure_collection(8)


@pytest.mark.parametrize("size", [0, -1])
def test_bad_vector_size_is_refused(store, size):
    with pytest.raises(IndexingError):
        store.ensure_collection(size)


def test_different_model_does_not_see_the_other_collection(make_chunk, unit_vector, open_store):
    """이름을 갈랐으니 실제로 서로의 벡터가 안 보여야 한다 — 이 파일의 결론."""
    from rag_pipeline.ingestion.indexing import to_records

    small = open_store(
        EmbeddingConfig(embedding_model="text-embedding-3-small", embedding_dimensions=4)
    )
    small.ensure_collection(4)
    small.upsert(to_records([make_chunk(0)], [unit_vector(0)], document_id="안내서.pdf"))
    assert small.count() == 1

    # 같은 경로에 다른 모델로 다시 연다. 파일 잠금 때문에 앞의 것을 먼저 닫는다
    small.close()
    large = open_store(
        EmbeddingConfig(embedding_model="text-embedding-3-large", embedding_dimensions=4)
    )

    assert large.collection != small.collection
    assert large.count() == 0
