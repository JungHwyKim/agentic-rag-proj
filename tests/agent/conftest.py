"""도구 테스트가 개발자 `.env` 의 실키를 타지 않게 못 박는다.

## 왜 autouse 인가

`WebSearchConfig()` 는 다른 설정 객체와 마찬가지로 `PROJECT_ROOT/.env` 를 **직접 읽는다.**
그래서 `WebSearchTool()` 을 인자 없이 만드는 것만으로 개발자 머신의 `TAVILY_API_KEY` 가
실려 **기본 pytest 가 Tavily 에 실제 과금한다.** 각 테스트가 손으로 설정을 꽂는 규약이면
새 테스트가 잊는 순간 다시 새므로, `tests/api/conftest.py` 의 `ORIGINALS_DIR`·
`QUERY_LOG_PATH` 와 같은 자리에 같은 모양으로 둔다.

## **`delenv` 로는 안 막힌다 — 빈 값으로 덮어써야 한다** (2026-08-29 실측)

여기서 한 번 틀렸고 그 대가로 Tavily 호출 2건이 실제로 나갔다. 처음에는
`monkeypatch.delenv("TAVILY_API_KEY")` 로 짰는데 **아무것도 안 막았다** — pydantic-settings
는 환경변수와 `.env` 파일을 **둘 다** 읽고, 환경변수를 지우는 것은 파일 쪽을 안 건드린다.
즉 지운 자리를 `.env` 가 곧바로 채운다.

`tests/api/conftest.py` 의 선례가 통하는 이유가 여기서 갈린다 — 저쪽은 `ORIGINALS_DIR` 를
**덮어쓰는** 것이라 환경변수가 파일을 이기고, 이쪽은 **지우는** 것이라 파일이 남는다.
**모양이 같아 보여 그대로 베끼면 조용히 안 먹는다.**

그래서 빈 문자열로 덮어쓴다. `str | None` 필드에 `''` 가 들어가고 그것이 falsy 라
`_make_client()` 가 `None` 을 돌려준다(실측 확인).

**가짜 키를 넣는 안은 더 나쁘다** — `_make_client()` 가 클라이언트를 실제로 만들어
**네트워크까지 간 뒤** 401 로 죽는다. 막으려던 것이 호출인데 호출이 나간다.

## 순서만으로는 안 됐다

2026-08-26 에 질의 라우트를 픽스처보다 **먼저** 배선했더니 훅이 몇 초 만에 `tests/api` 를
돌려 `data/queries.jsonl` 에 15줄이 샜고, 그때 얻은 교훈이 *"가른 것은 지식이 아니라 순서"*
였다. 이번에는 순서를 지켰는데도 샜다 — **픽스처를 먼저 넣었지만 그 픽스처가 틀렸다.**
가른 것은 `auto_test_check.py` 가 구현 직후 실제로 테스트를 돌린 것 하나다.
"""

from __future__ import annotations

import pytest


# 도구 테스트 전체에 먹는다. **지우지 않고 빈 값으로 덮어쓴다** (모듈 docstring 참고)
@pytest.fixture(autouse=True)
def _no_web_search_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
