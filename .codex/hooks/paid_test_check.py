"""유료 API 를 실제로 호출하는 테스트 실행을 막는 PreToolUse 훅.

`-m integration` 테스트는 LlamaParse/OpenAI 에 **실제 과금**을 일으킨다. 이 저장소는
그 사실을 CLAUDE.md, `rag-code-review`·`plan-log`·`task-log` 스킬, `code-reviewer`
에이전트까지 다섯 곳에 적어뒀지만 전부 모델을 향한 **설득**이다. 반면 그보다 덜
치명적인 "커밋 전 검사"는 `pre_commit_check.py` 로 하네스에 못 박혀 있었다. 이 훅은
그 비대칭을 없앤다 — 제일 비싼 실수를 제일 약한 장치로 막고 있던 상태를 뒤집는다.

`pyproject.toml` 의 `addopts = "-m 'not integration'"` 이 1차 방어선이지만 그건
**기본값**일 뿐이라 명령줄로 얼마든지 뒤집힌다. 그래서 뒤집는 두 경로를 다 본다.

  1. `-m` 마커 표현식이 integration 을 고르는 경우
  2. `-o addopts=...` / `--override-ini addopts=...` 로 기본 제외를 통째로 날리는 경우

**오탐을 줄이는 게 이 훅의 제일 어려운 부분이다.** `git commit -m "integration 테스트
추가"` 같은 명령에도 `-m` 과 `integration` 이 둘 다 들어 있다. 그래서 명령을 세그먼트로
자르고 **첫 토큰이 실제 pytest 실행기인 세그먼트에서만** 마커를 본다. 반대로
`-m "not integration"`(= 기본 안전 호출)을 막아버리면 훅이 쓸모없어지므로 부정도 판다.

`--collect-only` 는 수집만 하고 테스트를 돌리지 않아 과금이 없다. 막지 않는다.

`pre_commit_check.py` 의 규약을 그대로 따른다 — 훅 입력을 못 읽으면 통과시키고,
사유는 stdout 에 UTF-8 로 낸다. 탈출구는 `ALLOW_PAID_TESTS=1` 이다.
"""

from __future__ import annotations

import json
import os
import re
import sys

# 명령을 세그먼트로 자르는 구분자. `git add -A && pytest -m integration` 처럼 붙은 명령에서
# 뒤쪽만 걸러내기 위한 것이다
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")

# `-m <값>` 을 찾는다. `-m integration`, `-m=integration`, `-mintegration`, 따옴표 형태를
# 모두 잡는다. 앞의 (?<![\w-]) 는 `--m` 이나 `-xm` 에 걸리지 않게 한다
_MARKER_RE = re.compile(r"(?<![\w-])-m[\s=]*(\"[^\"]*\"|'[^']*'|[^\s]+)")

# `not integration` / `not (integration)` — 부정은 integration 을 **빼는** 표현이다
_NOT_INTEGRATION_RE = re.compile(r"\bnot\s*\(?\s*integration\s*\)?", re.IGNORECASE)

# addopts 를 명령줄에서 덮어쓰는 형태. 기본 제외(`-m 'not integration'`)가 날아간다
_ADDOPTS_OVERRIDE_RE = re.compile(
    r"(?<![\w-])(?:-o|--override-ini)[\s=]*(\"[^\"]*\"|'[^']*'|[^\s]+)"
)

# 수집만 하는 호출은 테스트를 돌리지 않으므로 과금이 없다
_COLLECT_ONLY_RE = re.compile(r"(?<![\w-])--(?:collect-only|co)\b")

# `-m pytest` 로 부르는 인터프리터. 이 형태면 세그먼트 안에 `-m pytest` 가 있어야 pytest 다
_PYTHON_RE = re.compile(r"^python[\d.]*(?:\.exe)?$", re.IGNORECASE)

# pytest 실행기 자체
_PYTEST_RE = re.compile(r"^py\.?test(?:\.exe)?$", re.IGNORECASE)

# pytest 를 대신 띄우는 러너들. 세그먼트 어딘가에 pytest 라는 말이 있어야 한다
_RUNNERS = frozenset({"uv", "uvx", "poetry", "hatch", "pdm", "rye", "nox", "tox"})

# 인터프리터가 pytest 를 모듈로 부르는 형태
_DASH_M_PYTEST_RE = re.compile(r"(?<![\w-])-m[\s=]*[\"']?pytest[\"']?\b")

# 러너 세그먼트에서 pytest 라는 말을 찾는다
_PYTEST_WORD_RE = re.compile(r"\bpy\.?test\b", re.IGNORECASE)

# 실행기가 변수에 담긴 형태(`& $py -m pytest`, `$env:X`). 정적으로는 무엇인지 알 수 없다
_VARIABLE_RE = re.compile(r"^[$%]\{?[\w:]")


# 따옴표를 벗긴다. `"not slow"` -> `not slow`
def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


# 경로·따옴표를 걷어낸 실행 파일 이름. `.\.venv\Scripts\python.exe` -> `python.exe`
def _basename(token: str) -> str:
    token = _unquote(token.strip())
    for sep in ("/", "\\"):
        token = token.rsplit(sep, 1)[-1]
    return token


