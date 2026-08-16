"""`paid_test_check` 판정 회귀 테스트.

이 훅은 두 방향으로 다 틀릴 수 있고, 두 실패의 비용이 다르다.

  놓치면(false negative)  LlamaParse/OpenAI 에 실제 과금이 발생한다. 되돌릴 수 없다.
  헛막으면(false positive) `pytest -q` 나 `git commit -m "..."` 처럼 하루에 수십 번 쓰는
                          명령이 막힌다. 그러면 훅이 곧바로 무시당하고 탈출구가 상시
                          켜져서, 결국 첫 번째 실패까지 같이 열린다.

그래서 통과해야 하는 케이스를 차단 케이스만큼 촘촘히 박는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import paid_test_check
import pytest

HOOK_PATH = Path(paid_test_check.__file__)


# 막아야 하는 명령
BLOCKED = [
    "pytest -m integration",
    r".\.venv\Scripts\python.exe -m pytest -m integration",
    'python -m pytest -m "integration"',
    "pytest -m 'not slow and integration' tests/",
    "pytest -m integration or unit",
    "uv run pytest -m integration",
    # 복합 명령의 뒤쪽만 유료인 경우
    "git add -A && pytest -m integration",
    "ruff check . ; pytest -m integration",
    # PowerShell 호출 연산자와 변수. 이 저장소의 명령 예시가 이 형태다
    "& $py -m pytest -m integration",
    '& "C:\\agentic-rag-proj\\.venv\\Scripts\\python.exe" -m pytest -m integration',
    # -m 을 안 쓰고 기본 제외를 날리는 우회
    "pytest -o addopts= tests/",
    'pytest --override-ini "addopts=" ',
]

# 막으면 안 되는 명령
ALLOWED = [
    "pytest -q",
    r".\.venv\Scripts\python.exe -m pytest",
    "& $py -m pytest -q",
    # pyproject 기본값과 같은 안전한 호출. 이걸 막으면 훅이 통째로 무시당한다
    "pytest -m 'not integration'",
    'pytest -m "not slow and not integration"',
    'pytest -o "addopts=-m \'not integration\'"',
    # 수집만 한다. 테스트 본문이 안 돌아 과금이 없다
    "pytest --collect-only -m integration",
    # -m 과 integration 이 둘 다 있지만 pytest 가 아니다
    'git commit -m "integration 테스트 추가"',
    'git commit -m "pytest integration marker 추가"',
    "echo integration",
    "python -m ruff check .",
    "pytest tests/ingestion/loader/document/test_pdf.py",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_paid_commands_are_blocked(command: str):
    assert paid_test_check.blocked_reason(command) is not None, command


@pytest.mark.parametrize("command", ALLOWED)
def test_safe_commands_pass(command: str):
    assert paid_test_check.blocked_reason(command) is None, command


# 훅을 실제로 실행해 확인한다. 판정 함수가 맞아도 배선이 틀리면 아무 소용이 없다
def _run_hook(command: str, *, raw: bytes | None = None, env: dict | None = None):
    payload = raw if raw is not None else json.dumps({"tool_input": {"command": command}}).encode()
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        timeout=30,
        env=env,
    )


# 차단 시 PreToolUse deny 형식을 정확히 내야 한다. 형식이 틀리면 하네스가 무시한다
def test_deny_payload_shape():
    proc = _run_hook("pytest -m integration")

    assert proc.returncode == 0
    output = json.loads(proc.stdout.decode("utf-8"))
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    # 탈출구를 사유에 적어두지 않으면 사용자가 막힌 채로 갇힌다
    assert "ALLOW_PAID_TESTS" in specific["permissionDecisionReason"]


# 통과할 때는 아무 말도 하지 않아야 한다. 빈 출력이 곧 '허용'이다
def test_safe_command_produces_no_output():
    proc = _run_hook("pytest -q")
    assert proc.returncode == 0
    assert proc.stdout.strip() == b""


# 탈출구. 사용자가 과금을 승인했을 때 막히면 안 된다
def test_escape_hatch():
    proc = _run_hook("pytest -m integration", env=dict(os.environ, ALLOW_PAID_TESTS="1"))
    assert proc.stdout.strip() == b""


# BOM 이 붙은 입력도 판정돼야 한다.
# 이게 이 훅의 제일 위험한 실패 모드였다 — json.load 가 실패하면 "판단 근거 없음" 경로로
# 빠져 조용히 통과한다. 훅이 죽지도, 경고를 내지도 않아서 알아챌 방법이 없다.
# 실제로 PowerShell 5.1 의 `Out-File -Encoding utf8` 이 BOM 을 붙여 겪었다
def test_bom_prefixed_input_is_still_judged():
    raw = b"\xef\xbb\xbf" + json.dumps({"tool_input": {"command": "pytest -m integration"}}).encode()
    proc = _run_hook("", raw=raw)
    assert b"deny" in proc.stdout


# 판단이 서지 않으면 막지 않는다 — 훅 입력이 JSON 이 아니면 조용히 통과
def test_unreadable_input_does_not_block():
    proc = _run_hook("", raw=b"not json at all")
    assert proc.returncode == 0
    assert proc.stdout.strip() == b""
