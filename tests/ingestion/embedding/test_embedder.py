"""임베딩 구현체의 계약 — 길이 보장과 실패 경로.

이 파일이 지키는 것은 하나다: **`len(반환) == len(입력)` 이거나 예외다.** 호출부가
`zip(chunks, vectors)` 로 자리를 맞추므로 짧은 목록이 조용히 돌아오면 검색이 엉뚱한 문서를
가리키게 된다. 그래서 실패 경로 테스트가 성공 경로보다 많다.

`FakeClient` 와 `fake_vector` 는 `make_client` · `vector_of` 픽스처로 받는다 — `tests/` 에
`__init__.py` 가 없어 테스트 모듈끼리 import 할 수 없다.
"""

from __future__ import annotations

from typing import Any

import pytest

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.exceptions import EmbeddingError
from rag_pipeline.ingestion.embedding import get_embedder, openai_api


# _make_client 를 가짜로 갈아끼운다. **이 함수가 모듈 레벨이라 가능한 것이다**
def _patch(mocker, client: Any):
    return mocker.patch.object(openai_api, "_make_client", return_value=client)


def test_document_count_matches_input(mocker, embedding_config, make_client):
    client = make_client(dim=8)
    _patch(mocker, client)

    texts = ["첫 번째 청크", "두 번째 청크", "세 번째 청크"]
    vectors = get_embedder(embedding_config).embed_documents(texts)

    assert len(vectors) == len(texts)
    assert {len(vector) for vector in vectors} == {8}


def test_vectors_match_their_own_text(mocker, embedding_config, make_client, vector_of):
    """자리가 밀리면 여기서 걸린다 — 각 벡터가 자기 텍스트에서 나온 값이어야 한다."""
    _patch(mocker, make_client(dim=8))

    texts = ["가", "나", "다"]
    vectors = get_embedder(embedding_config).embed_documents(texts)

    assert vectors == [vector_of(text, 8) for text in texts]


def test_query_and_document_share_the_space(mocker, embedding_config, make_client):
    """지금은 글자 그대로 같은 값이다. 분리는 로컬 모델 대비일 뿐이라는 사실의 확인."""
    _patch(mocker, make_client(dim=8))
    embedder = get_embedder(embedding_config)

    assert embedder.embed_query("연차휴가는 며칠인가?") == embedder.embed_documents(
        ["연차휴가는 며칠인가?"]
    )[0]


def test_empty_input_does_not_call_api(mocker, embedding_config, make_client):
    client = make_client()
    _patch(mocker, client)

    assert get_embedder(embedding_config).embed_documents([]) == []
    assert client.call_count == 0


def test_dimensions_are_forwarded(mocker, embedding_config, make_client):
    client = make_client(dim=8)
    _patch(mocker, client)
    config = embedding_config.model_copy(update={"embedding_dimensions": 4})

    vectors = get_embedder(config).embed_documents(["가"])

    assert client.calls[0].dimensions == 4
    assert len(vectors[0]) == 4


# --- 실패 경로 --------------------------------------------------------------


def test_missing_key_raises(mocker, embedding_config):
    """폴백 층이 없으므로 빈 리스트가 아니라 예외다."""
    _patch(mocker, None)
    config = embedding_config.model_copy(update={"openai_api_key": None})

    with pytest.raises(EmbeddingError, match="OPENAI_API_KEY"):
        get_embedder(config).embed_documents(["가"])


def test_api_failure_raises(mocker, embedding_config, make_client):
    _patch(mocker, make_client(error=RuntimeError("서비스 장애")))

    with pytest.raises(EmbeddingError, match="RuntimeError"):
        get_embedder(embedding_config).embed_documents(["가"])


def test_short_response_raises(mocker, embedding_config, make_client):
    """응답이 요청보다 적게 오면 조용히 짧은 목록을 돌려주지 않는다."""
    _patch(mocker, make_client(drop=1))

    with pytest.raises(EmbeddingError, match="응답 개수"):
        get_embedder(embedding_config).embed_documents(["가", "나", "다"])


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_raises(mocker, embedding_config, make_client, text):
    """0벡터로 때우면 검색 구멍이 조용히 생긴다."""
    client = make_client()
    _patch(mocker, client)

    with pytest.raises(EmbeddingError, match="비어 있"):
        get_embedder(embedding_config).embed_documents(["정상 텍스트", text])
    assert client.call_count == 0


def test_broken_vector_raises_embedding_error(mocker, embedding_config, make_client):
    """손상된 응답에서 AttributeError·TypeError 가 날것으로 새면 backend 가 500 을 낸다."""
    _patch(mocker, make_client(broken_vector=True))

    with pytest.raises(EmbeddingError, match="벡터를 읽지 못했습니다"):
        get_embedder(embedding_config).embed_documents(["가"])


def test_ragged_dimensions_raise(mocker, embedding_config, make_client):
    _patch(mocker, make_client(dim=8, ragged=True))

    with pytest.raises(EmbeddingError, match="차원이 다릅니다"):
        get_embedder(embedding_config).embed_documents(["가", "나"])


