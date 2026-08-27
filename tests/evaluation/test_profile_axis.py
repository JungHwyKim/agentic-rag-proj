"""러너가 **어느 임베딩으로 쟀는지**를 잃지 않는지 본다.

프로필 축의 위험은 축이 안 도는 것이 아니라 **축이 돌았다는 사실이 결과에 안 남는 것**이다.
그러면 다른 모델로 잰 수치가 기준선과 나란히 놓이는데 아무 에러도 안 난다 — 포맷이 다른
두 실행을 견주는 사고와 같은 종류이고, `compare_to_baseline()` 이 그것을 이미 막고 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.config import (
    DEFAULT_EMBEDDING_PROFILE,
    DEFAULT_RERANK_PROFILE,
    DEFAULT_REWRITE_PROFILE,
)
from evaluation.run_eval import BASELINE_PATH, as_baseline, compare_to_baseline


def _report(**over: object) -> dict:
    report = {
        "label": "t",
        "format": "pdf",
        "embedding_profile": DEFAULT_EMBEDDING_PROFILE,
        # 리랭커 축이 생기며 기준선에 함께 담긴다. **`as_baseline()` 을 `.get()` 으로
        # 관대하게 만들지 않는 것이 의도다** — 조용히 빠지는 것이 이 키가 고치는 결함이라,
        # 리포트를 만드는 쪽이 빠뜨리면 여기서 KeyError 로 시끄럽게 죽어야 한다
        "rerank_profile": DEFAULT_RERANK_PROFILE,
        # 재작성 축도 같은 대우다. **축을 더한 회차가 이 helper 를 함께 넓히지 않으면
        # 위 KeyError 가 그것을 잡는다** — 관대하게 만들었다면 새 축이 기준선에서 조용히
        # 빠졌을 것이고, 그것이 정확히 이 파일이 막는 결함이다
        "rewrite_profile": DEFAULT_REWRITE_PROFILE,
        "retriever": {"use_bm25": False, "rerank": DEFAULT_RERANK_PROFILE},
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


# **커밋된 기준선은 라벨을 스스로 들고 있어야 한다 — fallback 에 기대면 안 된다.**
#
# 위 `test_a_baseline_without_the_key_is_read_as_default` 가 보는 fallback
# (`baseline.get("embedding_profile", DEFAULT_EMBEDDING_PROFILE)`)은 **읽는 쪽의 관용**이지
# 파일의 성질이 아니다. 라벨이 없는 파일은 `DEFAULT_EMBEDDING_PROFILE` 이 바뀌는 순간
# **자기가 아닌 프로필의 이름으로 읽힌다** — 그러면 프로필 가드가 통과해 버려서, OpenAI 로
# 잰 기준선과 다른 모델로 잰 실행이 나란히 놓이고 승·패가 나온다. **에러 없이 수치로만
# 오염되는 모양이다.**
#
# 실제로 2026-08-25 에 그 문턱까지 갔다 — 운영 기본값을 KURE-v1 로 옮기며 평가 기본
# 프로필도 함께 옮길지 검토했고, 옮겼다면 이 파일(`hit@10` 0.4833, OpenAI 로 잰 것)이
# `kure-v1` 로 읽혔을 것이다. 기본값을 안 옮기는 것으로 피했지만 그건 **피한 것이지 닫은
# 것이 아니라서**, 라벨을 박아 닫았다. 새 리포트는 `run_eval.py` 가 항상 쓰므로 라벨이
# 없을 수 있는 파일은 이 커밋된 기준선 하나뿐이다
@pytest.mark.skipif(not BASELINE_PATH.exists(), reason="기준선 파일이 없다")
def test_committed_baseline_carries_an_explicit_profile() -> None:
    baseline = json.loads(Path(BASELINE_PATH).read_text(encoding="utf-8"))

    assert "embedding_profile" in baseline, (
        "커밋된 기준선에 embedding_profile 이 없으면 DEFAULT_EMBEDDING_PROFILE 이 바뀔 때 "
        "자기가 아닌 프로필 이름으로 읽힌다 — 라벨을 명시로 박아야 한다"
    )

    # **라벨이 파일이 실제로 잰 것과 맞는지까지 본다.** 라벨만 있고 값이 틀리면
    # fallback 보다 나쁘다 — 틀린 이름을 확신을 갖고 들고 있게 된다
    from evaluation.config import embedding_profile

    declared = baseline["embedding_profile"]
    assert baseline["settings"]["embedding"] == embedding_profile(declared), (
        f"기준선의 라벨은 {declared!r} 인데 settings.embedding 블록이 그 프로필과 다르다"
    )
