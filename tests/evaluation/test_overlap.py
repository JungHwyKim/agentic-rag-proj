"""겹침 구간 채점이 **두 실행을 같은 자[尺]로 재는지** 본다.

이 모듈이 저장소 코드가 된 이유가 곧 여기서 잠글 것이다 — 인라인 복사본 둘이 미세하게
갈리면 두 실행이 다른 구간표로 채점되는데 **아무 에러도 안 난다.**
"""

from __future__ import annotations

import pytest

from evaluation.overlap import compare_by_overlap, overlap_ratio, spread
from evaluation.qa_set import QAItem


def _item(query_id: str, query: str, *spans: str) -> QAItem:
    return QAItem(query_id=query_id, query=query, source_doc="g", gold_spans=list(spans))


# 질의 낱말이 전부 정답에 있으면 1.0, 하나도 없으면 0.0
def test_ratio_ends() -> None:
    assert overlap_ratio("기록물 이관", ["기록물 이관"]) == pytest.approx(1.0)
    assert overlap_ratio("사과 바나나", ["헬리콥터 잠수함"]) == pytest.approx(0.0)


# 질의에 토큰이 없으면 0으로 본다. 나누기가 터지면 그 질의 하나가 채점 전체를 죽인다
def test_empty_query_is_zero_not_an_error() -> None:
    assert overlap_ratio("", ["무엇이든"]) == 0.0


# **공백으로 자르면 조사 때문에 과소평가된다.** BM25 가 보는 토큰으로 재는 이유다 —
# 이 짝은 공백 기준으로는 겹침이 정확히 0 인데 실제로는 같은 말을 하고 있다
def test_particles_do_not_hide_the_overlap() -> None:
    query, span = "기록물의 이관시기는", "기록물 이관 시기"
    assert not (set(query.split()) & set(span.split()))
    assert overlap_ratio(query, [span]) > 0.0


# 세트 분포. 겹침 0 인 질의 수는 "재려는 구간의 표본이 몇인가"라 따로 센다
def test_spread_counts_zero_overlap_queries() -> None:
    items = [_item("a", "사과", "사과"), _item("b", "포도", "헬리콥터")]
    dist = spread(items)
    assert dist["queries"] == 2
    assert dist["zero"] == 1
    assert dist["max"] == pytest.approx(1.0)


# **경계는 겹침만으로 정해진다.** 순위가 어떻든 같은 세트면 같은 구간표여야 한다 —
# 아니면 구간별 이득이 검색이 아니라 구간 이동을 재게 된다
def test_bucket_edges_do_not_depend_on_the_runs() -> None:
    items = [_item(f"q{i}", "가나 다라", *(["가나 다라"] if i % 2 else ["헬리콥터"])) for i in range(6)]
    good = dict.fromkeys([i.query_id for i in items], 1)
    bad = {i.query_id: None for i in items}

    first = compare_by_overlap(items, good, bad, buckets=2)
    second = compare_by_overlap(items, bad, good, buckets=2)

    assert [(b.low, b.high, b.queries) for b in first] == [
        (b.low, b.high, b.queries) for b in second
    ]


# 이득은 뒤 실행에서 앞 실행을 뺀 것이고, 순위 이동은 hit 여부와 따로 센다.
# **후보 창 밖(rank=None)에서 안으로 들어온 것도 오름이다** — 안 그러면 제일 큰 개선이 안 보인다
def test_gain_and_moves() -> None:
    items = [_item("q1", "가나", "가나"), _item("q2", "다라", "헬리콥터")]
    rows = compare_by_overlap(items, {"q1": 30, "q2": None}, {"q1": 3, "q2": 5}, buckets=1)

    assert rows[0].queries == 2
    assert rows[0].before_hit == pytest.approx(0.0)
    assert rows[0].after_hit == pytest.approx(1.0)
    assert rows[0].gain == pytest.approx(1.0)
    assert (rows[0].up, rows[0].down) == (2, 0)


# **한쪽에만 있는 질의를 조용히 버리지 않는다.** 버리면 구간 크기가 달라지는데 신호가 없다
def test_missing_query_stops_instead_of_shrinking_the_buckets() -> None:
    items = [_item("q1", "가나", "가나"), _item("q2", "다라", "다라")]
    with pytest.raises(SystemExit) as err:
        compare_by_overlap(items, {"q1": 1, "q2": 2}, {"q1": 1}, buckets=1)
    assert "q2" in str(err.value)


# 질의보다 구간이 많으면 멈춘다. 빈 구간이 섞이면 평균이 0으로 희석된다
def test_too_many_buckets_stops() -> None:
    items = [_item("q1", "가나", "가나")]
    with pytest.raises(SystemExit):
        compare_by_overlap(items, {"q1": 1}, {"q1": 1}, buckets=3)
