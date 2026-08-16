# 작업 기록 색인

설계 결정 · 평가 지표 · 실패 분석을 작업 단위로 남긴다. 최신이 위.

기록을 남기는 방법은 `task-log` 스킬에 있다. `rag_pipeline/` 이나 `backend/` 를 고친 뒤
기록 없이 세션을 끝내려 하면 Stop 훅(`.claude/hooks/task_log_check.py`)이 한 번 막는다.

- [2026-08-16-유료테스트-차단훅.txt](2026-08-16-유료테스트-차단훅.txt) — `-m integration` 실행을 PreToolUse 훅으로 차단. 부정(`not integration`)과 `git commit -m` 오탐을 갈라내 부정(`not integration`)과 `git commit -m` 오탐을 갈라냄. PreToolUse matcher 를 Bash|PowerShell 로 넓혀 커밋 전 검사의 우회 구멍도 막음. tests/hooks 46개 신설(훅 배선·-uall·-z 회귀). pytest 164→210
- [routing-diff-시행령개정안.txt](routing-diff-시행령개정안.txt) — HWPX 라우팅 실파일 검증. 쪽 넘긴 표의 쪽 계산 버그와 레이아웃 표 강등을 고침. page_count 24→26, 표 26→9
- [phase1.txt](phase1.txt) — 1단계(loader) 완료 보고. llama-parse 이중 deprecated 교체, OCR PSM 6, 전처리 임계값 600px, 한글 음절 분해 대응. ruff 통과 / pytest 106 passed
