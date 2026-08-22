"""배치 분할과 순서 보존.

임베딩은 청크 수백 개를 한 번에 받는다. 나눠 보내고 다시 합치는 과정에서 자리가 한 칸만
밀려도 아무 에러 없이 검색이 엉뚱한 문서를 가리키므로, 이 파일은 **순서**만 본다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.ingestion.embedding import get_embedder, openai_api


def _patch(mocker, client: Any):
    return mocker.patch.object(openai_api, "_make_client", return_value=client)


def test_input_is_split_into_batches(mocker, embedding_config, make_client):
    client = make_client(dim=4)
    _patch(mocker, client)
    config = embedding_config.model_copy(update={"embedding_batch_size": 3})

    texts = [f"청크 {index}" for index in range(7)]
    vectors = get_embedder(config).embed_documents(texts)

    assert len(vectors) == 7
    assert client.call_count == 3
    assert [len(call.input) for call in client.calls] == [3, 3, 1]


def test_order_survives_batching(mocker, embedding_config, make_client, vector_of):
    _patch(mocker, make_client(dim=4))
    config = embedding_config.model_copy(update={"embedding_batch_size": 2})

    texts = [f"청크 {index}" for index in range(5)]
    vectors = get_embedder(config).embed_documents(texts)

    assert vectors == [vector_of(text, 4) for text in texts]


def test_shuffled_response_is_reordered(mocker, embedding_config, make_client, vector_of):
    """자리로만 믿지 않고 응답의 index 로 제자리를 찾는지."""
    _patch(mocker, make_client(dim=4, shuffle=True))

    texts = ["가", "나", "다", "라"]
    vectors = get_embedder(embedding_config).embed_documents(texts)

    assert vectors == [vector_of(text, 4) for text in texts]


def test_batch_size_of_one(mocker, embedding_config, make_client, vector_of):
    client = make_client(dim=4)
    _patch(mocker, client)
    config = embedding_config.model_copy(update={"embedding_batch_size": 1})

    texts = ["가", "나", "다"]
    vectors = get_embedder(config).embed_documents(texts)

    assert client.call_count == 3
    assert vectors == [vector_of(text, 4) for text in texts]


def test_zero_batch_size_does_not_hang(mocker, embedding_config, make_client):
    """설정을 0으로 잘못 주면 range(0, n, 0) 가 죽는다. 1로 끌어올린다."""
    client = make_client(dim=4)
    _patch(mocker, client)
    config = embedding_config.model_copy(update={"embedding_batch_size": 0})

    assert len(get_embedder(config).embed_documents(["가", "나"])) == 2
    assert client.call_count == 2


def test_every_batch_carries_the_model(mocker, embedding_config, make_client):
    client = make_client(dim=4)
    _patch(mocker, client)
    config = embedding_config.model_copy(
        update={"embedding_batch_size": 2, "embedding_model": "text-embedding-3-large"}
    )

    get_embedder(config).embed_documents(["가", "나", "다"])

    assert {call.model for call in client.calls} == {"text-embedding-3-large"}