# 이 세그먼트가 pytest 를 실행하나. 첫 토큰만 보므로 `git commit -m "..."` 에 안 걸린다
def _runs_pytest(segment: str) -> bool:
    tokens = segment.split()
    # 실행기 앞에 붙는 것들을 걷어낸다.
    #   `&`      PowerShell 호출 연산자. 이 저장소의 명령 예시가 전부 이 형태라
    #            (`& $py -m pytest ...`) 안 걷어내면 제일 흔한 호출이 그대로 샌다
    #   `FOO=1`  환경변수 지정
    while tokens and (tokens[0] == "&" or ("=" in tokens[0] and not tokens[0].startswith("-"))):
        tokens = tokens[1:]
    if not tokens:
        return False

    head = _basename(tokens[0])

    if _PYTEST_RE.match(head):
        return True
    if _PYTHON_RE.match(head):
        return bool(_DASH_M_PYTEST_RE.search(segment))
    if head.lower() in _RUNNERS:
        return bool(_PYTEST_WORD_RE.search(segment))
    if _VARIABLE_RE.match(head):
        # 인터프리터 경로가 변수에 담겨 있으면(`& $py -m pytest ...`) 무엇을 부르는지
        # 정적으로 알 수 없다. 세그먼트에 pytest 라는 말이 있으면 pytest 로 본다.
        # 첫 토큰이 변수인 명령이 커밋 메시지일 리는 없으므로 오탐 위험이 낮다
        return bool(_PYTEST_WORD_RE.search(segment))
    return False


# 이 마커 표현식이 integration 을 고르나. `not integration` 은 반대로 빼는 것이라 아니다
def _selects_integration(expr: str) -> bool:
    # 부정을 먼저 지운다. 이걸 안 하면 pyproject 기본값과 같은 안전한 호출까지 막혀서
    # 훅이 통째로 무시당하게 된다
    remaining = _NOT_INTEGRATION_RE.sub(" ", expr)
    return "integration" in remaining.lower()


# 이 세그먼트가 유료 테스트를 돌리나. 돌린다면 사유 한 줄, 아니면 None
def _paid_reason(segment: str) -> str | None:
    if not _runs_pytest(segment):
        return None

    if _COLLECT_ONLY_RE.search(segment):
        # 수집만 한다. 테스트 본문이 안 돌므로 외부 호출도 없다
        return None

    for raw in _MARKER_RE.findall(segment):
        value = _unquote(raw)
        # `python -m pytest` 의 앞쪽 -m 은 모듈 이름이라 마커가 아니다
        if value.lower() in {"pytest", "py.test"}:
            continue
        if _selects_integration(value):
            return f"마커 표현식 `-m {value}` 가 integration 테스트를 선택합니다."

    for raw in _ADDOPTS_OVERRIDE_RE.findall(segment):
        value = _unquote(raw)
        if not value.lower().startswith("addopts"):
            continue
        # 덮어쓴 값이 스스로 integration 을 빼고 있으면 괜찮다
        if _NOT_INTEGRATION_RE.search(value):
            continue
        return (
            f"`-o {value}` 가 pyproject 의 addopts 를 덮어씁니다. "
            "기본 제외(`-m 'not integration'`)가 사라져 유료 테스트까지 돌아갑니다."
        )

    return None


# 명령 전체를 보고 막아야 할 사유. 막을 이유가 없으면 None.
# 세그먼트로 자르는 것이 판정의 일부라 main() 이 아니라 여기에 둔다 — 테스트가 이 함수를
# 부르면 실제 실행 경로와 같은 것을 검사하게 된다
def blocked_reason(command: str) -> str | None:
    for segment in _SEGMENT_SPLIT_RE.split(command):
        reason = _paid_reason(segment)
        if reason:
            return reason
    return None


# 실행을 막고 사유를 모델에게 넘긴다
def _deny(detail: str) -> None:
    # Windows 콘솔 기본 인코딩(cp949)으로는 한국어 사유를 못 내보내 UnicodeEncodeError 로
    # 훅이 죽는다. 실패 경로에서만 드러나므로 여기서 못 박는다
    sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "유료 API 를 호출하는 테스트라 막았습니다.\n\n"
                        f"{detail}\n\n"
                        "integration 마커 테스트는 LlamaParse/OpenAI 에 실제 과금이 "
                        "발생합니다. 기본 검증은 `pytest -q` 로 충분합니다.\n"
                        "정말 돌려야 하면 **사용자에게 허락을 구하고** "
                        "`ALLOW_PAID_TESTS=1` 을 설정한 뒤 다시 실행하세요."
                    ),
                },
                "systemMessage": "유료 테스트 실행을 막았습니다.",
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


# 훅 진입점. stdin 으로 받은 도구 호출을 보고 유료 테스트일 때만 막는다
def main() -> None:
    # 한국어가 담겨 오므로 stdin 을 UTF-8 로 못 박는다. cp949 로 읽으면 UnicodeDecodeError
    # 가 나는데 이건 ValueError 하위라 아래 except 에 조용히 먹히고 검사가 무력화된다.
    # utf-8 이 아니라 utf-8-sig 인 이유: BOM 이 붙어 오면 json.load 가 실패해 같은 경로로
    # 조용히 통과한다. 실제로 이 훅을 검증하다 겪었다(PowerShell 5.1 의 `Out-File -Encoding
    # utf8` 은 BOM 을 붙인다). BOM 이 없는 입력에는 utf-8 과 완전히 같게 동작한다
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # 판단 근거가 없다. 막지 않는다 — `_hookutil` 의 공통 규약과 같다
        return

    # 탈출구: 사용자가 과금을 승인하고 의도적으로 돌릴 때
    if os.environ.get("ALLOW_PAID_TESTS"):
        return

    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return

    reason = blocked_reason(command)
    if reason:
        _deny(reason)


if __name__ == "__main__":
    main()
