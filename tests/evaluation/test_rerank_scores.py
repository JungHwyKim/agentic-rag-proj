"""리랭커 점수 판독기. **재지 않고 읽는다** 는 계약과, 실코퍼스가 못 만드는 경계를 잠근다.

제일 중요한 것 넷이다.

1. **캐시에 없으면 값이 아니라 실패다.** 미스를 «점수 없음» 으로 접으면 풀 재현이 틀린
   실행이 조용히 수치를 낸다 — 그것이 이 도구가 막으려는 단 하나의 사고다
2. **뒤집힌 공유 쌍의 점수 차가 0이면 잡음이다.** 뒤집힘 수만 세면 «후보 구성을 탄다» 로
   잘못 읽는다. 정렬이 stable 이라 **동점은 들어온 순서가 이긴다**(`apply_scores` 계약)
3. **예측은 대상의 점수를 안 본다.** 보면 예측이 아니라 재계산이다. 그래서 예측 함수의
   입력은 «신참 수» 와 «다른 짝에서 잰 분포» 뿐이다
4. **경계는 합성만 만들 수 있다.** 실코퍼스는 신참이 늘 수백 개라 «신참 0개» 도
   «신참이 전부 상위» 도 원리적으로 안 나온다

외부 API 도 모델 가중치도 저장소도 안 건드린다.
"""

from __future__ import annotations

import pytest

from evaluation.rerank_scores import (
    DEPTHS,
    PairRow,
    _cached_scores,
    _inversions,
    agreement,
    newcomer_percentiles,
    predict_agreement,
    summarize,
)
from rag_pipeline.ingestion.loader.content.base import ResultCache, content_hash
from rag_pipeline.retrieval.reranker.base import rerank_cache_key
from rag_pipeline.schemas.retrieval import RetrievedChunk


