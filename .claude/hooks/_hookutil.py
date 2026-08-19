"""훅들이 공유하는 최소 도구 모음.

`task_log_check.py`(구현 후 회고) · `plan_log_check.py`(설계 논의) · `auto_test_check.py`
(자동 테스트)는 트리거가 다 다르지만 바닥에서 하는 일은 같다 — 훅 입력을 읽고, 무엇이
바뀌었는지 보고, 세션당 한 번만 막는다.

**이 모듈이 존재하는 진짜 이유는 조용히 무력화되는 함정들이다.** 아래 `changed_paths()` 의
`-z` 와 `-uall` 은 둘 다 없으면 검사가 소리 없이 통과한다. 실제로 `-uall` 누락은 구현 중에
한 번 겪었고(새 디렉터리를 통째로 만들면 "?? rag_pipeline/" 한 줄로 접혀 안의 .py 가 안
보인다) 단위 테스트로는 안 잡혀 실제 저장소 호출로만 드러났다. `read_payload()` 의
utf-8-sig 도 같은 종류다. 같은 코드가 훅마다 복사되면 한쪽만 고쳐져 그 버그가 되살아난다 —
BOM 교훈이 실제로 `paid_test_check.py` 에만 있다가 `auto_test_check.py` 에서 다시 걸렸다.
그래서 이런 교훈이 한 곳에만 있게 한다. `relative_to_root()` 를 여기 둔 것도 그래서다.

공통 규약: **판단이 서지 않으면 막지 않는다.** 기록 하나를 놓치는 것보다 사용자를 끝나지
않는 세션에 가두는 쪽이 훨씬 나쁘다. 그래서 모든 실패 경로가 None/False 를 돌려준다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# .claude/hooks/_hookutil.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]

GIT_TIMEOUT_SEC = 20


# stdin 으로 온 훅 입력. 못 읽으면 None (막지 않는다)
def read_payload() -> dict | None:
    # 훅 입력에 한국어 경로·메시지가 담겨 오므로 UTF-8 로 못 박는다. cp949 로 읽으면
    # UnicodeDecodeError 가 나는데 이건 ValueError 하위라 아래 except 에 조용히 먹히고
    # 검사가 통째로 무력화된다.
    #
    # utf-8 이 아니라 **utf-8-sig** 인 이유: BOM 이 붙어 오면 json.load 가
    # "Unexpected UTF-8 BOM" 으로 죽고, 그것도 ValueError 하위라 같은 경로로 조용히
    # 통과한다. PowerShell 이 파이프에 BOM 을 붙이는 일이 실제로 있다 —
    # paid_test_check.py 가 먼저 겪어 자체 stdin 읽기에 utf-8-sig 를 박아뒀는데,
    # 그 교훈이 공용부에는 안 와 있어서 auto_test_check.py 를 검증하다 다시 걸렸다.
    # utf-8-sig 는 BOM 이 없는 입력도 그대로 읽으므로 잃는 것이 없다
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# git 이 보고하는 변경 경로 목록. 판단할 수 없으면 None
def changed_paths() -> list[str] | None:
    try:
        proc = subprocess.run(
            # 옵션 두 개가 다 필수다. 하나라도 빠지면 검사가 조용히 통과한다.
            #
            # -z     기본 --porcelain 은 ASCII 밖 경로를
            #        "task_log/routing-diff-\354\213\234..." 처럼 따옴표+8진 이스케이프로
            #        내보낸다. 이 저장소는 한국어 파일명이 흔해서, 그대로 접두사 비교를
            #        하면 기록 파일을 못 알아본다
            #
            # -uall  기본값은 통째로 untracked 인 디렉터리를 "?? rag_pipeline/" 한 줄로
            #        접는다. 그러면 안의 .py 가 안 보여서 검사가 통과한다. 하필 제일
            #        중요한 경우가 그렇다 — 새 파이프라인 단계 디렉터리를 통째로 새로
            #        만든 작업은 남길 게 제일 많은데 검사가 안 걸린다
            ["git", "status", "--porcelain", "-uall", "-z"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            timeout=GIT_TIMEOUT_SEC,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if proc.returncode != 0:
        return None

    paths: list[str] = []
    for chunk in proc.stdout.split(b"\0"):
        if not chunk:
            continue
        # 항목은 "XY <경로>" 형태다. 이름이 바뀐 항목(R/C)은 원래 경로가 다음 레코드에
        # 접두사 없이 따라오는데, 그것도 실제 경로이므로 그대로 받는다
        entry = chunk[3:] if chunk[2:3] == b" " else chunk
        paths.append(entry.decode("utf-8", "replace"))
    return paths


# 절대·상대 경로를 저장소 기준 posix 경로로 바꾼다. 저장소 밖이면 None
def relative_to_root(raw: str) -> str | None:
    # 훅 입력의 file_path 는 절대 경로로 온다. 접두사 비교를 하려면 먼저 저장소 기준으로
    # 낮춰야 한다. 저장소 밖(스크래치패드 등)은 우리가 볼 것이 아니라 None 이다
    if not raw:
        return None
    try:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        rel = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except (OSError, ValueError):
        return None
    return rel.as_posix()


# 세션 마커 파일 경로. 세션 id 를 못 쓰면 None
def _marker_path(namespace: str, session_id: str) -> Path | None:
    # 세션 id 가 그대로 파일명이 되므로 경로 문자를 걷어낸다
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
    if not safe:
        return None
    # .git/ 안에 둔다. git 이 관리하지 않는 영역이라 따로 gitignore 할 필요가 없다
    return PROJECT_ROOT / ".git" / namespace / safe


# 이 세션에서 마커를 처음 만들면 True, 이미 있으면 False
def claim_session(namespace: str, session_id: str) -> bool:
    marker = _marker_path(namespace, session_id)
    if marker is None:
        return False

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL 로 "만들면서 동시에 검사"한다. 존재 확인 후 생성으로 나누면 훅이 겹쳐
        # 돌 때 둘 다 통과해버릴 수 있다
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError:
        # 마커를 못 남기면 다음 Stop 에서 또 막게 되고 그게 무한 루프다. 막지 않는다
        return False

    os.close(fd)
    return True


# 이 세션에 마커가 있나
def is_marked(namespace: str, session_id: str) -> bool:
    marker = _marker_path(namespace, session_id)
    return marker is not None and marker.exists()


# 세션이 시작된 시각을 남기는 마커. 그 세션에서 처음 도는 훅이 찍고 나머지는 읽기만 한다.
#
# **이것이 필요한 이유는 "누가 썼는가"로 판정하면 구멍이 난다는 것이다.** 기록 훅들은
# "이번 세션에 기록했는가"를 PostToolUse 의 `tool_input.file_path` 로만 봤는데, 그 필드는
# Write/Edit 에만 있다. Bash 로 쓴 기록은 **아무리 잘 써도 안 보인다** — 2026-08-19 에
# plan_log/ 를 python heredoc 으로 고친 세션이 회차·INDEX 를 다 남기고 커밋까지 하고도
# 막혔다. 도구를 Write/Edit 로 쓰라고 지시해서 고칠 수는 없다. 모델에게 시키는 일은
# 지켜질 때도 있고 아닐 때도 있다는 것이 이 장치 전체의 전제이기 때문이다.
#
# 그래서 **도구가 아니라 파일이 실제로 바뀌었는가**를 본다. 그러려면 "언제부터"가 필요하고,
# 그 기준이 이 마커다. 어느 훅이 먼저 돌든 상관없도록 네임스페이스를 공유한다
SESSION_START_NS = "claude_session_started"


# 세션 시작 표시를 남긴다. 이번 호출이 처음 만든 것이면 True.
#
# True 면 **기준 시각으로 쓸 수 없다** — 방금 찍었으니 모든 파일이 그보다 옛것이 된다.
# 부르는 쪽이 이 값을 보고 mtime 판정을 건너뛴다
def mark_session_start(session_id: str) -> bool:
    return claim_session(SESSION_START_NS, session_id)


# 세션 시작 시각(epoch). 표시가 없거나 못 읽으면 None
def session_started_at(session_id: str) -> float | None:
    marker = _marker_path(SESSION_START_NS, session_id)
    if marker is None:
        return None
    try:
        return marker.stat().st_mtime
    except OSError:
        return None


# 세션이 시작된 뒤 <prefix> 아래 파일이 하나라도 새로 쓰였나. 판단이 안 서면 None.
#
# **`changed_paths()`(git)로 하지 않는 이유가 이 함수의 요점이다.** git status 는 세션을
# 구분하지 못해서, 지난 세션이 남긴 미커밋 기록이 이번 세션을 그냥 통과시킨다 —
# 2026-08-17 에 실제로 그 상태였고(미커밋 3건), 기록이 밀릴수록 검사가 더 헐거워지는
# 정반대 방향이었다. 세션 시작 시각과 비교하면 그 구멍이 안 생긴다. 커밋은 작업트리의
# mtime 을 건드리지 않으므로 방금 커밋한 기록도 그대로 잡힌다
def wrote_under(prefix: str, session_id: str) -> bool | None:
    started = session_started_at(session_id)
    if started is None:
        return None

    root = PROJECT_ROOT / prefix
    if not root.is_dir():
        return False

    try:
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_mtime > started:
                return True
    except OSError:
        return None
    return False


# 마커의 mtime. 없거나 못 읽으면 None. 세션 안에서 "마지막으로 ~한 시각"을 담는 데 쓴다
def marker_mtime(namespace: str, session_id: str) -> float | None:
    marker = _marker_path(namespace, session_id)
    if marker is None:
        return None
    try:
        return marker.stat().st_mtime
    except OSError:
        return None


# 마커를 만들거나 mtime 을 지금으로 갱신한다. 실패해도 조용히 넘어간다
def touch_marker(namespace: str, session_id: str) -> None:
    marker = _marker_path(namespace, session_id)
    if marker is None:
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return


# 기준 시각 이후에 바뀐 파일들의 저장소 기준 경로. 판단이 안 서면 None.
#
# `changed_paths()`(git)와 쓰임이 다르다. 저쪽은 "커밋 안 된 것"을 묻고 이쪽은 "이 시각
# 이후에 손댄 것"을 묻는다. 세션 안에서 방금 무엇을 고쳤는지 보려면 이쪽이어야 한다
def paths_newer_than(prefixes: tuple[str, ...], since: float) -> list[str] | None:
    found: list[str] = []
    try:
        for prefix in prefixes:
            root = PROJECT_ROOT / prefix
            if not root.is_dir():
                continue
            for path in root.rglob("*.py"):
                if path.is_file() and path.stat().st_mtime > since:
                    found.append(path.relative_to(PROJECT_ROOT).as_posix())
    except OSError:
        return None
    return found


# 종료를 막고 사유를 모델에게 넘긴다. 이 함수는 돌아오지 않는다
def block(reason: str) -> None:
    # Windows 콘솔 기본 인코딩(cp949)으로는 한국어 사유를 못 내보내 UnicodeEncodeError 로
    # 훅이 죽는다. 실패 경로에서만 드러나므로 여기서 못 박는다
    sys.stderr.reconfigure(encoding="utf-8")
    print(reason, file=sys.stderr)
    # Stop 훅에서 종료코드 2 는 "멈추지 말고 대화를 이어가라"는 뜻이고, stderr 가 모델에게
    # 전달된다
    sys.exit(2)
