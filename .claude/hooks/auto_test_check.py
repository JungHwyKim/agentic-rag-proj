"""코드를 고치면 테스트를 자동으로 돌리는 훅. 두 이벤트를 한 스크립트가 처리한다.

  PostToolUse (Write|Edit)  고친 파일이 속한 **영역의 테스트만** 돌린다. 빠른 되먹임용
  Stop                      **전체 pytest** 를 세션당 한 번 돌려 종료 전에 보증한다

`pre_commit_check.py` 가 이미 커밋 순간에 전체를 돌리지만, 거기까지 가는 동안 깨진 걸
모른다. 커밋 시점에 한꺼번에 터지면 어느 변경이 깼는지 되짚어야 한다. 이 훅은 그 간격을
메운다.

**왜 PostToolUse 인가 (PreToolUse 가 아니라).** 취향이 아니라 강제다.
`tests/hooks/test_hook_wiring.py:test_pretooluse_covers_both_shells` 가 PreToolUse 의 *모든*
matcher 에 `re.fullmatch(matcher, "Bash")` 와 `"PowerShell"` 을 요구한다. matcher 를
"Write|Edit" 로 둔 PreToolUse 그룹을 추가하면 그 테스트가 깨지고, 그러면 pre_commit_check 가
커밋 자체를 막는다. 그 테스트는 옳다 — PreToolUse matcher 가 한쪽 셸만 덮으면 도구를 바꾸는
것만으로 검사가 통째로 우회되기 때문이다. 그래서 이쪽이 비켜간다.

**왜 영역별로 가르나.** 실측(2026-08-17): 전체 234개가 18.3초인데 영역별은
cleaner 6.7초 / loader-document 5.3초 / loader-content 3.1초 / hooks 0.6초 / api 0.6초다.
한 작업에서 파일을 대여섯 번 고치면 그 차이가 그대로 쌓인다.

**왜 전체 게이트가 따로 필요한가.** 영역별은 그 영역만 본다. `config.py`·`schemas/` 처럼
전 영역에 영향을 주는 변경은 부분 실행이 아예 무의미해서 매핑하지 않았고(UNMAPPED_BY_DESIGN),
그런 변경은 Stop 게이트가 전체로 잡는다.

integration 마커 테스트는 절대 돌리지 않는다. pyproject 의 addopts = "-m 'not integration'"
이 기본 제외하며 여기서 그 값을 덮지 않는다. LlamaParse/OpenAI 실제 과금을 훅이 몰래
일으키면 안 된다.

공통 규약은 다른 훅과 같다 — **판단이 서지 않으면 막지 않는다.** 매핑에 없는 경로, 못 읽은
입력, 세션 id 없음은 전부 조용히 통과시킨다.
"""

from __future__ import annotations

import os
import subprocess
import sys

import _hookutil

PROJECT_ROOT = _hookutil.PROJECT_ROOT

# 실패 출력이 길면 모델 컨텍스트를 잡아먹는다
MAX_OUTPUT_CHARS = 4000

# settings.json 의 timeout 보다 짧게 잡아, 훅이 강제 종료되는 대신 스스로 사유를 남기게 한다
SCOPED_TIMEOUT_SEC = 90
FULL_TIMEOUT_SEC = 540

# Stop 게이트가 세션당 한 번만 막게 하는 마커 이름
GATE_NS = "claude_autotest_gate"

# 이 아래 .py 가 바뀌면 전체 게이트 대상으로 본다
SOURCE_PREFIXES = ("rag_pipeline/", "backend/", ".claude/hooks/")

# 경로 -> 테스트 대상. **최장 접두사가 이긴다** (loader/document 가 loader 보다 먼저).
# 새 파이프라인 단계를 만들면 여기 한 줄을 추가한다. 잊으면 그 단계만 자동 테스트가 한 번도
# 안 도는데 아무 에러도 안 나므로, tests/hooks/test_auto_test_check.py 가 이걸 검사한다
_MAPPINGS: tuple[tuple[str, str], ...] = (
    ("rag_pipeline/ingestion/loader/document/", "tests/ingestion/loader/document"),
    ("rag_pipeline/ingestion/loader/content/", "tests/ingestion/loader/content"),
    ("rag_pipeline/ingestion/loader/", "tests/ingestion/loader"),
    ("rag_pipeline/ingestion/cleaner/", "tests/ingestion/cleaner"),
    ("rag_pipeline/ingestion/splitter/", "tests/ingestion/splitter"),
    ("rag_pipeline/ingestion/pipeline.py", "tests/ingestion/test_pipeline.py"),
    ("backend/", "tests/api"),
    (".claude/hooks/", "tests/hooks"),
)

# **일부러 매핑하지 않는 것.** 여기 있는 경로는 전 영역에 영향을 주므로 부분 실행이
# 무의미하다. 조용히 빠지는 게 아니라 "빠뜨린 게 아님"을 명시하는 자리다 —
# test_hook_wiring.py 의 LIBRARY_MODULES 와 같은 역할이고, 커버리지 테스트가 이 집합을 뺀
# 나머지를 검사한다. 이 변경들은 Stop 게이트가 전체 pytest 로 잡는다
UNMAPPED_BY_DESIGN: tuple[str, ...] = (
    "rag_pipeline/config.py",
    "rag_pipeline/exceptions.py",
    "rag_pipeline/schemas/",
    "tests/conftest.py",
)


