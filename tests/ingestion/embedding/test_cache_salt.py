"""캐시 salt 와 컬렉션 이름이 **같은 자[尺]로 파생되는지** 본다.

## 왜 이 테스트가 있나

둘은 같은 것을 가른다 — *"이 벡터가 어느 모델의 것인가"*. 저쪽(`collection_name_for`)은
벡터가 **저장되는** 자리를, 이쪽(`cache_salt_for`)은 벡터가 **캐시되는** 자리를 가른다.
갈리면 한쪽만 새 모델을 알아보는데, **그 상태는 예외가 아니라 나쁜 검색 결과로만 드러난다.**

값을 대조하는 것이 아니라 **무엇에 반응하는가**를 대조한다 — 두 파생의 문자열 모양은
원래 다르다. 한쪽에만 축을 더하면 이 테스트가 잡는다.

## provider 는 일부러 어느 쪽에도 없다 (3회차, 2026-08-24)

로컬 구현체가 실재하게 됐지만 `embedding_provider` 는 **두 파생 어디에도 안 들어간다.**
provider 는 **모델 이름을 통해서만** 벡터를 바꾸고, 두 이름 공간이 안 겹치기 때문이다
(OpenAI 는 `text-embedding-*`, 로컬은 `org/name` 꼴로 슬래시를 갖는다). 넣었다면
`.cache/embedding` 이 전량 미스가 되고 개발자 `.qdrant` 컬렉션이 고아가 됐다.

그래서 여기서 잠그는 것은 *"provider 가 둘 다 안 움직인다"* 이고, 위 대조가 **같이 안
움직이는 것**까지 검사하므로 한쪽에만 몰래 더해지면 여전히 잡힌다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.embedding import cache_salt_for
from rag_pipeline.ingestion.embedding.openai_api import OpenAIEmbedder
from rag_pipeline.ingestion.indexing import collection_name_for


# Literal 의 합법값 목록. Optional 로 감싸도 찾아낸다. Literal 이 아니면 None
def _literal_choices(annotation: Any) -> tuple[Any, ...] | None:
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    for arg in get_args(annotation):
        if get_origin(arg) is Literal:
            return get_args(arg)
    return None


# 필드 하나를 "다른 값"으로 바꾼다. **모르는 타입이면 조용히 건너뛰지 않고 실패한다** —
# 건너뛰면 새 필드가 이 대조에서 빠지는데 그것이 정확히 이 테스트가 막으려는 상황이다
def _other_value(name: str, annotation: Any, default: Any) -> Any:
    # Literal 은 타입이 아니라 **값 목록**이라 아래 규칙이 안 통한다. 지금 값이 아닌 것을
    # 하나 고른다 — 합법값이 하나뿐이면 흔들 수 없으므로 그것도 실패로 본다.
    #
    # **`Literal[...] | None` 도 여기서 잡아야 한다** — 그러지 않으면 union 을 편 뒤
    # `base` 가 타입이 아니라 리터럴 값이 되어, 아래 실패 메시지가 타입 이름 자리에 값을
    # 찍는다("타입 openai 에 대한 규칙이 없습니다"). 그러면 *규칙을 더하라*는 뜻인지
    # *필드가 잘못됐다*는 뜻인지 안 갈린다
    literal = _literal_choices(annotation)
    if literal is not None:
        choices = [value for value in literal if value != default]
        if not choices:
            raise AssertionError(f"'{name}' 의 합법값이 하나뿐이라 흔들 수 없습니다")
        return choices[0]

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


# **provider 는 어느 쪽도 안 움직인다.** 위 대조는 '같이 움직인다'만 보므로 방향을 못 박는다 —
# 나중에 둘 다에 provider 를 더하면 위 테스트는 통과하지만 이것이 실패한다.
# 그때는 이 테스트가 아니라 plan_log/embedding.md 3회차의 결정을 먼저 다시 본다
def test_provider_moves_neither_derivation() -> None:
    indexing = IndexingConfig(qdrant_path=Path("x"))
    base = EmbeddingConfig(openai_api_key=None)
    moved = base.model_copy(update={"embedding_provider": "local"})

    assert cache_salt_for(base) == cache_salt_for(moved)
    assert collection_name_for(indexing, base) == collection_name_for(indexing, moved)


# **두 provider 의 모델 이름 공간이 안 겹친다**는 것이 위 결정의 유일한 근거다.
# 겹치는 순간 같은 자리를 다른 벡터가 보게 되므로, 그 전제를 여기에 남겨 둔다.
#
# **기본값 하나만 보되 provider 를 함께 읽는다.** 전에는 *"기본 모델 이름에 `/` 가 없다"* 로
# 잠갔는데, 그건 기본값이 OpenAI 쪽일 때만 뜻이 있는 **대리 지표**였다 — 2026-08-25 에
# 기본값이 `nlpai-lab/KURE-v1` 로 가자 규칙은 그대로인데 테스트만 깨졌다. 그래서 방향을
# provider 에서 얻는다: 로컬 이름은 `org/name` 이라 `/` 가 **있어야** 하고, OpenAI 이름은
# `/` 가 **없어야** 한다. 어느 쪽이 기본값이 되든 같은 규칙을 잰다
def test_the_two_name_spaces_do_not_overlap() -> None:
    provider = EmbeddingConfig.model_fields["embedding_provider"].default
    model = EmbeddingConfig.model_fields["embedding_model"].default

    if provider == "local":
        assert "/" in model, (
            f"로컬 모델 이름 {model!r} 에 org 접두사가 없으면 OpenAI 이름 공간과 겹칠 수 있다 — "
            "그러면 cache_salt_for() 에 provider 를 넣어야 한다"
        )
    else:
        assert "/" not in model, (
            f"OpenAI 모델 이름 {model!r} 에 슬래시가 생기면 로컬(org/name)과 이름 공간이 "
            "겹칠 수 있다 — 그러면 cache_salt_for() 에 provider 를 넣어야 한다"
        )


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
