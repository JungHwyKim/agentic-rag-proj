"""에이전트 트랙. 도구(Tool) · 상태(AgentState) · 라우터 · 근거 조립이 여기 산다.

## 왜 `retrieval/` 아래가 아닌가

**도구는 검색을 감싸는 쪽이지 검색의 일부가 아니다.** `RetrieverTool` 은 `retrieve()` 를
부르기만 하고 검색 로직을 하나도 안 갖는다. `retrieval/` 아래 두면 *검색이 자기를 감싼
껍데기를 품는* 모양이 되고, 나중에 붙는 웹 검색 도구가 갈 곳이 없어진다.

`answer_cache/` 가 신설 최상위로 간 것과 같은 기준이다 — 소비자가 여럿이고 어느 한 단계에
속하지 않으면 제 이름의 자리를 갖는다.

## 스키마만 `schemas/agent.py` 에 있다

이 저장소는 스키마를 `schemas/` 에 모은다(`document.py`·`chunk.py`·`vector.py`·
`retrieval.py`·`answer.py`). 여기에 또 두면 **여섯 번째 자리**가 생기고, 그때부터
"이 타입이 어디 있더라"가 매번 두 곳을 뒤지는 일이 된다.

## 판단과 실행은 여전히 갈려 있다

**Router 는 도구를 실행하지 않는다.** 루프(`loop.py`)가 생긴 뒤에도 그대로다 — 합치면
*라우팅이 틀렸나 도구가 틀렸나* 를 못 가른다(Evaluator 가 판정과 생성을 안 섞은 것과 같은
이유다). `decide_next_action()` 은 부수 효과가 없고, 상태를 바꾸는 것은 전부 핸들러다.

## 여기서 `graph.py` 를 import 하지 않는다

**일부러다.** `langgraph` 는 선택 갈래라(`requirements.txt`) 안 깔린 머신이 있을 수 있는데,
여기서 끌어오면 `from rag_pipeline.agent import ...` 가 **통째로** ImportError 로 죽는다.
기본 경로인 `run_agent_loop()` 는 langgraph 를 아예 안 건드리는 것이 그 결정의 실제 내용이라,
그래프가 필요한 쪽만 `rag_pipeline.agent.graph` 를 직접 import 한다.

## 이 단계의 규약이 어디 있나

- 도구 계약(예외를 밖으로 안 낸다) — `tools/base.py`
- 자원 수명을 왜 생성자로 받나 — `tools/retriever.py`
- 웹 검색을 왜 비워 뒀나 — `tools/web_search.py`
- 상태 갱신 규칙(로그와 결과가 함께 간다) — `state.py`
- 라우팅 규칙의 순서와 `failed` 처리 — `router.py`
- 근거 모양의 출처가 하나인 이유 — `context.py`
- 재시도가 무엇을 승급하나 / 안 멈추는 것을 무엇이 막나 — `loop.py`
- 그래프가 while 과 같은 것을 도는지 어떻게 아나 — `graph.py`
"""

from __future__ import annotations

from rag_pipeline.agent.context import build_context, select_within_budget
from rag_pipeline.agent.loop import LoopDeps, run_agent_loop, step_budget
from rag_pipeline.agent.router import decide_next_action
from rag_pipeline.agent.state import (
    mark_evidence_unchanged,
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
from rag_pipeline.agent.tools import BaseTool, RetrieverTool, WebSearchTool

__all__ = [
    "BaseTool",
    "LoopDeps",
    "RetrieverTool",
    "WebSearchTool",
    "build_context",
    "decide_next_action",
    "mark_evidence_unchanged",
    "mark_searched",
    "new_state",
    "record_tool_call",
    "run_agent_loop",
    "select_within_budget",
    "start_retry",
    "step_budget",
    "with_answer",
    "with_context",
    "with_next_action",
    "with_stop_reason",
    "with_verdict",
]
