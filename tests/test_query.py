"""세 번째 진입점 answer_question(). 검색 -> 생성 합성만 본다."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline import query as query_module
from rag_pipeline.config import GenerationConfig, IngestConfig
from rag_pipeline.query import answer_question
from rag_pipeline.schemas.answer import Answer
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 검색으로 돌아온 청크 하나
def _chunk(chunk_id: str = "안내서.pdf:0001") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="안내서.pdf",
        text="제12조 …",
        metadata={"source_name": "안내서.pdf", "page": 4},
        dense_score=0.9,
    )


# 키를 지운 설정. GenerationConfig() 가 .env 를 읽으므로 안 지우면 실키가 실린다
@pytest.fixture
def config(tmp_path: Path) -> IngestConfig:
    cfg = IngestConfig()
    object.__setattr__(cfg, "_generation", GenerationConfig(openai_api_key=None))
    return cfg


# 검색 결과가 그대로 sources 로 나가고, 생성에도 같은 것이 넘어간다
def test_sources_are_retrieved_chunks(mocker, config):
    chunks = [_chunk()]
    mocker.patch.object(query_module, "retrieve", return_value=chunks)
    generate = mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question("질문", store=object(), embedder=object(), config=config)

    assert isinstance(answer, Answer)
    assert answer.query == "질문"
    assert answer.text == "답변"
    assert answer.sources == chunks
    # 검색된 것이 그대로 생성에 간다 — 사이에서 추리면 답변과 소스 태그가 어긋난다
    assert generate.call_args.args[1] == chunks


# **sources 는 비어 있을 수 있다.** 근거를 못 찾은 것은 실패가 아니라 정상 경로다
def test_empty_sources_is_not_an_error(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[])
    mocker.patch.object(
        query_module, "generate_answer", return_value="문서에서 찾지 못했습니다."
    )

    answer = answer_question("아무거나", store=object(), embedder=object(), config=config)

    assert answer.sources == []
    assert answer.text == "문서에서 찾지 못했습니다."


# top_k 를 안 주면 설정의 generation_top_k 를 쓴다
def test_top_k_defaults_to_config(mocker, config):
    object.__setattr__(
        config, "_generation", GenerationConfig(openai_api_key=None, generation_top_k=9)
    )
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[])
    mocker.patch.object(query_module, "generate_answer", return_value="x")

    answer_question("질문", store=object(), embedder=object(), config=config)

    assert retrieve.call_args.kwargs["top_k"] == 9


# 인자로 준 top_k 가 설정을 이긴다
def test_explicit_top_k_wins(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[])
    mocker.patch.object(query_module, "generate_answer", return_value="x")

    answer_question("질문", store=object(), embedder=object(), config=config, top_k=3)

    assert retrieve.call_args.kwargs["top_k"] == 3


# store·embedder 를 그대로 넘긴다. 여기서 새로 열면 startup 싱글턴과 잠금이 충돌한다
def test_store_and_embedder_are_passed_through(mocker, config):
    store, embedder = object(), object()
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[])
    mocker.patch.object(query_module, "generate_answer", return_value="x")

    answer_question("질문", store=store, embedder=embedder, config=config)

    assert retrieve.call_args.kwargs["store"] is store
    assert retrieve.call_args.kwargs["embedder"] is embedder
