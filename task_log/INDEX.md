# 작업 기록 색인

설계 결정 · 평가 지표 · 실패 분석을 작업 단위로 남긴다. 최신이 위.

기록을 남기는 방법은 `task-log` 스킬에 있다. `rag_pipeline/` 이나 `backend/` 를 고친 뒤
기록 없이 세션을 끝내려 하면 Stop 훅(`.claude/hooks/task_log_check.py`)이 한 번 막는다.

- [2026-08-17-하네스-확장-자동테스트훅.txt](2026-08-17-하네스-확장-자동테스트훅.txt) — subagent 3종(ingestion 은 두껍게, retrieval·evaluation 은 얇게)·skill 2종·자동 테스트 훅 신설. 편집 시 영역별(cleaner 6.7초) / 종료 시 전체(18.3초)로 가름. **`_hookutil.read_payload()` 가 BOM 입력에 조용히 죽어 훅 셋이 통째로 무력화되던 것을 발견해 utf-8-sig 로 고침.** 매핑 누락은 산문이 아니라 테스트로 강제. 훅 타임아웃 예산(settings.json 바깥 > 훅 내부)도 테스트로 박음 — 합이 아니라 이벤트별 최댓값으로 재야 pre_commit_check 오탐이 안 난다. pytest 234→268
- [2026-08-16-cleaner-공백정규화-반복머리말.txt](2026-08-16-cleaner-공백정규화-반복머리말.txt) — cleaner 2차. 반복 머리말 판정을 '등장 횟수 비율'에서 '걸친 폭'으로 갈아엎음(HWPX 97쪽/PDF 28쪽 분모 오류에 강함). AI 가이드 두 포맷 모두 25개 제거, pytest 210→234
- [2026-08-16-유료테스트-차단훅.txt](2026-08-16-유료테스트-차단훅.txt) — `-m integration` 실행을 PreToolUse 훅으로 차단. 부정(`not integration`)과 `git commit -m` 오탐을 갈라내 부정(`not integration`)과 `git commit -m` 오탐을 갈라냄. PreToolUse matcher 를 Bash|PowerShell 로 넓혀 커밋 전 검사의 우회 구멍도 막음. tests/hooks 46개 신설(훅 배선·-uall·-z 회귀). pytest 164→210
- [routing-diff-시행령개정안.txt](routing-diff-시행령개정안.txt) — HWPX 라우팅 실파일 검증. 쪽 넘긴 표의 쪽 계산 버그와 레이아웃 표 강등을 고침. page_count 24→26, 표 26→9
- [phase1.txt](phase1.txt) — 1단계(loader) 완료 보고. llama-parse 이중 deprecated 교체, OCR PSM 6, 전처리 임계값 600px, 한글 음절 분해 대응. ruff 통과 / pytest 106 passed
