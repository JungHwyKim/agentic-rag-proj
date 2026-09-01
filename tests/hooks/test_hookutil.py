"""`_hookutil` 회귀 테스트 — 훅이 **조용히 무력화되는** 경우를 막는다.

이 모듈이 지키려는 것은 기능이 아니라 실패 모드다. 훅은 예외를 던지면 오히려 눈에 띄지만,
`git status` 옵션 하나가 빠지면 검사가 매번 통과하면서 아무 흔적도 안 남긴다. `_hookutil`
docstring 이 "단위 테스트로는 안 잡히고 실제 저장소 호출로만 드러났다"고 적어둔 것이 정확히
이 경우라, 여기서는 실제 git 저장소를 만들어 잰다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _hookutil
import pytest


# 새 디렉터리를 통째로 만들면 안의 .py 가 개별 경로로 보여야 한다.
# `-uall` 이 빠지면 "?? rag_pipeline/" 한 줄로 접혀 검사가 통과한다. 하필 제일 중요한
# 경우가 그렇다 — 새 파이프라인 단계를 통째로 만든 작업은 남길 게 제일 많다
def test_new_directory_is_listed_file_by_file(git_repo: Path, monkeypatch: pytest.MonkeyPatch):
    stage = git_repo / "rag_pipeline" / "ingestion" / "splitter"
    stage.mkdir(parents=True)
    (stage / "__init__.py").write_text("", encoding="utf-8")
    (stage / "splitter.py").write_text("# 청크 분할\n", encoding="utf-8")

    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", git_repo)
    paths = _hookutil.changed_paths()

    assert paths is not None
    assert "rag_pipeline/ingestion/splitter/splitter.py" in paths
    # 디렉터리 한 줄로 접히면 안 된다
    assert "rag_pipeline/" not in paths


# 한국어 경로가 8진 이스케이프 없이 그대로 나와야 한다.
# `-z` 가 빠지면 "task_log/\354\213\234..." 로 나와서 접두사 비교가 통째로 어긋난다
def test_korean_path_is_not_escaped(git_repo: Path, monkeypatch: pytest.MonkeyPatch):
    log_dir = git_repo / "task_log"
    log_dir.mkdir()
    (log_dir / "시행령-개정안.txt").write_text("기록\n", encoding="utf-8")

    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", git_repo)
    paths = _hookutil.changed_paths()

    assert paths is not None
    assert "task_log/시행령-개정안.txt" in paths
    # 따옴표로 감싸이거나 이스케이프되면 안 된다
    assert not any(p.startswith('"') or "\\3" in p for p in paths)


# BOM 이 붙은 입력도 읽혀야 한다.
#
# `read_payload()` 가 실패하면 모든 훅이 "판단 근거 없음" 경로로 빠져 **조용히 통과**한다.
# 훅이 죽지도 경고를 내지도 않아서 알아챌 방법이 없다. json.load 는 BOM 을 만나면
# "Unexpected UTF-8 BOM" 으로 죽고 그건 ValueError 하위라 그 경로로 그대로 들어간다.
#
# paid_test_check.py 가 이걸 먼저 겪어 자체 stdin 읽기를 utf-8-sig 로 박아뒀는데,
# 그 교훈이 공용부에는 안 와 있었다. auto_test_check.py 를 실제로 돌려보다 다시 걸렸다 —
# PowerShell 이 파이프에 BOM 을 붙여서, 훅이 0.1초 만에 exit 0 으로 끝나고 있었다.
@pytest.mark.parametrize("prefix", [b"", b"\xef\xbb\xbf"], ids=["BOM 없음", "BOM 있음"])
def test_read_payload_accepts_bom(prefix: bytes):
    hooks_dir = Path(_hookutil.__file__).parent
    snippet = (
        f"import sys; sys.path.insert(0, r'{hooks_dir}'); "
        "import _hookutil; print(_hookutil.read_payload())"
    )

    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        input=prefix + b'{"session_id": "abc"}',
        capture_output=True,
        timeout=30,
    )

    assert b"abc" in proc.stdout, f"payload 를 못 읽었다: {proc.stdout!r} {proc.stderr!r}"


# 판단이 서지 않으면 막지 않는다 — git 을 못 부르면 None 이어야 한다.
# 실제로 git 이 없는 환경을 만들 수 없으므로 호출 자체를 실패시킨다
def test_changed_paths_returns_none_when_git_unavailable(monkeypatch: pytest.MonkeyPatch):
    def _boom(*args, **kwargs):
        raise OSError("git 없음")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _hookutil.changed_paths() is None


# 세션당 한 번만 막는 보장. 이게 깨지면 종료코드 2 가 반복돼 세션이 영원히 안 끝난다
def test_claim_session_succeeds_only_once(marker_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", marker_root)

    assert _hookutil.claim_session("ns", "session-1") is True
    assert _hookutil.claim_session("ns", "session-1") is False
    # 다른 세션은 영향받지 않는다
    assert _hookutil.claim_session("ns", "session-2") is True


# is_marked 는 claim 이후에만 참이다
def test_is_marked_follows_claim(marker_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", marker_root)

    assert _hookutil.is_marked("ns", "session-1") is False
    _hookutil.claim_session("ns", "session-1")
    assert _hookutil.is_marked("ns", "session-1") is True


# 세션 id 가 파일명이 되므로 경로 문자가 걷혀야 한다. 마커가 .git 밖으로 새면 안 된다
def test_session_id_cannot_escape_marker_dir(marker_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", marker_root)

    assert _hookutil.claim_session("ns", "../../evil") is True

    created = list((marker_root / ".git" / "ns").iterdir())
    assert len(created) == 1
    assert created[0].parent == marker_root / ".git" / "ns"
    assert not (marker_root.parent / "evil").exists()


# 세션 id 가 비면 "한 번만 막기"를 보장할 수 없다 = 루프 위험. 막지 않는 쪽으로 간다
def test_empty_session_id_does_not_claim(marker_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", marker_root)
    assert _hookutil.claim_session("ns", "") is False
    assert _hookutil.is_marked("ns", "") is False


# 마커를 못 만들면 다음 Stop 에서 또 막게 되고 그게 무한 루프다. False 로 통과시킨다
def test_claim_session_returns_false_on_os_error(
    marker_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(_hookutil, "PROJECT_ROOT", marker_root)

    def _boom(*args, **kwargs):
        raise OSError("디스크 오류")

    monkeypatch.setattr(os, "open", _boom)
    assert _hookutil.claim_session("ns", "session-1") is False


# 자식 환경에 utf-8 을 못 박나. **갈아끼우지 않고 복제하는 것까지 함께 본다** —
# PATH 를 잃으면 훅이 .venv 를 못 찾아 검사가 통째로 안 돈다
def test_child_env_pins_utf8(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp949")

    env = _hookutil.child_env()

    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env.get("PATH") == os.environ.get("PATH")


# 값이 박히는 것과 **실제로 왕복하는 것**은 다르다. 한국어가 그대로 돌아와야 한다.
#
# 부모 환경이 이미 utf-8 이면 두 갈래가 똑같이 통과해 테스트가 아무것도 안 잰다. 그래서
# cp949 를 일부러 깔고 **안 박은 쪽이 깨지는 것까지** 단언한다 — 대조군 없이 성공만 재면
# 이 테스트는 `child_env()` 가 빈 dict 를 돌려줘도 통과한다
def test_child_env_makes_korean_readable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp949")
    code = "print('임계값이 분포의 틈에서 안 나온다')"
    kwargs = {"capture_output": True, "encoding": "utf-8", "errors": "replace"}

    without = subprocess.run([sys.executable, "-c", code], **kwargs)
    pinned = subprocess.run([sys.executable, "-c", code], env=_hookutil.child_env(), **kwargs)

    assert "�" in without.stdout
    assert "임계값이 분포의 틈에서 안 나온다" in pinned.stdout
