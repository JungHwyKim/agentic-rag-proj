"""생성 단계. 검색된 근거로 답변 문장을 만든다.

여기 있는 것은 **생성 한 번과 판정 한 번이다.** Evaluator(근거가 충분한가)는 2026-08-26 에
들어왔고 **꺼진 채 커밋된다**(`EVALUATOR_MODEL` 이 비면 `get_evaluator()` 가 `None`).
Rewriter(질의를 고쳐 다시 검색)는 여전히 없다 — `plan_log/검색-생성.md` 7회차가 물렸다.

**판정은 답변을 안 바꾼다.** *불충분* 이어도 생성은 그대로 돌고 판정은 `Answer.verdict` 에
실리기만 한다. 재시도할 곳이 없기도 하고, 생성을 막으면 *판정이 맞았나* 와 *답이 좋아졌나* 가
한 실행에 섞이는데 **답변 품질을 재는 자[尺]가 아직 없다.**

**실패 계약은 제3의 상태다**(2026-08-26). *"충분/불충분 둘 다 조용히 나쁘다"* 의 답이
그것이다 — 실패를 어느 판정으로도 안 떨어뜨리고 따로 내며, 폴백은 호출부가 정하고 경고는
별도 채널로 나간다. 리랭커의 *"전부 아니면 전무"* 폴백과 같은 모양이라 새 잣대가 아니다.
실패를 *불충분*으로 떨어뜨리면 **부정 예시 질의에서 죽은 Evaluator 가 만점**을 받는데,
그것이 이 답을 고르게 한 자리다.

## 이 단계의 규약이 어디 있나

- 왜 빈 문자열이 아니라 예외인가 / 검색 0건이 실패가 아닌 이유 — `answer.py` docstring
- 왜 임계값이 아니라 프롬프트로 근거를 묶나 / 쪽 번호를 프롬프트에 안 넣는 이유
  — `prompts.py` docstring
- 모킹 이음새(`_make_client`) — `answer.py` · `evaluate.py` docstring
- `GenerationError` 가 왜 예외 계열인가 — `rag_pipeline/exceptions.py`
- 답변을 무엇으로 재나(아직 못 잰다) — `plan_log/backend.md` 미결
- 판정이 왜 `retrieve()` 안이 아닌가 / 실패가 왜 제3의 상태인가 / 왜 Protocol 이 없나
  — `evaluate.py` docstring
- 판정 프롬프트가 왜 답을 안 쓰게 하나 / 하위 규칙을 왜 안 넣나 — `prompts.py` docstring
- 판정을 무엇으로 재나 — `evaluation/verdicts.py`(혼동행렬 + 실패 열)
"""

from __future__ import annotations

from rag_pipeline.generation.answer import generate_answer
from rag_pipeline.generation.evaluate import (
    EvaluatorUsage,
    OpenAIEvaluator,
    get_evaluator,
)
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER

__all__ = [
    "NO_EVIDENCE_ANSWER",
    "EvaluatorUsage",
    "OpenAIEvaluator",
    "generate_answer",
    "get_evaluator",
]
