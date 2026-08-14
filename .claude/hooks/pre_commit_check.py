"""git commit 직전에 린트·빌드·테스트를 돌리는 PreToolUse 훅.

Claude Code 가 `git commit` 을 실행하려 할 때 settings.json 의 PreToolUse 훅이
이 스크립트를 부른다. 셋 중 하나라도 실패하면 permissionDecision="deny" 를 돌려
커밋 자체를 막고, 실패 출력을 그대로 모델에게 넘겨 고치게 한다.

git 의 .git/hooks/pre-commit 과 목적은 같지만 계층이 다르다. 이쪽은 Claude 가
도구를 호출하는 시점에 걸리므로, git 훅을 설치하지 않아도(그리고 아직 git init
하지 않았어도 나중에) 동작한다.

'빌드'는 파이썬에 별도 빌드 단계가 없으므로 두 가지로 대신한다.
  1. compileall  — 임포트되지 않는 모듈까지 포함한 전체 문법 검사
  2. app 임포트  — create_app() 을 실제로 돌려 라우터·설정 배선이 깨졌는지 확인
pytest 는 conftest 를 통해 파이프라인을 임포트하지만 backend 앱 조립까지는
건드리지 않으므로, 2번이 실제로 값을 한다.

settings.json 은 Bash 호출 전체에 이 훅을 걸고, `git commit` 인지 아닌지는 아래
_GIT_COMMIT_RE 가 판단한다. 훅 설정의 `if` 필터로 거를 수도 있지만 그쪽은 패턴이
어긋나면 훅이 **조용히** 한 번도 안 도는 실패 모드가 있다. 검사가 안 돌고 있다는
걸 모르는 것이 최악이므로, 커밋이 아닌 호출에서 약 0.1초를 쓰더라도 판단을
여기로 모았다.

integration 마커 테스트는 절대 돌리지 않는다. pyproject 의
addopts = "-m 'not integration'" 이 기본 제외하며, 여기서 그 값을 덮지 않는다.
LlamaParse/OpenAI 실제 과금을 커밋 훅이 몰래 일으키면 안 된다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# .claude/hooks/pre_commit_check.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 실패 출력이 길면 모델 컨텍스트를 잡아먹으므로 검사당 이 길이로 자른다
MAX_OUTPUT_CHARS = 4000

# 검사 전체 시간 예산. settings.json 의 timeout 보다 짧게 잡아 훅이 강제 종료되는
# 대신 스스로 사유를 남기고 끝나게 한다
LINT_TIMEOUT_SEC = 120
BUILD_TIMEOUT_SEC = 120
TEST_TIMEOUT_SEC = 600

# `git ... commit` 을 찾는다. `git add -A && git commit -m ...` 같은 복합 명령도 걸린다.
# `git commit-tree` 같은 다른 하위명령에 걸리지 않도록 뒤에 단어 경계를 둔다
_GIT_COMMIT_RE = re.compile(r"\bgit\s+(?:-\S+\s+|--\S+=\S+\s+)*commit\b")


# 프로젝트 .venv 의 파이썬을 찾는다. 없으면 이 스크립트를 돌린 파이썬으로 대체
def _venv_python() -> str:
    for candidate in (
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
        PROJECT_ROOT / ".venv" / "bin" / "python",  # POSIX
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


# 검사 하나를 돌리고 (성공여부, 표시용 출력) 을 돌려준다
def _run(args: list[str], *, timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            # 하위 프로세스가 stdin 을 기다리며 멈추지 않게 한다
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return False, f"{timeout}초 안에 끝나지 않아 중단했습니다: {' '.join(args)}"
    except OSError as exc:
        return False, f"실행할 수 없습니다 ({exc}): {' '.join(args)}"

    output = (proc.stdout + proc.stderr).strip()
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n... (출력 생략)"
    return proc.returncode == 0, output


# 커밋을 막고 사유를 모델에게 넘긴다
def _deny(reason: str) -> None:
    # Windows 콘솔 기본 인코딩(cp949)으로는 한국어 사유와 em dash 를 못 내보내
    # UnicodeEncodeError 로 훅이 죽는다. 실패 경로에서만 드러나므로 여기서 못 박는다
    sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
                "systemMessage": "커밋 전 검사 실패 — 커밋을 막았습니다.",
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


# 훅 진입점. stdin 으로 받은 도구 호출을 보고 git commit 일 때만 검사한다
def main() -> None:
    # 한국어 커밋 메시지가 담겨 오므로 stdin 도 UTF-8 로 못 박는다. cp949 로 읽으면
    # UnicodeDecodeError 가 나는데 이건 ValueError 하위라 아래 except 에 조용히
    # 먹히고, 검사가 통째로 무력화된다
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # 훅 입력을 못 읽었다면 판단 근거가 없다. 막지 않고 통과시킨다
        return

    command = str(payload.get("tool_input", {}).get("command", ""))

    if not _GIT_COMMIT_RE.search(command):
        return

    # 탈출구: 사용자가 의도적으로 검사를 건너뛰고 싶을 때
    if "--no-verify" in command or os.environ.get("SKIP_PRECOMMIT"):
        return

    python = _venv_python()

    checks: list[tuple[str, list[str], int]] = [
        ("린트 (ruff)", [python, "-m", "ruff", "check", "."], LINT_TIMEOUT_SEC),
        (
            "빌드 (문법 컴파일)",
            [python, "-m", "compileall", "-q", "rag_pipeline", "backend", "tests"],
            BUILD_TIMEOUT_SEC,
        ),
        (
            "빌드 (앱 임포트)",
            [python, "-c", "import backend.app.main"],
            BUILD_TIMEOUT_SEC,
        ),
        ("테스트 (pytest)", [python, "-m", "pytest", "-q"], TEST_TIMEOUT_SEC),
    ]

    failures: list[str] = []
    for label, args, timeout in checks:
        ok, output = _run(args, timeout=timeout)
        if not ok:
            failures.append(f"### {label} 실패\n\n```\n{output or '(출력 없음)'}\n```")
            # 린트가 깨졌으면 뒤의 검사도 대개 같은 원인이다. 첫 실패에서 멈춰
            # 사용자가 하나씩 고치게 한다
            break

    if failures:
        _deny(
            "커밋 전 검사가 실패했습니다. 아래를 고친 뒤 다시 커밋하세요.\n\n"
            + "\n\n".join(failures)
            + "\n\n검사를 건너뛰려면 `git commit --no-verify` 를 쓰세요."
        )


if __name__ == "__main__":
    main()
