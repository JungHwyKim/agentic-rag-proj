"""plan mode 면담 게이트가 **열려야 할 때만 열리나.**

이 훅이 지키는 실패 모드는 둘이고 값이 서로 다르다.

  게이트가 헛되이 열린다   모델이 면담 없이 계획을 확정한다. 스킬 지시만 있던 상태로
                          돌아가는 것이라 잃는 것이 크다
  게이트가 안 닫힌다/안 열린다  사용자가 문구를 말했는데 계속 막히면 SKIP_PLANINTERVIEW=1
                          이 습관이 되고, 그러면 검사 자체가 죽는다

그래서 문구 판정을 양쪽으로 잰다 — 변형을 받아들이나, 부정형을 걸러내나.

`UserPromptSubmit` 분기는 따로 subprocess 로 잰다. 이 이벤트에서 0 아닌 종료코드는
**사용자가 방금 친 프롬프트를 지운다.** 임포트 실패나 인코딩 사고까지 잡으려면 실제
프로세스로 돌려봐야 한다.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import _hookutil
import plan_interview_check
import pytest

SESSION = "sess-plan-interview"

# tests/hooks/test_plan_interview_check.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOK = PROJECT_ROOT / ".claude" / "hooks" / "plan_interview_check.py"


# 훅 모듈이 임시 저장소를 루트로 보게 만든다. 마커가 실제 .git/ 에 남지 않게 하는 것이 요점이다
@pytest.fixture
def hook_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", tmp_path)
    return tmp_path


# 훅 입력을 stdin 으로 넣고 main() 을 돌린다.
# StringIO 가 아니라 TextIOWrapper 인 이유: `_hookutil.read_payload()` 가 stdin 에
# `reconfigure()` 를 부르는데 StringIO 에는 그 메서드가 없다
def _run(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    plan_interview_check.main()


# UserPromptSubmit 페이로드. **필드명은 하네스가 실제로 보내는 `prompt` 다.**
#
# 이 가짜가 `user_input` 하나로 돼 있어서, 훅이 같은 오해를 한 채로 **테스트는 통과하고
# 게이트는 한 번도 안 열리는** 상태가 유지됐다(2026-08-21 발견). 코드와 가짜가 똑같이
# 틀리면 테스트는 그 오해를 검증한다 — 이 저장소가 LlamaParse 응답 모양·병합 셀 사각형에
# 이어 세 번째로 겪은 형태다. 그래서 기본은 실제 이름을 쓰고, 폴백은 아래 전용 테스트가 본다
def _prompt(text: str, *, field: str = "prompt", **extra: object) -> dict:
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": SESSION,
        field: text,
        **extra,
    }


def _exit_plan_mode(event: str) -> dict:
    return {"hook_event_name": event, "session_id": SESSION, "tool_name": "ExitPlanMode"}


def _ready(root: Path) -> bool:
    return _hookutil.is_marked(plan_interview_check.READY_NS, SESSION)


# 조사 하나 틀렸다고 사용자가 갇히면 안 되므로 변형을 받는다
@pytest.mark.parametrize(
    "text",
    [
        "설계서 작성해줘",
        "이제 설계서 작성해",
        "설계서 써줘",
        "설계서를 만들어줘",
        "설계 문서 작성해줘",
        "좋아, 여기까지면 됐어. 설계서 정리해줘",
    ],
)
def test_open_phrases_arm_the_gate(hook_root: Path, monkeypatch: pytest.MonkeyPatch, text: str):
    _run(monkeypatch, _prompt(text))

    assert _ready(hook_root), f"게이트를 열어야 할 말인데 안 열렸다: {text!r}"


# 반대쪽. 부정형과 무관한 말은 게이트를 열면 안 된다
@pytest.mark.parametrize(
    "text",
    [
        "아직 설계서 작성하지 마",
        "설계서 작성하지 말고 질문 더 해줘",
        "설계서는 나중에 쓰자",
        "splitter 청크 크기 어떻게 할까",
        "이 함수 어떻게 동작해?",
    ],
)
def test_other_prompts_leave_the_gate_closed(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, text: str
):
    _run(monkeypatch, _prompt(text))

    assert not _ready(hook_root), f"게이트가 열리면 안 되는 말이다: {text!r}"


# 부정어를 메시지 전체에서 찾으면 이런 정상 요청까지 닫힌다. 창을 좁게 둔 이유가 이것이다
def test_negation_far_from_the_phrase_does_not_close_the_gate(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch
):
    _run(monkeypatch, _prompt("아직 못 정한 게 남아 있지만 여기까지 하고 설계서 작성해줘"))

    assert _ready(hook_root)


# 게이트가 안 열렸으면 계획 확정을 막는다
def test_exit_plan_mode_is_denied_without_the_phrase(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, _exit_plan_mode("PreToolUse"))

    assert excinfo.value.code == 0
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    # 해제 문구가 사유에 없으면 사용자가 갇힌다
    assert plan_interview_check.OPEN_PHRASE in decision["permissionDecisionReason"]


# 문구가 나왔으면 통과시킨다. 아무것도 내지 않는 것이 통과의 표시다
def test_exit_plan_mode_passes_after_the_phrase(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(monkeypatch, _prompt("설계서 작성해줘"))
    capsys.readouterr()

    _run(monkeypatch, _exit_plan_mode("PreToolUse"))

    assert capsys.readouterr().out == ""


# 계획이 실제로 제시되면 게이트가 다시 닫힌다. 한 세션에서 plan mode 를 두 번 쓰는 경우다
def test_gate_closes_again_after_the_plan_is_presented(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(monkeypatch, _prompt("설계서 작성해줘"))
    _run(monkeypatch, _exit_plan_mode("PreToolUse"))
    _run(monkeypatch, _exit_plan_mode("PostToolUse"))
    capsys.readouterr()

    assert not _ready(hook_root)
    with pytest.raises(SystemExit):
        _run(monkeypatch, _exit_plan_mode("PreToolUse"))


# **PreToolUse 에서 소비하지 않는다는 계약.** 사용자가 계획을 거절하면 PostToolUse 가 안
# 돌고, 그때 마커가 살아 있어야 모델이 고쳐서 다시 낼 수 있다. 여기서 소비하도록 되돌리면
# 일을 하고도 막히는 게이트가 되어 SKIP_PLANINTERVIEW=1 이 습관이 된다
def test_pre_tool_use_does_not_consume_the_marker(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(monkeypatch, _prompt("설계서 작성해줘"))

    _run(monkeypatch, _exit_plan_mode("PreToolUse"))
    _run(monkeypatch, _exit_plan_mode("PreToolUse"))
    capsys.readouterr()

    assert _ready(hook_root)


# matcher 를 넓혀도 엉뚱한 도구를 막으면 안 된다.
# `AskUserQuestion` 이 함께 있는 이유: PostToolUse 쪽 matcher 가 그 도구까지 넓어졌으므로,
# PreToolUse 쪽도 같이 넓혔다가 면담 질문 자체가 막히는 회귀를 여기서 잡는다
@pytest.mark.parametrize("tool", ["Write", "AskUserQuestion"])
def test_other_tools_are_untouched(
    hook_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
):
    _run(
        monkeypatch,
        {"hook_event_name": "PreToolUse", "session_id": SESSION, "tool_name": tool},
    )

    assert capsys.readouterr().out == ""


# 탈출구. 사용자가 면담을 건너뛰기로 했으면 막지 않는다
def test_skip_env_disables_the_gate(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("SKIP_PLANINTERVIEW", "1")

    _run(monkeypatch, _exit_plan_mode("PreToolUse"))

    assert capsys.readouterr().out == ""


# plan mode 로 확인될 때만 리마인더를 낸다. 필드가 없으면 아무 말도 안 한다 —
# plan mode 가 아닌 세션마다 헛소리를 뿌리는 것이 리마인더를 못 내는 것보다 나쁘다
def test_hint_only_when_plan_mode_is_visible(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(monkeypatch, _prompt("splitter 어떻게 설계하지"))
    assert capsys.readouterr().out == ""

    _run(monkeypatch, _prompt("splitter 어떻게 설계하지", permission_mode="plan"))
    assert "plan-interview" in capsys.readouterr().out


# 같은 줄이 매 메시지마다 붙으면 그게 소음이다
def test_hint_is_emitted_once_per_session(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(monkeypatch, _prompt("설계 방향 잡자", permission_mode="plan"))
    capsys.readouterr()

    _run(monkeypatch, _prompt("그럼 다음은 어떤 방식이 나을까", permission_mode="plan"))

    assert capsys.readouterr().out == ""


# **이 훅에서 제일 비싼 사고를 막는 자리.** UserPromptSubmit 에서 0 아닌 종료코드는
# 사용자가 방금 친 글을 지운다. 임포트 실패·인코딩 사고까지 잡으려면 실제 프로세스여야 한다
@pytest.mark.parametrize(
    "stdin_bytes",
    [
        b"",
        b"not json at all",
        b"[]",
        '﻿{"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "설계 논의하자"}'.encode(),
        json.dumps(
            {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": None}
        ).encode(),
    ],
)
def test_user_prompt_submit_never_exits_nonzero(stdin_bytes: bytes):
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin_bytes,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


# 하네스가 필드명을 바꿔도 한쪽이 받아야 한다. `user_input` 만 오던 시절의 페이로드로도
# 게이트가 열리는지 본다 — 폴백을 지우면 여기서 걸린다
def test_gate_opens_with_legacy_field(hook_root: Path, monkeypatch: pytest.MonkeyPatch):
    _run(monkeypatch, _prompt("설계서 작성해줘", field="user_input"))

    assert _ready(hook_root)


# 두 필드가 같이 오고 옛 필드가 비어 있으면 실제 이름(prompt)을 읽어야 한다.
# 값이 있는 첫 필드를 쓴다는 규약이 여기서 발화한다
def test_real_field_wins_over_empty_legacy(hook_root: Path, monkeypatch: pytest.MonkeyPatch):
    _run(monkeypatch, _prompt("설계서 작성해줘", user_input=""))

    assert _ready(hook_root)


# ---------------------------------------------------------------------------
# AskUserQuestion 답변으로 여는 경로
#
# 이쪽의 실패 모드는 위 문구 판정과 비대칭이다. 문구는 사용자가 직접 친 글이지만 선택지
# **라벨은 모델이 쓴다** — 그래서 "헛되이 열린다" 쪽이 더 무겁고, 실측에서 나온 오탐 하나가
# 아래 두 번째 테스트로 박혀 있다. 반대쪽(안 열림)은 사용자가 문구를 말하는 경로가 그대로
# 살아 있어 갇히지 않는다
# ---------------------------------------------------------------------------


# AskUserQuestion 의 PostToolUse 페이로드.
#
# **답이 질문 텍스트로 짝지어지는 것이 실제 모양이다** — `tool_response` 는
# `{questions, answers, annotations}` 이고 `answers` 가 `{질문: 고른 라벨}` 이다(전사 실측).
# 가짜를 `{라벨: True}` 같은 편한 모양으로 만들면 코드와 가짜가 똑같이 틀린 채 통과한다 —
# 이 저장소가 LlamaParse 응답·병합 셀·게이트 필드명에 이어 네 번째로 겪을 뻔한 형태다
def _ask(rounds: list[tuple[str, list[str], str]], *, field: str = "tool_response") -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "session_id": SESSION,
        "tool_name": "AskUserQuestion",
        field: {
            "questions": [
                {
                    "question": question,
                    "header": "진행",
                    "multiSelect": False,
                    "options": [{"label": label, "description": ""} for label in labels],
                }
                for question, labels, _ in rounds
            ],
            "answers": {question: chosen for question, _, chosen in rounds},
            "annotations": {},
        },
    }


# 마지막 문항에서 "작성"을 고르면 그것으로 열린다. 이 경로 전체의 존재 이유다
def test_answer_opens_the_gate(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(
        monkeypatch,
        _ask([("지금 설계서를 작성할까요?", ["지금 설계서 작성", "라운드 더"], "지금 설계서 작성")]),
    )

    assert _ready(hook_root)
    assert "plan-interview" in capsys.readouterr().out


# **실측 오탐을 그대로 박은 회귀선.** 선택지 둘 다 문구를 담고 있어 어느 쪽을 골라도 열리는
# 문항이었다(전사에서 나온 유일한 오탐, header `트리거 문구`). 대안 가드를 지우면 여기서 걸린다
def test_answer_without_an_alternative_leaves_the_gate_closed(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(
        monkeypatch,
        _ask(
            [
                (
                    "게이트를 여는 문구를 어떻게 잡을까요?",
                    ['"설계서 작성" 포함 변형 (추천)', '정확히 "설계서 작성해줘" 만'],
                    '"설계서 작성" 포함 변형 (추천)',
                )
            ]
        ),
    )

    assert not _ready(hook_root)
    assert capsys.readouterr().out == ""


# **답만 본다.** 질문·선택지까지 훑으면 문구를 논의하는 문항이 전부 열린다(실측 오탐 5건)
def test_phrase_only_in_the_question_leaves_the_gate_closed(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(
        monkeypatch,
        _ask(
            [
                (
                    '지금 "설계서 작성해줘" 라고 말할까요?',
                    ["설계서 작성해줘", "라운드를 더 돌자"],
                    "라운드를 더 돌자",
                )
            ]
        ),
    )

    assert not _ready(hook_root)
    assert capsys.readouterr().out == ""


# 사용자가 "Other" 에 직접 친 글도 같은 자리로 온다. 라벨 목록에 없다고 버리면 안 된다 —
# 전사의 진짜 3건 중 1건이 이 모양이었다(`1번. 설계서 작성해줘`)
def test_free_text_answer_opens_the_gate(hook_root: Path, monkeypatch: pytest.MonkeyPatch):
    _run(
        monkeypatch,
        _ask(
            [
                (
                    "회귀 방지선을 무엇으로 둘까요?",
                    ["합성 + 실파일 둘 다 (권장)", "합성만", "실파일만"],
                    "1번. 설계서 작성해줘",
                )
            ]
        ),
    )

    assert _ready(hook_root)


# 부정형은 답에서도 걸러야 한다. 문구 판정을 프롬프트 경로와 같은 함수로 두는 이유다
def test_negated_answer_leaves_the_gate_closed(hook_root: Path, monkeypatch: pytest.MonkeyPatch):
    _run(
        monkeypatch,
        _ask(
            [("지금 설계서를 작성할까요?", ["지금 설계서 작성", "라운드 더"], "아직 설계서 작성하지 마")]
        ),
    )

    assert not _ready(hook_root)


# 하네스가 답을 `tool_input` 에 실어 보내도 받아야 한다. 이 도구는 스키마상 `answers` 가
# 입력에도 있어서 어느 쪽인지가 계약이 아니다 — 이중 조회를 지우면 여기서 걸린다
def test_answers_from_tool_input_open_the_gate(hook_root: Path, monkeypatch: pytest.MonkeyPatch):
    _run(
        monkeypatch,
        _ask(
            [("지금 설계서를 작성할까요?", ["지금 설계서 작성", "라운드 더"], "지금 설계서 작성")],
            field="tool_input",
        ),
    )

    assert _ready(hook_root)


# 두 이벤트가 실제로 이어지나. 답변 하나로 계획 확정까지 가는 것이 이 작업의 결과물이다
def test_exit_plan_mode_passes_after_an_answer(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _run(
        monkeypatch,
        _ask([("지금 설계서를 작성할까요?", ["지금 설계서 작성", "라운드 더"], "지금 설계서 작성")]),
    )
    capsys.readouterr()

    _run(monkeypatch, _exit_plan_mode("PreToolUse"))

    assert capsys.readouterr().out == ""


# **안내는 세션당 한 번으로 묶지 않는다.** 리마인더(HINT)와 반대다 — 게이트가 열리는 순간에
# 안 나오면 모델이 문구를 다시 청하고, 그게 이 경로가 없애려는 왕복이다
def test_answer_hint_is_emitted_every_time(
    hook_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    payload = _ask(
        [("지금 설계서를 작성할까요?", ["지금 설계서 작성", "라운드 더"], "지금 설계서 작성")]
    )

    _run(monkeypatch, payload)
    _run(monkeypatch, payload)

    assert capsys.readouterr().out.count("plan-interview") == 2
