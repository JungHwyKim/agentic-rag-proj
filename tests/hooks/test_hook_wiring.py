"""훅이 **배선돼 있는지**를 검사한다.

판정 로직이 아무리 맞아도 `settings.json` 이 가리키는 경로가 틀리면 훅은 한 번도 안 돈다.
그리고 그때 아무 에러도 안 난다 — 그냥 검사가 없는 것과 같아진다. 이 저장소의 훅들이
공통으로 두려워하는 실패 모드가 그것이라, 배선 자체를 테스트로 박는다.

`pre_commit_check.py` 의 compileall 대상은 `rag_pipeline`/`backend`/`tests` 라
`.claude/hooks/` 는 문법 검사를 안 받는다. 아래 임포트 테스트가 그 구멍을 대신 메운다.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from _escape_hatches import ESCAPE_HATCHES

# tests/hooks/test_hook_wiring.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PROJECT_ROOT / ".claude" / "hooks"
SETTINGS = PROJECT_ROOT / ".claude" / "settings.json"

# settings.json 의 command 문자열에서 훅 스크립트 이름을 뽑는다
_SCRIPT_RE = re.compile(r"\.claude/hooks/([\w.]+\.py)")

# 다른 훅이 임포트해 쓰는 공용 모듈. 단독 실행되지 않으므로 배선 대상이 아니다
LIBRARY_MODULES = {"_hookutil.py"}

# 셸 명령을 검사하지 **않는** PreToolUse 훅. 아래 test_pretooluse_covers_both_shells 의 예외다.
#
# **기본값이 "셸 대상"인 것이 이 집합의 핵심이다.** 새 PreToolUse 훅을 무심코 추가하면 그
# 테스트가 깨져서 "이건 셸을 보는 훅인가"를 답하게 만든다. 반대로 빠지는 쪽이 기본이면
# 검사는 시간이 지나며 저절로 헐거워진다 — UNMAPPED_BY_DESIGN 과 같은 모양이다.
#
# plan_interview_check 는 ExitPlanMode 만 본다. 셸 도구를 바꿔서 우회할 대상이 아니다
NON_SHELL_PRETOOLUSE = {"plan_interview_check.py"}

# 훅의 시간 제한은 두 겹이다.
#   바깥 = settings.json 의 timeout      훅 프로세스를 강제 종료하는 한계
#   안쪽 = 훅 코드의 *_TIMEOUT_SEC        훅이 스스로 멈추고 사유를 남기는 한계
#
# **안쪽이 더 짧아야 한다.** 그래야 "N초 안에 안 끝나 중단했습니다"를 모델에게 전달할 수
# 있다. 뒤집히면 훅이 침묵한 채 죽고, 검사가 안 돌았다는 것조차 아무도 모른다 — 이 파일이
# 애초에 존재하는 이유인 그 실패 모드다.
#
# 이벤트별로 가르는 이유: 한 스크립트가 여러 이벤트에 배선되면 이벤트마다 도는 코드가
# 다르다. auto_test_check 는 PostToolUse 에서 영역별(90초)만, Stop 에서 전체(540초)만 돈다.
# 한쪽 기준으로 뭉뚱그리면 Stop 예산으로 PostToolUse 를 재게 돼 검사가 무의미해진다.
#
# **합이 아니라 최댓값으로 잰다.** pre_commit_check 는 검사 4개를 순서대로 돌려 합이
# 960 인데 예산은 900 이다. 이건 버그가 아니다 — 개별 검사가 걸렸을 때 스스로 사유를 남기는
# 것이 목적이라 제일 큰 하나가 예산 안에 들면 된다. 합으로 재면 멀쩡한 설정을 실패로 잡는다
_TIMEOUT_BUDGETS: dict[tuple[str, str], tuple[str, ...]] = {
    ("auto_test_check.py", "PostToolUse"): ("SCOPED_TIMEOUT_SEC",),
    ("auto_test_check.py", "Stop"): ("FULL_TIMEOUT_SEC",),
    ("pre_commit_check.py", "PreToolUse"): (
        "LINT_TIMEOUT_SEC",
        "BUILD_TIMEOUT_SEC",
        "TEST_TIMEOUT_SEC",
    ),
}


# settings.json 에 적힌 (이벤트, matcher, 스크립트명) 전부
def _wired() -> list[tuple[str, str, str]]:
    config = json.loads(SETTINGS.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str]] = []
    for event, groups in config.get("hooks", {}).items():
        for group in groups:
            matcher = group.get("matcher", "")
            for hook in group.get("hooks", []):
                for script in _SCRIPT_RE.findall(hook.get("command", "")):
                    rows.append((event, matcher, script))
    return rows


# (스크립트, 이벤트) -> settings.json 의 timeout. 안 적혀 있으면 None
def _wired_timeouts() -> dict[tuple[str, str], int | None]:
    config = json.loads(SETTINGS.read_text(encoding="utf-8"))
    budgets: dict[tuple[str, str], int | None] = {}
    for event, groups in config.get("hooks", {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                for script in _SCRIPT_RE.findall(hook.get("command", "")):
                    budgets[(script, event)] = hook.get("timeout")
    return budgets


# 배선된 스크립트가 실제로 존재해야 한다. 이름을 바꾸면 훅이 조용히 사라진다
def test_wired_scripts_exist():
    wired = _wired()
    assert wired, "settings.json 에 배선된 훅이 하나도 없다"

    for event, _matcher, script in wired:
        assert (HOOKS_DIR / script).is_file(), f"{event} 이 없는 파일을 가리킨다: {script}"


# 만들어 놓고 배선을 잊은 훅이 없어야 한다
def test_every_hook_script_is_wired():
    on_disk = {p.name for p in HOOKS_DIR.glob("*.py")} - LIBRARY_MODULES
    wired = {script for _event, _matcher, script in _wired()}

    assert on_disk - wired == set(), "settings.json 에 배선되지 않은 훅이 있다"


# 셸 명령을 보는 PreToolUse 훅은 Bash 와 PowerShell 을 모두 덮어야 한다.
# 이 저장소의 실행 명령 예시는 전부 PowerShell 이라(CLAUDE.md), matcher 가 "Bash" 뿐이면
# 커밋 전 검사도 유료 테스트 차단도 도구 하나만 바꾸면 통째로 우회된다.
#
# 셸을 안 보는 훅(NON_SHELL_PRETOOLUSE)에는 이 요구가 뜻이 없다. 예외를 집합으로 명시하는
# 이유는 위 주석에 있다 — 기본값이 "셸 대상"이라야 검사가 헐거워지지 않는다
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_pretooluse_covers_both_shells(tool: str):
    matchers = [
        m
        for event, m, script in _wired()
        if event == "PreToolUse" and script not in NON_SHELL_PRETOOLUSE
    ]
    assert matchers, "셸을 보는 PreToolUse 훅이 하나도 없다"

    for matcher in matchers:
        assert re.fullmatch(matcher, tool), f"PreToolUse matcher {matcher!r} 가 {tool} 을 놓친다"


# 예외 집합에 죽은 이름이 남으면 안 된다. 훅을 지우거나 이벤트를 옮긴 뒤 여기만 남으면,
# 나중에 같은 이름의 셸 훅이 생겼을 때 그 훅이 조용히 검사에서 빠진다
def test_non_shell_exceptions_are_wired_to_pretooluse():
    wired = {script for event, _matcher, script in _wired() if event == "PreToolUse"}

    stale = NON_SHELL_PRETOOLUSE - wired
    assert not stale, (
        f"NON_SHELL_PRETOOLUSE 의 {sorted(stale)} 가 PreToolUse 에 배선돼 있지 않다. "
        "예외를 지워라"
    )


# 훅이 스스로 멈출 수 있어야 한다. 안쪽 제한이 바깥 예산보다 크면 훅은 사유를 남기지
# 못하고 강제 종료되고, 사용자는 검사가 왜 아무 말도 안 했는지 알 방법이 없다
@pytest.mark.parametrize(("script", "event"), sorted(_TIMEOUT_BUDGETS))
def test_internal_timeout_fits_in_hook_budget(script: str, event: str):
    outer = _wired_timeouts().get((script, event))
    assert outer is not None, f"{script} 가 {event} 에 배선돼 있지 않거나 timeout 이 없다"

    module = importlib.import_module(Path(script).stem)
    for name in _TIMEOUT_BUDGETS[(script, event)]:
        inner = getattr(module, name)
        assert inner < outer, (
            f"{script}({event}) 의 {name}={inner} 가 settings.json 의 timeout={outer} 이상이다. "
            "훅이 사유를 남기지 못하고 죽는다 — 안쪽을 줄이거나 바깥을 늘려라"
        )


# 예산 표에 안 들어간 타임아웃이 없어야 한다.
# 새 검사를 추가하면서 표에 안 넣으면 그 검사만 예산 밖으로 새는데 아무도 모른다.
# test_every_hook_script_is_wired 와 같은 모양의 커버리지 검사다
def test_every_hook_timeout_is_budgeted():
    scripts = {script for _event, _matcher, script in _wired()} - LIBRARY_MODULES

    for script in sorted(scripts):
        module = importlib.import_module(Path(script).stem)
        declared = {name for name in vars(module) if name.endswith("_TIMEOUT_SEC")}
        if not declared:
            continue

        budgeted: set[str] = set()
        for (budget_script, _event), names in _TIMEOUT_BUDGETS.items():
            if budget_script == script:
                budgeted.update(names)

        assert declared <= budgeted, (
            f"{script} 의 {sorted(declared - budgeted)} 가 _TIMEOUT_BUDGETS 에 없다. "
            "어느 이벤트에서 도는 제한인지 표에 적어라"
        )


# 모든 훅 모듈이 임포트돼야 한다. 문법 오류는 "훅이 조용히 안 돎" 으로만 드러난다
@pytest.mark.parametrize(
    "module",
    sorted(p.stem for p in (PROJECT_ROOT / ".claude" / "hooks").glob("*.py")),
)
def test_hook_module_imports(module: str):
    assert importlib.import_module(module) is not None


# 훅이 읽는 환경변수 중 탈출구가 아닌 것. 기본값은 "탈출구"다 — 아래 커버리지 테스트가
# 그렇게 서 있어야 새 훅을 무심코 추가했을 때 "이건 게이트를 여는 변수인가"를 답하게 된다.
# 반대로 빠지는 쪽이 기본이면 검사는 시간이 지나며 저절로 헐거워진다
# (NON_SHELL_PRETOOLUSE · UNMAPPED_BY_DESIGN 과 같은 모양이다)
NON_ESCAPE_ENV: set[str] = set()

# 훅 소스에서 읽는 환경변수 이름을 뽑는다
_ENV_RE = re.compile(r'os\.environ\.get\(\s*"([A-Z_][A-Z0-9_]*)"')


# 훅의 탈출구가 전부 conftest 의 ESCAPE_HATCHES 에 있어야 한다.
#
# 빠지면 그 변수를 켠 개발자의 머신에서만 훅 테스트가 달라진다 — 실측(2026-08-22)으로
# SKIP_PLANINTERVIEW=1 은 13개를 빨갛게 만들었고, 나머지는 **조용히 통과**시켰다.
# 뒤쪽이 더 나쁘다: 검사가 있는 줄 알았는데 없는 상태가 아무 신호 없이 유지된다
def test_every_escape_hatch_is_cleared_in_tests():
    found: set[str] = set()
    for path in HOOKS_DIR.glob("*.py"):
        found.update(_ENV_RE.findall(path.read_text(encoding="utf-8")))

    hatches = found - NON_ESCAPE_ENV
    missing = hatches - set(ESCAPE_HATCHES)

    assert not missing, (
        f"{sorted(missing)} 가 ESCAPE_HATCHES 에 없다. "
        "그 변수를 켠 환경에서는 훅 테스트가 검사 대상을 안 돌린다 — "
        "목록에 넣거나, 탈출구가 아니면 NON_ESCAPE_ENV 에 이유와 함께 적어라"
    )


# 목록에 죽은 이름이 남으면 안 된다. 훅을 지우거나 탈출구 이름을 바꾼 뒤 여기만 남으면,
# 나중에 같은 이름이 다시 생겼을 때 그것이 검사 없이 통과한다.
# test_non_shell_exceptions_are_wired_to_pretooluse 와 같은 판단이다
def test_no_stale_escape_hatch_names():
    found: set[str] = set()
    for path in HOOKS_DIR.glob("*.py"):
        found.update(_ENV_RE.findall(path.read_text(encoding="utf-8")))

    stale = set(ESCAPE_HATCHES) - found

    assert not stale, f"{sorted(stale)} 는 어느 훅도 안 읽는다. ESCAPE_HATCHES 에서 지워라"
