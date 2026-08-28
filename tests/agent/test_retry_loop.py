"""⚑Checkpoint A — 재시도 루프.

스펙의 DoD 셋이 1~3 이고, 나머지는 **이 루프를 지으며 실제로 밟은 함정**이다.

1. 충분 판정이면 즉시 끝난다
2. 불충분이 이어지면 `retry_count == max_retries` 에서 **정확히** 멈춘다
3. 종료 사유가 셋으로 갈린다

함정 중 둘이 특히 조용하다.

- **도구가 실패한 회차도 「이 질의로 시도했다」를 남겨야 한다.** 안 남기면 Router 가 같은
  검색을 되고르며 `max_retries` 와 무관하게 영영 안 멈춘다
- **승급할 질의가 없을 때 접지 않으면 제자리를 돈다.** 판정은 여전히 *불충분* 이고 재시도도
  남아 있어 Router 가 `rewrite` 를 계속 고른다

전부 오프라인이다 — `store`·`embedder`·qdrant·OpenAI 를 하나도 안 부른다.
가짜는 `_loop_stubs.py` 에 있다 — 그래프 경로와 **같은 것**을 봐야 동등성이 성립한다.
"""

from __future__ import annotations

import pytest
from _loop_stubs import QUERY, StubEvaluator, StubGenerate, StubRetriever, StubRewriter, chunk

from rag_pipeline.agent import loop as loop_module
from rag_pipeline.agent.loop import run_agent_loop, step_budget
from rag_pipeline.generation import NO_EVIDENCE_ANSWER

# --- DoD 1 ---------------------------------------------------------------


# 충분하면 재시도 없이 끝난다. 검색은 한 번뿐이다
def test_sufficient_stops_immediately():
    retriever = StubRetriever()
    rewriter = StubRewriter()

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("sufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert state.stop_reason == "answered"
    assert state.retry_count == 0
    assert retriever.queries == [QUERY]
    assert state.final_answer == "답변(1건)"


# --- DoD 2 ---------------------------------------------------------------


# 불충분이 이어지면 정확히 max_retries 에서 멈추고, 검색은 그보다 한 번 더 돈다
@pytest.mark.parametrize("max_retries", [0, 1, 3])
def test_insufficient_stops_exactly_at_max_retries(max_retries: int):
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)
    evaluator = StubEvaluator("insufficient")

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=evaluator,
        rewriter=rewriter,
        generate=StubGenerate(),
        max_retries=max_retries,
    )

    assert state.retry_count == max_retries
    assert len(retriever.queries) == max_retries + 1
    assert state.stop_reason == "retries_exhausted"
    # 판정도 검색마다 새로 돈다 — 낡은 판정을 물려쓰지 않는다
    assert len(evaluator.queries) == max_retries + 1


# 재시도마다 **다른 질의**로 찾는다. 같은 질의를 되풀이하면 재시도가 아무것도 안 바꾼다
def test_each_retry_searches_a_different_query():
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)

    run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert retriever.queries[0] == QUERY
    assert len(set(retriever.queries)) == len(retriever.queries)


# --- DoD 3 · 사유가 갈린다 -----------------------------------------------


# 승급할 질의를 못 내면 접는다. **검색이 더 안 돈다**
def test_rewrite_without_a_new_query_folds():
    rewriter = StubRewriter([None])
    retriever = StubRetriever(rewriter=rewriter)

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert state.stop_reason == "rewrite_unavailable"
    assert retriever.queries == [QUERY]
    assert state.retry_count == 0


# 같은 질의를 되돌려줘도 같다 — 같은 질의로 다시 찾으면 같은 결과다
def test_rewrite_returning_the_same_query_folds():
    rewriter = StubRewriter([QUERY])
    retriever = StubRetriever(rewriter=rewriter)

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert state.stop_reason == "rewrite_unavailable"
    assert retriever.queries == [QUERY]


# 재작성기를 아예 안 넘겨도 접는다 — 안 넘기는 것이 곧 끄는 스위치다
def test_without_a_rewriter_it_folds_instead_of_spinning():
    retriever = StubRetriever()

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        generate=StubGenerate(),
    )

    assert state.stop_reason == "rewrite_unavailable"
    assert retriever.queries == [QUERY]


# --- 함정 -----------------------------------------------------------------


# **실패는 재시도를 안 태운다** — 죽은 판정자 때문에 질의당 호출이 늘면 안 된다
def test_failed_verdict_does_not_retry():
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("failed"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert state.retry_count == 0
    assert state.stop_reason == "answered"
    assert retriever.queries == [QUERY]


# **도구가 실패해도 안 돈다.** 표시가 안 남으면 Router 가 같은 검색을 영영 되고른다
def test_a_failing_tool_does_not_spin():
    retriever = StubRetriever(fails=True)

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=StubRewriter(),
        generate=StubGenerate(),
    )

    assert retriever.queries == [QUERY]
    assert state.searched_query == QUERY
    assert state.stop_reason == "rewrite_unavailable"
    assert state.tool_call_log[-1].output_summary.startswith("RuntimeError")


