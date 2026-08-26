"""생성 단계. 검색된 근거로 답변 문장을 만든다.

지금 있는 것은 **단순 생성 한 번뿐이다.** Evaluator(근거가 충분한가) · Rewriter(질의를
고쳐 다시 검색) 는 `plan_log/검색-생성.md` 가 Phase 4~5 로 예약해 두었다.

**그 둘의 실패 계약은 더 이상 미결이 아니다**(2026-08-26). *"충분/불충분 둘 다 조용히
나쁘다"* 의 답은 **제3의 상태**다 — 실패를 어느 판정으로도 안 떨어뜨리고 따로 내며, 폴백은
호출부가 정하고 경고는 별도 채널로 나간다. 리랭커의 *"전부 아니면 전무"* 폴백과 같은
모양이라 새 잣대가 아니다. 실패를 *불충분*으로 떨어뜨리면 **부정 예시 질의에서 죽은
Evaluator 가 만점**을 받는데, 그것이 이 답을 고르게 한 자리다.

그래서 지금 안 만드는 이유는 계약이 아니라 **승인 단위**다 — Phase 4 가 다음이고
`plan_log/검색-생성.md` 의 「다음 승인 단위」가 그 자리다.

## 이 단계의 규약이 어디 있나

- 왜 빈 문자열이 아니라 예외인가 / 검색 0건이 실패가 아닌 이유 — `answer.py` docstring
- 왜 임계값이 아니라 프롬프트로 근거를 묶나 / 쪽 번호를 프롬프트에 안 넣는 이유
  — `prompts.py` docstring
- 모킹 이음새(`_make_client`) — `answer.py` docstring
- `GenerationError` 가 왜 예외 계열인가 — `rag_pipeline/exceptions.py`
- 답변을 무엇으로 재나(아직 못 잰다) — `plan_log/backend.md` 미결
"""

from __future__ import annotations

from rag_pipeline.generation.answer import generate_answer
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER

__all__ = ["NO_EVIDENCE_ANSWER", "generate_answer"]
