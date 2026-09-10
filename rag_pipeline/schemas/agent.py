"""에이전트 트랙의 상태·도구 스키마.

`retrieval.py`(검색 한 건) · `answer.py`(질의 하나의 최종 출력)와 **나란히 두되 겹치지
않는다.** 저 둘은 한 번 도는 파이프라인의 입출력이고, 이쪽은 **여러 번 도는 동안 쌓이는
것**이다 — 몇 번 재시도했나, 무슨 도구를 불렀나, 지금 무엇을 할 차례인가.

원안은 `IMPLEMENTATION_SPEC.md` 의 Pre-Lock A·B 다. **그대로 옮기지 않았고, 갈린 곳 셋은
전부 그동안 실제로 지어진 것이 초안을 이긴 자리다.**

## `EvaluatorVerdict` 를 다시 정의하지 않는다

초안은 `is_sufficient: bool` 인데 **실제 구현은 3상태**(`sufficient|insufficient|failed`,
`answer.py`)다. 초안대로 만들면 *판정을 못 했다* 가 *불충분* 으로 삼켜지는데, 그것은
`answer.py` 가 **타입으로** 막아 둔 바로 그 사고다 — 죽은 Evaluator 가 부정 예시 질의에서
만점을 받는다. 그래서 여기서는 `answer.py` 것을 **import 해서 쓴다.** 같은 이유로
`RetrievedChunk` 도 `retrieval.py` 것을 그대로 쓴다(초안에 없는 재작성 갈래 점수가 실제로는
있다).

## `next_action` 에서 `vision`·`llamaparse` 를 뺐다

같은 스펙의 **Pre-Lock C 가 그 둘을 이미 제외**했다 — 질의 시점에 건넬 바이트가 없기
때문이다(`blob` 은 splitter → records → chunk 경로에 한 번도 안 나온다). 초안 A 와 C 가
서로 어긋나 있고, 뒤엣것이 근거를 들고 있다. 합법값에 남겨두면 **Router 가 영원히 안 고르는
값**이 계약에 남아, 그 분기를 다루는 코드와 테스트가 계속 딸려 붙는다.

## `ToolOutput.data` 가 왜 `Any` 인가

도구마다 내놓는 것이 다르다 — `RetrieverTool` 은 `list[RetrievedChunk]`, 검색 도구는 문서
목록이다. 여기서 좁히면 도구를 더할 때마다 이 스키마를 고치게 되고, 그것이 곧
`BaseTool` 을 만든 이유(호출부가 도구를 몰라도 되게 한다)를 무너뜨린다.

## `searched_query` 와 `stop_reason` 이 왜 상태에 있나

**둘 다 재시도 루프가 없으면 필요 없던 칸이다**(Checkpoint A 에서 늘었다).

`searched_query` 는 *마지막으로 **검색에 쓴** 질의* 다. Router 가 *다시 찾아야 하나* 를
`current_query` 와의 대조로 판정하는데, 그 값이 없으면 재작성으로 질의를 갈아끼운 뒤에도
**로그가 이미 차 있어** 다시 검색할 근거를 못 만든다. `ToolCallLog.input_summary` 로 대신하면
안 된다 — 그것은 120자로 잘린 요약이라 긴 질의에서 조용히 같아진다.

`stop_reason` 은 *루프가 왜 멈췄나* 다. `retry_count` 로는 유추가 안 된다 — 충분 판정으로
끝났는데 마침 3회차였을 수도 있고, *재작성이 승급할 질의를 못 냈다* 와 *재시도 소진* 이
같은 수를 낸다. `Answer.verdict`·`Answer.rewrite` 를 필드로 실은 것과 같은 기준이고,
**필드여야 LangGraph 경로에서도 같은 자리에서 읽힌다**(그래프 노드는 상태를 주고받는다).

## `evidence_unchanged_at` 은 접는 칸이 아니라 **세는 칸**이다

재시도가 질의를 바꿨는데 검색이 **글자 그대로 같은 근거**를 물어온 회차가 있다(실측 6회 중
2회). 그 회차를 접을지는 **아직 안 정했다** — 원리적으로 중복인 것은 판정 하나이고 검색까지
무의미하지는 않기 때문이다(`agent/loop.py`). 그래서 이 칸은 흐름을 안 바꾸고 **세기만** 한다.

**`warnings: list[str]` 이 아닌 이유**는 목적이 세는 것이어서다. 문장을 파싱해야 세지는 채널로
두면 그 목적이 약해지고, 일반 채널을 먼저 만들면 관계없는 것이 섞여 들어와 **그 수가 더 이상
그 수가 아니게** 된다. 소비자가 둘이 될 때 채널로 넓힌다.

## `ToolCallLog` 가 요약 문자열인 이유

청크 전문을 실으면 **한 질의의 상태가 코퍼스만큼 커진다.** 이 로그의 소비자는 *무슨 도구가
몇 번 불렸나* 를 보는 쪽이고, 내용이 필요하면 `AgentState.retrieved_chunks` 에 그대로 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from rag_pipeline.schemas.answer import EvaluatorVerdict, StopReason
from rag_pipeline.schemas.retrieval import RetrievedChunk

__all__ = [
    "AgentState",
    "Message",
    "NextAction",
    "StopReason",
    "ToolCallLog",
    "ToolInput",
    "ToolOutput",
]

# 다음에 할 일. **Pre-Lock C 가 제외한 vision·llamaparse 는 없다** (모듈 docstring 참고)
NextAction = Literal["retrieve", "web_search", "rewrite", "generate", "end"]

# 루프가 멈춘 이유. **정의는 `schemas/answer.py` 에 있고 여기서는 이름만 되판다** —
# `RetryTrace` 가 `Answer` 에 붙으면서 이 낱말이 답변 쪽 어휘도 되었는데, 이 모듈이
# `EvaluatorVerdict` 를 저기서 가져오므로 반대 방향은 순환이다. 두 벌로 적으면 한쪽만
# 고쳐져도 에러가 안 난다. **부르는 쪽은 계속 여기서 가져가면 된다**


# 도구에 넣는 것. 도구가 늘어도 이 모양은 안 바뀐다
class ToolInput(BaseModel):
    query: str
    # 도구별 부가 인자. 여기 없는 것을 도구가 요구하면 그것은 생성자로 받을 것이다
    metadata: dict[str, Any] = Field(default_factory=dict)


# 도구가 내는 것. **실패도 예외가 아니라 이 모양으로 돌아온다**
class ToolOutput(BaseModel):
    success: bool
    # 도구마다 다르다 (모듈 docstring 참고)
    data: Any = None
    # 성공이면 None. 실패 사유는 사람이 읽을 한 줄이다
    error: str | None = None


# 대화 한 줄. 지금은 쌓기만 하고 읽는 쪽이 없다 — 소비자는 Phase 11 이다
class Message(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str


# 도구 호출 하나의 흔적. **요약만 싣는다** (모듈 docstring 참고)
class ToolCallLog(BaseModel):
    tool_name: str
    input_summary: str
    output_summary: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# 한 질의가 도는 동안의 상태 전부
class AgentState(BaseModel):
    conversation_history: list[Message] = Field(default_factory=list)
    # 사용자가 실제로 물은 것. **재작성이 돌아도 이 값은 안 바뀐다**
    original_query: str
    # 지금 검색에 쓰는 질의. 재작성이 없으면 original_query 와 같다
    current_query: str

    # 마지막으로 **검색에 쓴** 질의. Router 가 이것과 current_query 를 대조해 재검색을 정한다
    searched_query: str | None = None

    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    # 판정을 안 돌렸으면 None. 3상태이므로 `failed` 와 `insufficient` 가 갈린다
    evaluator_verdict: EvaluatorVerdict | None = None
    retry_count: int = 0
    # 스펙이 정한 값이다. 설정으로 안 연 이유는 고를 축이 아직 없어서다
    max_retries: int = 3

    tool_call_log: list[ToolCallLog] = Field(default_factory=list)

    # 조립된 근거 문자열. Phase 9 가 채운다
    final_context: str | None = None
    final_answer: str | None = None

    # Router 가 정한 다음 행동. 아직 안 정했으면 None
    next_action: NextAction | None = None

    # 루프가 멈춘 이유. 아직 안 멈췄으면 None (모듈 docstring 참고)
    stop_reason: StopReason | None = None

    # 앞 회차와 근거가 글자 그대로 같았던 재시도의 `retry_count`. **흐름을 안 바꾼다**
    # (모듈 docstring 참고)
    evidence_unchanged_at: list[int] = Field(default_factory=list)
