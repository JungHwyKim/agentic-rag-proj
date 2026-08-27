"""Phase 11 — 그래프가 while 루프와 **같은 것**을 도는지.

스펙의 DoD 는 *"end-to-end 실행 시 Checkpoint A 의 retry 동작이 그래프 상에서도 동일하게
재현"* 하나다. 그래서 이 파일은 그래프를 따로 재지 않고 **두 경로를 나란히 놓고 대조한다** —
가짜도 `_loop_stubs.py` 로 같은 것을 쓴다.

## 무엇을 대조하나 — 지표 하나가 아니라 상태 전부

`stop_reason` 만 보면 재시도 횟수가 갈린 것이 안 보이고, `retry_count` 만 보면 어느 질의로
찾았는지가 안 보인다. 그래서 **`model_dump()` 전체**를 대조하고 뺄 것만 이름으로 적는다.
`answer_cache/fingerprint.py` 가 *"빠뜨리면 조용히 틀리고 넘치면 미스가 한 번 더 날 뿐"* 이라
손으로 안 고른 것과 같은 방향이다.

빼는 것은 둘뿐이다.

- **`next_action`** — 그래프에서는 **엣지가 곧 그 값**이라 상태에 안 적힌다. 적게 만들면
  라우팅의 두 번째 진실이 생긴다(`graph.py` docstring)
- **`ToolCallLog.timestamp`** — 벽시계라 같을 수가 없다

## `langgraph` 가 없으면 조용히 skip 된다

**그것이 「선택 갈래」가 실제로 성립하는지의 검사이기도 하다** — 이 파일이 skip 된 채로
`tests/agent` 가 통과해야 기본 pytest 의 계약(키도 새 패키지도 없이 통과)이 지켜진다.
skip 사유를 `-rs` 로 읽으려면 `PYTHONIOENCODING=utf-8` 이 필요하다(cp949 로 깨진다).
"""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph", reason="langgraph 는 선택 갈래다 (requirements.txt 참고)")

from _loop_stubs import (  # noqa: E402 - importorskip 뒤에 와야 한다
    QUERY,
    StubEvaluator,
    StubGenerate,
    StubRetriever,
    StubRewriter,
)

from rag_pipeline.agent import graph as graph_module  # noqa: E402
from rag_pipeline.agent.graph import run_agent_graph  # noqa: E402
from rag_pipeline.agent.loop import run_agent_loop  # noqa: E402
from rag_pipeline.schemas.agent import AgentState  # noqa: E402


# 대조에 쓸 모양. **뺄 것만 이름으로 적는다** (모듈 docstring 참고)
def _comparable(state: AgentState) -> dict:
    data = state.model_dump()
    data.pop("next_action")
    for entry in data["tool_call_log"]:
        entry.pop("timestamp")
    return data


# 같은 시나리오를 두 경로에 넣는다. **가짜는 매번 새로 짓는다** — 나눠 쓰면 첫 경로가
# 재작성기·판정자의 내부 순서를 소진해 두 번째가 다른 것을 보게 된다
def _both(scenario, **kwargs) -> tuple[AgentState, StubRetriever, AgentState, StubRetriever]:
    loop_retriever, loop_deps = scenario()
    loop_state = run_agent_loop(QUERY, retriever=loop_retriever, **loop_deps, **kwargs)

    graph_retriever, graph_deps = scenario()
    graph_state = run_agent_graph(QUERY, retriever=graph_retriever, **graph_deps, **kwargs)

    return loop_state, loop_retriever, graph_state, graph_retriever


# --- 시나리오 셋 (Checkpoint A 의 DoD 와 같은 것들) ------------------------


def _sufficient():
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)
    return retriever, {
        "evaluator": StubEvaluator("sufficient"),
        "rewriter": rewriter,
        "generate": StubGenerate(),
    }


def _exhausting():
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)
    return retriever, {
        "evaluator": StubEvaluator("insufficient"),
        "rewriter": rewriter,
        "generate": StubGenerate(),
    }


