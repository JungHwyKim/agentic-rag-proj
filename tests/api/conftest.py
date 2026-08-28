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
# 기동 워밍업도 같은 이유로 끈다. **`Settings.warmup_on_startup` 기본값은 꺼짐인데
# 개발자의 `.env` 가 그것을 덮는다** — 그러면 `with TestClient(create_app())` 를 쓰는
# 테스트마다 배경 스레드가 KURE 가중치를 올린다(2026-08-28 실측: 스레드를 기다려 보니
# `Loading weights 391/391` · **13.61초** · `.cache/embedding` +1).
#
# **짧은 실행에서는 안 보이는 것이 이 구멍의 성질이다.** 워밍업 스레드는 `daemon` 이라
# 프로세스가 먼저 끝나면 조용히 죽는다 — `tests/api` 만 돌리면 2.45초에 끝나 캐시 델타가
# 0이고, 전체 pytest(150초대)에서만 완주해 `Loading weights` 가 출력에 섞인다. 그래서
# **재현이 들쭉날쭉하고 원인이 pytest 쪽으로 잘못 보인다**(실제로 한 번 잘못 짚었다).
#
# CLAUDE.md 는 이 자리를 *"설정 기본값이 유일한 방어"* 라고 적었는데, `.env` 가 덮을 수
# 있으므로 그 방어는 개발자 머신에서 안 선다. 임베딩 provider 를 `offline_config` 에
# 못 박은 것과 **같은 판단**이다 — 기본값에 기대지 않고 테스트가 직접 박는다.
#
# **`test_warmup.py` 는 이 핀을 덮어쓴다.** 픽스처가 먼저 돌고 테스트 본문의
# `monkeypatch.setenv` 가 나중이라 켜는 쪽 검사가 그대로 산다
@pytest.fixture(autouse=True)
def warmup_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARMUP_ON_STARTUP", "false")


@pytest.fixture(autouse=True)
def query_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data" / "queries.jsonl"
    monkeypatch.setenv("QUERY_LOG_PATH", str(path))
    return path
