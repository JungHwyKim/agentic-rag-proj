"""plan mode 에서 **사용자가 허락하기 전까지** 계획 확정을 막는 훅.

plan mode 의 기본 흐름은 모델이 혼자 탐색하고 혼자 결론을 내서 `ExitPlanMode` 로 계획을
들이미는 것이다. 이 저장소가 원하는 건 반대다 — 설계는 사용자와 주고받으며 만들어지고,
사용자가 "설계서 작성해줘" 라고 말하기 전까지는 질문-답변이 반복돼야 한다.

**왜 스킬만으로는 안 되나.** `plan_log_check.py` 의 docstring 이 이미 답한 것과 같다.
모델에게 시키는 일은 지켜질 때도 있고 아닐 때도 있지만 훅은 하네스가 돌린다. 실제로
2026-08-17 에 스킬을 안 불러 기록 검사가 통째로 무력화된 세션이 있었다. 그래서 여기서는
**스킬 호출 여부와 무관하게** 게이트가 산다 — `ExitPlanMode` 가 불렸다는 사실 자체가
plan mode 라는 증거이므로, `plan_log_check` 처럼 "이 세션이 그 세션인가"를 따로 표시할
필요가 없다.

  UserPromptSubmit
    사용자 메시지가 게이트 문구면 READY 마커를 만든다. plan mode 인 것이 payload 로
    확인되면 스킬을 쓰라는 한 줄을 stdout 으로 낸다(세션당 1회).

  PreToolUse (matcher: ExitPlanMode)
    READY 마커가 없으면 `permissionDecision: "deny"` 로 막고, 라운드를 더 돌라는 사유를
    모델에게 넘긴다.

  PostToolUse (matcher: ExitPlanMode)
    READY 마커를 지운다. 게이트가 다시 닫혀, 같은 세션에서 plan mode 를 또 쓰면 처음부터
    면담을 한다.

**소비를 PostToolUse 에서 하는 이유.** PreToolUse 에서 지우면 사용자가 계획을 거절하고
수정을 요청했을 때 마커가 이미 없어 다음 `ExitPlanMode` 가 또 막힌다. 일을 하고도 막히는
게이트는 `SKIP_*=1` 을 습관으로 만들어 검사 자체를 죽인다 —
`tests/hooks/test_record_hooks.py` 가 같은 판단으로 존재한다. 거절된 도구는 PostToolUse 가
안 돌므로 마커가 살아남아 재시도가 된다. PostToolUse 가 아예 안 오는 환경이면 그 세션의
게이트만 열린 채 남는데, 그건 사용자를 가두는 쪽이 아니라 통과시키는 쪽이라 이 저장소
훅들의 공통 규약("판단이 서지 않으면 막지 않는다")과 같은 방향이다.

**`permission_mode` 는 훅 입력 공통부에 실제로 들어온다** — 확인 결과 `session_id` ·
`transcript_path` · `cwd` 와 같은 객체에 담기고, plan mode 의 값은 문자열 `"plan"` 이다.
그래도 없을 때를 대비해 두는 이유는 이 필드가 **리마인더 한 줄에만 쓰이기** 때문이다.
못 읽으면 조용히 아무것도 안 낸다 — plan mode 가 아닌 세션마다 헛소리를 뿌리는 쪽이 더
나쁘고, 실제 강제는 아래 deny 사유가 하며 그 사유가 스킬 안내를 겸한다.

탈출구는 `SKIP_PLANINTERVIEW=1` 이다.
"""

from __future__ import annotations

import json
import os
import re
import sys

import _hookutil

# 게이트가 열렸다는 표시. **켰다 껐다 한다**는 점이 다른 마커들과 다르다 —
# 그래서 claim_session(한 번 켜면 끝)이 아니라 touch/clear 짝으로 쓴다
READY_NS = "claude_plan_interview_ready"

# 세션당 리마인더 한 번만 내기 위한 표시
HINTED_NS = "claude_plan_interview_hinted"

# 사용자가 이 말을 해야 게이트가 열린다. 사유 문구에 그대로 실어 사용자가 갇히지 않게 한다
OPEN_PHRASE = "설계서 작성해줘"

# 게이트를 여는 말. "설계서 작성해줘" 하나만 정확히 받으면 조사 하나 틀렸을 때 사용자가
# 갇히므로 몇 가지 변형을 인정한다
_OPEN_RE = re.compile(r"설계(?:서|\s*문서)\s*(?:를)?\s*(?:작성|써|쓰|만들|정리)")

# 위와 겹치면 이쪽이 이긴다. "아직 설계서 작성하지 마" 를 열림으로 읽으면 안 된다
_NEGATIONS = ("하지 마", "하지마", "하지말", "말고", "아직", "나중에")

# 부정을 찾을 창의 크기. 문구 앞뒤 이만큼만 본다
_WINDOW_BEFORE = 8
_WINDOW_AFTER = 12

HINT = (
    "[plan-interview] plan mode 입니다. 혼자 결론 내지 말고 plan-interview 스킬로 "
    f'질문-답변 라운드를 도세요. 계획 확정(ExitPlanMode)은 사용자가 "{OPEN_PHRASE}" '
    "라고 말한 뒤에만 열립니다."
)

