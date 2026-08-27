"""라우터 규칙. **파일 이름에 `agent_` 가 붙은 이유가 있다** —
`tests/ingestion/loader/content/test_router.py` 가 이미 있고, `tests/` 에 `__init__.py` 가
없어 basename 이 곧 모듈명이라 겹치면 전체 pytest 가 collection error 로 죽는다.

DoD 는 *다양한 state 시나리오에 합리적 next_action* 이다. 그중 셋은 규칙이 아니라 **함정**이라
따로 잠근다.

1. **0건에서 `retrieve` 를 되고르지 않는다** — 검색은 결정적이라 같은 질의면 같은 결과다.
   되고르면 상태를 안 바꾼 채 `max_retries` 까지 돈다
2. **`failed` 를 재시도로 안 삼킨다** — 죽은 판정자 때문에 질의당 LLM 호출이 늘어난다
3. **`retrieve` 판정은 청크가 아니라 도구 로그로 한다** — 청크로 하면 *검색했는데 0건* 과
   *아직 안 했다* 가 안 갈린다
"""

from __future__ import annotations

import pytest

from rag_pipeline.agent.router import decide_next_action
from rag_pipeline.agent.state import new_state
from rag_pipeline.schemas.agent import AgentState, ToolCallLog
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", document_id="안내서.pdf", text="본문", metadata={})


def _searched(**updates) -> AgentState:
    """검색을 한 번 돌린 뒤의 상태. 기본은 근거 1건·판정 없음."""
    base = new_state("질문").model_copy(
        update={
            "tool_call_log": [
                ToolCallLog(tool_name="retriever", input_summary="질문", output_summary="청크 1개")
            ],
            "retrieved_chunks": [_chunk()],
        }
    )
    return base.model_copy(update=updates) if updates else base


def _verdict(value: str) -> EvaluatorVerdict:
    return EvaluatorVerdict(verdict=value, reasoning="테스트")


# 아무것도 안 한 상태에서는 검색부터
def test_fresh_state_retrieves():
    assert decide_next_action(new_state("질문")) == "retrieve"


# 답이 있으면 끝. 근거가 남아 있어도 다시 안 돈다
def test_answer_ends():
    state = _searched(final_context="근거", final_answer="답변")

    assert decide_next_action(state) == "end"


# 근거가 조립됐으면 생성뿐이다
def test_context_generates():
    assert decide_next_action(_searched(final_context="근거")) == "generate"


# 근거가 있고 판정이 충분하면 생성
def test_sufficient_generates():
    assert decide_next_action(_searched(evaluator_verdict=_verdict("sufficient"))) == "generate"


# 판정을 안 돌렸어도 근거가 있으면 생성 — 판정은 선택이지 관문이 아니다
def test_no_verdict_with_chunks_generates():
    assert decide_next_action(_searched()) == "generate"


# 불충분이고 재시도가 남았으면 질의를 다시 쓴다
def test_insufficient_rewrites():
    assert decide_next_action(_searched(evaluator_verdict=_verdict("insufficient"))) == "rewrite"


# **실패는 재시도가 아니다** — 죽은 판정자 때문에 호출이 늘면 안 된다
def test_failed_verdict_generates():
    assert decide_next_action(_searched(evaluator_verdict=_verdict("failed"))) == "generate"


# 0건이면 판정이 없어도 다시 쓴다. **`retrieve` 가 아니다** (모듈 docstring 참고)
def test_empty_result_rewrites_not_retrieves():
    assert decide_next_action(_searched(retrieved_chunks=[])) == "rewrite"


# 재시도를 다 썼으면 불충분이어도 생성으로 나간다 — 여기가 루프의 정지점이다
@pytest.mark.parametrize("verdict", ["insufficient", "failed", "sufficient"])
def test_exhausted_retries_generate(verdict: str):
    state = _searched(evaluator_verdict=_verdict(verdict), retry_count=3, max_retries=3)

    assert decide_next_action(state) == "generate"


# 0건이어도 재시도를 다 썼으면 멈춘다. 근거 없이 답하는 것은 생성 쪽 규약이 다룬다
def test_exhausted_retries_with_no_chunks_generate():
    state = _searched(retrieved_chunks=[], retry_count=3, max_retries=3)

    assert decide_next_action(state) == "generate"


# 판단만 한다 — 상태를 안 건드린다
def test_router_does_not_mutate_state():
    state = _searched(evaluator_verdict=_verdict("insufficient"))
    before = state.model_dump()

    decide_next_action(state)

    assert state.model_dump() == before
