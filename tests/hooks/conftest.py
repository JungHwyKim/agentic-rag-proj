"""훅 테스트 공용 픽스처.

훅은 `.claude/hooks/` 에 있어 패키지가 아니다. 실제 실행도 파일 경로로 직접 호출되므로
(`settings.json` 의 command), 테스트도 그 디렉터리를 `sys.path` 에 얹어 같은 방식으로
임포트한다. `task_log_check.py` 와 `plan_log_check.py` 가 `import _hookutil` 로 평평하게
부르기 때문에 이 경로가 없으면 임포트 자체가 안 된다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# tests/hooks/conftest.py -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]

HOOKS_DIR = PROJECT_ROOT / ".claude" / "hooks"

# 모듈 수준에서 얹는다. conftest 는 같은 폴더의 테스트 모듈보다 먼저 임포트되므로
# 이 시점이면 테스트가 `import _hookutil` 하기 전이 보장된다
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))


# 비어 있는 임시 git 저장소. `changed_paths()` 를 실제 git 으로 재기 위한 것이다.
#
# 이 픽스처가 fake 가 아니라 진짜 저장소인 이유가 있다. `_hookutil` 의 `-uall`/`-z` 누락은
# git 의 실제 출력 형태로만 드러나서, subprocess 를 모킹하면 그 버그가 그대로 통과한다.
# 실제로 구현 중에 그렇게 놓친 적이 있다(_hookutil 모듈 docstring 참조).
#
# git 이 없는 환경에서도 기본 테스트는 통과해야 하므로 그때는 조용히 skip 한다 —
# tests/fixtures/ 가 없을 때 실파일 회귀 테스트가 skip 되는 것과 같은 방침이다
@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git 이 PATH 에 없다")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repo,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return repo


# 마커 파일만 검사하는 테스트용. `.git/` 이 있으면 되고 git 실행은 필요 없다
@pytest.fixture
def marker_root(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path
