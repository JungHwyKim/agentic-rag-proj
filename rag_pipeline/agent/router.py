"""상태를 보고 **다음에 무엇을 할지만** 정한다.

## 왜 판단과 실행을 가르나

이 함수는 도구를 안 부른다. 합치면 *라우팅이 틀렸나 도구가 틀렸나* 를 못 가르고, 그러면
"에이전트가 이상하다"는 관찰이 어느 쪽 수정으로도 이어지지 않는다. Evaluator 가 판정만 하고
생성을 안 건드리는 것과 같은 이유다(`query.py`).

부수 효과가 없으므로 **상태 하나를 지어 넣으면 규칙 전체를 테스트할 수 있다.** 이 단계에
LLM 이 없는 것도 같은 값어치다 — 규칙이 프롬프트에 숨지 않는다.

## 규칙의 순서가 곧 규칙이다

위에서부터 먼저 걸리는 것이 이긴다.

1. `final_answer` 가 있으면 `end` — 끝난 것을 다시 돌리지 않는다
2. `final_context` 가 있으면 `generate` — 근거가 조립됐으면 다음은 생성뿐이다
3. 아직 아무 도구도 안 불렀으면 `retrieve` — 첫 검색이다
4. *불충분* 이고 재시도가 남았으면 `rewrite`
5. 근거가 0건이고 재시도가 남았으면 `rewrite`
6. 그 밖에는 `generate`

## 0건에서 `retrieve` 를 다시 고르지 않는 이유

**같은 질의로 다시 검색하면 같은 결과다.** 검색은 결정적이라 0건에서 `retrieve` 를 되고르면
루프가 상태를 하나도 안 바꾸며 `max_retries` 까지 도는데, 그 동안 무엇도 달라지지 않는다.
값을 하는 것은 **질의를 바꾸는 쪽**이다. 그래서 두 번째부터의 재검색은 전부 `rewrite` 다.

## `failed` 를 재시도로 안 삼킨다

판정이 *실패* 인 것은 **근거가 부족하다는 뜻이 아니라 판정을 못 했다는 뜻**이다. 이것을
*불충분* 처럼 다뤄 재작성을 돌리면, Evaluator 가 죽어 있는 동안 질의마다 LLM 호출이 최대
`max_retries` 번 더 붙는다 — **아무도 못 읽는 판정 때문에 돈이 나가는 자리**다.
`schemas/answer.py` 가 verdict 를 세 상태로 둔 이유가 여기서 그대로 쓰인다.

## `web_search` 를 지금은 안 고른다

**고를 근거가 없다.** *이 질문이 코퍼스 밖인가* 를 판정할 신호를 아직 안 가졌고(부정 예시
세트는 사람이 라벨을 매긴 것이지 실행 시점 신호가 아니다), 도구 자체도 미구성이다.
근거 없는 규칙을 넣는 것보다 안 넣는 것이 낫다 — 다시 볼 조건은 **웹 검색 도구가 실제로
구현될 때**이고, 그때 필요한 것은 규칙이 아니라 **신호**다.
"""

from __future__ import annotations

from rag_pipeline.schemas.agent import AgentState, NextAction

__all__ = ["decide_next_action"]


# 상태를 보고 다음 행동 하나를 고른다. **아무것도 실행하지 않는다**
def decide_next_action(state: AgentState) -> NextAction:
    if state.final_answer is not None:
        return "end"
    if state.final_context is not None:
        return "generate"
    # 도구를 한 번도 안 불렀으면 첫 검색이다. 청크 유무로 판정하면 *검색했는데 0건* 과
    # *아직 안 했다* 가 안 갈려, 0건에서 같은 검색을 되풀이한다
    if not state.tool_call_log:
        return "retrieve"

    if not _has_retries_left(state):
        return "generate"

    verdict = state.evaluator_verdict
    # 실패는 재시도로 안 삼킨다 (모듈 docstring 참고)
    if verdict is not None and verdict.verdict == "insufficient":
        return "rewrite"
    # 근거가 하나도 없으면 판정이 없어도 다시 써 볼 값이 있다
    if not state.retrieved_chunks:
        return "rewrite"
    return "generate"


# 재시도가 남았나. 세는 것은 루프이고 여기는 읽기만 한다
def _has_retries_left(state: AgentState) -> bool:
    return state.retry_count < state.max_retries