# 이 경로를 고쳤을 때 돌릴 테스트 대상. 없으면 None (조용히 통과)
def test_target_for(raw_path: str) -> str | None:
    rel = _hookutil.relative_to_root(raw_path)
    if rel is None or not rel.endswith(".py"):
        return None

    # 일부러 매핑 안 한 것이 먼저다. 아래 접두사 표에 걸리기 전에 걸러낸다
    if any(rel == item or rel.startswith(item) for item in UNMAPPED_BY_DESIGN):
        return None

    # 테스트 파일을 직접 고쳤으면 그 파일만 돌린다. 제일 좁고 제일 빠르다.
    # conftest 처럼 test_ 로 시작하지 않는 것은 영향 범위가 넓어 전체 게이트로 넘긴다
    if rel.startswith("tests/"):
        name = rel.rsplit("/", 1)[-1]
        return rel if name.startswith("test_") else None

    # 최장 접두사가 이긴다
    best: tuple[str, str] | None = None
    for prefix, target in _MAPPINGS:
        matches = rel == prefix or rel.startswith(prefix)
        if matches and (best is None or len(prefix) > len(best[0])):
            best = (prefix, target)
    return best[1] if best else None


# 프로젝트 .venv 의 파이썬. 없으면 이 스크립트를 돌린 파이썬
def _venv_python() -> str:
    for candidate in (
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
        PROJECT_ROOT / ".venv" / "bin" / "python",  # POSIX
    ):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


# pytest 를 돌리고 (성공여부, 표시용 출력) 을 돌려준다
def _run_pytest(args: list[str], *, timeout: int) -> tuple[bool, str]:
    command = [_venv_python(), "-m", "pytest", *args, "-q"]
    try:
        proc = subprocess.run(
            command,
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
        return False, f"{timeout}초 안에 끝나지 않아 중단했습니다."
    except OSError as exc:
        return False, f"실행할 수 없습니다 ({exc})."

    output = (proc.stdout + proc.stderr).strip()
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n... (출력 생략)"
    return proc.returncode == 0, output


# PostToolUse — 고친 파일의 영역 테스트만 돌린다. 사용자를 막지 않고 모델에게 되먹인다
def _handle_post_tool_use(payload: dict) -> None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return

    target = test_target_for(str(tool_input.get("file_path") or ""))
    if target is None:
        return

    if not (PROJECT_ROOT / target).exists():
        # 매핑은 있는데 대상이 없다. 새 단계를 만드는 중일 수 있으니 막지 않는다
        return

    ok, output = _run_pytest([target], timeout=SCOPED_TIMEOUT_SEC)
    if ok:
        return

    # PostToolUse 에서 종료코드 2 는 도구를 되돌리는 게 아니다(이미 실행됐다).
    # stderr 가 모델에게 전달돼 방금 만든 실패를 바로 고치게 한다
    _hookutil.block(
        f"방금 고친 곳의 테스트가 실패했습니다: {target}\n\n"
        f"```\n{output or '(출력 없음)'}\n```\n\n"
        "지금 고치는 편이 커밋 직전에 한꺼번에 마주치는 것보다 쉽습니다.\n"
        "이 검사를 끄려면 SKIP_AUTOTEST=1 을 쓰세요."
    )


# Stop — 소스를 고친 세션이 전체 테스트를 통과하지 않은 채 끝나는 것을 막는다
def _handle_stop(payload: dict) -> None:
    # 공식 문서의 Stop 입력 필드 목록에는 없지만, 들어온다면 존중한다.
    # 마커가 주 안전장치이고 이건 덤이다
    if payload.get("stop_hook_active"):
        return

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # 세션을 못 가리면 "한 번만 막기"를 보장할 수 없다 = 무한 루프 위험
        return

    paths = _hookutil.changed_paths()
    if paths is None:
        return

    if not any(p.startswith(SOURCE_PREFIXES) and p.endswith(".py") for p in paths):
        return

    # 실제로 돌리기 **전에** 마커를 잡는다. 테스트가 오래 걸리거나 훅이 중간에 죽어도
    # 다음 Stop 에서 또 돌지 않게 하기 위해서다. 루프를 막는 쪽이 우선이다
    if not _hookutil.claim_session(GATE_NS, session_id):
        return

    ok, output = _run_pytest([], timeout=FULL_TIMEOUT_SEC)
    if ok:
        return

    _hookutil.block(
        "전체 테스트가 실패한 채로 끝나려 합니다.\n\n"
        f"```\n{output or '(출력 없음)'}\n```\n\n"
        "고치고 마치거나, 지금 고칠 수 없는 이유를 사용자에게 알리세요.\n"
        "건너뛰려면 SKIP_AUTOTEST=1 을 쓸 수 있습니다.\n"
        "(이 검사는 세션당 한 번만 막습니다)"
    )


# 훅 진입점. 한 스크립트가 두 이벤트를 처리하므로 이벤트 이름으로 가른다
def main() -> None:
    payload = _hookutil.read_payload()
    if payload is None:
        return

    # 탈출구: 사용자가 의도적으로 자동 테스트를 끄고 싶을 때
    if os.environ.get("SKIP_AUTOTEST"):
        return

    event = str(payload.get("hook_event_name") or "")
    if event == "PostToolUse":
        _handle_post_tool_use(payload)
    elif event == "Stop":
        _handle_stop(payload)


if __name__ == "__main__":
    main()
