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
        # 조회와 저장에 넘어온 지문. **둘이 같아야 한다** — 다르면 방금 저장한 답이
        # 다음 질의에서 자기 지문과 안 맞아 영원히 미스가 나는데 에러가 안 난다
        self.fingerprints: list[str] = []

    def lookup(self, query: str, *, embedder: object, fingerprint: str) -> Answer | None:
        self.fingerprints.append(fingerprint)
        return self._hit

    def put(self, answer: Answer, *, embedder: object, fingerprint: str) -> bool:
        self.fingerprints.append(fingerprint)
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


# 재작성기 노릇을 하는 최소 가짜. **`last_rewritten` 을 갖는 것이 요점이다** — 재작성 흔적을
# 읽는 자리가 `retrieve()` 의 반환이 아니라 이 인스턴스라, 여기가 곧 그 계약이다
class _FakeRewriter:
    def __init__(self, last_rewritten: str | None = None, mode: str = "fuse") -> None:
        self.last_rewritten = last_rewritten
        self.mode = mode


# **재작성기를 그대로 retrieve() 에 넘긴다.** 리랭커와 같은 모양이고, 여기서 하는 일도
# 수명을 정해 넘기는 것뿐이다 — 실제로 붙는 자리는 retrieve() **안**(1차 검색 뒤·합류 앞)이다.
# 이 배선이 끊기면 .env 를 켜도 아무 일이 안 일어나는데 **에러가 안 난다**
def test_rewriter_is_passed_through_to_retrieve(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    rewriter = _FakeRewriter()

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
        "원 질의입니다", store=object(), embedder=object(), config=config, rewriter=_FakeRewriter()
    )

    assert retrieve.call_args.args[0] == "원 질의입니다"


# **캐시가 히트하면 재작성도 안 돈다** — retrieve() 를 통째로 건너뛰기 때문이다.
# 리랭커와 같은 자리인데 이쪽은 대가가 돈이다: 그 건너뜀이 곧 유료 호출 하나를 아끼는 것이다
def test_cache_hit_skips_the_rewriter_too(mocker, config):
    retrieve = mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="새 답변")
    cached = Answer(query="질문", text="옛 답변", sources=[])

    # **여기만 일부러 맨 `object()` 다** — 캐시 경로가 재작성기의 무엇도 안 읽는다는 것을
    # 타입으로 못 박는다. 흔적을 읽는 줄이 캐시 검사 위로 올라가면 AttributeError 로 터진다
    answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        rewriter=object(),
        cache=_FakeCache(cached),
    )

    assert retrieve.call_args is None


