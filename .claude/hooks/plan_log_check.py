"""설계 논의 세션이 기록 없이 끝나는 것을 막는 훅. 세 이벤트를 한 스크립트가 처리한다.

`task_log_check.py` 와 목적은 같지만 **트리거가 정반대다.** 저쪽은 코드 변경을 보고
판정하는데, 설계 논의 세션은 코드를 한 줄도 안 고친다. git 으로는 "이 세션이 설계
논의였다"를 알 방법이 없다. 그래서 세션에 표시를 남기는 단계가 하나 더 필요하다.

  UserPromptSubmit
    사용자 메시지가 설계 질문으로 보이면 스킬을 쓰라는 한 줄을 stdout 으로 낸다. 이
    이벤트의 stdout 은 그대로 모델 컨텍스트에 주입된다. 사슬의 첫 칸(스킬 호출)이 모델
    판단이라 놓칠 수 있는데, 놓치면 마커가 없어 Stop 검사까지 통째로 무력화되기
    때문이다. **표시는 여기서 만들지 않는다** — 오탐으로 엉뚱한 세션이 막히면 짜증나지만,
    리마인더는 오탐이어도 한 줄 무시하면 끝이라 비용이 비대칭이다.

  PostToolUse (matcher: Skill|Write|Edit)
    plan-log 스킬이 호출되면 `.git/claude_plan_log_active/<session_id>` 마커를 만든다.
    "이 세션은 설계 논의 세션"이라는 표시다. `.claude/wiki/plan_log/` 아래에 쓰면 별도로
    `claude_plan_log_written` 마커를 만든다 — "이번 세션에 실제로 기록했다"는 표시다.

  Stop
    설계 논의 표시가 있는데 기록이 없으면 종료코드 2 로 막는다. **기록 여부는 두 경로로
    본다** — 위의 마커, 그리고 세션 시작 이후 `.claude/wiki/plan_log/` 아래 파일의 mtime 이다.

Skill 호출을 PostToolUse 로 잡을 수 있다는 건 실제 트랜스크립트로 확인했다 —
`{"name":"Skill","input":{"skill":"rag-code-review"}}` 형태로 남는다. 즉 tool_name 은
"Skill", 스킬명은 tool_input.skill 이다.

**기록 여부를 `git status` 로 보지 않는 이유.** 처음에는 `.claude/wiki/plan_log/` 에 변경이 하나라도
있으면 통과시켰는데, 그 판정은 세션을 구분하지 못한다. 지난 세션이 남긴 미커밋 기록이
작업트리에 있으면 이번 세션이 한 줄도 안 써도 통과한다. 2026-08-17 에 실제로 그 상태를
발견했다 — `.claude/wiki/plan_log/` 에 미커밋 3건이 쌓여 검사가 통째로 무력화돼 있었다. 기록이 밀릴수록
검사가 더 헐거워지니 방향이 정반대다. 표시를 만드는 수법이 이미 여기 있었으므로 그대로
한 칸 더 쓴다.

표시를 모델이 직접 파일로 쓰게 하지 않고 훅으로 만드는 이유는 이 장치 전체의 전제와
같다. 모델에게 시키는 일은 지켜질 때도 있고 아닐 때도 있지만, 훅은 하네스가 돌린다.

**마커만으로는 부족하다는 것이 2026-08-19 에 드러났다.** 기록 마커는 PostToolUse 의
`tool_input.file_path` 로 만드는데 그 필드는 Write/Edit 에만 있다. 그래서 `.claude/wiki/plan_log/` 를
Bash(python heredoc)로 고친 세션이 회차·INDEX 를 다 남기고 커밋까지 하고도 막혔다.
같은 전제가 여기에도 걸린다 — "기록은 Write/Edit 로 쓰라"고 지시해서 고칠 수는 없다.
그래서 도구가 아니라 **파일이 실제로 바뀌었는가**를 함께 본다(`_hookutil.wrote_under`).
비교 기준이 세션 시작 시각이라, git 판정이 못 갈랐던 "지난 세션의 미커밋 기록"은 여전히
안 통과한다.

무한 루프 방지·탈출구·실패 시 통과 규약은 `_hookutil` 의 설명과 같다. 판단이 서지
않으면 막지 않는다.
"""

from __future__ import annotations

import os
import sys

import _hookutil

# 이 스킬이 호출된 세션을 설계 논의 세션으로 본다
SKILL_NAME = "plan-log"

