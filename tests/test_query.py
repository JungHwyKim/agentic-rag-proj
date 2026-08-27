"""세 번째 진입점 answer_question(). 검색 -> 생성 합성만 본다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rag_pipeline import query as query_module
from rag_pipeline.config import GenerationConfig, IngestConfig
from rag_pipeline.query import answer_question
from rag_pipeline.schemas.answer import Answer, CacheOrigin, EvaluatorVerdict
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


# --- 답변 캐시 ----------------------------------------------------------------


# 캐시 자리. lookup 이 무엇을 돌려줄지만 정하면 된다
class _FakeCache:
    def __init__(self, hit: Answer | None = None) -> None:
        self._hit = hit
        self.put_calls: list[Answer] = []

    def lookup(self, query: str, *, embedder: object) -> Answer | None:
        return self._hit

    def put(self, answer: Answer, *, embedder: object) -> bool:
        self.put_calls.append(answer)
        return True


# **캐시를 안 넘기면 기존 경로가 한 줄도 안 바뀐다.** 안 넘기는 것이 곧 끄는 스위치라
# 이 성질이 깨지면 설정 없이 끄는 방법이 사라진다
def test_without_cache_the_path_is_unchanged(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    generate = mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question("질문", store=object(), embedder=object(), config=config)

    assert retrieve.call_count == 1
    assert generate.call_count == 1
    assert answer.cached_from is None


# 히트하면 **검색도 생성도 안 돈다.** 이것이 이 기능의 절약 전부다 —
# retrieve() 뒤에 두면 아끼는 것이 LLM 호출 하나뿐이다
def test_cache_hit_skips_retrieval_and_generation(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    generate = mocker.patch.object(query_module, "generate_answer", return_value="새 답변")
    cached = Answer(
        query="옛 질문",
        text="옛 답변",
        sources=[_chunk()],
        cached_from=CacheOrigin(query="옛 질문", created_at=datetime.now(UTC), score=0.97),
    )

    answer = answer_question(
        "질문", store=object(), embedder=object(), config=config, cache=_FakeCache(cached)
    )

    assert answer.text == "옛 답변"
    assert retrieve.call_count == 0
    assert generate.call_count == 0


# 미스면 평소대로 돌고, 만든 답을 캐시에 넣는다
def test_cache_miss_runs_normally_and_stores(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    cache = _FakeCache(hit=None)

    answer = answer_question(
        "질문", store=object(), embedder=object(), config=config, cache=cache
    )

    assert answer.text == "답변"
    assert cache.put_calls == [answer]


# --- 재작성(Rewriter) ------------------------------------------------------------


# **재작성기를 그대로 retrieve() 에 넘긴다.** 리랭커와 같은 모양이고, 여기서 하는 일도
# 수명을 정해 넘기는 것뿐이다 — 실제로 붙는 자리는 retrieve() **안**(1차 검색 뒤·합류 앞)이다.
# 이 배선이 끊기면 .env 를 켜도 아무 일이 안 일어나는데 **에러가 안 난다**
def test_rewriter_is_passed_through_to_retrieve(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    rewriter = object()

    answer_question(
        "질문", store=object(), embedder=object(), config=config, rewriter=rewriter
    )

    assert retrieve.call_args.kwargs["rewriter"] is rewriter


# 안 넘기면 None 이 간다. bm25·reranker·cache 와 같은 스위치다
def test_without_rewriter_none_is_passed(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer_question("질문", store=object(), embedder=object(), config=config)

    assert retrieve.call_args.kwargs["rewriter"] is None


# **여기서 질의를 미리 다시 쓰지 않는다.** retrieve() 에 가는 질의는 원 질의 그대로여야
# 하고, 재작성은 그 안에서 일어난다 — 여기서 하면 run_eval 이 retrieve() 를 직접 부르므로
# 평가가 그 경로를 안 밟아 **재작성을 영원히 못 잰다**(7회차 기각 항목)
def test_query_reaches_retrieve_unchanged(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer_question(
        "원 질의입니다", store=object(), embedder=object(), config=config, rewriter=object()
    )

    assert retrieve.call_args.args[0] == "원 질의입니다"


# **캐시가 히트하면 재작성도 안 돈다** — retrieve() 를 통째로 건너뛰기 때문이다.
# 리랭커와 같은 자리인데 이쪽은 대가가 돈이다: 그 건너뜀이 곧 유료 호출 하나를 아끼는 것이다
def test_cache_hit_skips_the_rewriter_too(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="새 답변")
    cached = Answer(query="질문", text="옛 답변", sources=[])

    answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        rewriter=object(),
        cache=_FakeCache(cached),
    )

    assert retrieve.call_args is None


# --- 리랭커 --------------------------------------------------------------------


# **리랭커를 그대로 retrieve() 에 넘긴다.** 여기서 하는 일은 수명을 정해 넘기는 것뿐이고,
# 실제로 붙는 자리는 retrieve() **안**이다(RRF 뒤·절단 앞). 이 배선이 끊기면 리랭커를
# 설정해도 아무 일이 안 일어나는데 **에러가 안 난다** — 순서가 그냥 예전 그대로다
def test_reranker_is_passed_through_to_retrieve(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    reranker = object()

    answer_question(
        "질문", store=object(), embedder=object(), config=config, reranker=reranker
    )

    assert retrieve.call_args.kwargs["reranker"] is reranker


# 안 넘기면 None 이 간다. bm25·cache 와 같은 스위치다
def test_without_reranker_none_is_passed(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer_question("질문", store=object(), embedder=object(), config=config)

    assert retrieve.call_args.kwargs["reranker"] is None


# **캐시가 히트하면 리랭커도 안 돈다.** retrieve() 를 통째로 건너뛰기 때문인데, 그래서
# 리랭커를 켜기 전에 쌓인 캐시 항목은 켠 뒤에도 리랭킹 안 된 순서로 서빙된다.
# 지금은 캐시가 기본으로 꺼져 있어 안 물리지만, **"리랭커가 늘 돈다"고 쓰면 안 되는 자리다**
def test_cache_hit_skips_the_reranker_too(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="새 답변")
    cached = Answer(query="질문", text="옛 답변", sources=[])

    answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        reranker=object(),
        cache=_FakeCache(cached),
    )

    assert retrieve.call_count == 0


# 판정하는 척하는 가짜. 실제 구현은 tests/generation/test_evaluate.py 가 잠근다
class _FakeEvaluator:
    def __init__(self, verdict: str = "sufficient", reasoning: str = "") -> None:
        self._verdict = verdict
        self._reasoning = reasoning
        self.calls: list[tuple[str, list[RetrievedChunk]]] = []

    def evaluate(self, query: str, chunks: list[RetrievedChunk]) -> EvaluatorVerdict:
        self.calls.append((query, chunks))
        return EvaluatorVerdict(verdict=self._verdict, reasoning=self._reasoning)


# **Evaluator 를 안 넘기면 기존 경로가 한 줄도 안 바뀐다.** bm25·reranker·cache 와 같은
# 스위치라, 이 성질이 깨지면 설정 없이 끄는 방법이 사라진다
def test_without_evaluator_the_path_is_unchanged(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question("질문", store=object(), embedder=object(), config=config)

    assert answer.verdict is None
    assert answer.text == "답변"


# 넘기면 판정이 실린다. **판정이 본 것은 생성이 보는 것과 같아야 한다**
def test_verdict_rides_along_on_the_answer(mocker, config):
    chunks = [_chunk()]
    mocker.patch.object(query_module, "retrieve", return_value=chunks)
    generate = mocker.patch.object(query_module, "generate_answer", return_value="답변")
    evaluator = _FakeEvaluator("sufficient", "조항이 그대로 있습니다")

    answer = answer_question(
        "질문", store=object(), embedder=object(), config=config, evaluator=evaluator
    )

    assert answer.verdict is not None
    assert answer.verdict.verdict == "sufficient"
    assert evaluator.calls[0] == ("질문", chunks)
    assert generate.call_args.args[1] == chunks


# ***불충분* 이어도 생성은 그대로 돈다.** 재시도할 곳이 없고, 생성을 바꾸면 판정 수치와
# 답변 변화가 한 실행에 섞인다 (query.py docstring)
def test_insufficient_does_not_block_generation(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    generate = mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        evaluator=_FakeEvaluator("insufficient"),
    )

    assert answer.verdict.verdict == "insufficient"
    assert answer.text == "답변"
    assert generate.call_count == 1


# **실패는 판정 하나로 접히지 않고 그대로 나간다.** 여기서 불충분으로 적으면 러너가
# 실패를 따로 못 세고, 점수가 실력이 아니라 실패율을 잰다
def test_failed_verdict_is_carried_as_is(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        evaluator=_FakeEvaluator("failed", "판정을 얻지 못했습니다"),
    )

    assert answer.verdict.verdict == "failed"
    assert answer.text == "답변"


# **캐시가 히트하면 판정도 안 돈다** — 그래서 캐시에서 온 답은 verdict 가 None 이다.
# 그 답이 판정을 거쳤다고 말할 수 없기 때문이고, cached_from 이 캐시 여부를 혼자 판정하는
# 것과 같은 모양이다
def test_cache_hit_skips_the_evaluator(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="새 답변")
    evaluator = _FakeEvaluator()

    answer = answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        evaluator=evaluator,
        cache=_FakeCache(Answer(query="질문", text="옛 답변", sources=[])),
    )

    assert answer.text == "옛 답변"
    assert answer.verdict is None
    assert evaluator.calls == []
