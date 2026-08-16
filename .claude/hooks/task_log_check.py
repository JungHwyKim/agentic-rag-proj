"""소스를 고친 세션이 작업 기록 없이 끝나는 것을 막는 Stop 훅.

Claude 가 응답을 마치려 할 때 settings.json 의 Stop 훅이 이 스크립트를 부른다.
`rag_pipeline/` 이나 `backend/` 의 파이썬 파일이 바뀌었는데 `task_log/` 에 아무
변경이 없으면 종료코드 2 로 끝나 대화를 이어가게 만들고, stderr 로 남긴 사유가
모델에게 전달돼 기록을 쓰게 한다.

CLAUDE.md 에 "기록을 남겨라"라고 적는 방법과 계층이 다르다. CLAUDE.md 는 문서
표현으로 "context, not enforced configuration" 이라 지켜질 때도 있고 아닐 때도
있다. 잃으면 안 되는 건 성공한 코드가 아니라 **기각된 대안과 되돌린 시도**다.
그건 저장소에 안 남고 세션과 함께 사라지므로, 지시가 아니라 훅으로 못 박는다.

판정에 별도 상태 파일을 쓰지 않고 `git status` 만 본다. 이미 커밋했으면 clean 이라
안 막고, 코드를 읽기만 한 세션도 안 막는다. "아직 기록하지 않은 변경"이 세션 경계를
넘어 그대로 남는다는 점도 상태 파일보다 낫다.

무한 루프를 막는 것이 이 훅에서 제일 중요하다. 종료코드 2 는 대화를 이어가므로
다음 Stop 에서 이 훅이 또 돈다. 조건이 그대로면 영원히 끝나지 않는다. 공식 문서의
Stop 입력 필드에는 `stop_hook_active` 가 없다(그건 Decision Control 쪽 내부 출력
필드다). 따라서 입력값에 기대지 않고 **세션당 마커 파일 하나**로 못 박는다 —
한 세션에서 최대 한 번만 막는다.

이 훅은 **구현 후 회고**를 담당한다. 설계 논의 세션은 코드를 안 고치므로 여기 안
걸린다. 그쪽은 `plan_log_check.py` 가 본다.
"""

from __future__ import annotations

import os

import _hookutil

# 이 아래 .py 가 바뀌면 기록 대상 작업으로 본다
SOURCE_PREFIXES = ("rag_pipeline/", "backend/")

# 기록이 남는 곳
LOG_PREFIX = "task_log/"

# 세션당 한 번만 막기 위한 마커 이름
MARKER_NS = "claude_task_log_seen"

# 사유에 파일을 너무 많이 나열하면 읽히지 않는다
MAX_LISTED_FILES = 20


# 차단 사유 문구
def _reason(source_changes: list[str]) -> str:
    listed = source_changes[:MAX_LISTED_FILES]
    lines = [f"  {p}" for p in listed]
    if len(source_changes) > len(listed):
        lines.append(f"  ... 외 {len(source_changes) - len(listed)}개")

    return (
        "소스를 고쳤는데 task_log/ 에 기록이 없습니다.\n\n"
        "변경된 파일:\n" + "\n".join(lines) + "\n\n"
        "task-log 스킬로 task_log/<날짜>-<주제>.txt 를 남기고 task_log/INDEX.md 에 "
        "한 줄 추가한 뒤 마치세요. 설계 결정에서 **버린 대안과 버린 이유**, 그리고 "
        "잘못 짚었다가 되돌린 시도가 제일 중요합니다.\n"
        "기록할 만한 작업이 아니면 SKIP_TASKLOG=1 로 건너뛸 수 있습니다.\n"
        "(이 검사는 세션당 한 번만 막습니다)"
    )


# 훅 진입점
def main() -> None:
    payload = _hookutil.read_payload()
    if payload is None:
        return

    # 탈출구: 사용자가 의도적으로 기록을 건너뛰고 싶을 때
    if os.environ.get("SKIP_TASKLOG"):
        return

    # 공식 문서의 Stop 입력 필드 목록에는 없지만, 들어온다면 존중한다.
    # 마커가 주 안전장치이고 이건 덤이다
    if payload.get("stop_hook_active"):
        return

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # 세션을 못 가리면 "한 번만 막기"를 보장할 수 없다 = 루프 위험
        return

    paths = _hookutil.changed_paths()
    if paths is None:
        return

    if any(p.startswith(LOG_PREFIX) for p in paths):
        return

    source_changes = sorted(p for p in paths if p.startswith(SOURCE_PREFIXES) and p.endswith(".py"))
    if not source_changes:
        return

    if not _hookutil.claim_session(MARKER_NS, session_id):
        return

    _hookutil.block(_reason(source_changes))


if __name__ == "__main__":
    main()
