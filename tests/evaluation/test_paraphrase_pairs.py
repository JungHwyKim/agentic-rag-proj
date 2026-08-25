"""패러프레이즈 쌍 세트. **임계값을 재기 전에 세트 자체가 성립하는지 본다.**

여기서 재는 것은 *"쌍이 좋은가"* 가 아니다 — 그건 사람 검수 몫이다. 이 파일이 막는 것은
**세트가 조용히 깨지는 것**이다: `query_id` 오타로 짝이 없는 쌍, 손으로 고친 뒤 `overlap`
이 안 따라간 쌍, 원문을 그대로 베낀 쌍.

`overlap` 을 다시 계산해 대조하는 것이 핵심이다. 그 값이 이 세트의 **자기 검사**라
(원문 낱말을 그대로 쓴 쌍이 섞이면 *"두 분포가 깨끗이 갈린다"* 는 착시가 생겨 *겹치면
안 켠다* 라는 안전장치를 뒤집는다) **값이 낡으면 검사가 아니라 장식이 된다.**

**검수 도장은 `overlap` 이 못 지킨다.** 겹침 값은 *바뀌는* 수정만 잡아서, 겹침 0.0 인 쌍은
0 을 유지하는 어떤 재작성도 조용히 통과한다(2026-08-25 검수 시점 20건). 그래서 `qa_set` 이
쓰는 것과 같은 `reviewed_hash` 를 함께 본다 — 검수 뒤에 문장을 고치면 **검수자가 안 본 문장에
도장이 남는데 아무 에러도 안 난다.** 지문 함수는 `qa_set.content_hash` 를 그대로 쓴다.
두 번째 지문 정의를 만들면 같은 세트에 서로 다른 두 도장이 생긴다.

세트가 없으면 조용히 skip 한다 — `tests/fixtures/` 실파일 테스트와 같은 규약이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.overlap import overlap_ratio
from evaluation.qa_set import REVIEW_STATES, content_hash

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAIRS = PROJECT_ROOT / "evaluation" / "paraphrase_pairs.jsonl"
QA_SETS = ("qa_set.jsonl", "qa_set_human.jsonl")

# 손으로 고친 쌍이 이 값을 넘으면 원문을 베낀 것에 가깝다. **임계값이 아니라 검수 알림**이다 —
# 넘는다고 틀린 쌍은 아니고(짧은 질의는 낱말을 안 바꾸면 뜻이 달라진다), 사람이 볼 자리를
# 가리킬 뿐이다. 그래서 실패가 아니라 목록으로 낸다
_REVIEW_HINT = 0.5


# 쌍 목록. 파일이 없으면 skip 한다
def _pairs() -> list[dict]:
    if not PAIRS.exists():
        pytest.skip("evaluation/paraphrase_pairs.jsonl 이 없습니다.")
    return [json.loads(line) for line in PAIRS.read_text(encoding="utf-8").splitlines() if line.strip()]


# 기존 질의 세트의 query_id -> query
def _base_queries() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in QA_SETS:
        path = PROJECT_ROOT / "evaluation" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                found[record["query_id"]] = record["query"]
    return found


def test_every_pair_has_the_required_fields():
    required = {
        "pair_id",
        "query_id",
        "base_query",
        "paraphrase",
        "overlap",
        "written_by",
        "reviewed_by",
        "reviewed_hash",
    }

    for pair in _pairs():
        assert required <= set(pair), f"{pair.get('pair_id')} 에 빠진 필드: {required - set(pair)}"


# **query_id 가 실재해야 한다.** 오타 하나면 그 쌍은 짝을 잃고, 양성 분포에서 조용히 빠진다
def test_query_ids_exist_in_the_qa_sets():
    base = _base_queries()
    if not base:
        pytest.skip("질의 세트가 없습니다.")

    unknown = [p["pair_id"] for p in _pairs() if p["query_id"] not in base]
    assert not unknown, f"질의 세트에 없는 query_id: {unknown}"


# base_query 를 세트에서 다시 읽어 대조한다. 손으로 고치다 원문이 어긋나면 이 세트가
# 재는 것이 "같은 뜻인가" 가 아니라 "내가 적어둔 두 문장이 비슷한가" 가 된다
def test_base_query_matches_the_qa_set():
    base = _base_queries()
    if not base:
        pytest.skip("질의 세트가 없습니다.")

    drifted = [p["pair_id"] for p in _pairs() if p["base_query"] != base[p["query_id"]]]
    assert not drifted, f"원문이 질의 세트와 다릅니다: {drifted}"


# **overlap 을 다시 계산해 맞춘다.** 쌍을 손보고 값을 안 고치면 자기 검사가 장식이 된다
def test_overlap_is_current():
    stale = []
    for pair in _pairs():
        expected = round(overlap_ratio(pair["paraphrase"], [pair["base_query"]]), 4)
        if abs(expected - float(pair["overlap"])) > 1e-4:
            stale.append((pair["pair_id"], pair["overlap"], expected))

    assert not stale, f"overlap 이 낡았습니다 (id, 적힌 값, 실제): {stale}"


def test_paraphrase_is_not_the_original():
    same = [p["pair_id"] for p in _pairs() if p["paraphrase"].strip() == p["base_query"].strip()]
    assert not same, f"원문과 같은 쌍: {same}"


def test_pair_ids_are_unique():
    ids = [p["pair_id"] for p in _pairs()]
    assert len(ids) == len(set(ids))


# `qa_set` 과 같은 값만 쓴다. 이 파일에는 이 검사가 없어서 오타 하나면 그 쌍이 검수됐는지
# 안 됐는지를 아무도 못 말하는 상태가 된다
def test_review_state_is_known():
    unknown = [(p["pair_id"], p["reviewed_by"]) for p in _pairs() if p["reviewed_by"] not in REVIEW_STATES]
    assert not unknown, f"reviewed_by 가 {sorted(REVIEW_STATES)} 밖입니다: {unknown}"


# **검수 도장을 그때 본 문장에 묶는다.** 찍고 나서 문장을 고치면 검수자가 안 본 쌍에 도장이
# 남는데, `overlap` 은 값이 바뀌는 수정만 잡아 겹침 0.0 인 쌍은 통째로 구멍이다
def test_review_stamp_matches_the_reviewed_sentences():
    broken = []
    for pair in _pairs():
        if pair["reviewed_by"] != "human":
            continue
        expected = content_hash(pair["base_query"], [pair["paraphrase"]])
        if pair["reviewed_hash"] != expected:
            broken.append((pair["pair_id"], pair["reviewed_hash"] or "없음", expected))

    assert not broken, (
        f"검수 뒤에 문장이 바뀌었습니다 (id, 기록, 지금): {broken} "
        "— 다시 검수하고 reviewed_hash 를 갱신하거나 pending 으로 되돌려라"
    )


# 도장을 안 찍은 쌍이 지문만 들고 있으면 **검수한 것처럼 보이는 흔적**이 남는다
def test_pending_pairs_carry_no_stamp():
    stamped = [
        p["pair_id"] for p in _pairs() if p["reviewed_by"] == "pending" and p["reviewed_hash"]
    ]
    assert not stamped, f"pending 인데 reviewed_hash 가 있습니다: {stamped}"


# 겹침이 높은 쌍은 **실패가 아니라 검수 대상**이다. 값을 못 박지 않고 목록만 보여준다 —
# 이 세트의 목적은 임계값이고, 임계값은 사람 검수가 끝난 뒤에 잰다
def test_report_high_overlap_pairs_for_review(capsys):
    high = sorted(
        ((p["overlap"], p["pair_id"]) for p in _pairs() if p["overlap"] >= _REVIEW_HINT),
        reverse=True,
    )
    with capsys.disabled():
        if high:
            print(f"\n검수 우선(겹침 {_REVIEW_HINT} 이상): {high}")
