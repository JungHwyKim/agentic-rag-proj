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

# tests/hooks/test_hook_wiring.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = PROJECT_ROOT / ".claude" / "hooks"
SETTINGS = PROJECT_ROOT / ".claude" / "settings.json"

# settings.json 의 command 문자열에서 훅 스크립트 이름을 뽑는다
_SCRIPT_RE = re.compile(r"\.claude/hooks/([\w.]+\.py)")

# 다른 훅이 임포트해 쓰는 공용 모듈. 단독 실행되지 않으므로 배선 대상이 아니다
LIBRARY_MODULES = {"_hookutil.py"}


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


# PreToolUse 훅은 Bash 와 PowerShell 을 모두 덮어야 한다.
# 이 저장소의 실행 명령 예시는 전부 PowerShell 이라(CLAUDE.md), matcher 가 "Bash" 뿐이면
# 커밋 전 검사도 유료 테스트 차단도 도구 하나만 바꾸면 통째로 우회된다
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_pretooluse_covers_both_shells(tool: str):
    matchers = [m for event, m, _script in _wired() if event == "PreToolUse"]
    assert matchers, "PreToolUse 훅이 없다"

    for matcher in matchers:
        assert re.fullmatch(matcher, tool), f"PreToolUse matcher {matcher!r} 가 {tool} 을 놓친다"


# 모든 훅 모듈이 임포트돼야 한다. 문법 오류는 "훅이 조용히 안 돎" 으로만 드러난다
@pytest.mark.parametrize(
    "module",
    sorted(p.stem for p in (PROJECT_ROOT / ".claude" / "hooks").glob("*.py")),
)
def test_hook_module_imports(module: str):
    assert importlib.import_module(module) is not None
