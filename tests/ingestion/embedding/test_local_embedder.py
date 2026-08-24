"""로컬 임베딩 구현체(`LocalEmbedder`)의 계약과 **기본 pytest 방어**를 잠근다.

## 실제 가중치를 절대 안 받는다

모두 `_make_model()` 을 모킹한다. 그 함수가 모듈 레벨인 이유가 이것이고, 인라인화하면
이 파일이 통째로 2.27GB 를 내려받기 시작한다 — 그런 테스트는 CI 에서 조용히 느려지다가
오프라인에서 죽는다.

## 파일 이름

`tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이다. 저장소 전체에서 유일해야 한다.
"""

from __future__ import annotations

from typing import Any

import pytest

from rag_pipeline.config import EmbeddingConfig
from rag_pipeline.exceptions import EmbeddingError
from rag_pipeline.ingestion.embedding import cache_salt_for, get_embedder, local_st
from rag_pipeline.ingestion.embedding.local_st import LocalEmbedder


# sentence-transformers 모델 흉내. **`encode` 에 넘어온 인자를 그대로 보관한다** —
# normalize_embeddings 가 실제로 넘어가는지를 보는 것이 이 파일의 검사 하나다
class FakeModel:
    def __init__(self, dim: int = 4, vectors: list[list[float]] | None = None) -> None:
        self.dim = dim
        self.vectors = vectors
        self.calls: list[dict[str, Any]] = []
        self.tokenizer = lambda texts, add_special_tokens=True: {
            "input_ids": [[0] * max(len(text), 1) for text in texts]
        }

    def encode(self, batch: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append({"batch": list(batch), **kwargs})
        if self.vectors is not None:
            return self.vectors
        return [[float(index + 1)] * self.dim for index, _ in enumerate(batch)]


# 로컬 프로필. 캐시는 tmp_path 로 갈라 테스트끼리 상태를 안 물려받게 한다
@pytest.fixture
def local_config(tmp_path) -> EmbeddingConfig:
    return EmbeddingConfig(
        openai_api_key=None,
        embedding_provider="local",
        embedding_model="nlpai-lab/KURE-v1",
        embedding_cache_enabled=False,
        embedding_cache_dir=tmp_path / "embedding",
    )


# --- 계약: 길이와 자리 ---


# 반환 길이는 입력 길이와 반드시 같다. 호출부가 zip(chunks, vectors) 로 자리를 맞춘다
def test_returns_one_vector_per_input(mocker, local_config) -> None:
    mocker.patch.object(local_st, "_make_model", return_value=FakeModel())
    vectors = LocalEmbedder(local_config).embed_documents(["가", "나", "다"])
    assert len(vectors) == 3
    assert {len(vector) for vector in vectors} == {4}


# 모델이 입력보다 적게 돌려주면 **조용히 짧은 목록을 반환하지 않는다.** 한 칸만 밀려도
# 검색이 엉뚱한 문서를 가리키는데 아무도 모른다
def test_short_result_raises(mocker, local_config) -> None:
    mocker.patch.object(
        local_st, "_make_model", return_value=FakeModel(vectors=[[1.0, 2.0, 3.0, 4.0]])
    )
    with pytest.raises(EmbeddingError, match="개수가 입력과 다릅니다"):
        LocalEmbedder(local_config).embed_documents(["가", "나"])


# 한 배치 안에서 차원이 흔들리면 저장 단계가 아니라 여기서 잡는다
def test_ragged_dimensions_raise(mocker, local_config) -> None:
    mocker.patch.object(
        local_st, "_make_model", return_value=FakeModel(vectors=[[1.0, 2.0], [3.0]])
    )
    with pytest.raises(EmbeddingError, match="차원이 다릅니다"):
        LocalEmbedder(local_config).embed_documents(["가", "나"])


# 빈 입력은 임베딩할 수 없다. OpenAI 쪽과 같은 계약이다
def test_blank_input_raises(mocker, local_config) -> None:
    mocker.patch.object(local_st, "_make_model", return_value=FakeModel())
    with pytest.raises(EmbeddingError, match="비어 있습니다"):
        LocalEmbedder(local_config).embed_documents(["가", "   "])


# 모델이 터지면 EmbeddingError 로 바꿔 올린다. 폴백 층이 없는 자리라 빈 결과를 안 돌려준다
def test_model_failure_becomes_embedding_error(mocker, local_config) -> None:
    model = FakeModel()
    mocker.patch.object(model, "encode", side_effect=RuntimeError("boom"))
    mocker.patch.object(local_st, "_make_model", return_value=model)
    with pytest.raises(EmbeddingError, match="로컬 임베딩에 실패했습니다"):
        LocalEmbedder(local_config).embed_documents(["가"])


# --- 계약: 이 구현체만의 것 ---


# **정규화를 명시적으로 켠다.** indexing 이 Dot 을 쓰는 근거가 "벡터가 단위 길이" 라는
# 실측이라, 체크포인트의 Normalize 모듈에 기대면 모델을 갈아끼울 때 조용히 깨진다
def test_normalization_is_requested_explicitly(mocker, local_config) -> None:
    model = FakeModel()
    mocker.patch.object(local_st, "_make_model", return_value=model)
    LocalEmbedder(local_config).embed_documents(["가"])
    assert model.calls[0]["normalize_embeddings"] is True


# 차원 축소는 **거절한다.** bge-m3 계열은 MRL 미학습이라 앞 N 차원을 잘라 재정규화하면
# 품질이 조용히 나빠진다 — "도는데 틀린" 상태가 제일 늦게 발각된다
def test_dimension_reduction_is_refused(local_config) -> None:
    reduced = local_config.model_copy(update={"embedding_dimensions": 256})
    with pytest.raises(EmbeddingError, match="차원 축소를 지원하지 않습니다"):
        LocalEmbedder(reduced)


# 미설치면 **설치 안내가 담긴** EmbeddingError 다. 새 예외를 만들지 않았으므로
# backend 의 _STATUS_BY_EXCEPTION 매핑은 그대로 쓰인다
def test_missing_dependency_gives_install_hint(mocker, local_config) -> None:
    def _boom(model_name: str) -> Any:
        try:
            raise ImportError("No module named 'sentence_transformers'")
        except ImportError as exc:
            raise EmbeddingError(
                f"sentence-transformers 가 설치되지 않았습니다: {exc}",
                user_message=(
                    "로컬 임베딩을 쓰려면 requirements-local.txt 를 설치해야 합니다: "
                    "uv pip install -r requirements-local.txt"
                ),
            ) from exc

    mocker.patch.object(local_st, "_make_model", side_effect=_boom)
    with pytest.raises(EmbeddingError) as caught:
        LocalEmbedder(local_config).embed_documents(["가"])
    assert "requirements-local.txt" in caught.value.user_message


# 문서와 질의가 같은 자리를 본다. **KURE-v1 은 질의 접두사를 안 쓰므로 지금은 같은 값이다** —
# 접두사 모델을 붙이는 날 이 테스트가 바뀌고 cache_salt_for() 도 함께 바뀐다
def test_query_and_document_agree_without_a_prefix(mocker, local_config) -> None:
    mocker.patch.object(local_st, "_make_model", return_value=FakeModel())
    embedder = LocalEmbedder(local_config)
    assert embedder.embed_query("가") == embedder.embed_documents(["가"])[0]


# 캐시 salt 는 구현체가 짜지 않고 공유 함수에서 온다. 두 구현체가 같은 것을 부르는지가 요점
def test_salt_comes_from_the_shared_rule(local_config) -> None:
    assert LocalEmbedder(local_config)._salt == cache_salt_for(local_config)
    assert LocalEmbedder(local_config)._salt == "embed:nlpai-lab/KURE-v1:default"


# 가중치는 **쓸 때만** 올린다. 캐시가 전량 적중하면 모델을 아예 안 만든다
def test_model_is_not_loaded_until_needed(mocker, local_config) -> None:
    make = mocker.patch.object(local_st, "_make_model", return_value=FakeModel())
    embedder = LocalEmbedder(local_config)
    assert make.call_count == 0
    embedder.embed_documents([])
    assert make.call_count == 0
    embedder.embed_documents(["가"])
    assert make.call_count == 1


# 토큰 상한은 로컬에서도 본다. 과금은 0이지만 "이 코퍼스에서 사고를 의심할 구간" 이라는
# 두 번째 목적은 provider 를 안 탄다
def test_token_ceiling_still_applies(mocker, local_config) -> None:
    mocker.patch.object(local_st, "_make_model", return_value=FakeModel())
    tight = local_config.model_copy(update={"max_embedding_tokens_per_doc": 3})
    with pytest.raises(EmbeddingError, match="토큰 상한"):
        LocalEmbedder(tight).embed_documents(["가나다라마바사"])


# --- 팩토리와 기본 pytest 방어 ---


# get_embedder() 가 provider 로 갈린다. 호출부는 어느 구현체인지 모른다
def test_factory_branches_on_provider(local_config) -> None:
    from rag_pipeline.ingestion.embedding.openai_api import OpenAIEmbedder

    assert isinstance(get_embedder(local_config), LocalEmbedder)
    assert isinstance(
        get_embedder(local_config.model_copy(update={"embedding_provider": "openai"})),
        OpenAIEmbedder,
    )


# **여기가 이 파일의 제일 중요한 검사다.** 로컬 갈래는 키가 없어도 돌기 때문에, 지금까지의
# 방어("키를 지운다")로는 기본 pytest 가 모델 가중치를 내려받는 경로를 못 막는다.
# offline_config 가 provider 를 못 박는 것이 그 방어이고, 지워지면 여기서 걸린다
def test_offline_fixture_pins_the_provider(offline_config) -> None:
    assert offline_config.embedding.embedding_provider == "openai"
    assert offline_config.embedding.openai_api_key is None
