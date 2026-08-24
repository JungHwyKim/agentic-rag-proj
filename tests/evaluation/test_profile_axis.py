"""러너가 **어느 임베딩으로 쟀는지**를 잃지 않는지 본다.

프로필 축의 위험은 축이 안 도는 것이 아니라 **축이 돌았다는 사실이 결과에 안 남는 것**이다.
그러면 다른 모델로 잰 수치가 기준선과 나란히 놓이는데 아무 에러도 안 난다 — 포맷이 다른
두 실행을 견주는 사고와 같은 종류이고, `compare_to_baseline()` 이 그것을 이미 막고 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.config import DEFAULT_EMBEDDING_PROFILE
from evaluation.run_eval import BASELINE_PATH, as_baseline, compare_to_baseline


def _report(**over: object) -> dict:
    report = {
        "label": "t",
        "format": "pdf",
        "embedding_profile": DEFAULT_EMBEDDING_PROFILE,
        "groups": ["g"],
        "k": 20,
        "report_k": 10,
        "per_query_k": 20,
        "settings": {"embedding": {"embedding_model": "text-embedding-3-small"}},
        "indexed": [],
        "summary": {},
        "by_k": {},
        "by_group": {},
        "per_query": [{"query_id": "q1", "rank": 3}],
    }
    report.update(over)
    return report


# 기준선 파일이 프로필을 잃으면 다음 세션이 무엇과 견주는지 모른다
def test_baseline_keeps_the_profile() -> None:
    assert as_baseline(_report(embedding_profile="x"))["embedding_profile"] == "x"


# 기본 프로필이면 재는 방법에 --embedding 이 안 붙는다. 붙으면 지금까지의 명령과 달라 보인다
def test_recipe_omits_the_flag_for_the_default() -> None:
    recipe = as_baseline(_report())["_meta"]["재는 방법"]
    assert "--embedding" not in recipe


# 반대로 다른 프로필이면 반드시 붙어야 한다 — 그 줄만 보고 다시 돌릴 수 있어야 한다
def test_recipe_carries_a_named_profile() -> None:
    recipe = as_baseline(_report(embedding_profile="local-x"))["_meta"]["재는 방법"]
    assert "--embedding local-x" in recipe


# **다른 모델로 잰 순위를 승·패로 세면 안 된다.** 검색이 좋아진 것인지 모델이 다른 것인지
# 못 가른다 — 포맷 가드와 같은 이유다
def test_compare_refuses_across_profiles() -> None:
    lines = compare_to_baseline(
        _report(embedding_profile="local-x"), _report(embedding_profile="baseline")
    )
    assert len(lines) == 1
    assert "직접 비교하지 않습니다" in lines[0]
    assert "local-x" in lines[0]


# 같은 프로필이면 지금까지처럼 승·패·무가 나온다. 가드가 정상 경로를 막으면 안 된다
def test_compare_still_works_within_one_profile() -> None:
    lines = compare_to_baseline(
        _report(per_query=[{"query_id": "q1", "rank": 1}]), _report()
    )
    assert lines[0].startswith("  이김 1")


# **프로필 축이 생기기 전의 기준선에는 이 키가 없다.** 그때는 실제로 기본 프로필이었으므로
# 없으면 기본으로 본다 — 안 그러면 이미 커밋된 기준선이 대조를 거부한다
def test_a_baseline_without_the_key_is_read_as_default() -> None:
    old = _report()
    del old["embedding_profile"]
    assert compare_to_baseline(_report(), old)[0].startswith("  이김")


# 그리고 그 호환이 **실제 커밋된 기준선 파일**에서 성립하는지 본다. 합성 dict 로만 재면
# 진짜 파일이 다른 모양일 때 못 잡는다
@pytest.mark.skipif(not BASELINE_PATH.exists(), reason="기준선 파일이 없다")
def test_committed_baseline_still_compares() -> None:
    baseline = json.loads(Path(BASELINE_PATH).read_text(encoding="utf-8"))
    report = _report(
        format=baseline["format"],
        per_query=[dict(row) for row in baseline["per_query"]],
    )
    assert "직접 비교하지 않습니다" not in compare_to_baseline(report, baseline)[0]