# 검색은 재작성 질의로, **판정과 생성은 원 질의로** 한다 (loop.py docstring 참고)
def test_only_search_uses_the_rewritten_query():
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)
    evaluator = StubEvaluator("insufficient")
    generate = StubGenerate()

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=evaluator,
        rewriter=rewriter,
        generate=generate,
        max_retries=2,
    )

    assert state.original_query == QUERY
    assert state.current_query != QUERY
    assert retriever.queries[1:] != []
    assert set(evaluator.queries) == {QUERY}
    assert generate.queries == [QUERY]


# 판정자가 없어도 돈다 — 근거가 있으면 한 바퀴다
def test_runs_without_an_evaluator():
    retriever = StubRetriever()

    state = run_agent_loop(QUERY, retriever=retriever, generate=StubGenerate())

    assert state.stop_reason == "answered"
    assert state.retry_count == 0
    assert retriever.queries == [QUERY]


# 근거가 0건이고 다시 쓸 수도 없으면 **생성이 그대로 돈다**. 기본 생성기는 모델을 안 부른다
def test_no_evidence_still_answers():
    state = run_agent_loop(QUERY, retriever=StubRetriever(chunks=[]))

    assert state.final_answer == NO_EVIDENCE_ANSWER
    assert state.stop_reason == "rewrite_unavailable"


# 대화에는 사용자 줄과 답변 줄이 하나씩 남는다 — 다음 턴의 입력이 이 기록이다
def test_history_gets_the_answer():
    state = run_agent_loop(QUERY, retriever=StubRetriever(), generate=StubGenerate())

    assert [m.role for m in state.conversation_history] == ["user", "assistant"]


# --- 안 멈추는 것을 무엇이 막나 -------------------------------------------


# 상한은 여유가 아니라 **정확한 수식**이다 (loop.py docstring 참고)
@pytest.mark.parametrize(("max_retries", "expected"), [(0, 3), (1, 5), (3, 9)])
def test_step_budget_is_exact(max_retries: int, expected: int):
    assert step_budget(max_retries) == expected


# 판단과 실행이 어긋나면 조용히 도는 대신 터진다
def test_a_router_that_never_finishes_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop_module, "decide_next_action", lambda state: "rewrite")
    rewriter = StubRewriter()

    with pytest.raises(RuntimeError, match="바퀴 안에 안 끝났습니다"):
        run_agent_loop(
            QUERY,
            retriever=StubRetriever(rewriter=rewriter),
            rewriter=rewriter,
            generate=StubGenerate(),
        )


# 다룰 수 없는 행동은 조용히 안 넘어간다. Router 가 지금 `web_search` 를 안 고르지만,
# 고르게 되는 날 아무 일도 안 하는 바퀴가 예산만 먹는 것을 막는다
def test_unknown_action_raises():
    with pytest.raises(RuntimeError, match="다룰 수 없는 행동"):
        loop_module.step_for("web_search")


# --- 근거가 안 바뀐 재시도를 센다 (접지는 않는다) --------------------------


# 재작성이 질의만 바꾸고 검색이 같은 것을 물어오면 그 회차가 세어진다.
# **그런데도 루프는 안 접는다** — 접는 것은 증명이 아니라 추정이다 (loop.py docstring 참고)
def test_unchanged_evidence_is_counted_not_folded():
    rewriter = StubRewriter()
    retriever = StubRetriever(rewriter=rewriter)

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert state.evidence_unchanged_at == [1, 2, 3]
    # 접었으면 여기가 달라진다 — 재시도는 끝까지 돌고 사유도 안 바뀐다
    assert state.retry_count == 3
    assert state.stop_reason == "retries_exhausted"
    assert len(retriever.queries) == 4


# 근거가 실제로 달라진 회차는 안 세어진다
def test_changed_evidence_is_not_counted():
    rewriter = StubRewriter()
    retriever = StubRetriever(
        chunks_per_call=[[chunk("c1")], [chunk("c2")], [chunk("c3")], [chunk("c4")]],
        rewriter=rewriter,
    )

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert state.evidence_unchanged_at == []
    assert state.retry_count == 3


# **도구가 실패한 회차는 안 센다.** 실패하면 `record_tool_call()` 이 결과를 아예 안 건드려
# 앞뒤가 자동으로 같아진다 — 세면 한 칸이 *같은 것을 물어왔다* 와 *검색이 죽었다* 두 가지를
# 뜻하게 된다 (loop.py docstring 참고). `mark_searched` 가 실패해도 표시하는 것의 거울상이다
def test_a_failed_search_is_not_counted():
    rewriter = StubRewriter()
    # 1차 검색이 죽어 `note_retrieval` 이 안 돌므로 승급할 질의를 손으로 놓는다.
    # 그래야 **실패한 채로 재시도가 한 번 돈다**
    rewriter.last_rewritten = "다시 쓴 질의"
    retriever = StubRetriever(fails=True)

    state = run_agent_loop(
        QUERY,
        retriever=retriever,
        evaluator=StubEvaluator("insufficient"),
        rewriter=rewriter,
        generate=StubGenerate(),
    )

    assert retriever.queries == [QUERY, "다시 쓴 질의"]
    assert state.retry_count == 1
    assert state.evidence_unchanged_at == []


# 첫 검색은 안 센다 — 견줄 앞 회차가 없다
def test_the_first_search_is_never_counted():
    state = run_agent_loop(
        QUERY,
        retriever=StubRetriever(),
        evaluator=StubEvaluator("sufficient"),
        generate=StubGenerate(),
    )

    assert state.retry_count == 0
    assert state.evidence_unchanged_at == []
