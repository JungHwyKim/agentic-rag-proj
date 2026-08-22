"""캐시와 salt.

캐시 키의 salt 에 **모델명과 차원**이 들어가야 한다. 빠지면 모델을 바꿔도 옛 벡터가 나와
검색이 조용히 망가진다 — 실패가 눈에 안 보이는 종류라 테스트로만 잡힌다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.ingestion.embedding import get_embedder, openai_api


def _patch(mocker, client: Any):
    return mocker.patch.object(openai_api, "_make_client", return_value=client)


def test_second_run_hits_cache(mocker, cached_config, make_client):
    client = make_client(dim=4)
    _patch(mocker, client)
    config = cached_config()

    first = get_embedder(config).embed_documents(["같은 텍스트"])
    second_embedder = get_embedder(config)
    second = second_embedder.embed_documents(["같은 텍스트"])

    assert first == second
    assert client.call_count == 1
    assert second_embedder.usage.api_calls == 0
    assert second_embedder.usage.cache_hits == 1


def test_changing_model_misses_cache(mocker, cached_config, make_client):
    """salt 에 모델명이 없으면 여기서 옛 벡터가 나온다."""
    client = make_client(dim=4)
    _patch(mocker, client)

    get_embedder(cached_config()).embed_documents(["같은 텍스트"])
    embedder = get_embedder(cached_config(embedding_model="text-embedding-3-large"))
    embedder.embed_documents(["같은 텍스트"])

    assert client.call_count == 2
    assert embedder.usage.cache_hits == 0


def test_changing_dimensions_misses_cache(mocker, cached_config, make_client):
    client = make_client(dim=4)
    _patch(mocker, client)

    get_embedder(cached_config()).embed_documents(["같은 텍스트"])
    embedder = get_embedder(cached_config(embedding_dimensions=4))
    embedder.embed_documents(["같은 텍스트"])

    assert client.call_count == 2
    assert embedder.usage.cache_hits == 0


def test_disabled_cache_always_calls(mocker, cached_config, make_client):
    client = make_client(dim=4)
    _patch(mocker, client)
    config = cached_config(embedding_cache_enabled=False)

    get_embedder(config).embed_documents(["같은 텍스트"])
    get_embedder(config).embed_documents(["같은 텍스트"])

    assert client.call_count == 2


def test_partial_cache_keeps_order(mocker, cached_config, make_client, vector_of):
    """일부만 캐시에 있을 때 API 로 간 것들이 제자리로 돌아오는지 — 제일 밀리기 쉬운 자리."""
    client = make_client(dim=4)
    _patch(mocker, client)
    config = cached_config()

    # 가운데 하나만 미리 캐시에 넣는다
    get_embedder(config).embed_documents(["나"])
    client.calls.clear()

    texts = ["가", "나", "다"]
    embedder = get_embedder(config)
    vectors = embedder.embed_documents(texts)

    assert vectors == [vector_of(text, 4) for text in texts]
    assert embedder.usage.cache_hits == 1
    # API 에는 캐시에 없던 둘만 갔다
    assert [call.input for call in client.calls] == [["가", "다"]]


def test_query_uses_the_same_cache(mocker, cached_config, make_client):
    """같은 글자면 문서로 넣든 질의로 넣든 같은 캐시 항목이다."""
    client = make_client(dim=4)
    _patch(mocker, client)
    config = cached_config()

    get_embedder(config).embed_documents(["연차휴가는 며칠인가?"])
    embedder = get_embedder(config)
    embedder.embed_query("연차휴가는 며칠인가?")

    assert client.call_count == 1
    assert embedder.usage.cache_hits == 1


def test_cache_hit_does_not_count_tokens(mocker, cached_config, make_client):
    """캐시로 아낀 것은 과금되지 않았으므로 토큰에 안 넣는다."""
    _patch(mocker, make_client(dim=4))
    config = cached_config()

    get_embedder(config).embed_documents(["같은 텍스트"])
    embedder = get_embedder(config)
    embedder.embed_documents(["같은 텍스트"])

    assert embedder.usage.total_tokens == 0
