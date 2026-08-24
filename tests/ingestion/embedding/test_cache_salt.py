"""캐시 salt 와 컬렉션 이름이 **같은 자[尺]로 파생되는지** 본다.

## 왜 이 테스트가 있나

둘은 같은 것을 가른다 — *"이 벡터가 어느 모델의 것인가"*. 저쪽(`collection_name_for`)은
벡터가 **저장되는** 자리를, 이쪽(`cache_salt_for`)은 벡터가 **캐시되는** 자리를 가른다.
갈리면 한쪽만 새 모델을 알아보는데, **그 상태는 예외가 아니라 나쁜 검색 결과로만 드러난다.**

로컬 임베딩 구현체가 붙을 때 `provider`·질의 접두사가 더해질 텐데, 한쪽에만 더하면
이 테스트가 잡는다. 값을 대조하는 것이 아니라 **무엇에 반응하는가**를 대조하는 이유다 —
두 파생의 문자열 모양은 원래 다르다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.embedding import cache_salt_for
from rag_pipeline.ingestion.embedding.openai_api import OpenAIEmbedder
from rag_pipeline.ingestion.indexing import collection_name_for


# 필드 하나를 "다른 값"으로 바꾼다. **모르는 타입이면 조용히 건너뛰지 않고 실패한다** —
# 건너뛰면 새 필드가 이 대조에서 빠지는데 그것이 정확히 이 테스트가 막으려는 상황이다
def _other_value(name: str, annotation: Any, default: Any) -> Any:
    args = [a for a in get_args(annotation) if a is not type(None)] or [annotation]
    base = args[0]
    if base is bool:
        return not bool(default)
    if base is int:
        return (default or 0) + 7
    if base is str:
        return f"{default or ''}-changed"
    if base is Path:
        return Path("some-other-place")
    raise AssertionError(f"새 필드 '{name}' 의 타입 {base} 을 흔드는 규칙이 없습니다")


_FIELDS = sorted(EmbeddingConfig.model_fields)


# 두 파생이 **같은 필드에 반응해야 한다.** 한쪽만 반응하면 그 축이 한 곳에서만 갈린다
@pytest.mark.parametrize("field", _FIELDS)
def test_two_derivations_react_to_the_same_fields(field: str) -> None:
    indexing = IndexingConfig(qdrant_path=Path("x"))
    base = EmbeddingConfig(openai_api_key=None)
    info = EmbeddingConfig.model_fields[field]
    moved = base.model_copy(
        update={field: _other_value(field, info.annotation, getattr(base, field))}
    )

    salt_moved = cache_salt_for(base) != cache_salt_for(moved)
    name_moved = collection_name_for(indexing, base) != collection_name_for(indexing, moved)

    assert salt_moved == name_moved, (
        f"'{field}' 에 한쪽만 반응한다 — salt {salt_moved} · collection {name_moved}. "
        "벡터를 바꾸는 설정이면 둘 다에, 아니면 둘 다에서 빠져야 한다"
    )


# 지금 반응해야 하는 축은 둘이다. 위 테스트는 '같이 움직인다'만 보므로 방향도 못 박는다
@pytest.mark.parametrize("field", ["embedding_model", "embedding_dimensions"])
def test_vector_changing_fields_do_move_the_salt(field: str) -> None:
    base = EmbeddingConfig(openai_api_key=None)
    info = EmbeddingConfig.model_fields[field]
    moved = base.model_copy(
        update={field: _other_value(field, info.annotation, getattr(base, field))}
    )
    assert cache_salt_for(base) != cache_salt_for(moved)


# **값 자체를 잠근다.** 모양이 바뀌면 기존 캐시가 전부 빗나가 조용히 재과금된다 —
# 구현체 밖으로 옮기는 이 변경이 그것을 안 건드렸다는 증거가 여기 남아야 한다
def test_salt_string_is_unchanged_by_the_move() -> None:
    config = EmbeddingConfig(openai_api_key=None, embedding_model="text-embedding-3-small")
    assert cache_salt_for(config) == "embed:text-embedding-3-small:default"
    reduced = config.model_copy(update={"embedding_dimensions": 256})
    assert cache_salt_for(reduced) == "embed:text-embedding-3-small:256"


# 구현체는 규칙을 갖지 않고 빌려 쓴다. 두 번째 구현체도 같은 것을 부르게 하려는 것이 요점이다
def test_openai_embedder_delegates_to_the_shared_rule() -> None:
    config = EmbeddingConfig(openai_api_key=None, embedding_dimensions=512)
    assert OpenAIEmbedder(config)._salt == cache_salt_for(config)
