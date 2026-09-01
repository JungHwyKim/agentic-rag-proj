"""Phase 11 — 같은 루프를 LangGraph 로 한 번 더 돈다.

**여기에 로직이 하나도 없다.** 노드는 `loop.py` 의 핸들러이고, 분기는 `router.py` 의
`decide_next_action()` 이며, 상한은 `loop.py` 의 `step_budget()` 이다. 이 파일이 하는 일은
그 셋을 그래프 모양으로 **배선하는 것뿐**이다 — 스펙이 *"범위 밖: 개별 노드 내부 로직
재구현 — 검증된 함수를 노드로 감싸기만"* 이라고 적은 그대로다.

그래서 Phase 11 의 DoD(*"Checkpoint A 의 retry 동작이 그래프 상에서도 동일하게 재현"*)가
**우연이 아니라 구조**다. 두 경로가 같은 함수를 부르므로, 갈릴 수 있는 것은 배선뿐이고
그것이 정확히 이 파일이 재는 것이다.

## `langgraph` 는 선택 갈래다

`agent/__init__.py` 가 이 모듈을 **일부러 import 하지 않는다.** 안 깔린 머신에서
`from rag_pipeline.agent import ...` 가 통째로 죽지 않게 하려는 것이고, 그래서 기본 경로인
`run_agent_loop()` 은 langgraph 를 아예 안 건드린다. `cohere` 가 밟은 길과 같다 —
**기본 pytest 가 새 패키지 없이도 통과하는 계약**이 이 저장소에서 여러 번 값을 한 자리다.

## 상태를 `AgentState` 그대로 쓴다

`StateGraph(AgentState)` 로 pydantic 모델을 상태 스키마에 그대로 넣는다. 그래프 전용
TypedDict 를 따로 두면 **상태가 두 벌**이 되어 *동일 재현* 을 필드 대조로 못 잰다 —
26회차의 *"살아 있는 타입이 초안을 이긴다"* 가 여기서 그대로 문다.

**노드는 `dict(state)` 를 돌려준다. `model_dump()` 가 아니다.** 앞엣것은 필드 이름에 실제
객체를 담아 주고, 뒤엣것은 중첩 pydantic 을 dict 로 뭉갠다 — 그러면 `RetrievedChunk`·
`EvaluatorVerdict` 가 재검증을 거치며 신원을 잃는다(실측으로 앞엣것은 타입이 그대로다).

## reducer(`Annotated`)를 안 쓴다

reducer 가 필요한 조건은 **한 칸에 둘 이상이 동시에 쓰는 것**(병렬 분기)인데 이 그래프는
한 번에 노드 하나다. 게다가 `state.py` 가 이미 *"결과는 덧붙이는 것이 아니라 갈아끼운다"* 로
**전량 교체**를 못 박아 두어, LangGraph 의 기본(마지막 쓰기가 이긴다)과 뜻이 같다.

`DESIGN.md` 가 *"`AgentState` 를 pydantic 으로 굳힌 뒤 옮기면 reducer·`Annotated` 때문에
다시 쓰게 된다"* 로 Phase 11 을 Phase 7 **앞**에 두라고 적었는데, 그 순서는 이미 지났고
(26회차가 Phase 7 을 먼저 커밋했다) **대가는 안 치렀다.** 이유가 위 두 문단이다.

## `next_action` 은 while 경로만 채운다

그래프에서는 **엣지가 곧 그 값**이라 상태에 또 적으면 두 번째 진실이 된다. 그래서 두 경로의
상태를 대조할 때 이 칸은 뺀다 — 빼는 이유가 이것이고,
`tests/agent/test_agent_graph.py` 가 그 사실을 명시적으로 적어 둔다.

## 상한은 같은 수식이되 그래프가 한 바퀴 덜 쓴다

`recursion_limit` 에 `step_budget(max_retries)` 를 그대로 넘긴다. while 쪽은 마지막에
`end` 를 **한 바퀴 써서** 확인하는데 그래프는 END 로 가는 전이가 super-step 이 아니라,
그래프가 정확히 한 바퀴를 덜 쓴다. 넉넉히 주는 것이 아니라 **같은 수식에서 나온 차이**라
따로 상수를 안 만든다.

넘치면 `GraphRecursionError` 인데 **`RuntimeError` 로 바꿔 올린다** — 부르는 쪽이 langgraph
예외 계층을 알아야 하면 "선택 갈래" 가 아니게 된다.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from rag_pipeline.agent.loop import (
    ACTION_STEPS,
    LoopDeps,
    default_generate,
    step_budget,
    step_for,
)
from rag_pipeline.agent.router import decide_next_action
from rag_pipeline.agent.state import new_state
from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.generation import OpenAIEvaluator
from rag_pipeline.retrieval import QueryRewriter
from rag_pipeline.schemas.agent import AgentState
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = ["build_agent_graph", "run_agent_graph"]


# 핸들러 하나를 노드로 감싼다. **`dict()` 이지 `model_dump()` 가 아니다** (모듈 docstring)
def _node(
    step: Callable[[AgentState, LoopDeps], AgentState], deps: LoopDeps
) -> Callable[[AgentState], dict]:
    def node(state: AgentState) -> dict:
        return dict(step(state, deps))

    return node


# 다음 노드. **판단은 Router 가 하고 여기서는 이름만 옮긴다**
def _route(state: AgentState) -> str:
    action = decide_next_action(state)
    if action == "end":
        return END
    # while 경로와 **같은 함수**에 물어본다 — 다루는 행동 집합이 두 벌이면 조용히 갈린다
    step_for(action)
    return action


# 그래프를 짓는다. 노드 이름이 곧 `NextAction` 값이라 로그·분기가 같은 낱말을 쓴다
def build_agent_graph(deps: LoopDeps):
    graph = StateGraph(AgentState)
    for action, step in ACTION_STEPS.items():
        graph.add_node(action, _node(step, deps))

    branches: dict[str, str] = {action: action for action in ACTION_STEPS}
    branches[END] = END

    # **진입도 조건부다.** `START -> retrieve` 로 못 박으면 *첫 행동은 검색* 이라는 지식이
    # Router 밖에 한 벌 더 생긴다
    graph.add_conditional_edges(START, _route, branches)
    for action in ACTION_STEPS:
        graph.add_conditional_edges(action, _route, branches)

    return graph.compile()


# `run_agent_loop()` 과 **같은 인자**를 받는다 — 안 그러면 두 경로를 나란히 못 놓는다
def run_agent_graph(
    query: str,
    *,
    retriever: BaseTool,
    evaluator: OpenAIEvaluator | None = None,
    rewriter: QueryRewriter | None = None,
    generate: Callable[[str, list[RetrievedChunk]], str] = default_generate,
    max_retries: int = 3,
    context_budget_chars: int | None = None,
) -> AgentState:
    deps = LoopDeps(
        retriever=retriever,
        evaluator=evaluator,
        rewriter=rewriter,
        generate=generate,
        context_budget_chars=context_budget_chars,
    )
    limit = step_budget(max_retries)

    try:
        final = build_agent_graph(deps).invoke(
            new_state(query, max_retries=max_retries), config={"recursion_limit": limit}
        )
    except GraphRecursionError as exc:
        # 부르는 쪽이 langgraph 예외를 알아야 하면 "선택 갈래" 가 아니게 된다
        raise RuntimeError(
            f"재시도 루프가 {limit}바퀴 안에 안 끝났습니다: 그래프가 END 에 못 닿았습니다"
        ) from exc

    # `invoke()` 는 dict 를 돌려준다 — 두 경로의 반환 타입을 맞춰야 대조가 성립한다
    return AgentState.model_validate(final)
