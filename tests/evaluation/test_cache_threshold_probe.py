"""분리 가능성 탐침. **이 파일이 지키는 것은 "수를 못 낼 때 안 내는 것"이다.**

이 탐침의 값어치는 임계값을 내는 데 있지 않고 **겹쳤을 때 거절하는 데** 있다. 거절이 사라지면
겹친 분포에서 값을 고르게 되는데, 그것이 이 저장소가 제목 가설로 세 번 데인 실수다.

두 번째로 지키는 것은 **음성에서 같은 문서 조합을 안 거르는 것**이다. 실측(2026-08-25)으로
같은 문서 음성 최대가 0.8212 이고 다른 문서는 0.6753 이라, 거르는 순간 틈이 있는 것처럼 보인다.
누가 "잡음을 줄이려고" 같은 문서를 빼면 **판정이 조용히 뒤집힌다.**

임베딩은 안 부른다 — 벡터를 손으로 만들어 규칙만 고립시킨다. 모델 적재는 2.27GB 다.
"""

from __future__ import annotations

import json
import math

import pytest

from evaluation.cache_threshold_probe import (
    Probe,
    describe,
    load_probe_set,
    negatives,
    positives,
    separability,
)


# 단위 길이 2차원 벡터. 각도만 주면 되므로 코사인이 각도차의 cos 로 바로 읽힌다
def unit(degrees: float) -> list[float]:
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


# 문서 둘에 걸친 쌍 넷. `query_id` 는 다르고 표현은 둘씩이다
def _probes() -> list[Probe]:
    return [
        Probe(query_id="a-1", source_doc="문서A", base_query="가", paraphrase="가'"),
        Probe(query_id="a-2", source_doc="문서A", base_query="나", paraphrase="나'"),
        Probe(query_id="b-1", source_doc="문서B", base_query="다", paraphrase="다'"),
        Probe(query_id="b-2", source_doc="문서B", base_query="라", paraphrase="라'"),
    ]


# **겹치면 값을 못 낸다.** 이 탐침의 존재 이유가 이 한 줄이다
def test_overlapped_distributions_yield_no_threshold():
    verdict = separability([0.50, 0.70, 0.90], [0.30, 0.60, 0.82])

    assert verdict.threshold is None
    assert verdict.separable is False
    assert verdict.gap < 0


# 갈렸을 때만 값이 나오고, 그 값은 틈의 중앙이다
def test_separated_distributions_yield_the_midpoint():
    verdict = separability([0.80, 0.90], [0.30, 0.60])

    assert verdict.separable is True
    assert verdict.threshold == pytest.approx(0.70)
    assert verdict.gap == pytest.approx(0.20)


# 음성 최대에 잠기는 양성을 센다 — 겹친 **정도**가 판정문에 실려야 원인을 읽을 수 있다
def test_submerged_counts_positives_under_the_worst_negative():
    verdict = separability([0.40, 0.50, 0.95], [0.10, 0.60])

    assert verdict.submerged == 2


# 경계값이 딱 같으면 겹친 것으로 본다. **`<` 가 아니라 `<=` 로 잠기는 쪽이 안전하다**
def test_touching_distributions_count_as_overlapped():
    verdict = separability([0.60, 0.80], [0.20, 0.60])

    assert verdict.threshold is None
    assert verdict.submerged == 1


# **같은 문서 조합을 거르지 않는다.** 거르면 어려운 음성이 빠져 틈이 있는 것처럼 보인다
def test_negatives_keep_same_document_combinations():
    probes = _probes()
    base = [unit(0), unit(10), unit(80), unit(90)]
    para = [unit(1), unit(11), unit(81), unit(91)]

    every, same_doc, cross_doc, labelled = negatives(probes, base, para)

    assert len(every) == len(same_doc) + len(cross_doc)
    assert same_doc, "같은 문서 음성이 비었다 — 어려운 음성이 통째로 빠진다"
    # 같은 문서 쪽이 실제로 더 어렵다(각도가 가깝다). 이 관계가 뒤집히면 세트가 이상한 것이다
    assert max(same_doc) > max(cross_doc)
    assert len(labelled) == len(every)


# 표현 둘을 다 쓴다. 질의 4개 × 표현 2개 = 8개 중 같은 `query_id` 짝 4개만 빠진다
def test_negatives_use_both_surface_forms_and_skip_the_same_query():
    probes = _probes()
    base = [unit(0), unit(30), unit(60), unit(90)]
    para = [unit(5), unit(35), unit(65), unit(95)]

    every, _, _, labelled = negatives(probes, base, para)

    assert len(every) == math.comb(8, 2) - len(probes)
    assert all(left != right for _, left, right in labelled)


# 양성은 쌍마다 하나다. 길이가 어긋나면 조용히 짧은 쪽에 맞추지 않고 예외를 던진다
def test_positives_reject_mismatched_lengths():
    with pytest.raises(ValueError):
        positives([unit(0), unit(10)], [unit(0)])


def test_positives_pair_each_index():
    values = positives([unit(0), unit(0)], [unit(0), unit(60)])

    assert values[0] == pytest.approx(1.0)
    assert values[1] == pytest.approx(0.5)


# 빈 분포로는 판정을 못 낸다 — 0건을 "안 겹쳤다"로 읽으면 임계값이 허공에서 나온다
def test_separability_rejects_empty_distributions():
    with pytest.raises(ValueError):
        separability([], [0.1])
    with pytest.raises(ValueError):
        separability([0.1], [])


# **검수 안 끝난 쌍이 섞이면 멈춘다.** 겹침 수치는 뜻이 같은가를 말하지 않는다
def test_load_probe_set_stops_on_pending_pairs(tmp_path):
    path = tmp_path / "pairs.jsonl"
    rows = [
        {"pair_id": "p-1", "query_id": "sih-01", "base_query": "가", "paraphrase": "가'",
         "overlap": 0.0, "written_by": "llm-draft", "reviewed_by": "human", "reviewed_hash": "x"},
        {"pair_id": "p-2", "query_id": "sih-02", "base_query": "나", "paraphrase": "나'",
         "overlap": 0.0, "written_by": "llm-draft", "reviewed_by": "pending", "reviewed_hash": ""},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        load_probe_set(path)

    assert "p-2" in str(caught.value)


# 세트가 없으면 조용히 통과하지 않는다 — 0쌍으로 낸 판정은 판정이 아니다
def test_load_probe_set_stops_when_the_file_is_missing(tmp_path):
    with pytest.raises(SystemExit):
        load_probe_set(tmp_path / "없는파일.jsonl")


# 요약은 관측값만 싣는다. 보간하면 세트에 없는 수가 리포트에 실린다
def test_describe_reports_observed_values_only():
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    dist = describe("시험", values)

    assert dist.count == 5
    assert dist.minimum == 0.1
    assert dist.maximum == 0.5
    assert dist.p50 in values
    assert dist.p95 in values
