"""훅들의 탈출구 환경변수 목록.

`conftest.py` 의 autouse 픽스처가 이것을 지우고 `test_hook_wiring.py` 가 훅 소스와 대조한다.
**둘이 같은 목록을 봐야 하는데 `conftest` 라는 이름으로는 그럴 수 없어** 여기로 뺐다 —
`tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이라, 전체 실행에서는 루트
`tests/conftest.py` 가 먼저 `sys.modules["conftest"]` 를 차지한다. 그래서 훅 테스트에서
`import conftest` 는 엉뚱한 모듈을 집는다. **영역만 돌릴 때는 통과하고 전체에서만 깨져서**
CLAUDE.md 가 테스트 파일 이름에 대해 경고한 것과 정확히 같은 형태다.
"""

from __future__ import annotations

# `.claude/hooks/*.py` 의 `os.environ.get(...)` 과 짝이다. 짝이 어긋나면
# test_hook_wiring.py 의 커버리지 테스트 둘이 양쪽에서 잡는다
ESCAPE_HATCHES = (
    "ALLOW_PAID_TESTS",
    "SKIP_AUTOTEST",
    "SKIP_HEREDOC",
    "SKIP_PLANINTERVIEW",
    "SKIP_PLANLOG",
    "SKIP_PRECOMMIT",
    "SKIP_PROSECHECK",
    "SKIP_TASKLOG",
)
