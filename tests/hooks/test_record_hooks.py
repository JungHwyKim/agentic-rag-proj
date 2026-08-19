"""기록 훅(`plan_log_check` · `task_log_check`)이 **무엇으로 썼는지에 상관없이** 기록을 알아보나.

이 파일이 지키는 실패 모드는 하나다 — 기록을 다 남긴 세션이 막히는 것. 두 훅은 "이번 세션에
기록했는가"를 PostToolUse 의 `tool_input.file_path` 로만 판정했는데 그 필드는 Write/Edit 에만
있다. 그래서 `plan_log/` 를 Bash(python heredoc)로 고친 세션이 회차·INDEX 를 다 남기고
커밋까지 하고도 막혔다(2026-08-19 실제 발생).

**오탐의 값이 이 검사를 정당화한다.** 일을 하고도 막히는 게이트는 `SKIP_*=1` 을 습관으로
만들어 검사 자체를 죽이므로, 무력화의 한 형태다.

동시에 **반대 방향도 지켜야 한다** — 지난 세션이 남긴 미커밋 기록이 이번 세션을 통과시키면
안 된다(2026-08-17 에 실제로 그 상태였다). 그래서 mtime 을 세션 시작 시각과 비교한다.
아래 두 방향을 모두 잰다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import _hookutil
import plan_log_check
import pytest
import task_log_check

SESSION = "sess-record-hook"


# 마커를 만들고 그 시각을 세션 시작으로 삼는다. 이후에 쓴 파일은 mtime 이 더 크다
def _start_session(root: Path, session_id: str = SESSION) -> None:
    _hookutil.mark_session_start(session_id)
    # 파일시스템 타임스탬프 해상도와 무관하게 "나중"이 보장되도록 한 틱 띄운다
    time.sleep(0.01)


# 훅 모듈들이 임시 저장소를 루트로 보게 만든다
@pytest.fixture
def hook_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", tmp_path)
    return tmp_path


# Bash 로 쓴 설계 기록도 기록으로 쳐야 한다. 이것이 2026-08-19 에 오탐을 낸 자리다
def test_plan_log_written_by_bash_is_recognised(hook_root: Path) -> None:
    _start_session(hook_root)
    (hook_root / "plan_log").mkdir()
    (hook_root / "plan_log" / "이미지-경로.md").write_text("3회차", encoding="utf-8")

    assert plan_log_check._recorded_this_session(SESSION, trust_mtime=True) is True


# 작업 기록 쪽도 같다
def test_task_log_written_by_bash_is_recognised(hook_root: Path) -> None:
    _start_session(hook_root)
    (hook_root / "task_log").mkdir()
    (hook_root / "task_log" / "2026-08-19-무언가.md").write_text("회고", encoding="utf-8")

    assert task_log_check._recorded_this_session(SESSION, trust_mtime=True) is True


# 지난 세션이 남긴 미커밋 기록은 통과시키면 안 된다.
# git status 판정이 정확히 여기서 무력화됐었다(2026-08-17, 미커밋 3건)
def test_stale_record_from_previous_session_does_not_pass(hook_root: Path) -> None:
    (hook_root / "plan_log").mkdir()
    stale = hook_root / "plan_log" / "지난세션.md"
    stale.write_text("지난 세션이 남긴 것", encoding="utf-8")
    # 세션은 그 파일이 쓰인 **뒤에** 시작한다
    _start_session(hook_root)

    assert plan_log_check._recorded_this_session(SESSION, trust_mtime=True) is False


# 아무것도 안 썼으면 당연히 False
def test_no_record_is_not_recognised(hook_root: Path) -> None:
    _start_session(hook_root)
    (hook_root / "plan_log").mkdir()

    assert plan_log_check._recorded_this_session(SESSION, trust_mtime=True) is False


# 기준 시각을 이번 호출이 방금 찍었으면 mtime 으로 판정할 수 없다.
# 모든 파일이 그보다 옛것이 되므로, 종전대로 마커만 본다
def test_fresh_baseline_is_not_trusted(hook_root: Path) -> None:
    _start_session(hook_root)
    (hook_root / "plan_log").mkdir()
    (hook_root / "plan_log" / "새기록.md").write_text("회차", encoding="utf-8")

    assert plan_log_check._recorded_this_session(SESSION, trust_mtime=False) is False


# 마커가 있으면 mtime 을 안 봐도 통과한다 (Write/Edit 로 쓴 종전 경로)
def test_marker_alone_still_passes(hook_root: Path) -> None:
    _hookutil.claim_session(plan_log_check.WRITTEN_NS, SESSION)

    assert plan_log_check._recorded_this_session(SESSION, trust_mtime=False) is True


# 세션 시작 표시는 처음 만든 쪽만 True 를 받는다.
# 두 훅이 같은 네임스페이스를 공유하므로, 먼저 도는 쪽이 기준을 정한다
def test_session_start_is_claimed_once(hook_root: Path) -> None:
    assert _hookutil.mark_session_start("sess-once") is True
    assert _hookutil.mark_session_start("sess-once") is False


# 기준 시각이 없으면 판단할 수 없다는 뜻으로 None
def test_wrote_under_without_baseline_is_unknown(hook_root: Path) -> None:
    (hook_root / "plan_log").mkdir()

    assert _hookutil.wrote_under("plan_log/", "sess-no-baseline") is None


# 실제 훅을 서브프로세스로 돌려, Bash 로만 기록한 세션이 Stop 에서 안 막히는지 본다.
# 위 단위 테스트는 헬퍼를 직접 부르므로 main() 의 배선(기준 시각 찍기·인자 전달)은 못 잰다
def test_stop_does_not_block_when_record_written_outside_write_tool(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "plan_log").mkdir()

    hook = Path(_hookutil.__file__).parent / "plan_log_check.py"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "PYTHONIOENCODING": "utf-8"}

    def run(payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps(payload),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    # 훅은 자기 파일 위치로 PROJECT_ROOT 를 정하므로 서브프로세스에서는 실제 저장소를 본다.
    # 그래서 여기서는 "설계 세션 표시가 없으면 Stop 이 통과한다"만 확인한다 —
    # 배선이 깨져 예외로 죽으면 종료코드가 0 이 아니게 되어 잡힌다
    result = run(
        {
            "hook_event_name": "Stop",
            "session_id": "sess-wiring-check",
        }
    )

    assert result.returncode == 0, result.stderr
