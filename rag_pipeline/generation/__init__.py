"""생성 단계. 검색된 근거로 답변 문장을 만든다.

지금 있는 것은 **단순 생성 한 번뿐이다.** Evaluator(근거가 충분한가) · Rewriter(질의를
고쳐 다시 검색) 는 `plan_log/검색-생성.md` 가 Phase 4~5 로 예약해 두었고, 그 둘의 실패 계약
(*"충분/불충분 둘 다 조용히 나쁘다"*)이 아직 미결이라 지금 만들지 않는다.

## 이 단계의 규약이 어디 있나

- 왜 빈 문자열이 아니라 예외인가 / 검색 0건이 실패가 아닌 이유 — `answer.py` docstring
- 왜 임계값이 아니라 프롬프트로 근거를 묶나 / 쪽 번호를 프롬프트에 안 넣는 이유
  — `prompts.py` docstring
- 모킹 이음새(`_make_client`) — `answer.py` docstring
- `GenerationError` 가 왜 예외 계열인가 — `rag_pipeline/exceptions.py`
- 답변을 무엇으로 재나(아직 못 잰다) — `plan_log/frontend.md` 미결
"""

from __future__ import annotations

from rag_pipeline.generation.answer import generate_answer
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER

__all__ = ["NO_EVIDENCE_ANSWER", "generate_answer"]