# 기록이 남는 곳. 폴더 이름은 그대로이고 뿌리만 `_hookutil.WIKI_ROOT` 가 준다
LOG_PREFIX = _hookutil.WIKI_ROOT + "plan_log/"

# 설계 논의 세션 표시
ACTIVE_NS = "claude_plan_log_active"

# 이번 세션에 실제로 기록을 썼다는 표시
WRITTEN_NS = "claude_plan_log_written"

# 세션당 한 번만 막기 위한 표시
BLOCKED_NS = "claude_plan_log_blocked"

# 세션당 리마인더 한 번만 내기 위한 표시
HINTED_NS = "claude_plan_log_hinted"

# 설계 의도 어휘 — "무엇을 만들지 정하려는" 말
_DESIGN_PHRASES = (
    "설계",
    "어떻게 만들",
    "어떻게 짤",
    "어떻게 할까",
    "어떤 방식",
    "어느 쪽이",
    "선택지",
    "뭐가 나을",
    "좋을까",
    "방향",
    "정해야",
    "골라야",
)

# 코드 이해 어휘 — "이미 있는 게 어떻게 도는지" 묻는 말. 위와 겹치면 이쪽이 이긴다.
# "이거 어떻게 동작해?" 를 설계 질문으로 오인하면 매번 헛리마인더가 뜬다
_CODE_PHRASES = (
    "어떻게 동작",
    "어떻게 되어",
    "어디에 있",
    "왜 이렇게 짜",
    "어떻게 구현돼",
    "어떻게 구현되",
)

HINT = (
    "[plan-log] 설계를 정하는 질문으로 보입니다. 맞으면 plan-log 스킬로 논의를 진행하고, "
    "일단락되면 .claude/wiki/plan_log/<기능>.md 에 회차를 남기세요. "
    "설계 질문이 아니면 이 줄을 무시하세요."
)

REASON = (
    "설계 논의를 했는데 .claude/wiki/plan_log/ 에 기록이 없습니다.\n\n"
    "plan-log 스킬의 형식대로 .claude/wiki/plan_log/<기능>.md 에 이번 회차를 덧붙이고 "
    "맨 위 '확정된 것'과 '미결'을 갱신한 뒤 마치세요. .claude/wiki/plan_log/INDEX.md 도 함께 손봅니다.\n"
    "제일 중요한 건 **기각한 안과 기각 이유**, 그리고 **아직 못 정한 것과 무엇을 재야 "
    "정해지는지**입니다. 그게 다음 논의의 시작점이 됩니다.\n"
    "남길 논의가 아니었으면 SKIP_PLANLOG=1 로 건너뛸 수 있습니다.\n"
    "(이 검사는 세션당 한 번만 막습니다)"
)


# tool_input.skill 이 plan-log 인가. "플러그인:이름", "경로:이름" 형태도 받는다
def _is_plan_log_skill(payload: dict) -> bool:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    name = str(tool_input.get("skill") or "").strip()
    # 스킬은 "plan-log" 외에 "plugin:plan-log" / "apps/web:plan-log" 로도 지정될 수 있다
    return name.rsplit(":", 1)[-1] == SKILL_NAME


# 이번 도구 호출이 .claude/wiki/plan_log/ 아래에 쓴 것인가
def _wrote_plan_log(payload: dict) -> bool:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    rel = _hookutil.relative_to_root(str(tool_input.get("file_path") or ""))
    return rel is not None and rel.startswith(LOG_PREFIX)