def test_dimension_mismatch_with_config_raises(mocker, embedding_config, make_client):
    """응답 차원이 요청과 다르면 저장 단계가 아니라 여기서 잡는다."""
    client = make_client(dim=8)
    # 요청의 dimensions 를 무시하고 항상 8차원을 돌려주는 서버를 흉내낸다
    client.dimensions_for_call = lambda params: 8
    _patch(mocker, client)
    config = embedding_config.model_copy(update={"embedding_dimensions": 4})

    with pytest.raises(EmbeddingError, match="응답 차원"):
        get_embedder(config).embed_documents(["가"])


# --- 비용 --------------------------------------------------------------------


def test_usage_counts_only_successful_calls(mocker, embedding_config, make_client):
    _patch(mocker, make_client(dim=8))
    embedder = get_embedder(embedding_config)

    embedder.embed_documents(["가", "나"])

    assert embedder.usage.api_calls == 1
    assert embedder.usage.total_tokens == 20  # FakeClient: 텍스트당 10


def test_failed_call_does_not_count(mocker, embedding_config, make_client):
    _patch(mocker, make_client(error=RuntimeError("장애")))
    embedder = get_embedder(embedding_config)

    with pytest.raises(EmbeddingError):
        embedder.embed_documents(["가"])

    assert embedder.usage.api_calls == 0
    assert embedder.usage.total_tokens == 0


def test_usage_falls_back_to_local_count(mocker, embedding_config, make_client):
    """응답에 usage 가 없어도 비용이 0으로 보이면 안 된다."""
    _patch(mocker, make_client(dim=8, report_usage=False))
    embedder = get_embedder(embedding_config)

    embedder.embed_documents(["한국어 텍스트입니다"])

    assert embedder.usage.total_tokens > 0


def test_token_cap_blocks_before_calling(mocker, embedding_config, make_client):
    """상한은 보낸 뒤가 아니라 보내기 전에 본다 — Vision 의 호출 상한과 같은 자리."""
    client = make_client()
    _patch(mocker, client)
    config = embedding_config.model_copy(update={"max_embedding_tokens_per_doc": 1})

    with pytest.raises(EmbeddingError, match="토큰 상한"):
        get_embedder(config).embed_documents(["상한을 넘길 만큼 충분히 긴 한국어 문장입니다"])
    assert client.call_count == 0


def test_usage_reset_reopens_the_budget(mocker, embedding_config, make_client):
    """상한은 reset() 사이의 구간에 걸린다 — 문서당으로 쓰려면 호출부가 리셋한다."""
    _patch(mocker, make_client(dim=8))
    # FakeClient 는 텍스트당 10토큰을 보고하므로 첫 호출 뒤 누적이 20이다. 상한 21이면
    # 두 번째 호출은 (한글 두 글자 = 최소 2토큰) 반드시 넘고, 첫 호출은 반드시 안 넘는다
    config = embedding_config.model_copy(update={"max_embedding_tokens_per_doc": 21})
    embedder = get_embedder(config)

    embedder.embed_documents(["가", "나"])
    with pytest.raises(EmbeddingError, match="토큰 상한"):
        embedder.embed_documents(["다", "라"])

    embedder.usage.reset()
    assert len(embedder.embed_documents(["다", "라"])) == 2


# --- 앞 단계와의 연결 ---------------------------------------------------------


def test_splitter_output_feeds_straight_in(mocker, embedding_config, make_client):
    """splitter 가 내놓은 `Chunk.text` 를 그대로 넘길 수 있어야 한다.

    Embedder 는 `Chunk` 를 모른다 — 상위가 `[c.text for c in chunks]` 를 만든다.
    그래야 질의 임베딩과 대칭이 유지된다.
    """
    from rag_pipeline.ingestion.splitter import split
    from rag_pipeline.schemas.document import (
        DocumentMetadata,
        Element,
        ElementType,
        FileFormat,
        ParsedDocument,
    )

    elements = [
        Element(type=ElementType.TITLE, text="제1장 총칙", order=1),
        Element(type=ElementType.PARAGRAPH, text="이 규정은 다음을 목적으로 한다.", order=2),
        Element(type=ElementType.TITLE, text="제2장 적용", order=3),
        Element(type=ElementType.PARAGRAPH, text="적용 범위는 다음과 같다.", order=4),
    ]
    document = ParsedDocument(
        metadata=DocumentMetadata(
            source_name="규정.hwpx", file_format=FileFormat.HWPX, size_bytes=100
        ),
        elements=elements,
    )
    chunks = split(document)

    client = make_client(dim=8)
    _patch(mocker, client)
    vectors = get_embedder(embedding_config).embed_documents([c.text for c in chunks])

    assert len(chunks) >= 2
    assert len(vectors) == len(chunks)
    # 청크와 벡터를 자리로 맞춰도 안전하다는 것이 이 단계의 계약이다
    assert all(len(vector) == 8 for _, vector in zip(chunks, vectors, strict=True))


def test_get_embedder_without_config_reads_env():
    """설정을 안 주면 EmbeddingConfig() 를 만든다 — 스크립트에서 그냥 부를 수 있어야 한다."""
    assert get_embedder() is not None
    assert isinstance(EmbeddingConfig(), EmbeddingConfig)
