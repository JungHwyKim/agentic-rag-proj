"""리랭커 배선과 두 구현체. 외부 API 도 모델 가중치도 안 건드린다.

제일 중요한 것 둘이다.

1. **`reranker=None` 이면 예전과 글자 그대로 같다.** 깨지면 비교의 대조군이 함께 움직여
   무엇이 달라졌는지 못 가른다 — `bm25=None` 동등성과 같은 자리다
2. **리랭커가 `top_k` 가 아니라 후보 풀을 본다.** 이 회차가 존재하는 이유가 그것이라,
   `top_k` 밖에 있던 후보를 리랭커가 1위로 올릴 수 있어야 한다

그리고 **실패는 전부 아니면 전무다** — 점수를 하나라도 못 얻으면 아무것도 안 채우고
RRF 순서로 내려간다. 반쯤 바뀐 순서는 *"리랭커가 나쁜가 응답이 반쪽이었나"* 를 못 가른다.

**로컬 갈래 테스트가 모델을 안 내려받는다** — `_load_model()` 을 패치한다. 그 함수가 모듈
레벨인 이유가 이것이고, 인라인하면 기본 pytest 가 가중치를 받으러 나간다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.config import RerankConfig, RetrievalConfig
from rag_pipeline.retrieval import get_reranker, retrieve
from rag_pipeline.retrieval.reranker import local_ce, openai_llm
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import SearchResult


# 어떤 질의든 같은 벡터를 준다
class FakeEmbedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


# 받은 top_k 를 기록한다. 풀을 넓혔는지 보는 자리다
class FakeStore:
    def __init__(self, results) -> None:
        self.results = results
        self.top_ks: list[int] = []

    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        self.top_ks.append(top_k)
        return self.results[:top_k]

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


def _chunk(chunk_id: str, fused: float, text: str = "본문") -> RetrievedChunk:
    chunk = RetrievedChunk(
        chunk_id=chunk_id,
        document_id="안내서.pdf",
        text=text,
        metadata={},
    )
    chunk.fused_score = fused
    return chunk


# 점수를 정해둔 대로 돌려주는 가짜 리랭커. 배선만 보는 테스트에 쓴다
class FakeReranker:
    def __init__(self, scores_by_id: dict[str, float] | None = None) -> None:
        self.scores_by_id = scores_by_id or {}
        self.seen: list[list[str]] = []
        self.warnings: list[str] = []
        self.usage = None

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        self.seen.append([chunk.chunk_id for chunk in candidates])
        for chunk in candidates:
            chunk.rerank_score = self.scores_by_id.get(chunk.chunk_id, 0.0)
        return sorted(candidates, key=lambda c: c.ranking_score, reverse=True)


# ------------------------------------------------------------- 동등성

# **이 파일에서 제일 중요한 테스트 중 하나다.** 리랭커를 안 넘기면 예전 경로 그대로다
def test_no_reranker_is_exactly_before():
    results = [_result("a", 0.82), _result("b", 0.71)]
    before = retrieve(
        "질의", store=FakeStore(results), embedder=FakeEmbedder(), top_k=2
    )
    after = retrieve(
        "질의", store=FakeStore(results), embedder=FakeEmbedder(), top_k=2, reranker=None
    )

    assert [c.chunk_id for c in before] == [c.chunk_id for c in after] == ["a", "b"]
    assert all(chunk.rerank_score is None for chunk in after)
    # 안 넘기면 풀을 안 넓힌다 — 넓혀 봐야 버릴 뿐인데 저장소는 이미 훑는다
    assert FakeStore(results).top_ks == []


# 리랭커가 없으면 dense 갈래가 top_k 만 가져온다. 있으면 넓힌다
def test_reranker_widens_the_dense_fetch():
    store = FakeStore([_result(f"c{i}", 1.0 - i / 100) for i in range(60)])
    config = RetrievalConfig(candidate_k=50)

    retrieve("질의", store=store, embedder=FakeEmbedder(), top_k=5, config=config)
    assert store.top_ks == [5]

    retrieve(
        "질의",
        store=store,
        embedder=FakeEmbedder(),
        top_k=5,
        reranker=FakeReranker(),
        config=config,
    )
    # **BM25 없이 리랭커만 켜도 넓혀야 한다** — 안 넓히면 리랭커가 볼 것이 5개뿐이라
    # 이 회차가 노린 11~50위의 여지를 통째로 버린다
    assert store.top_ks == [5, 50]


# ------------------------------------------------------------- 풀을 먹는가

# **이 회차의 존재 이유를 잠그는 테스트다.** top_k 밖에 있던 후보를 리랭커가 되살린다
def test_reranker_reaches_beyond_top_k():
    store = FakeStore([_result(f"c{i}", 1.0 - i / 100) for i in range(60)])
    # 30번째 후보에 제일 높은 점수를 준다. top_k=10 이라 리랭커가 없으면 안 보이는 자리다
    reranker = FakeReranker({"c29": 99.0})

    hits = retrieve(
        "질의",
        store=store,
        embedder=FakeEmbedder(),
        top_k=10,
        reranker=reranker,
        config=RetrievalConfig(candidate_k=50),
    )

    assert len(hits) == 10
    assert hits[0].chunk_id == "c29"
    # 리랭커가 실제로 풀 50개를 봤다 — top_k 10 이 아니다
    assert len(reranker.seen[0]) == 50


# ------------------------------------------------------------- get_reranker

# **모델 이름이 비면 꺼진다.** 저장소에는 꺼진 채 커밋되고, 켜는 것은 .env 에 이름이
# 들어가는 순간이다 — 켜지면 질의당 LLM 호출이 1회에서 2회가 된다
def test_no_model_means_no_reranker():
    assert get_reranker(RerankConfig(rerank_model=None)) is None
    assert get_reranker(RerankConfig(rerank_model="")) is None


# provider 가 구현체를 가른다. 구현체가 둘이라 이 설정이 죽은 설정이 아니다
@pytest.mark.parametrize(
    ("provider", "expected"),
    [("openai", openai_llm.OpenAILLMReranker), ("local", local_ce.LocalCrossEncoderReranker)],
)
def test_provider_picks_the_implementation(provider, expected):
    made = get_reranker(RerankConfig(rerank_provider=provider, rerank_model="m"))
    assert isinstance(made, expected)


# ------------------------------------------------------------- OpenAI 갈래


# 응답 하나를 흉내낸다. 실제 SDK 모양(choices[0].message.content)을 지킨다 —
# 문자열로 만들면 테스트가 오해를 그대로 검증한다 (CLAUDE.md 의 LlamaParse 교훈)
class _FakeResponse:
    def __init__(self, content: str | None, tokens: int = 123) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]
        self.usage = type("U", (), {"total_tokens": tokens})()


class _FakeClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict] = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):  # noqa: ANN201 - 가짜 SDK
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.content)


def _openai_config(tmp_path) -> RerankConfig:
    return RerankConfig(
        openai_api_key="sk-test",
        rerank_provider="openai",
        rerank_model="gpt-4.1-mini",
        # **못 박는다.** 유료 갈래는 device 를 안 쓰지만 **캐시 salt 에는 들어가므로**,
        # 안 박으면 개발자 `.env` 의 `RERANK_DEVICE` 가 픽스처에 실린다.
        # 실제로 그렇게 샜다 — 리랭커를 켠 날 이 파일의 단언 하나가 그 값을 읽고 깨졌다
        rerank_device="cpu",
        rerank_cache_dir=tmp_path / "rerank",
        rerank_cache_enabled=False,
    )


# 모델이 준 점수대로 순서가 뒤집힌다
def test_openai_reranker_reorders(mocker, tmp_path):
    client = _FakeClient('{"scores": [{"i": 0, "score": 1.0}, {"i": 1, "score": 9.0}]}')
    mocker.patch.object(openai_llm, "_make_client", return_value=client)

    reranker = openai_llm.OpenAILLMReranker(_openai_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["b", "a"]
    assert reranker.usage.api_calls == 1
    assert reranker.usage.total_tokens == 123
    assert reranker.usage.reranked == 2
    assert reranker.warnings == []


# **항목 수가 부족하면 통째로 버린다.** 반쯤 채우면 판정이 오염된다
def test_openai_partial_response_is_discarded_whole(mocker, tmp_path):
    client = _FakeClient('{"scores": [{"i": 0, "score": 9.0}]}')
    mocker.patch.object(openai_llm, "_make_client", return_value=client)

    reranker = openai_llm.OpenAILLMReranker(_openai_config(tmp_path))
    candidates = [_chunk("a", 0.9), _chunk("b", 0.1)]
    ranked = reranker.rerank("질의", candidates)

    # RRF 순서 그대로이고 점수 칸은 비어 있다
    assert [c.chunk_id for c in ranked] == ["a", "b"]
    assert all(chunk.rerank_score is None for chunk in ranked)
    assert len(reranker.warnings) == 1
    assert reranker.usage.reranked == 0


# 깨진 JSON · 빈 응답 · 자리 중복 · 범위 밖 index — 전부 폴백이고 예외가 아니다
@pytest.mark.parametrize(
    "content",
    [
        "이건 JSON 이 아니다",
        None,
        '{"scores": [{"i": 0, "score": 1.0}, {"i": 0, "score": 2.0}]}',
        '{"scores": [{"i": 0, "score": 1.0}, {"i": 5, "score": 2.0}]}',
        '{"scores": [{"i": 0, "score": "높음"}, {"i": 1, "score": 2.0}]}',
    ],
)
def test_openai_broken_response_falls_back(mocker, tmp_path, content):
    mocker.patch.object(openai_llm, "_make_client", return_value=_FakeClient(content))

    reranker = openai_llm.OpenAILLMReranker(_openai_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["a", "b"]
    assert all(chunk.rerank_score is None for chunk in ranked)
    assert reranker.warnings


# API 가 죽어도 예외가 안 난다 — 내려갈 층(RRF 순서)이 있는 갈래다
def test_openai_api_failure_falls_back(mocker, tmp_path):
    client = _FakeClient(error=RuntimeError("503"))
    mocker.patch.object(openai_llm, "_make_client", return_value=client)

    reranker = openai_llm.OpenAILLMReranker(_openai_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["a", "b"]
    assert "503" in reranker.warnings[0]


# 키가 없으면 부르지도 않고 경고만 남긴다
def test_openai_without_key_warns_and_falls_back(mocker, tmp_path):
    mocker.patch.object(openai_llm, "_make_client", return_value=None)

    reranker = openai_llm.OpenAILLMReranker(_openai_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["a", "b"]
    assert "OPENAI_API_KEY" in reranker.warnings[0]


# 후보가 1개 이하면 부르지 않는다. 아낀다기보다 **정렬할 것이 없다**
def test_openai_skips_when_nothing_to_order(mocker, tmp_path):
    client = _FakeClient('{"scores": [{"i": 0, "score": 9.0}]}')
    mocker.patch.object(openai_llm, "_make_client", return_value=client)

    reranker = openai_llm.OpenAILLMReranker(_openai_config(tmp_path))
    reranker.rerank("질의", [_chunk("a", 0.9)])

    assert client.calls == []


# **못 쓸 응답은 캐시에 안 넣는다** (표-경로.md 13회차가 고친 그 실수다)
def test_openai_does_not_cache_unusable_response(mocker, tmp_path):
    client = _FakeClient("이건 JSON 이 아니다")
    mocker.patch.object(openai_llm, "_make_client", return_value=client)
    config = _openai_config(tmp_path)
    object.__setattr__(config, "rerank_cache_enabled", True)

    reranker = openai_llm.OpenAILLMReranker(config)
    reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])
    reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    # 캐시에 들어갔다면 두 번째는 호출을 안 했을 것이다
    assert len(client.calls) == 2


# 같은 (질의, 후보) 면 두 번째는 캐시가 답한다. **평가 재실행이 공짜가 되는 자리다**
def test_openai_caches_usable_response(mocker, tmp_path):
    client = _FakeClient('{"scores": [{"i": 0, "score": 1.0}, {"i": 1, "score": 9.0}]}')
    mocker.patch.object(openai_llm, "_make_client", return_value=client)
    config = _openai_config(tmp_path)
    object.__setattr__(config, "rerank_cache_enabled", True)

    reranker = openai_llm.OpenAILLMReranker(config)
    first = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])
    second = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert len(client.calls) == 1
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second] == ["b", "a"]
    assert reranker.usage.cache_hits == 1


# **프롬프트가 캐시 키에 들어간다** — 안 들어가면 프롬프트를 고쳐도 옛 응답이 새 프롬프트의
# 답인 척 돌아오는데, 에러가 아니라 수치로만 드러난다 (표-경로.md 14회차)
def test_prompt_version_is_in_the_cache_key(tmp_path):
    from rag_pipeline.retrieval.reranker.base import rerank_cache_salt_for

    config = _openai_config(tmp_path)
    assert rerank_cache_salt_for(config, prompt_version="v1") != rerank_cache_salt_for(
        config, prompt_version="v2"
    )


# **장치도 캐시 키에 들어간다** — `rerank_device` 가 존재하는 이유가 *"CPU 와 GPU 가 미세하게
# 다른 점수를 낸다"* 라서다. 안 들어가면 GPU 실행이 CPU 캐시를 읽어 **품질이 같게 나오고
# 속도만 빨라 보인다** — 지연을 재려던 실행이 아무것도 안 재고 통과한다
def test_device_is_in_the_cache_key(tmp_path):
    from rag_pipeline.retrieval.reranker.base import rerank_cache_salt_for

    cpu = RerankConfig(
        rerank_provider="local", rerank_model="BAAI/bge-reranker-v2-m3", rerank_device="cpu"
    )
    gpu = RerankConfig(
        rerank_provider="local", rerank_model="BAAI/bge-reranker-v2-m3", rerank_device="cuda"
    )

    assert rerank_cache_salt_for(cpu, prompt_version="v1") != rerank_cache_salt_for(
        gpu, prompt_version="v1"
    )
    # 유료 갈래도 같은 규칙을 탄다 — 갈래마다 규칙이 다르면 한쪽이 빠져도 아무 에러가 안 난다.
    # **값을 글자로 단언하지 않는다** — 초판이 `"cpu" in salt` 였는데 그 `cpu` 가 픽스처가
    # 아니라 개발자 `.env` 에서 온 값이라, 리랭커를 켠 날 깨졌다. 재야 할 것은 기본값이
    # 아니라 *device 를 바꾸면 salt 가 갈리는가* 다
    paid_cpu = _openai_config(tmp_path)
    paid_gpu = paid_cpu.model_copy(update={"rerank_device": "cuda"})
    assert rerank_cache_salt_for(paid_cpu, prompt_version="v1") != rerank_cache_salt_for(
        paid_gpu, prompt_version="v1"
    )


# ------------------------------------------------------------- 로컬 갈래


def _local_config(tmp_path) -> RerankConfig:
    return RerankConfig(
        rerank_provider="local",
        rerank_model="BAAI/bge-reranker-v2-m3",
        # `_openai_config` 와 같은 이유로 못 박는다 (그쪽 주석 참고)
        rerank_device="cpu",
        rerank_cache_dir=tmp_path / "rerank",
        rerank_cache_enabled=False,
    )


# 점수를 정해둔 대로 주는 가짜 cross-encoder
class _FakeCrossEncoder:
    def __init__(self, scores) -> None:
        self.scores = scores
        self.pairs: list[list[tuple[str, str]]] = []

    def predict(self, pairs):  # noqa: ANN201 - 가짜 모델
        self.pairs.append(list(pairs))
        return self.scores


# cross-encoder 점수대로 순서가 뒤집힌다. **OpenAI 갈래와 같은 계약을 두 번째로 잠근다**
def test_local_reranker_reorders(mocker, tmp_path):
    model = _FakeCrossEncoder([1.0, 9.0])
    mocker.patch.object(local_ce, "_load_model", return_value=model)

    reranker = local_ce.LocalCrossEncoderReranker(_local_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["b", "a"]
    # 로컬의 비용은 토큰이 아니라 채점한 후보 수다
    assert reranker.usage.reranked == 2
    assert reranker.usage.api_calls == 0
    assert reranker.usage.total_tokens == 0


# 모델을 못 올리면 폴백하고, **다시 시도하지 않는다** — 질의마다 재시도하면 같은 시간을
# 매번 태우고 경고가 무한히 쌓인다
def test_local_load_failure_is_remembered(mocker, tmp_path):
    load = mocker.patch.object(local_ce, "_load_model", return_value=None)

    reranker = local_ce.LocalCrossEncoderReranker(_local_config(tmp_path))
    for _ in range(3):
        ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])
        assert [c.chunk_id for c in ranked] == ["a", "b"]

    assert load.call_count == 1
    assert len(reranker.warnings) == 1


# 점수 개수가 안 맞으면 통째로 버린다. OpenAI 갈래와 같은 계약이다
def test_local_length_mismatch_is_discarded_whole(mocker, tmp_path):
    mocker.patch.object(local_ce, "_load_model", return_value=_FakeCrossEncoder([9.0]))

    reranker = local_ce.LocalCrossEncoderReranker(_local_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["a", "b"]
    assert all(chunk.rerank_score is None for chunk in ranked)
    assert reranker.warnings


# 채점이 터져도 예외가 안 난다
def test_local_predict_failure_falls_back(mocker, tmp_path):
    model = _FakeCrossEncoder([])
    mocker.patch.object(model, "predict", side_effect=RuntimeError("OOM"))
    mocker.patch.object(local_ce, "_load_model", return_value=model)

    reranker = local_ce.LocalCrossEncoderReranker(_local_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["a", "b"]
    assert "OOM" in reranker.warnings[0]


# 질의-후보 쌍을 그대로 넘긴다. 청크를 안 자르는 것이 계약이다
def test_local_sends_full_chunk_text(mocker, tmp_path):
    model = _FakeCrossEncoder([1.0, 2.0])
    mocker.patch.object(local_ce, "_load_model", return_value=model)

    long_text = "가" * 1200
    reranker = local_ce.LocalCrossEncoderReranker(_local_config(tmp_path))
    reranker.rerank("질의", [_chunk("a", 0.9, long_text), _chunk("b", 0.1)])

    assert model.pairs[0][0] == ("질의", long_text)


# ------------------------------------------------------------- 동점


# 동점은 들어온 순서(=RRF 순서)가 이긴다. fusion.py 의 "동점은 dense 가 이긴다"와 같은 모양
def test_ties_keep_the_fused_order(mocker, tmp_path):
    mocker.patch.object(local_ce, "_load_model", return_value=_FakeCrossEncoder([5.0, 5.0]))

    reranker = local_ce.LocalCrossEncoderReranker(_local_config(tmp_path))
    ranked = reranker.rerank("질의", [_chunk("a", 0.9), _chunk("b", 0.1)])

    assert [c.chunk_id for c in ranked] == ["a", "b"]
