"""질의 재작성 배선과 구현체. 외부 API 를 안 건드린다.

제일 중요한 것 셋이다.

1. **`rewriter=None` 이면 예전과 글자 그대로 같다.** 깨지면 비교의 대조군이 함께 움직여
   무엇이 달라졌는지 못 가른다 — `bm25=None`·`reranker=None` 동등성과 같은 자리다
2. **실패하면 1차 pool 로 내려간다.** 그리고 그 pool 은 재작성을 안 켠 결과와 같아야 한다.
   재작성이 예외를 던지면 폴백이 아니라 500 이 된다
3. **`fuse` 와 `replace` 가 실제로 다른 갈래를 돈다.** 둘이 같은 결과를 내면 이 축이 통째로
   뜻을 잃는데, 그것이 **에러 없이** 일어난다

**가짜 저장소가 질의마다 다른 결과를 준다** — 안 그러면 두 모드가 같은 것을 합쳐 구분이
안 되고, 테스트가 통과하면서 아무것도 안 잰다.

모킹 이음새는 `rewrite._make_client()` 다. 인라인하면 기본 pytest 가 OpenAI 로 나간다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.config import RetrievalConfig, RewriteConfig
from rag_pipeline.retrieval import get_rewriter, retrieve
from rag_pipeline.retrieval import rewrite as rewrite_mod
from rag_pipeline.retrieval.bm25 import Bm25Index
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import SearchResult

# 원 질의와 재작성 질의. 가짜 저장소가 이 둘을 갈라 다른 결과를 준다
_ORIGINAL = "개인정보를 함부로 지우면 어떻게 되나요"
_REWRITTEN = "공공기록물 관리에 관한 법률 벌칙 손상 제51조"


# 질의 문자열을 그대로 벡터 자리에 실어 나른다. 가짜 저장소가 그것으로 갈래를 가른다
class FakeEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [float(len(text)), 0.0, 0.0]


# 질의마다 다른 결과를 준다. `by_query` 에 없는 질의는 빈 결과다
class FakeStore:
    def __init__(self, by_query: dict[str, list[SearchResult]]) -> None:
        self._by_query = by_query
        self._lengths = {float(len(q)): q for q in by_query}
        self.top_ks: list[int] = []

    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        self.top_ks.append(top_k)
        query = self._lengths.get(query_vector[0])
        return self._by_query.get(query or "", [])[:top_k]

    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]
        return iter([])


def _result(chunk_id: str, score: float, text: str = "본문") -> SearchResult:
    return SearchResult(
        id=f"uuid-{chunk_id}",
        score=score,
        text=text,
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"source_name": "안내서.pdf"},
    )


# OpenAI 응답 모양. `choices[0].message.content` 만 본다
class _Message:
    def __init__(self, content: object) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: object) -> None:
        self.message = _Message(content)


class _Usage:
    def __init__(self, total_tokens: int) -> None:
        self.total_tokens = total_tokens


class _Response:
    def __init__(self, content: object, total_tokens: int = 42) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage(total_tokens)


# 보낸 인자를 기록하는 가짜 클라이언트
class FakeClient:
    def __init__(self, content: object = None, error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.calls: list[dict] = []
        self.chat = self

    @property
    def completions(self):  # noqa: ANN201 - 자기 자신. client.chat.completions.create 모양
        return self

    def create(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _Response(self._content)


def _config(tmp_path, **over) -> RewriteConfig:
    values = {
        "openai_api_key": "sk-test",
        "rewrite_model": "gpt-4.1-mini",
        "rewrite_cache_enabled": False,
        "rewrite_cache_dir": tmp_path / "rewrite",
    }
    values.update(over)
    return RewriteConfig(**values)


def _context(count: int = 3) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=f"c{index}",
            document_id="안내서.pdf",
            text=f"조각 {index}",
            metadata={},
        )
        for index in range(count)
    ]


# --- 스위치 -------------------------------------------------------------------


# 모델 이름이 없으면 통째로 꺼진다. **저장소 커밋본이 이 상태다**
def test_no_model_means_off(tmp_path) -> None:
    assert get_rewriter(_config(tmp_path, rewrite_model=None)) is None


# 이름이 있으면 만들어진다. 키가 없어도 만들어지는 것이 맞다 — 꺼짐은 모델 이름이 정한다
def test_model_name_builds_a_rewriter(tmp_path) -> None:
    assert get_rewriter(_config(tmp_path, openai_api_key=None)) is not None


# --- 구현체 -------------------------------------------------------------------


# 성공하면 다시 쓴 질의가 나오고 비용이 오른다. **카운터는 성공한 뒤에만 오른다**
def test_rewrite_returns_the_query(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "다시 쓴 질문"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    rewriter = get_rewriter(_config(tmp_path))
    assert rewriter is not None
    assert rewriter.rewrite(_ORIGINAL, _context()) == "다시 쓴 질문"
    assert rewriter.usage.api_calls == 1
    assert rewriter.usage.total_tokens == 42
    assert rewriter.warnings == []


# **호출 인자는 generation/evaluate.py 를 따른다** — `max_tokens` 가 아니라
# `max_completion_tokens` 다. gpt-5 계열이 앞엣것을 400 으로 거부한다
def test_call_uses_max_completion_tokens(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "q"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    get_rewriter(_config(tmp_path)).rewrite(_ORIGINAL, _context())

    assert "max_completion_tokens" in client.calls[0]
    assert "max_tokens" not in client.calls[0]


# **`None` 은 「안 보낸다」는 뜻이다** — 1.0 을 대신 보내면 0.0 이 재현을 위한 값이었다는
# 사실이 지워진다 (EvaluatorConfig 와 같은 자리)
def test_none_temperature_is_not_sent(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "q"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    get_rewriter(_config(tmp_path, rewrite_temperature=None)).rewrite(_ORIGINAL, _context())

    assert "temperature" not in client.calls[0]


def test_set_temperature_is_sent(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "q"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    get_rewriter(_config(tmp_path, rewrite_temperature=0.0)).rewrite(_ORIGINAL, _context())

    assert client.calls[0]["temperature"] == 0.0


# 1차 결과를 몇 개까지 보여주나. 프롬프트에 그만큼만 실려야 한다
def test_context_k_limits_the_prompt(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "q"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    get_rewriter(_config(tmp_path, rewrite_context_k=2)).rewrite(_ORIGINAL, _context(5))

    body = client.calls[0]["messages"][1]["content"]
    # 번호 블록으로 센다 — 프롬프트 머리말의 "조각 2개" 가 본문 문자열과 겹친다
    assert "[0]" in body and "[1]" in body
    assert "[2]" not in body


# --- 폴백: 전부 아니면 전무 -----------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        None,
        "JSON 이 아님",
        '{"queries": ["둘"]}',  # 키 이름이 틀렸다
        '{"query": 3}',  # 문자열이 아니다
        '{"query": "   "}',  # 공백뿐이면 질의가 아니다
        '["다시 쓴 질문"]',  # 객체가 아니다
    ],
)
def test_broken_response_falls_back(tmp_path, mocker, content) -> None:
    mocker.patch.object(rewrite_mod, "_make_client", return_value=FakeClient(content))

    rewriter = get_rewriter(_config(tmp_path))
    assert rewriter.rewrite(_ORIGINAL, _context()) is None
    # **부분 성공을 안 만든다** — 반쯤 쓴 질의는 판정을 오염시킨다
    assert rewriter.usage.api_calls == 0
    assert rewriter.warnings


# 호출 자체가 죽어도 예외가 새면 안 된다. 새면 폴백이 아니라 500 이다
def test_api_error_falls_back(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod, "_make_client", return_value=FakeClient(error=RuntimeError("429"))
    )

    rewriter = get_rewriter(_config(tmp_path))
    assert rewriter.rewrite(_ORIGINAL, _context()) is None
    assert "429" in rewriter.warnings[0]


# 키가 없으면 조용히 건너뛰되 사유를 남긴다. 안 남기면 "효과가 없었다"와 구분이 안 된다
def test_missing_key_warns(tmp_path, mocker) -> None:
    mocker.patch.object(rewrite_mod, "_make_client", return_value=None)

    rewriter = get_rewriter(_config(tmp_path, openai_api_key=None))
    assert rewriter.rewrite(_ORIGINAL, _context()) is None
    assert "OPENAI_API_KEY" in rewriter.warnings[0]


# 1차 결과가 없으면 PRF 가 성립하지 않는다. **호출을 아끼는 것이 아니라 뜻이 없다**
def test_empty_context_never_calls(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "q"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    assert get_rewriter(_config(tmp_path)).rewrite(_ORIGINAL, []) is None
    assert client.calls == []


# --- 다시 쓴 질의를 부르는 쪽이 어떻게 아나 --------------------------------------


# `retrieve()` 의 반환은 청크 목록이라 문자열이 낄 자리가 없다. 그래서 **인스턴스가 답한다** —
# `warnings`·`usage` 와 같은 자리이고, `answer_question()` 이 여기서 읽어 `Answer.rewrite` 에
# 싣는다. 이 속성이 없으면 화면과 로그가 재작성을 영원히 못 본다
def test_last_rewritten_reports_what_was_searched(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod, "_make_client", return_value=FakeClient('{"query": "다시 쓴 질문"}')
    )

    rewriter = get_rewriter(_config(tmp_path))
    assert rewriter.last_rewritten is None
    rewriter.rewrite(_ORIGINAL, _context())
    assert rewriter.last_rewritten == "다시 쓴 질문"


# **폴백은 앞 질의의 재작성을 물려받으면 안 된다.** 되돌리지 않으면 원 질의로만 검색한 질의가
# *앞 질의의* 재작성을 자기 것으로 보고하는데, 에러가 아니라 화면과 로그에 조용히 실린다 —
# 재작성기를 여러 질의에 걸쳐 재사용하는 `run_eval` 이 정확히 그 모양이다
def test_fallback_clears_the_previous_rewrite(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        side_effect=[FakeClient('{"query": "다시 쓴 질문"}'), FakeClient(error=RuntimeError("429"))],
    )

    rewriter = get_rewriter(_config(tmp_path))
    assert rewriter.rewrite(_ORIGINAL, _context()) == "다시 쓴 질문"
    assert rewriter.rewrite("다른 질문", _context()) is None
    assert rewriter.last_rewritten is None


# 보여줄 것이 없어 아예 안 부른 갈래도 같다. **호출 앞에서 되돌리는 것이 그래서 필요하다**
def test_empty_context_also_clears(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod, "_make_client", return_value=FakeClient('{"query": "다시 쓴 질문"}')
    )

    rewriter = get_rewriter(_config(tmp_path))
    rewriter.rewrite(_ORIGINAL, _context())
    assert rewriter.rewrite(_ORIGINAL, []) is None
    assert rewriter.last_rewritten is None


# **캐시 히트도 값이 있다.** 재작성이 걸린 것은 같고 갈리는 것은 과금뿐이라, 여기서 None 을
# 주면 캐시가 도는 순간 화면의 표시가 사라진다 — 켜 놓고도 안 보이는 상태가 된다
def test_cache_hit_also_reports_the_query(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod, "_make_client", return_value=FakeClient('{"query": "다시 쓴 질문"}')
    )

    config = _config(tmp_path, rewrite_cache_enabled=True)
    get_rewriter(config).rewrite(_ORIGINAL, _context())

    second = get_rewriter(config)
    second.rewrite(_ORIGINAL, _context())
    assert second.usage.cache_hits == 1
    assert second.last_rewritten == "다시 쓴 질문"


# --- 캐시 ---------------------------------------------------------------------


# 같은 (질의, 1차 후보) 는 두 번 안 부른다
def test_cache_hit_skips_the_call(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "다시 쓴 질문"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    config = _config(tmp_path, rewrite_cache_enabled=True)
    first = get_rewriter(config)
    first.rewrite(_ORIGINAL, _context())

    second = get_rewriter(config)
    assert second.rewrite(_ORIGINAL, _context()) == "다시 쓴 질문"
    assert second.usage.cache_hits == 1
    assert second.usage.api_calls == 0
    assert len(client.calls) == 1


# **`rewrite_mode` 가 salt 에 없다** — 두 모드가 캐시를 나눠 쓰는 것이 「둘 다 만들어
# 재본다」를 고른 근거다. 넣으면 두 번째 프로필이 통째로 다시 과금된다
def test_mode_does_not_split_the_cache(tmp_path, mocker) -> None:
    client = FakeClient('{"query": "다시 쓴 질문"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    get_rewriter(_config(tmp_path, rewrite_cache_enabled=True, rewrite_mode="fuse")).rewrite(
        _ORIGINAL, _context()
    )
    other = get_rewriter(_config(tmp_path, rewrite_cache_enabled=True, rewrite_mode="replace"))
    assert other.rewrite(_ORIGINAL, _context()) == "다시 쓴 질문"
    assert other.usage.cache_hits == 1
    assert len(client.calls) == 1


# **못 쓸 응답은 캐시에 안 넣는다** (표-경로.md 13회차가 고친 그 실수다)
def test_broken_response_is_not_cached(tmp_path, mocker) -> None:
    client = FakeClient("JSON 이 아님")
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    config = _config(tmp_path, rewrite_cache_enabled=True)
    get_rewriter(config).rewrite(_ORIGINAL, _context())
    get_rewriter(config).rewrite(_ORIGINAL, _context())

    assert len(client.calls) == 2


# --- retrieve() 배선 -----------------------------------------------------------


def _store() -> FakeStore:
    return FakeStore(
        {
            _ORIGINAL: [_result("a", 0.82), _result("b", 0.71)],
            _REWRITTEN: [_result("c", 0.93), _result("a", 0.40)],
        }
    )


# **이 파일에서 제일 중요한 테스트다.** rewriter 를 안 넘기면 예전 경로 그대로여야 한다
def test_no_rewriter_is_exactly_today(mocker) -> None:
    client = FakeClient('{"query": "' + _REWRITTEN + '"}')
    mocker.patch.object(rewrite_mod, "_make_client", return_value=client)

    chunks = retrieve(_ORIGINAL, store=_store(), embedder=FakeEmbedder(), top_k=5)

    assert [c.chunk_id for c in chunks] == ["a", "b"]
    assert [c.dense_score for c in chunks] == [pytest.approx(0.82), pytest.approx(0.71)]
    # RRF 를 안 태운다 — 경로가 하나뿐일 때의 동등성이다
    assert [c.fused_score for c in chunks] == [pytest.approx(0.82), pytest.approx(0.71)]
    assert all(c.rewrite_dense_score is None for c in chunks)
    # 재작성기를 안 넘겼으면 호출도 없다
    assert client.calls == []


# `replace` 는 재작성 질의로만 검색한다. **21회차 프로브가 잰 것이 이 모양이다**
def test_replace_searches_only_the_rewritten_query(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        return_value=FakeClient('{"query": "' + _REWRITTEN + '"}'),
    )
    rewriter = get_rewriter(_config(tmp_path, rewrite_mode="replace"))

    chunks = retrieve(
        _ORIGINAL, store=_store(), embedder=FakeEmbedder(), top_k=5, rewriter=rewriter
    )

    # 원 질의에만 있던 b 가 사라지고, 재작성 질의의 c 가 1위다
    assert [c.chunk_id for c in chunks] == ["c", "a"]
    # 원 질의로는 검색을 안 했다 — 그 사실이 payload 에 그대로 보인다
    assert all(c.dense_score is None for c in chunks)
    assert chunks[0].rewrite_dense_score == pytest.approx(0.93)


# `fuse` 는 원 질의 갈래를 남긴다. 그래서 b 가 살아 있고 두 칸이 다 찬 청크가 생긴다
def test_fuse_keeps_the_original_branch(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        return_value=FakeClient('{"query": "' + _REWRITTEN + '"}'),
    )
    rewriter = get_rewriter(_config(tmp_path, rewrite_mode="fuse"))

    chunks = retrieve(
        _ORIGINAL, store=_store(), embedder=FakeEmbedder(), top_k=5, rewriter=rewriter
    )

    ids = [c.chunk_id for c in chunks]
    assert set(ids) == {"a", "b", "c"}
    # 두 갈래 모두에 걸린 a 는 칸이 둘 다 찬다 — 한 칸을 돌려쓰면 안 갈린다
    a = next(c for c in chunks if c.chunk_id == "a")
    assert a.dense_score == pytest.approx(0.82)
    assert a.rewrite_dense_score == pytest.approx(0.40)
    # 원 질의에만 있던 b 는 재작성 칸이 비어 있다
    b = next(c for c in chunks if c.chunk_id == "b")
    assert b.rewrite_dense_score is None


# 두 모드가 실제로 다른 결과를 낸다. 같으면 이 축이 **에러 없이** 뜻을 잃는다
def test_modes_actually_differ(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        return_value=FakeClient('{"query": "' + _REWRITTEN + '"}'),
    )

    def ids(mode: str) -> list[str]:
        rewriter = get_rewriter(_config(tmp_path, rewrite_mode=mode))
        chunks = retrieve(
            _ORIGINAL, store=_store(), embedder=FakeEmbedder(), top_k=5, rewriter=rewriter
        )
        return [c.chunk_id for c in chunks]

    assert ids("fuse") != ids("replace")


# 재작성이 실패하면 1차 pool 이 그대로 나온다 — **재작성을 안 켠 결과와 같아야 한다**
def test_failed_rewrite_falls_back_to_the_first_pool(tmp_path, mocker) -> None:
    mocker.patch.object(rewrite_mod, "_make_client", return_value=FakeClient("JSON 이 아님"))
    rewriter = get_rewriter(_config(tmp_path))

    baseline = retrieve(_ORIGINAL, store=_store(), embedder=FakeEmbedder(), top_k=5)
    chunks = retrieve(
        _ORIGINAL, store=_store(), embedder=FakeEmbedder(), top_k=5, rewriter=rewriter
    )

    assert [c.chunk_id for c in chunks] == [c.chunk_id for c in baseline]
    assert [c.fused_score for c in chunks] == [pytest.approx(c.fused_score) for c in baseline]


# **재작성기를 켜면 후보 풀을 넓힌다.** 안 넓히면 PRF 가 top_k 개만 보고 재작성 갈래도
# 좁게 돌아, 이 단계가 노린 11~50위의 여지를 통째로 버린다
def test_rewriter_widens_the_pool(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        return_value=FakeClient('{"query": "' + _REWRITTEN + '"}'),
    )
    store = _store()
    retrieve(
        _ORIGINAL,
        store=store,
        embedder=FakeEmbedder(),
        top_k=5,
        rewriter=get_rewriter(_config(tmp_path)),
        config=RetrievalConfig(candidate_k=50),
    )

    assert store.top_ks == [50, 50]


# BM25 를 함께 켜면 갈래가 넷이다. 재작성 질의도 BM25 를 탄다
def test_fuse_with_bm25_uses_four_branches(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        return_value=FakeClient('{"query": "' + _REWRITTEN + '"}'),
    )
    store = _store()
    bm25 = Bm25Index(
        [
            _result("d", 0.0, text="공공기록물 관리에 관한 법률 제51조 벌칙"),
            _result("a", 0.0, text="개인정보 파기 절차"),
        ]
    )

    chunks = retrieve(
        _ORIGINAL,
        store=store,
        embedder=FakeEmbedder(),
        top_k=10,
        bm25=bm25,
        rewriter=get_rewriter(_config(tmp_path, rewrite_mode="fuse")),
    )

    by_id = {c.chunk_id: c for c in chunks}
    # 네 갈래가 다 돌았다면 BM25 에만 있는 d 도 후보다
    assert "d" in by_id
    # 재작성 질의의 BM25 갈래가 실제로 돌았다 (칸이 갈려 있어 확인이 된다)
    assert any(c.rewrite_bm25_score is not None for c in chunks)


# **리랭커에는 원 질의를 넘긴다** — 재작성 질의를 넘기면 rerank 캐시가 재작성 프롬프트에
# 딸려 전량 미스가 되고, 그것은 곧 유료 호출이다
def test_reranker_gets_the_original_query(tmp_path, mocker) -> None:
    mocker.patch.object(
        rewrite_mod,
        "_make_client",
        return_value=FakeClient('{"query": "' + _REWRITTEN + '"}'),
    )
    seen: list[str] = []

    class SpyReranker:
        def rerank(self, query: str, candidates):  # noqa: ANN001, ANN201
            seen.append(query)
            return candidates

        @property
        def warnings(self) -> list[str]:
            return []

    retrieve(
        _ORIGINAL,
        store=_store(),
        embedder=FakeEmbedder(),
        top_k=5,
        reranker=SpyReranker(),
        rewriter=get_rewriter(_config(tmp_path)),
    )

    assert seen == [_ORIGINAL]
