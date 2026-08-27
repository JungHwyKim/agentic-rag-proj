"""재시도 루프. **LangGraph 없이** 판단(Router)과 실행(핸들러)을 잇는다.

스펙(`retrieval/IMPLEMENTATION_SPEC.md`)의 ⚑Checkpoint A 다. 그래프를 씌우기 **전에** 재시도
제어만 따로 돌려 보는 것이 목적이고, 그래야 나중에 안 멈출 때 *그래프 wiring 이 틀렸나 retry
로직이 틀렸나* 를 가를 수 있다. 같은 핸들러를 `agent/graph.py` 가 노드로 감싼다 —
**루프가 두 벌이 되지 않는 것이 이 파일이 제품 코드인 이유다.**

## 네 번째 진입점이고 `answer_question()` 을 안 건드린다

`query.py:answer_question()` 은 *"판정이 답변을 안 바꾼다"* 를 계약으로 갖는다. 여기서는
판정이 **흐름을 가른다**. 같은 함수에 스위치로 넣으면 한 함수가 반대되는 두 계약을 동시에
갖게 되고, *판정이 맞았나* 와 *답이 좋아졌나* 가 한 실행에 섞인다 — 답변 품질을 재는 자[尺]가
아직 없어 그 섞임을 풀 방법도 없다.

**답변 캐시도 여기 없다.** 캐시는 `retrieve()` **앞**에 서는 것이고 이 루프는 검색을 여러 번
도는 흐름이라, 붙일 자리가 정해지려면 *무엇을 캐시하나*(첫 질의? 마지막 질의?)를 먼저 정해야
한다. 고를 축이 실재할 때 붙인다.

## 검색은 `current_query`, 판정·생성은 `original_query`

재작성 질의는 **검색용 확장어**다. *이 근거가 질문에 답하나* 의 대상도, 답을 만들 대상도
사용자가 실제로 물은 문장이다. `retrieval/hybrid.py` 가 리랭커에 원 질의를 넘기는 것과 같은
잣대이고, 어기면 **질문이 바뀐 답**이 나간다.

## `rewrite` 단계가 하는 일은 「승급」이다

이 저장소의 재작성기는 `(원질의, verdict) → 새 질의` 가 아니라 `retrieve()` **안**에서 도는
PRF 다(`retrieval/rewrite.py`). 그래서 루프가 재작성기를 **다시 부르지 않는다** —
직전 검색이 이미 쓴 질의를 `last_rewritten` 에서 읽어 `current_query` 로 올릴 뿐이다.

그 결과가 셋이다.

- **추가 LLM 호출이 0이다.** 재작성 호출은 원래부터 `retrieve()` 당 한 번이다
- **`fuse` 이득을 안 버린다.** 루프가 밟는 검색 경로가 `answer_question()`·`run_eval` 과
  글자 그대로 같아, 두 번째 진실이 안 생긴다
- **진행이 보장된다.** 승급된 질의로 다시 검색하면 1차 pool 이 달라지고, 그러면 다음 PRF 의
  입력 `(질의, pool)` 도 달라져 또 다른 질의가 나온다

승급할 것이 없으면(`None` 이거나 지금 질의와 같으면) **접는다.** 같은 질의로 다시 검색하면
같은 결과라 재시도가 아무것도 안 바꾸기 때문이다 — `router.py` 가 0건에서 `retrieve` 를
안 되고르는 것과 같은 이유다.

## 안 멈추는 것을 무엇이 막나 — 상수가 아니라 구조다

바퀴마다 상태가 반드시 전진한다. `retrieve` 는 `searched_query` 를(**도구가 실패해도**),
`rewrite` 는 `retry_count` 를 또는 `stop_reason` 을, `generate` 는 `final_answer` 를 바꾼다.

그 위에 `step_budget()` 이 있는데 **여유가 아니라 정확한 상한**이다 —
`retrieve` 1 + (`rewrite`+`retrieve`)×`max_retries` + `generate` 1 + `end` 1. 넘으면
`RuntimeError` 다. 조용히 `stop_reason` 으로 접으면 *Router 와 핸들러가 어긋났다* 는 버그가
**수치로만** 드러난다.

## `web_search` 는 예외로 터진다

Router 가 지금 그것을 절대 안 고른다(*고를 신호가 없다*, `router.py`). 그런데도 조용히
넘기면, 나중에 규칙이 생겼을 때 **아무 일도 안 하는 바퀴**가 예산을 먹다 `RuntimeError` 로
끝나고 원인이 여기 안 보인다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rag_pipeline.agent.context import build_context
from rag_pipeline.agent.router import decide_next_action
from rag_pipeline.agent.state import (
    mark_searched,
    new_state,
    record_tool_call,
    start_retry,
    with_answer,
    with_context,
    with_next_action,
    with_stop_reason,
    with_verdict,
)
from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.generation import OpenAIEvaluator, generate_answer
from rag_pipeline.retrieval import QueryRewriter
from rag_pipeline.schemas.agent import AgentState, NextAction, StopReason, ToolInput
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "ACTION_STEPS",
    "LoopDeps",
    "default_generate",
    "run_agent_loop",
    "step_budget",
    "step_for",
]


# 기본 생성기. **공개다** — `graph.py` 가 같은 기본값을 써야 두 경로의 시그니처가 나란하다.
# **설정을 인자로 안 받는다** — 필요하면 호출부가
# `lambda q, c: generate_answer(q, c, cfg)` 를 넘긴다. 그러면 인자를 안 늘리고도 설정이 닿는다
def default_generate(query: str, chunks: list[RetrievedChunk]) -> str:
    return generate_answer(query, chunks)


# 루프가 쓰는 자원 묶음. **수명을 정하는 것은 루프가 아니라 루프를 만드는 쪽이다** —
# `RetrieverTool` docstring 의 이유가 그대로다(qdrant 배타 잠금 · KURE 2.27GB 재적재).
# 한 덩어리인 것은 그래프 노드가 같은 것을 받아야 하기 때문이다
@dataclass(frozen=True)
class LoopDeps:
    retriever: BaseTool
    evaluator: OpenAIEvaluator | None = None
    rewriter: QueryRewriter | None = None
    generate: Callable[[str, list[RetrievedChunk]], str] = default_generate
    context_budget_chars: int | None = None


# 이 max_retries 로 돌 수 있는 바퀴 수. **정확한 상한이다** (모듈 docstring 참고)
def step_budget(max_retries: int) -> int:
    return 2 * max(max_retries, 0) + 3


# 검색 한 번 + 판정 한 번. **표시는 성공·실패를 안 가린다** (state.py 참고)
def step_retrieve(state: AgentState, deps: LoopDeps) -> AgentState:
    tool_input = ToolInput(query=state.current_query)
    output = deps.retriever.run(tool_input)
    state = mark_searched(record_tool_call(state, deps.retriever, tool_input, output), tool_input.query)

    if deps.evaluator is None:
        return state
    # **원 질의로 판정한다** (모듈 docstring 참고). 실패는 예외가 아니라 제3의 verdict 로 온다
    return with_verdict(state, deps.evaluator.evaluate(state.original_query, state.retrieved_chunks))


# 직전 검색이 이미 쓴 질의를 승급한다. **재작성기를 다시 안 부른다** (모듈 docstring 참고)
def step_rewrite(state: AgentState, deps: LoopDeps) -> AgentState:
    promoted = _promoted_query(state, deps)
    if promoted is None:
        return with_stop_reason(state, "rewrite_unavailable")
    return start_retry(state, query=promoted)


# 근거를 조립하고 답을 만든다. **여기가 유일한 정상 출구다**
def step_generate(state: AgentState, deps: LoopDeps) -> AgentState:
    context = build_context(state.retrieved_chunks, budget_chars=deps.context_budget_chars)
    state = with_context(state, context)
    state = with_answer(state, deps.generate(state.original_query, state.retrieved_chunks))
    # 앞 단계가 이미 사유를 적었으면 그것이 이긴다 — 더 구체적인 사유를 덮어쓰지 않는다
    return with_stop_reason(state, state.stop_reason or _reason_for(state))


# 액션 이름 -> 핸들러. **`agent/graph.py` 가 이 표를 그대로 노드로 만든다** —
# 표가 둘이면 그래프와 while 이 다른 것을 돌면서도 이름이 같아 안 갈린다
ACTION_STEPS: dict[NextAction, Callable[[AgentState, LoopDeps], AgentState]] = {
    "retrieve": step_retrieve,
    "rewrite": step_rewrite,
    "generate": step_generate,
}


# 이 행동을 누가 실행하나. **모르는 행동을 조용히 넘기지 않는다** (모듈 docstring 참고).
# `graph.py` 의 라우팅도 이 함수에 물어본다 — 다루는 행동 집합이 두 벌이면 while 과 그래프가
# 서로 다른 것을 하면서도 이름이 같아 안 갈린다
def step_for(action: NextAction) -> Callable[[AgentState, LoopDeps], AgentState]:
    step = ACTION_STEPS.get(action)
    if step is None:
        raise RuntimeError(f"재시도 루프가 다룰 수 없는 행동입니다: {action}")
    return step


# 승급할 질의. 없거나 지금 것과 같으면 None — 그것이 곧 "더 갈 수 없다"다
def _promoted_query(state: AgentState, deps: LoopDeps) -> str | None:
    if deps.rewriter is None:
        return None
    rewritten = deps.rewriter.last_rewritten
    if not rewritten or rewritten == state.current_query:
        return None
    return rewritten


# 왜 멈췄나. **재시도를 다 쓴 것과 만족한 것을 세지 않고 판정으로 가른다** —
# 마지막 재시도에서 *충분* 이 나온 경우가 `retry_count` 만 보면 소진으로 잘못 읽힌다
def _reason_for(state: AgentState) -> StopReason:
    verdict = state.evaluator_verdict
    wants_more = (verdict is not None and verdict.verdict == "insufficient") or not state.retrieved_chunks
    if wants_more and state.retry_count >= state.max_retries:
        return "retries_exhausted"
    return "answered"


# 질문 하나를 근거가 찰 때까지 다시 찾아가며 답한다. 반환은 **상태 전체**다 —
# 답만 돌려주면 몇 번 재시도했고 왜 멈췄나가 사라진다
def run_agent_loop(
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
    state = new_state(query, max_retries=max_retries)

    for _ in range(step_budget(max_retries)):
        action = decide_next_action(state)
        state = with_next_action(state, action)
        if action == "end":
            return state
        state = step_for(action)(state, deps)

    # 여기 오면 바퀴마다 전진한다는 전제가 깨진 것이다 (모듈 docstring 참고)
    raise RuntimeError(
        f"재시도 루프가 {step_budget(max_retries)}바퀴 안에 안 끝났습니다: "
        f"action={state.next_action} retry_count={state.retry_count}"
    )

