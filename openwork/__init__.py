"""기록 폴더에서 **아직 안 끝난 것**을 모으는 도구.

`evaluation/`(품질) · `regression/`(회귀) · `cost/`(비용)와 나란한 자리다 — 저장소를 **재는 도구**라
`rag_pipeline/` 아래가 아니다. 여기가 조용히 깨지면 "안 끝난 일" 목록이 실제보다 **짧게** 나오는데
**아무 에러도 안 난다**. 그래서 `auto_test_check.py` 의 `_MAPPINGS` 에 매핑이 걸려 있다.

설계 근거는 `.claude/wiki/plan_log/미완-작업.md` 1회차.
"""

from __future__ import annotations