DENY_REASON = (
    "아직 계획을 확정할 때가 아닙니다.\n\n"
    "이 저장소는 plan mode 에서 **사용자가 문구로 허락하기 전까지** 질문-답변을 반복하기로 "
    "돼 있습니다. plan-interview 스킬의 형식대로 라운드를 더 도세요.\n"
    "  · AskUserQuestion 으로, 이번 라운드에서 결정이 갈리는 것만 1~3개\n"
    "  · 선택지마다 이 프로젝트에서의 근거와 추천을 단다\n"
    "  · 답을 받으면 '지금까지 확정된 것 / 아직 미결' 을 짧게 되짚는다\n"
    "더 물을 것이 없으면 질문을 지어내지 말고, 남은 미결을 보여준 뒤 사용자를 기다리세요.\n\n"
    f'게이트는 사용자가 "{OPEN_PHRASE}" 라고 말해야 열립니다. **모델이 대신 말할 수 '
    "없습니다.**\n"
    "(면담을 건너뛰려면 사용자가 SKIP_PLANINTERVIEW=1 을 설정합니다)"
)


# 이 메시지가 게이트를 여는가
def _opens_gate(text: str) -> bool:
    match = _OPEN_RE.search(text)
    if match is None:
        return False

    # 부정은 문구 **바로 옆**에서만 본다. 메시지 전체에서 찾으면 "아직 못 정한 게 있지만
    # 설계서 작성해줘" 같은 정상 요청까지 닫아버린다
    window = text[max(0, match.start() - _WINDOW_BEFORE) : match.end() + _WINDOW_AFTER]
    return not any(neg in window for neg in _NEGATIONS)


# payload 로 plan mode 임을 확인할 수 있나. 필드가 없으면 False (리마인더를 안 낸다)
def _in_plan_mode(payload: dict) -> bool:
    mode = payload.get("permission_mode") or payload.get("permissionMode")
    return str(mode or "").strip().lower() == "plan"


# 이번 도구 호출이 계획 확정인가
def _is_exit_plan_mode(payload: dict) -> bool:
    return str(payload.get("tool_name") or "").strip() == "ExitPlanMode"


# 게이트 문구면 표시를 남기고, plan mode 면 스킬을 쓰라는 한 줄을 낸다. 절대 막지 않는다
def _handle_user_prompt_submit(payload: dict, session_id: str) -> None:
    # 이 이벤트에서 종료코드 2 는 다른 이벤트와 뜻이 완전히 다르다 — **사용자가 방금 친
    # 프롬프트를 지운다.** 그래서 어떤 예외도 밖으로 내보내지 않는다. 리마인더 한 줄을 못
    # 내는 것과 사용자가 쓴 글이 사라지는 것은 비교가 안 된다 (plan_log_check 와 같은 경고)
    try:
        # 필드명을 하나로 못 박지 않는다 — 실제로 오는 것은 `prompt` 인데 `user_input`
        # 하나만 읽다 게이트가 영영 안 열렸다. 근거는 _hookutil.prompt_text 에 있다
        text = _hookutil.prompt_text(payload)
        if not text:
            return

        if _opens_gate(text):
            _hookutil.touch_marker(READY_NS, session_id)
            return

        if not _in_plan_mode(payload):
            return

        # 세션당 한 번만. 매 메시지에 같은 줄이 붙으면 그게 소음이다
        if not _hookutil.claim_session(HINTED_NS, session_id):
            return

        # 이 이벤트의 stdout 은 모델 컨텍스트로 들어간다. 한국어라 UTF-8 로 못 박는다
        sys.stdout.reconfigure(encoding="utf-8")
        print(HINT)
    except Exception:  # noqa: BLE001 - 위 주석 참조. 프롬프트를 지키는 것이 최우선이다
        return


# 계획 확정을 막고 사유를 모델에게 넘긴다. 이 함수는 돌아오지 않는다
def _deny() -> None:
    # Windows 콘솔 기본 인코딩(cp949)으로는 한국어 사유를 못 내보내 UnicodeEncodeError 로
    # 훅이 죽는다. 실패 경로에서만 드러나므로 여기서 못 박는다
    sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENY_REASON,
                },
                "systemMessage": f'계획 확정을 막았습니다. "{OPEN_PHRASE}" 라고 하면 열립니다.',
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


# 게이트가 안 열렸으면 계획 확정을 막는다
def _handle_pre_tool_use(payload: dict, session_id: str) -> None:
    # matcher 가 이미 걸러주지만, matcher 를 넓혔을 때 엉뚱한 도구를 막지 않도록 여기서도 본다
    if not _is_exit_plan_mode(payload):
        return

    if _hookutil.is_marked(READY_NS, session_id):
        return

    _deny()


# 계획이 실제로 제시됐으면 게이트를 다시 닫는다
def _handle_post_tool_use(payload: dict, session_id: str) -> None:
    if not _is_exit_plan_mode(payload):
        return

    _hookutil.clear_marker(READY_NS, session_id)


# 훅 진입점
def main() -> None:
    payload = _hookutil.read_payload()
    if payload is None:
        return

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # 세션을 못 가리면 마커도 "세션당 한 번"도 성립하지 않는다
        return

    # 탈출구: 사용자가 면담 없이 바로 계획을 받고 싶을 때
    if os.environ.get("SKIP_PLANINTERVIEW"):
        return

    event = str(payload.get("hook_event_name") or "")
    if event == "UserPromptSubmit":
        _handle_user_prompt_submit(payload, session_id)
    elif event == "PreToolUse":
        _handle_pre_tool_use(payload, session_id)
    elif event == "PostToolUse":
        _handle_post_tool_use(payload, session_id)


if __name__ == "__main__":
    main()