def _rewrite_unavailable():
    rewriter = StubRewriter([None])
    retriever = StubRetriever(rewriter=rewriter)
    return retriever, {
        "evaluator": StubEvaluator("insufficient"),
        "rewriter": rewriter,
        "generate": StubGenerate(),
    }


SCENARIOS = {
    "sufficient": _sufficient,
    "exhausting": _exhausting,
    "rewrite_unavailable": _rewrite_unavailable,
}


# --- DoD: 동일 재현 -------------------------------------------------------


# 상태 전부가 같다 (`next_action`·timestamp 만 뺀다)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_graph_reproduces_the_loop_state(name: str):
    loop_state, _, graph_state, _ = _both(SCENARIOS[name])

    assert _comparable(graph_state) == _comparable(loop_state)


# **무엇으로 몇 번 찾았는지도 같다.** 상태만 보면 같은 답에 이르는 다른 길이 안 갈린다
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_graph_searches_the_same_queries(name: str):
    _, loop_retriever, _, graph_retriever = _both(SCENARIOS[name])

    assert graph_retriever.queries == loop_retriever.queries


# `max_retries` 를 바꿔도 두 경로가 같은 자리에서 멈춘다 — 상한 수식을 공유하는 것의 뜻이다
@pytest.mark.parametrize("max_retries", [0, 1, 3])
def test_graph_stops_at_the_same_retry_cap(max_retries: int):
    loop_state, loop_retriever, graph_state, graph_retriever = _both(
        _exhausting, max_retries=max_retries
    )

    assert graph_state.retry_count == loop_state.retry_count == max_retries
    assert graph_state.stop_reason == loop_state.stop_reason == "retries_exhausted"
    assert len(graph_retriever.queries) == len(loop_retriever.queries) == max_retries + 1


# --- 배선 자체 -----------------------------------------------------------


# 진입도 Router 가 정한다 — `START -> retrieve` 를 못 박으면 *첫 행동은 검색* 이 두 벌이 된다.
# **답이 생기면 끝내는 것까지 흉내 내야 한다** — 늘 `generate` 를 돌려주면 생성 뒤에도 생성으로
# 가서 상한에 걸린다(그 자체는 정상 동작이고, 아래 테스트가 그것을 따로 잰다)
def test_entry_is_routed_not_hardcoded(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        graph_module,
        "decide_next_action",
        lambda state: "end" if state.final_answer is not None else "generate",
    )

    state = run_agent_graph(QUERY, retriever=StubRetriever(), generate=StubGenerate())

    # 검색을 한 번도 안 거치고 바로 생성으로 갔다
    assert state.tool_call_log == []
    assert state.final_answer == "답변(0건)"


# 안 끝나면 `GraphRecursionError` 가 아니라 `RuntimeError` 로 올라온다 —
# 부르는 쪽이 langgraph 예외 계층을 알아야 하면 "선택 갈래" 가 아니게 된다
def test_a_graph_that_never_finishes_raises_runtime_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(graph_module, "decide_next_action", lambda state: "rewrite")
    rewriter = StubRewriter()

    with pytest.raises(RuntimeError, match="바퀴 안에 안 끝났습니다"):
        run_agent_graph(
            QUERY,
            retriever=StubRetriever(rewriter=rewriter),
            rewriter=rewriter,
            generate=StubGenerate(),
        )


# 반환 타입도 같다 — `invoke()` 가 dict 를 돌려주는 것을 여기서 되돌린다
def test_graph_returns_an_agent_state():
    state = run_agent_graph(QUERY, retriever=StubRetriever(), generate=StubGenerate())

    assert isinstance(state, AgentState)


# 다룰 수 없는 행동은 그래프에서도 조용히 안 넘어간다 (while 과 **같은 함수**에 물어본다)
def test_unknown_action_raises_in_the_graph(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(graph_module, "decide_next_action", lambda state: "web_search")

    with pytest.raises(RuntimeError, match="다룰 수 없는 행동"):
        run_agent_graph(QUERY, retriever=StubRetriever(), generate=StubGenerate())