# 설계 질문으로 보이면 스킬을 쓰라는 한 줄을 주입한다. 절대 막지 않는다
def _handle_user_prompt_submit(payload: dict, session_id: str) -> None:
    # 이 분기에서 종료코드 2 는 다른 이벤트와 뜻이 완전히 다르다. Stop 에서는 "멈추지 말고
    # 이어가라"였지만 UserPromptSubmit 에서는 **사용자가 방금 친 프롬프트를 지운다.**
    # 그래서 여기서는 _hookutil.block() 을 절대 부르지 않고(그 함수가 sys.exit(2) 다),
    # 어떤 예외도 밖으로 내보내지 않는다. 리마인더 한 줄을 못 내는 것과 사용자가 쓴 글이
    # 사라지는 것은 비교가 안 된다
    try:
        # 필드명을 하나로 못 박지 않는다 — 실제로 오는 것은 `prompt` 인데 `user_input`
        # 하나만 읽다 게이트가 영영 안 열렸다. 근거는 _hookutil.prompt_text 에 있다
        text = _hookutil.prompt_text(payload)
        if not text:
            return

        if not any(p in text for p in _DESIGN_PHRASES):
            return

        if any(p in text for p in _CODE_PHRASES):
            return

        # 이미 스킬을 쓴 세션이면 잔소리할 이유가 없다
        if _hookutil.is_marked(ACTIVE_NS, session_id):
            return

        # 세션당 한 번만. 매 메시지에 같은 줄이 붙으면 그게 소음이다
        if not _hookutil.claim_session(HINTED_NS, session_id):
            return

        # 이 이벤트의 stdout 은 모델 컨텍스트로 들어간다. 한국어라 UTF-8 로 못 박는다
        sys.stdout.reconfigure(encoding="utf-8")
        print(HINT)
    except Exception:  # noqa: BLE001 - 위 주석 참조. 프롬프트를 지키는 것이 최우선이다
        return


# 두 가지를 표시한다 — 설계 논의 세션인가, 이번 세션에 기록을 썼는가.
# 한 이벤트에서 둘 다 볼 수 있으므로 tool_name 으로 가르지 않고 양쪽을 그냥 확인한다
def _handle_post_tool_use(payload: dict, session_id: str) -> None:
    if _is_plan_log_skill(payload):
        _hookutil.claim_session(ACTIVE_NS, session_id)

    if _wrote_plan_log(payload):
        _hookutil.claim_session(WRITTEN_NS, session_id)


# 이번 세션에 기록을 남겼나. 마커가 주 경로이고 mtime 이 도구를 안 가리는 보조 경로다.
#
# 마커만 보면 Write/Edit 로 쓴 기록만 세고 **Bash 로 쓴 기록은 통째로 안 보인다.**
# 그러면 기록을 다 남긴 세션이 막히는데, 그 오탐의 값은 단순한 짜증이 아니다 —
# 일을 하고도 막히는 검사는 SKIP_PLANLOG=1 을 습관으로 만들어 검사 자체를 죽인다
def _recorded_this_session(session_id: str, *, trust_mtime: bool) -> bool:
    if _hookutil.is_marked(WRITTEN_NS, session_id):
        return True

    if not trust_mtime:
        return False

    # 판단이 안 서면(None) 마커만 보던 종전 동작으로 돌아간다. 어차피 세션당 한 번만
    # 막으므로 사용자가 갇히지는 않는다
    return _hookutil.wrote_under(LOG_PREFIX, session_id) is True


# 표시가 있는데 이번 세션에 기록을 안 썼으면 막는다
def _handle_stop(session_id: str, *, trust_mtime: bool) -> None:
    if not _hookutil.is_marked(ACTIVE_NS, session_id):
        # 설계 논의 세션이 아니다
        return

    if _recorded_this_session(session_id, trust_mtime=trust_mtime):
        return

    if not _hookutil.claim_session(BLOCKED_NS, session_id):
        return

    _hookutil.block(REASON)


# 훅 진입점
def main() -> None:
    payload = _hookutil.read_payload()
    if payload is None:
        return

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # 세션을 못 가리면 표시도 "한 번만 막기"도 성립하지 않는다
        return

    # 세션 기준 시각을 남긴다. 이 훅은 UserPromptSubmit 에 matcher 없이 걸려 있어
    # 세션에서 제일 먼저 도는 훅이고, 그래서 이 값이 곧 세션 시작 시각이 된다.
    # **SKIP_PLANLOG 보다 먼저 찍는 이유**: 같은 기준을 task_log_check 도 쓰므로
    # 한쪽 검사를 껐다고 다른 쪽 기준까지 사라지면 안 된다
    baseline_is_fresh = _hookutil.mark_session_start(session_id)

    # 탈출구: 사용자가 의도적으로 기록을 건너뛰고 싶을 때
    if os.environ.get("SKIP_PLANLOG"):
        return

    if payload.get("stop_hook_active"):
        return

    event = str(payload.get("hook_event_name") or "")
    if event == "UserPromptSubmit":
        _handle_user_prompt_submit(payload, session_id)
    elif event == "PostToolUse":
        _handle_post_tool_use(payload, session_id)
    elif event == "Stop":
        _handle_stop(session_id, trust_mtime=not baseline_is_fresh)


if __name__ == "__main__":
    main()
