"""소스를 고친 세션이 작업 기록 없이 끝나는 것을 막는 Stop 훅.

Claude 가 응답을 마치려 할 때 settings.json 의 Stop 훅이 이 스크립트를 부른다.
`rag_pipeline/` 이나 `backend/` 의 파이썬 파일이 바뀌었는데 `task_log/` 에 아무
변경이 없으면 종료코드 2 로 끝나 대화를 이어가게 만들고, stderr 로 남긴 사유가
모델에게 전달돼 기록을 쓰게 한다.

CLAUDE.md 에 "기록을 남겨라"라고 적는 방법과 계층이 다르다. CLAUDE.md 는 문서
표현으로 "context, not enforced configuration" 이라 지켜질 때도 있고 아닐 때도
있다. 잃으면 안 되는 건 성공한 코드가 아니라 **기각된 대안과 되돌린 시도**다.
그건 저장소에 안 남고 세션과 함께 사라지므로, 지시가 아니라 훅으로 못 박는다.

**"기록했는가"는 git 이 아니라 세션 마커로 판정한다.** 처음에는 `git status` 에
`task_log/` 경로가 하나라도 있으면 통과시켰는데, 그 판정은 **세션을 구분하지 못한다.**
지난 세션이 남긴 미커밋 기록이 작업트리에 있으면 이번 세션이 한 줄도 안 써도 통과한다.
커밋할 때까지 검사가 없는 것과 같아지고, 기록이 밀릴수록 검사가 더 헐거워진다는 점에서
방향이 정반대였다. 실제로 `plan_log/` 쪽이 미커밋 3건이 쌓인 채 무력화돼 있는 것을
2026-08-17 에 발견했다. 그래서 PostToolUse 로 `task_log/` 쓰기를 직접 잡는다 —
`plan_log_check.py` 가 Skill 호출을 잡는 것과 같은 수법이다.

무엇이 바뀌었는지는 여전히 `git status` 로 본다. 이미 커밋했으면 clean 이라 안 막고,
코드를 읽기만 한 세션도 안 막는다.

무한 루프를 막는 것이 이 훅에서 제일 중요하다. 종료코드 2 는 대화를 이어가므로
다음 Stop 에서 이 훅이 또 돈다. 조건이 그대로면 영원히 끝나지 않는다. 공식 문서의
Stop 입력 필드에는 `stop_hook_active` 가 없다(그건 Decision Control 쪽 내부 출력
필드다). 따라서 입력값에 기대지 않고 **세션당 마커 파일 하나**로 못 박는다 —
한 세션에서 최대 한 번만 막는다.

이 훅은 **구현 후 회고**를 담당한다. 순수한 설계 논의 세션은 코드를 안 고치므로 여기
안 걸린다. 그쪽은 `plan_log_check.py` 가 본다.
"""

from __future__ import annotations

import os

import _hookutil

# 이 아래 .py 가 바뀌면 기록 대상 작업으로 본다
SOURCE_PREFIXES = ("rag_pipeline/", "backend/")

# 하네스도 기록 대상이다. **`.claude/` 전부가 아니라 동작이 있는 것만** — 훅 로직과 그
# 배선이다. 이 둘에는 코드를 읽어서는 알 수 없는 판단이 유난히 많이 박힌다(`-uall` 을
# 빼면 새 디렉터리가 한 줄로 접혀 검사가 조용히 통과하고, UserPromptSubmit 에서 종료코드
# 2 는 사용자가 방금 친 프롬프트를 지운다). 그런 "버린 대안"이 task_log 4절의 몫이다.
# 게다가 훅이 조용히 무력화되면 그 뒤 모든 세션의 기록이 안 남는다.
#
# `.claude/skills/**` 와 CLAUDE.md 는 **일부러 뺐다.** 문서 갱신은 task-log 스킬 5단계가
# 이미 담당한다. 문서 한 줄 고칠 때마다 7절을 채우게 만들면 SKIP_TASKLOG=1 이 습관이
# 되고, 그러면 정작 필요할 때도 안 걸린다
HARNESS_PREFIX = ".claude/hooks/"
HARNESS_FILES = (".claude/settings.json",)

# 기록이 남는 곳
LOG_PREFIX = "task_log/"

# 세션당 한 번만 막기 위한 마커 이름
MARKER_NS = "claude_task_log_seen"

# 이번 세션에 실제로 기록을 썼다는 표시
WRITTEN_NS = "claude_task_log_written"

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
        "task-log 스킬로 task_log/<날짜>-<주제>.md 를 남기고 task_log/INDEX.md 에 "
        "한 줄 추가한 뒤 마치세요. 설계 결정에서 **버린 대안과 버린 이유**, 그리고 "
        "잘못 짚었다가 되돌린 시도가 제일 중요합니다.\n"
        "기록할 만한 작업이 아니면 SKIP_TASKLOG=1 로 건너뛸 수 있습니다.\n"
        "(이 검사는 세션당 한 번만 막습니다)"
    )


# 이 경로가 바뀌면 기록을 남길 작업으로 본다
def is_recordable(path: str) -> bool:
    if path in HARNESS_FILES:
        return True
    if not path.endswith(".py"):
        return False
    return path.startswith(SOURCE_PREFIXES) or path.startswith(HARNESS_PREFIX)


# task_log/ 에 쓴 것을 이번 세션의 기록으로 표시한다
def _handle_post_tool_use(payload: dict, session_id: str) -> None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return

    rel = _hookutil.relative_to_root(str(tool_input.get("file_path") or ""))
    if rel is None or not rel.startswith(LOG_PREFIX):
        return

    _hookutil.claim_session(WRITTEN_NS, session_id)


# 기록 대상을 고쳤는데 이번 세션에 기록을 안 썼으면 막는다
def _handle_stop(payload: dict, session_id: str) -> None:
    # 공식 문서의 Stop 입력 필드 목록에는 없지만, 들어온다면 존중한다.
    # 마커가 주 안전장치이고 이건 덤이다
    if payload.get("stop_hook_active"):
        return

    if _hookutil.is_marked(WRITTEN_NS, session_id):
        return

    paths = _hookutil.changed_paths()
    if paths is None:
        return

    source_changes = sorted(p for p in paths if is_recordable(p))
    if not source_changes:
        return

    if not _hookutil.claim_session(MARKER_NS, session_id):
        return

    _hookutil.block(_reason(source_changes))


# 훅 진입점. 한 스크립트가 두 이벤트를 처리하므로 이벤트 이름으로 가른다
def main() -> None:
    payload = _hookutil.read_payload()
    if payload is None:
        return

    # 탈출구: 사용자가 의도적으로 기록을 건너뛰고 싶을 때
    if os.environ.get("SKIP_TASKLOG"):
        return

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # 세션을 못 가리면 표시도 "한 번만 막기"도 성립하지 않는다 = 루프 위험
        return

    event = str(payload.get("hook_event_name") or "")
    if event == "PostToolUse":
        _handle_post_tool_use(payload, session_id)
    elif event == "Stop":
        _handle_stop(payload, session_id)


if __name__ == "__main__":
    main()
