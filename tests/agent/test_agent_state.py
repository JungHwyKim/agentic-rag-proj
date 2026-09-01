"""상태 헬퍼. **임의의 도구 호출 시퀀스에서 상태가 일관되게 쌓이는지**가 DoD 다.

특히 둘을 잠근다.

1. **로그와 검색 결과가 함께 간다** — 갈리면 *로그엔 검색이 찍혔는데 청크는 비어 있는*
   상태가 생기고, Router 가 거기서 같은 검색을 무한히 되풀이한다
2. **결과는 덧붙이지 않고 갈아끼운다** — 순위가 두 벌 섞이면 `ranking_score` 정렬이 뜻을 잃는다
3. **검색 표시는 도구 결과와 갈라져 있다** — 축이 *찾았나* 가 아니라 *이 질의로 시도했나* 라,
   실패한 회차에도 남아야 루프가 같은 검색을 안 되고른다. 그리고 클립 전 원본이어야 한다
4. **재시도를 시작하면 낡은 판정이 사라진다** — 안 지우면 다음 바퀴가 앞 질의의 판정을
   자기 것으로 읽는데 에러가 안 난다

그리고 전부 **새 상태를 돌려주는지**(원본 불변) 본다.
"""

from __future__ import annotations

from rag_pipeline.agent.state import (
    mark_searched,
    new_state,
    record_tool_call,
    start_retry,
    with_answer,
    with_context,
    with_next_action,
    with_verdict,
)
from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 정해둔 것을 그대로 내놓는 가짜 도구
class StubTool(BaseTool):
    name = "stub"
    description = "테스트용"

    def __init__(self, output: ToolOutput) -> None:
        self._output = output

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        return self._output


def _chunk(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, document_id="안내서.pdf", text=f"{chunk_id} 본문", metadata={}
    )


def _call(state, output: ToolOutput, query: str = "질문"):
    tool = StubTool(output)
    return record_tool_call(state, tool, ToolInput(query=query), output)


# 상태를 열면 원 질의와 지금 질의가 같고, 대화에 사용자 줄이 하나 남는다
def test_new_state_seeds_query_and_history():
    state = new_state("데이터세트 이관 절차는")

    assert state.original_query == state.current_query == "데이터세트 이관 절차는"
    assert [m.role for m in state.conversation_history] == ["user"]
    assert state.retry_count == 0
    assert state.max_retries == 3
    assert state.next_action is None


# 검색 도구의 결과는 로그와 청크에 **함께** 실린다
def test_tool_call_records_log_and_chunks_together():
    state = _call(new_state("질문"), ToolOutput(success=True, data=[_chunk("c1"), _chunk("c2")]))

    assert [c.chunk_id for c in state.retrieved_chunks] == ["c1", "c2"]
    assert len(state.tool_call_log) == 1
    assert state.tool_call_log[0].output_summary == "청크 2개"


# 두 번째 검색은 덧붙이지 않고 갈아끼운다
def test_second_retrieval_replaces_chunks():
    state = _call(new_state("질문"), ToolOutput(success=True, data=[_chunk("c1")]))
    state = _call(state, ToolOutput(success=True, data=[_chunk("c9")]))

    assert [c.chunk_id for c in state.retrieved_chunks] == ["c9"]
    assert len(state.tool_call_log) == 2


# 실패한 도구는 로그만 남기고 결과를 안 건드린다 — 있던 근거가 사라지면 안 된다
def test_failed_tool_keeps_previous_chunks():
    state = _call(new_state("질문"), ToolOutput(success=True, data=[_chunk("c1")]))
    state = _call(state, ToolOutput(success=False, error="ConnectionError: 저장소 응답 없음"))

    assert [c.chunk_id for c in state.retrieved_chunks] == ["c1"]
    assert state.tool_call_log[-1].output_summary.startswith("ConnectionError")


# 검색이 아닌 도구의 결과는 청크로 안 실린다
def test_non_chunk_output_does_not_touch_chunks():
    state = _call(new_state("질문"), ToolOutput(success=True, data="웹 문서 3건"))

    assert state.retrieved_chunks == []
    assert state.tool_call_log[0].output_summary == "웹 문서 3건"


# **도구 호출 자체는 검색 표시를 안 남긴다.** 남기는 것은 검색 단계다 (모듈 docstring 참고)
def test_tool_call_alone_does_not_mark_searched():
    state = _call(new_state("질문"), ToolOutput(success=True, data=[_chunk("c1")]))

    assert state.searched_query is None


# 표시는 **클립 전 원본**으로 남는다. 요약은 잘려 있어 판정에 못 쓴다
def test_mark_searched_keeps_the_unclipped_query():
    long_query = "가" * 500
    state = _call(new_state("질문"), ToolOutput(success=True, data=[_chunk("c1")]), query=long_query)

    marked = mark_searched(state, long_query)

    assert marked.searched_query == long_query
    assert marked.tool_call_log[0].input_summary != long_query


# 재시도를 시작하면 낡은 판정이 사라진다 (모듈 docstring 참고)
def test_start_retry_clears_stale_verdict():
    state = with_verdict(
        new_state("질문"), EvaluatorVerdict(verdict="insufficient", reasoning="근거 부족")
    )

    retried = start_retry(state, query="새 질의")

    assert retried.evaluator_verdict is None
    assert retried.retry_count == 1
    assert state.evaluator_verdict is not None


# 원본은 안 바뀐다 — 어느 시점의 상태였는지를 되짚을 수 있어야 한다
def test_helpers_do_not_mutate():
    first = new_state("질문")
    later = with_verdict(
        start_retry(_call(first, ToolOutput(success=True, data=[_chunk("c1")])), query="새 질의"),
        EvaluatorVerdict(verdict="insufficient", reasoning="근거 부족"),
    )

    assert first.retrieved_chunks == []
    assert first.retry_count == 0
    assert first.current_query == "질문"
    assert later.retry_count == 1
    assert later.current_query == "새 질의"
    assert later.original_query == "질문"
    assert later.evaluator_verdict.verdict == "insufficient"


# 답변을 실으면 대화에도 남는다 — 다음 턴의 입력이 이 기록이다
def test_answer_lands_in_history():
    state = with_answer(with_context(new_state("질문"), "근거 본문"), "답변입니다")

    assert state.final_context == "근거 본문"
    assert state.final_answer == "답변입니다"
    assert [m.role for m in state.conversation_history] == ["user", "assistant"]


# 라우터가 정한 행동은 그냥 실린다. 실행은 여기서 안 한다
def test_next_action_is_only_stored():
    assert with_next_action(new_state("질문"), "retrieve").next_action == "retrieve"


# 아주 긴 질의도 로그 한 줄로 잘린다
def test_long_input_is_clipped():
    state = _call(new_state("질문"), ToolOutput(success=True, data=[]), query="가" * 500)

    assert state.tool_call_log[0].input_summary.endswith("…")
    assert len(state.tool_call_log[0].input_summary) < 200
