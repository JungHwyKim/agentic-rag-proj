"""면접 노트에서 **질문을 뽑는** 도구.

`openwork/`(안 끝난 일) · `evaluation/`(품질) · `cost/`(비용)와 나란한 자리다 — 저장소를
**읽는 도구**라 `rag_pipeline/` 아래가 아니다. 여기가 조용히 깨지면 퀴즈가 실제보다 **적은**
문항을 내는데 **아무 에러도 안 난다**. 그래서 `auto_test_check.py` 의 `_MAPPINGS` 에 매핑이
걸려 있다.

**재료가 저장소 밖에 있다는 것이 다른 도구와 갈리는 점이다** —
`C:\\Users\\<사람>\\Documents\\면접준비\\` 라 커밋도 gitignore 도 안 된다. 그래서 경로를
코드에 안 박고(`QUIZ_NOTE_DIR`), 테스트도 실노트를 안 쓴다.

설계 근거는 `.claude/wiki/plan_log/면접-퀴즈.md` 1회차.
"""

from __future__ import annotations
