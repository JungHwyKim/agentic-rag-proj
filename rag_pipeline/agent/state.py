"""`AgentState` 를 만들고 갱신하는 헬퍼.

## 왜 제자리에서 안 고치나

**전부 새 상태를 돌려준다.** 루프가 도는 동안 어느 시점의 상태였는지를 되짚을 수 있어야
하고(재시도 3회가 각각 무엇을 봤나), 제자리 변경이면 그 기록이 마지막 값 하나로 뭉갠다.
pydantic 의 `model_copy(update=...)` 가 그 일을 한다.

## 왜 도구 로그와 검색 결과를 **같은 함수**가 다루나

`record_tool_call()` 이 로그를 쌓으면서 **검색 결과도 함께 싣는다.** 둘로 가르면
*로그에는 검색이 찍혔는데 `retrieved_chunks` 는 비어 있는* 조합이 생기고, 그 조합에서
Router 는 "검색을 안 했으니 검색하자"를 고르며 **같은 검색을 무한히 되풀이한다.**
`Answer.cached_from` 이 셋을 한 덩어리로 묶은 것과 같은 기준이다 — **못 쓰는 조합을 애초에
못 만들게 한다.**

**결과는 덧붙이는 것이 아니라 갈아끼운다.** `retrieve()` 가 돌려주는 것은 그 질의에 대한
**순위가 매겨진 목록 전체**라, 이어 붙이면 순위가 두 벌 섞여 `ranking_score` 정렬이
뜻을 잃는다. 재시도의 뜻도 *더 모은다* 가 아니라 *다시 찾는다* 다.

## 검색 표시(`mark_searched`)가 왜 `record_tool_call()` **밖**인가

Router 는 *다시 찾아야 하나* 를 `searched_query` 와 `current_query` 의 대조로 판정한다.
그 표시를 `record_tool_call()` 안에 두면 **결과 모양으로 판정하게 되는데**(청크가 왔나),
그러면 **도구가 실패한 회차에 표시가 안 남는다.** 실패해도 그 질의로는 이미 시도한 것이라,
안 남기면 루프가 같은 검색을 `max_retries` 와 무관하게 되고르며 영영 안 멈춘다 —
Checkpoint A 를 지으며 실제로 드러난 자리다.

그래서 축을 *찾았나* 가 아니라 ***이 질의로 시도했나*** 로 두고, 그 표시는 검색 단계가 직접
남긴다(`agent/loop.py:step_retrieve`). 도구 종류로 판정하는 안은 기각했다 — `state.py` 가
`RetrieverTool` 을 알게 되고, 가짜 도구로 루프를 재는 테스트가 통째로 막힌다.

**클립 전 원본을 싣는다.** `ToolCallLog.input_summary` 로 대신하면 앞 120자가 같은 긴 질의
둘이 **조용히 같아진다** — 요약은 사람이 읽는 것이고 이 칸은 코드가 읽는 것이라, 한 값으로
겸하면 자르는 길이가 곧 판정 기준이 된다.

## `mark_evidence_unchanged()` 도 흐름을 안 바꾼다

바로 위 표시와 **모양은 같고 뜻이 반대다.** `mark_searched` 는 *이 질의로 시도했나* 를 남겨
Router 가 읽지만, 이쪽은 **아무도 안 읽는다** — 재시도가 근거를 하나도 안 바꾼 회차를 세어
둘 뿐이고, 접을지는 몇 번 모아 본 뒤에 정한다(`agent/loop.py`).

그래서 `stop_reason` 을 안 건드린다. 표시가 곧 종료 사유가 되면 *세는 것* 과 *접는 것* 이
한 칸에 붙어, 세어 보고 안 접기로 결정하는 길이 없어진다.

## `start_retry()` 가 낡은 판정을 함께 지운다

재시도를 시작한다는 것은 **질의가 바뀐다**는 뜻이고, 그러면 직전 verdict 는 *이번 질의에 대한
판정이 아니다.* 안 지우면 다음 바퀴의 종료 사유와 로그가 앞 질의의 판정을 자기 것으로 읽는데,
**에러가 안 난다.** `QueryRewriter.rewrite()` 가 맨 앞에서 `last_rewritten` 을 지우는 것과
같은 사고이고, 위 *"못 쓰는 조합을 애초에 못 만들게 한다"* 의 같은 얼굴이다.

## 요약은 왜 잘라 넣나

`ToolCallLog` 에 청크 전문을 실으면 **한 질의의 상태가 코퍼스만큼 커진다.** 이 로그를 읽는
쪽이 보는 것은 *무슨 도구가 몇 번 불렸고 성공했나* 이고, 내용이 필요하면
`retrieved_chunks` 에 그대로 있다. 자르는 길이는 **한 줄로 읽히는 크기**로 잡았다 —
임계값이 아니라 용도라 실측할 대상이 아니다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import (
    AgentState,
    Message,
    NextAction,
    StopReason,
    ToolCallLog,
    ToolInput,
    ToolOutput,
)
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "mark_evidence_unchanged",
    "mark_searched",
    "with_stop_reason",
    "new_state",
    "record_tool_call",
    "start_retry",
    "with_answer",
    "with_context",
    "with_next_action",
    "with_verdict",
]

# 로그 한 줄이 사람 눈에 한 줄로 읽히는 크기 (모듈 docstring 참고)
_SUMMARY_MAX_CHARS = 120


# 질문 하나로 상태를 연다. **원 질의와 지금 질의는 여기서 같다** — 재작성이 그것을 가른다
def new_state(query: str, *, max_retries: int = 3) -> AgentState:
    return AgentState(
        original_query=query,
        current_query=query,
        conversation_history=[Message(role="user", content=query)],
        max_retries=max_retries,
    )


# 도구 호출 하나를 상태에 반영한다. **로그와 결과가 함께 간다** (모듈 docstring 참고)
def record_tool_call(
    state: AgentState,
    tool: BaseTool,
    tool_input: ToolInput,
    output: ToolOutput,
) -> AgentState:
    entry = ToolCallLog(
        tool_name=tool.name,
        input_summary=_clip(tool_input.query),
        output_summary=_summarize(output),
    )
    update: dict[str, object] = {"tool_call_log": [*state.tool_call_log, entry]}

    chunks = _chunks_of(output)
    if chunks is not None:
        # 덧붙이지 않고 갈아끼운다 (모듈 docstring 참고)
        update["retrieved_chunks"] = chunks

    return state.model_copy(update=update)


# 이 질의로 검색을 **시도했다**고 표시한다. 성공·실패를 안 가리는 것이 요점이다
# (모듈 docstring 참고). 클립 전 원본을 싣는다
def mark_searched(state: AgentState, query: str) -> AgentState:
    return state.model_copy(update={"searched_query": query})


# 이 재시도가 앞 회차와 **같은 근거**를 물어왔다고 세어 둔다. **접지 않는다**
# (모듈 docstring 참고). 판정은 부르는 쪽이 그대로 돌린다
def mark_evidence_unchanged(state: AgentState, retry_count: int) -> AgentState:
    return state.model_copy(
        update={"evidence_unchanged_at": [*state.evidence_unchanged_at, retry_count]}
    )


# 판정을 싣는다. **답변을 바꾸지 않는다** — 그것은 Router 가 읽고 정할 일이다
def with_verdict(state: AgentState, verdict: EvaluatorVerdict | None) -> AgentState:
    return state.model_copy(update={"evaluator_verdict": verdict})


# 재시도를 한 번 세고, 새 질의가 있으면 갈아끼운다. **원 질의는 안 건드리고 낡은 판정은
# 지운다** (모듈 docstring 참고)
def start_retry(state: AgentState, *, query: str | None = None) -> AgentState:
    update: dict[str, object] = {"retry_count": state.retry_count + 1, "evaluator_verdict": None}
    if query is not None:
        update["current_query"] = query
    return state.model_copy(update=update)


# Router 가 정한 다음 행동을 싣는다. 판단만 담고 실행은 안 한다
def with_next_action(state: AgentState, action: NextAction | None) -> AgentState:
    return state.model_copy(update={"next_action": action})


# 루프가 멈춘 이유를 싣는다. **한 번 정해지면 Router 가 생성으로만 보낸다** (router.py)
def with_stop_reason(state: AgentState, reason: StopReason) -> AgentState:
    return state.model_copy(update={"stop_reason": reason})


# 조립된 근거를 싣는다 (Phase 9)
def with_context(state: AgentState, context: str) -> AgentState:
    return state.model_copy(update={"final_context": context})


# 최종 답변을 싣고 대화에도 남긴다 — 다음 턴의 입력이 이 기록이다
def with_answer(state: AgentState, answer: str) -> AgentState:
    return state.model_copy(
        update={
            "final_answer": answer,
            "conversation_history": [
                *state.conversation_history,
                Message(role="assistant", content=answer),
            ],
        }
    )


# 도구 결과에서 청크 목록을 꺼낸다. 검색 도구가 아니면 None — 그때는 결과를 안 건드린다
def _chunks_of(output: ToolOutput) -> list[RetrievedChunk] | None:
    if not output.success or not isinstance(output.data, list):
        return None
    if not all(isinstance(item, RetrievedChunk) for item in output.data):
        return None
    return list(output.data)


# 도구가 무엇을 냈는지 한 줄로. **실패는 사유가 곧 요약이다**
def _summarize(output: ToolOutput) -> str:
    if not output.success:
        return _clip(output.error or "실패")
    chunks = _chunks_of(output)
    if chunks is not None:
        return f"청크 {len(chunks)}개"
    return _clip(str(output.data))


# 로그 한 줄 길이로 자른다
def _clip(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= _SUMMARY_MAX_CHARS:
        return flat
    return flat[:_SUMMARY_MAX_CHARS] + "…"