# **다시 쓴 질의가 `Answer` 에 실린다.** 화면 배지와 `query_log` 가 둘 다 이 하나를 읽으므로,
# 여기가 끊기면 재작성은 도는데 흔적만 사라진다 — 그리고 그것이 실제로 한 번 일어났다
# (2026-08-27, 서버가 두 벌 떠 있어 낡은 쪽이 답한 것을 캐시 수로만 알아냈다)
def test_rewrite_trace_is_carried_on_the_answer(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question(
        "지원 방식 알려줘",
        store=object(),
        embedder=object(),
        config=config,
        rewriter=_FakeRewriter("2026 중등 기초학력 보장 사업의 지원 방식", mode="fuse"),
    )

    assert answer.rewrite is not None
    assert answer.rewrite.query == "2026 중등 기초학력 보장 사업의 지원 방식"
    # **mode 가 같은 덩어리로 온다** — 화면 문구가 여기서 갈린다(fuse 는 원 질의도 함께 돌았다)
    assert answer.rewrite.mode == "fuse"
    # 원 질의는 그대로 남는다. 둘을 나란히 놓는 것이 이 흔적의 목적이다
    assert answer.query == "지원 방식 알려줘"


# **폴백이면 흔적이 없다.** 재작성기를 넘겼어도 호출이 실패했으면 원 질의로만 검색한 것이라,
# 배지를 그리면 사용자에게 거짓을 보여준다
def test_fallback_leaves_no_trace(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        rewriter=_FakeRewriter(None),
    )

    assert answer.rewrite is None


# 재작성기를 안 넘기면 흔적도 없다. 꺼짐·폴백·캐시 히트가 화면에서 같은 것을 하는 자리다
def test_no_rewriter_leaves_no_trace(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")

    answer = answer_question("질문", store=object(), embedder=object(), config=config)

    assert answer.rewrite is None


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


# ---------------------------------------------------------------- 파이프라인 지문


# **조회와 저장이 같은 지문을 써야 한다.** 다르면 방금 저장한 답이 다음 질의에서 자기
# 지문과 안 맞아 영원히 미스가 나는데 **에러가 안 난다** — 캐시가 켜져 있는데 아무 일도
# 안 하는 상태다. 그래서 지문을 한 번만 만들어 둘에 넘긴다
def test_cache_lookup_and_put_share_one_fingerprint(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    cache = _FakeCache(hit=None)

    answer_question("질문", store=object(), embedder=object(), config=config, cache=cache)

    assert len(cache.fingerprints) == 2
    assert cache.fingerprints[0] == cache.fingerprints[1]


# **설정이 아니라 실제로 넘어온 객체가 지문을 정한다.** 모델 이름이 차 있어도 부르는 쪽이
# 객체를 안 넘기면 그 단계는 안 도는데(`evaluation/run_eval.py` 가 그렇게 부른다),
# 설정만 보고 지문을 만들면 두 실행이 같은 지문을 달고 서로의 답을 히트한다
def test_fingerprint_follows_the_objects_not_the_config(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    plain = _FakeCache(hit=None)
    reranked = _FakeCache(hit=None)

    answer_question("질문", store=object(), embedder=object(), config=config, cache=plain)
    answer_question(
        "질문",
        store=object(),
        embedder=object(),
        config=config,
        cache=reranked,
        reranker=object(),
    )

    assert plain.fingerprints[0] != reranked.fingerprints[0]


# 유효 `top_k` 도 지문에 든다 — 근거를 몇 개 물었는지가 답 본문을 가른다
def test_fingerprint_follows_the_effective_top_k(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답변")
    default = _FakeCache(hit=None)
    explicit = _FakeCache(hit=None)

    answer_question("질문", store=object(), embedder=object(), config=config, cache=default)
    answer_question(
        "질문", store=object(), embedder=object(), config=config, cache=explicit, top_k=11
    )

    assert default.fingerprints[0] != explicit.fingerprints[0]


# --- 인용 검사 ----------------------------------------------------------------


# 인용이 근거와 어긋나면 흔적이 답변에 실린다. **답변 본문은 그대로다** —
# `verdict` 와 같은 계약이라, 고치기 시작하면 *검사가 맞았나* 와 *답이 좋아졌나* 가
# 한 실행에 섞인다
def test_citation_trace_rides_along_on_the_answer(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답은 [2] 입니다.")

    answer = answer_question("질문", store=object(), embedder=object(), config=config)

    assert answer.citation is not None
    assert answer.citation.problem == "out_of_range"
    assert answer.citation.numbers == [2]
    assert answer.text == "답은 [2] 입니다."


# 맞게 인용하면 흔적이 없다 — 늘 채우면 화면에 줄이 하나 늘어 정상이 소음이 된다
def test_valid_citation_leaves_no_trace_on_the_answer(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="답은 [1] 입니다.")

    answer = answer_question("질문", store=object(), embedder=object(), config=config)

    assert answer.citation is None


# **캐시가 돌려준 답에도 같은 검사를 먹인다.** 캐시는 이 값을 안 싣는데(payload 가 필드를
# 하나씩 나열한다) 검사가 (text, sources) 만 보는 순수 함수라 다시 계산이 공짜다.
# 안 먹이면 글자가 똑같은 답변이 캐시냐 아니냐로 배지가 갈리고 그것을 사용자가 본다 —
# `verdict` 가 캐시 히트에서 None 인 것과 **일부러 다르다**(저쪽은 실행의 속성이다)
def test_cache_hit_is_checked_again(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[_chunk()])
    mocker.patch.object(query_module, "generate_answer", return_value="새 답변")
    cached = Answer(
        query="옛 질문",
        text="옛 답은 [9] 입니다.",
        sources=[_chunk()],
        cached_from=CacheOrigin(query="옛 질문", created_at=datetime.now(UTC), score=0.97),
    )

    answer = answer_question(
        "질문", store=object(), embedder=object(), config=config, cache=_FakeCache(cached)
    )

    assert answer.text == "옛 답은 [9] 입니다."
    assert answer.citation is not None
    assert answer.citation.numbers == [9]
    # 캐시 배지는 그대로 산다 — 검사가 캐시 흔적을 덮어쓰면 안 된다
    assert answer.cached_from is not None


# 근거를 못 찾은 답변에는 흔적이 없다. **검색 0건은 실패가 아니라 정상 경로**이고,
# 여기가 안 갈리면 그 답변마다 인용 경고가 뜬다
def test_no_evidence_answer_leaves_no_trace(mocker, config):
    mocker.patch.object(query_module, "retrieve", return_value=[])
    mocker.patch.object(
        query_module, "generate_answer", return_value="문서에서 찾지 못했습니다."
    )

    answer = answer_question("아무거나", store=object(), embedder=object(), config=config)

    assert answer.citation is None
