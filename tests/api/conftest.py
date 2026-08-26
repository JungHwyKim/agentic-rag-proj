"""API 테스트가 개발자의 **실제** 보관함·질의 로그를 건드리지 않게 막는다.

`Settings.originals_dir` 의 기본값이 `PROJECT_ROOT/original_files` 라, 색인 라우트를 부르는
테스트가 실제 폴더에 파일을 쓴다. `tests/conftest.py` 의 `offline_config` 가 `qdrant_path` 를
`tmp_path` 로 덮는 것과 **완전히 같은 이유**다 — 저쪽 주석이 *"안 덮으면 테스트가 개발자의
실제 색인을 건드리고 테스트끼리 상태를 물려받는다"* 라고 적어 둔 그 함정이다.

**실제로 밟았다.** 원본 보관을 넣은 직후 `tests/api/test_index_route.py` 의 가짜 업로드가
저장소 루트에 `안내서__48fbb1.docx`(22바이트)를 남겼다. 그 테스트는 원본 보관을 모르고
`originals_dir` 을 덮지도 않는다 — **아무 에러도 안 났다.**

**autouse 인 것이 요점이다.** 라우트를 부르는 테스트마다 손으로 덮게 하면 새 테스트를 쓰면서
잊는 순간 다시 샌다. 여기 걸어 두면 이 폴더의 모든 테스트가 자동으로 안전하다.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# 모든 API 테스트의 원본 보관함을 tmp_path 로 돌린다 (모듈 docstring 참고).
#
# 환경변수로 덮는 이유: `Settings` 가 pydantic-settings 라 필드 이름이 곧 환경변수 이름이고,
# `get_settings()` 가 **매번 새로 만들기 때문에**(lru_cache 가 없다) 이 한 줄이 라우트든
# 헬퍼든 모든 호출부에 먹는다. 모듈마다 get_settings 를 patch 하면 새 호출부가 생길 때마다
# 빠뜨리게 된다
@pytest.fixture(autouse=True)
def originals_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "original_files"
    monkeypatch.setenv("ORIGINALS_DIR", str(directory))
    return directory


# 질의 로그도 같은 이유로 돌린다. **`Settings.query_log_enabled` 기본값이 켜짐이라 이 픽스처가
# 없으면 질의 라우트를 부르는 모든 테스트가 저장소의 `data/queries.jsonl` 에 줄을 쌓는다.**
#
# 원본 보관 때보다 대가가 크다 — 저쪽에 남은 것은 22바이트짜리 가짜 파일이었지만 여기 남는
# 것은 **사용자 질문 문자열**이고, 그것이 개발자의 실제 반복률 통계에 섞이면 나중에 캐시를
# 켤지 정하는 근거가 조용히 오염된다(테스트 질의는 늘 같은 문자열이라 반복률을 위로 민다).
#
# autouse 인 이유는 위와 같다: 손으로 덮는 규약이면 새 테스트가 잊는 순간 다시 샌다
@pytest.fixture(autouse=True)
def query_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data" / "queries.jsonl"
    monkeypatch.setenv("QUERY_LOG_PATH", str(path))
    return path