def _chunk(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(chunk_id=chunk_id, document_id="doc.pdf", text="본문")


def _row(
    *,
    off: list[str],
    on: list[str],
    newcomer_ranks: list[int] | None = None,
    agree: dict[str, bool] | None = None,
) -> PairRow:
    newcomers = set(on) - set(off)
    ranks = newcomer_ranks
    if ranks is None:
        ranks = [i for i, cid in enumerate(on, start=1) if cid in newcomers]
    return PairRow(
        query_id="q",
        off_ranked=off,
        on_ranked=on,
        shared=len(set(off) & set(on)),
        score_identical=True,
        order_preserved=True,
        max_abs_delta=0.0,
        inversions=0,
        max_inversion_gap=0.0,
        newcomers=len(newcomers),
        newcomer_ranks=ranks,
        off_rank=1,
        on_rank=1,
        report_off_rank=1,
        report_on_rank=1,
        agree=agree or {str(d): agreement(off, on, d) for d in DEPTHS},
    )


# 상위 n 일치는 **집합**이다 — 순서가 달라도 같은 것을 담았으면 같다
def test_agreement_is_a_set_not_a_sequence():
    assert agreement(["a", "b", "c"], ["b", "a", "c"], 3) is True
    assert agreement(["a", "b", "c"], ["b", "a", "z"], 3) is False


# 상위 1은 순서가 곧 집합이라 위 완화가 안 먹는다
def test_agreement_at_one_is_the_winner():
    assert agreement(["a", "b"], ["b", "a"], 1) is False


# 신참 자리를 풀 크기로 나눈 값이다. **자리 자체를 쓰면 풀 크기가 다른 판끼리 못 견준다**
def test_percentiles_are_normalized_by_pool_size():
    row = _row(off=["a", "b"], on=["x", "a"])
    assert newcomer_percentiles([row]) == [0.5]


# 예측은 «신참이 상위 n 에 하나도 안 앉을 확률» 이다.
# **신참이 0개면 전건 일치** — 실코퍼스가 원리적으로 못 만드는 경계다
def test_prediction_with_no_newcomers_is_full_agreement():
    row = _row(off=["a", "b", "c"], on=["a", "b", "c"])
    predicted = predict_agreement([row], [0.5])
    assert predicted["1"] == 1.0
    assert predicted["3"] == 1.0


# 반대쪽 경계 — 신참이 **전부 1위 자리**에 앉는 분포면 예측은 0이다
def test_prediction_with_newcomers_always_on_top_is_zero():
    row = _row(off=["a", "b", "c"], on=["x", "a", "b"])
    assert predict_agreement([row], [1 / 3])["1"] == 0.0


# 분포가 같아도 **신참 수가 많을수록** 일치 확률이 내려간다 — 이 단조성이 예측의 전부다
def test_prediction_falls_as_newcomers_grow():
    few = _row(off=["a", "b", "c", "d"], on=["x", "a", "b", "c"])
    many = _row(off=["a", "b", "c", "d"], on=["x", "y", "z", "a"])
    dist = [0.5]
    assert predict_agreement([many], dist)["1"] <= predict_agreement([few], dist)["1"]


# 분포가 비면 예측을 못 한다. **0으로 접지 않는다** — 0은 «절대 안 갈린다» 는 뜻이라 거짓이다
def test_prediction_without_a_distribution_counts_nothing():
    row = _row(off=["a"], on=["x"])
    assert predict_agreement([row], [])["1"] == 0.0


# 동점이 뒤집힌 것은 «후보 구성을 탄다» 가 아니다. 점수 차 0이 그 증거다
def test_inversion_gap_is_zero_when_scores_tie():
    count, gap = _inversions(["a", "b"], ["b", "a"], {"a": 0.5, "b": 0.5})
    assert (count, gap) == (1, 0.0)


# 점수가 실제로 다른데 뒤집혔으면 그 차가 남는다 — 이때만 «구성을 탄다» 를 의심한다
def test_inversion_gap_survives_a_real_difference():
    count, gap = _inversions(["a", "b"], ["b", "a"], {"a": 0.9, "b": 0.1})
    assert count == 1
    assert gap == pytest.approx(0.8)


# 순서가 그대로면 뒤집힌 쌍이 없다
def test_no_inversions_when_order_is_kept():
    assert _inversions(["a", "b"], ["a", "b"], {"a": 0.9, "b": 0.1}) == (0, 0.0)


# 요약은 깊이마다 «몇 질의가 같았나» 를 센다
def test_summary_counts_agreeing_queries_per_depth():
    same = _row(off=["a", "b"], on=["a", "b"])
    split = _row(off=["a", "b"], on=["x", "a"])
    summary = summarize([same, split])
    assert summary["queries"] == 2
    assert summary["agreement"]["1"] == 1


# 캐시 키는 **원 질의 + 후보 신원 순서 + salt** 다. 그대로 만들면 읽힌다
def test_cached_scores_reads_what_the_reranker_wrote(tmp_path):
    cache = ResultCache(tmp_path, enabled=True)
    pool = [_chunk("c1"), _chunk("c2")]
    key = content_hash(rerank_cache_key("질의", pool, salt="salt"))
    cache.set(key, {"scores": [0.2, 0.8]})
    assert _cached_scores("질의", pool, cache=cache, salt="salt") == [0.2, 0.8]


# **salt 가 다르면 못 읽는다** — 이것이 device·프롬프트 판이 섞이지 않는 이유다
def test_cached_scores_misses_on_a_different_salt(tmp_path):
    cache = ResultCache(tmp_path, enabled=True)
    pool = [_chunk("c1"), _chunk("c2")]
    cache.set(content_hash(rerank_cache_key("질의", pool, salt="salt")), {"scores": [0.2, 0.8]})
    assert _cached_scores("질의", pool, cache=cache, salt="다른-salt") is None


# 후보 순서가 다르면 다른 키다. **집합이 같아도 프롬프트 번호가 달라 다른 응답이다**
def test_cached_scores_misses_when_the_pool_order_changes(tmp_path):
    cache = ResultCache(tmp_path, enabled=True)
    pool = [_chunk("c1"), _chunk("c2")]
    cache.set(content_hash(rerank_cache_key("질의", pool, salt="salt")), {"scores": [0.2, 0.8]})
    assert _cached_scores("질의", list(reversed(pool)), cache=cache, salt="salt") is None


# 길이가 안 맞는 항목은 **못 쓴다** — 반쯤 채우면 순위가 조용히 RRF 로 떨어진다
def test_cached_scores_rejects_a_wrong_length_entry(tmp_path):
    cache = ResultCache(tmp_path, enabled=True)
    pool = [_chunk("c1"), _chunk("c2")]
    cache.set(content_hash(rerank_cache_key("질의", pool, salt="salt")), {"scores": [0.2]})
    assert _cached_scores("질의", pool, cache=cache, salt="salt") is None
