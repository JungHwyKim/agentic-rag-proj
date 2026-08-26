"""기동 워밍업이 **실제로 데우는지**와, 기본값이 꺼져 있는지를 잠근다.

이 파일이 지키는 것은 셋이다.

1. **기본값이 꺼짐이어야 한다.** `tests/api` 의 일부가 `with TestClient(...)` 로 lifespan 을
   진짜로 돌린다 — 켜진 채 커밋되면 기본 pytest 가 KURE 가중치 2.27GB 를 적재한다.
   CLAUDE.md 가 임베딩 provider 핀으로 막아 둔 것과 같은 종류의 유출이고, 여기서는
   설정 기본값이 유일한 방어다.
2. **워밍업은 객체 생성이 아니라 사용이어야 한다.** `get_embedder()`·`get_reranker()` 자체는
   0.002초이고 가중치는 첫 *사용*에 올라온다(verify_log/2026-08-26-첫질의-대표값.md).
   생성만 하는 워밍업은 **아무것도 안 하면서 다 한 것처럼 보인다** — 그 실패가 이 테스트
   없이는 "첫 질의가 여전히 21.8초" 로만 드러난다.
3. **후보가 둘 이상이어야 한다.** `local_ce.rerank()` 가 후보 1개 이하면 모델을 안 올리고
   받은 것을 그대로 돌려준다. 하나만 먹이면 워밍업이 조용히 리랭커를 안 데운다.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.api.routes import query as query_routes
from backend.app.core.config import Settings
from backend.app.main import create_app


# 가중치를 안 올리는 가짜 임베더. 무엇으로 불렸는지만 기억한다
class FakeEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.0]


# 가짜 리랭커. **후보 수를 기억하는 것이 요점이다** (모듈 docstring 3번)
class FakeReranker:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.queries: list[str] = []

    def rerank(self, query: str, candidates: list) -> list:  # noqa: ANN401
        self.calls.append(len(candidates))
        self.queries.append(query)
        return candidates


# 모델을 이미 다 만들어 둔 척하는 app. 게으른 생성 경로를 안 타게 해서
# **워밍업이 무엇을 쓰는가**만 고립시킨다
def _app_with(embedder=None, reranker=None) -> SimpleNamespace:  # noqa: ANN001
    state = SimpleNamespace(
        ingest_config=SimpleNamespace(embedding=None, rerank=None),
        embedder=embedder,
        reranker=reranker,
        _rerank_ready=True,
    )
    return SimpleNamespace(state=state)


# --- 1. 기본값 ---


# **꺼져 있어야 한다.** 켠 채 커밋하면 기본 pytest 가 2.27GB 를 문다
def test_warmup_is_off_by_default() -> None:
    assert Settings(_env_file=None).warmup_on_startup is False


# --- 2. lifespan 배선 ---


# 켜면 lifespan 이 워밍업을 띄운다. **배경 스레드라 이벤트로 기다린다**
def test_lifespan_starts_warmup_when_enabled(mocker, monkeypatch) -> None:
    monkeypatch.setenv("WARMUP_ON_STARTUP", "true")
    started = threading.Event()
    mocker.patch.object(query_routes, "warm_up", side_effect=lambda app: started.set())
    mocker.patch(
        "backend.app.main.get_store",
        return_value=SimpleNamespace(close=lambda: None, sibling_for=lambda _n: None),
    )

    with TestClient(create_app()):
        assert started.wait(timeout=5), "워밍업 스레드가 안 떴다"


# 꺼져 있으면 안 띄운다. **이것이 기본 pytest 를 지키는 계약이다.**
#
# **`.env` 를 명시로 덮는 것이 요점이다** — `Settings` 가 `PROJECT_ROOT/.env` 를 읽으므로,
# 안 덮으면 개발자가 켜 둔 값이 실려 이 테스트가 **그 머신의 설정을 따라 통과했다 실패했다**
# 한다. 실제로 밟았다: 워밍업을 `.env` 에 켜자마자 이 테스트가 깨졌다. CLAUDE.md 가
# 임베딩 provider·리랭커 장치에서 두 번 적어 둔 함정과 같은 축이다.
# 기본값 자체는 `test_warmup_is_off_by_default` 가 `_env_file=None` 으로 따로 잠근다
def test_lifespan_skips_warmup_when_disabled(mocker, monkeypatch) -> None:
    monkeypatch.setenv("WARMUP_ON_STARTUP", "false")
    warm = mocker.patch.object(query_routes, "warm_up")
    mocker.patch(
        "backend.app.main.get_store",
        return_value=SimpleNamespace(close=lambda: None, sibling_for=lambda _n: None),
    )

    with TestClient(create_app()):
        pass

    warm.assert_not_called()


# --- 3. 워밍업이 실제로 쓰는가 ---


# **객체를 만드는 것으로는 안 데워진다** — 실제 인코딩·채점이 한 번씩 나가야 한다
def test_warm_up_actually_uses_both_models() -> None:
    embedder, reranker = FakeEmbedder(), FakeReranker()

    query_routes.warm_up(_app_with(embedder, reranker))

    assert embedder.queries, "임베더를 만들기만 하고 안 썼다 — 가중치가 안 올라온다"
    assert reranker.calls, "리랭커를 만들기만 하고 안 썼다 — 가중치가 안 올라온다"


# 후보를 둘 이상 먹인다. 하나면 `local_ce.rerank()` 가 모델을 안 올리고 그대로 돌려준다
def test_warm_up_feeds_the_reranker_more_than_one_candidate() -> None:
    reranker = FakeReranker()

    query_routes.warm_up(_app_with(FakeEmbedder(), reranker))

    assert reranker.calls[0] >= 2


# **입력이 호출마다 달라야 한다.** 임베딩·리랭크 캐시가 텍스트로 키를 만들어서, 고정
# 문자열이면 워밍업이 **자기 입력을 캐시에 넣고 다음 기동부터 그 캐시에 걸려 모델을 안
# 올린다.** 실제로 그렇게 만들었다가 잡았다 — `warmup_errors` 는 빈 채이고 VRAM 만 평탄해서
# **가짜로는 안 보이고 실제 서버에서만 드러났다.** 그래서 여기서 잠근다
def test_warm_up_uses_a_fresh_input_each_time() -> None:
    embedder, reranker = FakeEmbedder(), FakeReranker()
    app = _app_with(embedder, reranker)

    query_routes.warm_up(app)
    query_routes.warm_up(app)

    assert len(set(embedder.queries)) == 2, "임베더 입력이 같다 — 두 번째 기동은 캐시에 걸린다"
    assert len(set(reranker.queries)) == 2, "리랭커 입력이 같다 — 두 번째 기동은 캐시에 걸린다"


# 리랭커가 꺼져 있으면(`RERANK_MODEL` 이 빔) 그 갈래는 그냥 건너뛴다
def test_warm_up_skips_a_disabled_reranker() -> None:
    app = _app_with(FakeEmbedder(), None)

    query_routes.warm_up(app)

    assert app.state.warmup_errors == []


# --- 4. 실패 계약 ---


# **워밍업 실패가 서버를 죽이면 게으른 적재보다 나빠진다.** 안 던지고 남기기만 한다
def test_warm_up_does_not_raise_when_a_model_fails() -> None:
    class Broken:
        def embed_query(self, text: str):  # noqa: ANN202
            raise RuntimeError("가중치 없음")

    app = _app_with(Broken(), FakeReranker())

    query_routes.warm_up(app)

    assert any("embedder" in message for message in app.state.warmup_errors)


# --- 5. 경쟁 조건 ---


# **워밍업과 질의가 같은 인스턴스를 봐야 한다.** 안 그러면 KURE 2.27GB 가 두 벌 뜨는데
# 에러가 안 나서 메모리로만 드러난다
def test_getter_returns_one_instance(mocker) -> None:
    made = [object(), object()]
    mocker.patch.object(query_routes, "get_embedder", side_effect=lambda _cfg: made.pop(0))
    app = _app_with(embedder=None)

    first = query_routes._embedder(app)
    second = query_routes._embedder(app)

    assert first is second, "두 번 만들었다 — 게으른 생성에 락이 없다"


# 여러 스레드가 동시에 들어와도 하나만 만든다
def test_concurrent_getters_build_once(mocker) -> None:
    calls: list[int] = []

    def _make(_cfg):  # noqa: ANN001, ANN202
        calls.append(1)
        return object()

    mocker.patch.object(query_routes, "get_embedder", side_effect=_make)
    app = _app_with(embedder=None)
    seen: list[object] = []
    barrier = threading.Barrier(4)

    def _worker() -> None:
        barrier.wait()
        seen.append(query_routes._embedder(app))

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(calls) == 1, f"임베더를 {len(calls)}번 만들었다"
    assert len({id(item) for item in seen}) == 1
