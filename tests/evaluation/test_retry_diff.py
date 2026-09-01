"""`evaluation/retry_diff.py` — 근거가 안 바뀐 회차의 다음 회차를 짝짓는 규칙.

**전부 오프라인이다.** 저장소·임베더·OpenAI·Cohere 를 하나도 안 부른다 — 여기서 잠그는 것은
짝짓기 규칙과 강제 래퍼의 계약이지 검색 품질이 아니다.

가짜를 **여기서 직접 짓는다.** `tests/agent/_loop_stubs.py` 를 import 하지 않는다 — `tests/` 에
`__init__.py` 가 없어 basename 이 곧 모듈명이라, 폴더를 가로지르는 import 는 루트 `conftest` 가
`sys.modules` 를 먼저 차지하는 함정과 같은 자리다.
"""

from __future__ import annotations

import json

import pytest

from evaluation.qa_set import QAItem
from evaluation.retry_diff import (
    VERDICT_HELPED,
    VERDICT_NO_HELP,
    VERDICT_UNSEEN,
    PairRow,
    RetryRound,
    RetryRun,
    _ForcingEvaluator,
    _is_better,
    _run_of,
    _wants_more,
    collect_runs,
    pair_rows,
    production_stop,
    summarize,
)
from rag_pipeline.schemas.agent import AgentState
from rag_pipeline.schemas.answer import EvaluatorVerdict


# 회차 하나를 짓는다. 기본은 «성공했고 실사용에서도 돌았을» 회차다
def _round(
    retry_count: int,
    rank: int | None = None,
    *,
    verdict: str | None = "insufficient",
    chunk_ids: list[str] | None = None,
) -> RetryRound:
    return RetryRound(
        retry_count=retry_count,
        query=f"질의{retry_count}",
        chunk_ids=["c1"] if chunk_ids is None else chunk_ids,
        rank=rank,
        real_verdict=verdict,
        succeeded=True,
    )


def _run(rounds: list[RetryRound], unchanged: list[int], query_id: str = "q1") -> RetryRun:
    return RetryRun(
        query_id=query_id,
        query="질의",
        source_doc="시행령",
        stop_reason="retries_exhausted",
        retry_count=len(rounds) - 1,
        evidence_unchanged_at=unchanged,
        rounds=rounds,
    )


# --- 짝짓기 ---------------------------------------------------------------


# 다음 회차가 정답을 위로 올렸으면 **접었으면 놓쳤다** 다
def test_a_better_next_round_means_folding_would_have_lost_it():
    run = _run([_round(0, 30), _round(1, 12), _round(2, 4)], unchanged=[1])

    rows = pair_rows([run])

    assert [r.verdict for r in rows] == [VERDICT_HELPED]
    assert (rows[0].rank_n, rows[0].rank_next) == (12, 4)


# 다음 회차가 더 나빠지거나 그대로면 안 도왔다
@pytest.mark.parametrize("next_rank", [12, 20, None])
def test_a_worse_or_equal_next_round_did_not_help(next_rank: int | None):
    run = _run([_round(0, 30), _round(1, 12), _round(2, next_rank)], unchanged=[1])

    assert [r.verdict for r in pair_rows([run])] == [VERDICT_NO_HELP]


# **못 찾다가 찾으면 도운 것이다** — None 을 숫자와 섞어 비교하면 터지는 자리
def test_finding_it_at_all_counts_as_helping():
    run = _run([_round(0, None), _round(1, None), _round(2, 9)], unchanged=[1])

    assert [r.verdict for r in pair_rows([run])] == [VERDICT_HELPED]


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [(4, 12, True), (12, 4, False), (4, 4, False), (7, None, True), (None, 7, False), (None, None, False)],
)
def test_is_better_treats_missing_as_the_worst_value(new, old, expected):
    assert _is_better(new, old) is expected


# **마지막 회차에서 근거가 같으면 «못 봤다» 다.** 이것을 «안 도왔다» 와 한 칸에 넣으면
# 정보가 없는 것이 근거로 둔갑한다 — 2026-08-28 단일 프로브가 정확히 그 자리였다
def test_an_unchanged_last_round_is_counted_as_unseen_not_as_no_help():
    run = _run([_round(0, 30), _round(1, 12), _round(2, 7)], unchanged=[2])

    rows = pair_rows([run])

    assert [r.verdict for r in rows] == [VERDICT_UNSEEN]
    assert rows[0].seen is False
    assert rows[0].rank_next is None


# 한 질의가 여러 번 같을 수도 있다 — 각각 짝지어진다
def test_every_unchanged_round_gets_its_own_row():
    run = _run([_round(0, 30), _round(1, 30), _round(2, 30), _round(3, 5)], unchanged=[1, 2])

    rows = pair_rows([run])

    assert [(r.n, r.verdict) for r in rows] == [(1, VERDICT_NO_HELP), (2, VERDICT_HELPED)]


# --- 실사용 부분집합 -------------------------------------------------------


# `_wants_more` 가 답하는 것은 «이 회차 **다음으로** 갔을까» 다 (`router.py` 규칙 4·5)
@pytest.mark.parametrize(
    ("verdict", "chunk_ids", "expected"),
    [
        ("insufficient", ["c1"], True),
        ("sufficient", ["c1"], False),
        # 근거가 0건이면 판정이 충분이라 해도 Router 는 다시 쓴다
        ("sufficient", [], True),
        # 판정 실패는 재시도로 안 삼킨다
        ("failed", ["c1"], False),
        (None, ["c1"], None),
    ],
)
def test_wants_more_follows_the_router_rules(verdict, chunk_ids, expected):
    assert _wants_more(_round(1, 3, verdict=verdict, chunk_ids=chunk_ids)) is expected


# **도구가 실패한 회차는 판단 불가다.** `record_tool_call()` 이 실패 시 결과를 안 건드려 그때
# Router 가 본 것은 앞 회차 근거이지 0건이 아니다 — 여기 적힌 빈 목록으로 규칙 5 를 읽으면 거꾸로다
def test_a_failed_round_is_not_judged():
    failed = RetryRound(
        retry_count=1, query="질의1", chunk_ids=[], rank=None,
        real_verdict="sufficient", succeeded=False,
    )

    assert _wants_more(failed) is None


# 실사용은 처음으로 «더 안 찾는다» 가 나온 회차에서 멈춘다
def test_production_stops_at_the_first_satisfied_round():
    rounds = [_round(0, 30), _round(1, 4, verdict="sufficient"), _round(2, 4)]

    assert production_stop(rounds) == 1


# 끝까지 «더 찾고 싶다» 면 마지막 회차까지 간다 (`retries_exhausted` 자리)
def test_production_runs_to_the_end_when_never_satisfied():
    assert production_stop([_round(0, None), _round(1, None), _round(2, None)]) == 2


# 판단 불가를 만나면 **거기서부터 아래가 전부 짐작**이라 None 이다
def test_production_stop_gives_up_at_an_unjudgeable_round():
    rounds = [
        _round(0, 30),
        RetryRound(retry_count=1, query="q", chunk_ids=[], rank=None,
                   real_verdict="sufficient", succeeded=False),
        _round(2, 4),
    ]

    assert production_stop(rounds) is None


# **여기가 요점이다** — 회차 N 자신이 «더 찾고 싶다» 여도, 실사용이 거기 **닿지 못했으면**
# 실사용 회차가 아니다. 회차 하나만 보면 닿지도 않았을 회차가 실사용으로 세어진다
def test_a_round_production_never_reaches_is_not_a_production_round():
    rounds = [
        # 실사용은 여기서 답하고 멈춘다
        _round(0, 1, verdict="sufficient"),
        _round(1, 1),
        # 이 회차 자신은 «더 찾고 싶다» 지만 실사용은 여기 못 온다
        _round(2, 1),
        _round(3, 1),
    ]
    run = _run(rounds, unchanged=[2])

    rows = pair_rows([run])

    assert rows[0].would_run_in_production is False
    assert summarize([run], rows)["would_run_in_production"] == 0


# 반대쪽 — 끝까지 불충분이면 닿는다
def test_a_reachable_round_is_counted_as_production():
    run = _run([_round(0, None), _round(1, None), _round(2, None), _round(3, 9)], unchanged=[2])

    rows = pair_rows([run])

    assert rows[0].would_run_in_production is True
    assert summarize([run], rows)["would_run_in_production"] == 1


# 진짜 판정을 안 받았으면 부분집합을 **못 만든다** — 0 으로 세면 «실사용에서 한 번도 안 돈다»
# 로 읽히는데 그것은 안 잰 것이다
def test_summary_refuses_to_count_the_production_subset_without_real_verdicts():
    run = _run([_round(0, 30, verdict=None), _round(1, 12, verdict=None)], unchanged=[1])

    assert summarize([run], pair_rows([run]))["would_run_in_production"] is None


def test_summary_counts_seen_and_unseen_separately():
    runs = [
        _run([_round(0, 30), _round(1, 12), _round(2, 4)], unchanged=[1], query_id="a"),
        _run([_round(0, 30), _round(1, 12), _round(2, 7)], unchanged=[2], query_id="b"),
    ]

    s = summarize(runs, pair_rows(runs))

    assert (s["unchanged_rounds"], s["paired"], s["unseen"]) == (2, 1, 1)
    assert (s["helped"], s["no_help"]) == (1, 0)
    assert s["queries_with_unchanged"] == 2


# --- 강제 래퍼 -------------------------------------------------------------


class _StubEvaluator:
    def __init__(self, *verdicts: str) -> None:
        self._verdicts = list(verdicts)
        self.calls = 0

    def evaluate(self, query: str, chunks: list) -> EvaluatorVerdict:
        index = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return EvaluatorVerdict(verdict=self._verdicts[index], reasoning="테스트")


# **진짜 판정을 부르고 그 값을 남긴 뒤 루프에는 불충분을 돌려준다.** 이 한 줄이 «한 번 돌려
# 둘 다 답한다» 의 전부다 — 지우면 실사용 부분집합을 영원히 못 만든다
def test_forcing_keeps_the_real_verdict_and_still_forces_a_retry():
    inner = _StubEvaluator("sufficient")
    forcing = _ForcingEvaluator(inner, record=True)

    returned = forcing.evaluate("질의", [])

    assert returned.verdict == "insufficient"
    assert forcing.seen == ["sufficient"]
    assert inner.calls == 1


# `failed` 도 그대로 싣는다 — 불충분으로 삼키면 죽은 판정자가 실사용 부분집합에 들어간다
def test_forcing_records_failed_as_failed():
    forcing = _ForcingEvaluator(_StubEvaluator("failed"), record=True)

    forcing.evaluate("질의", [])

    assert forcing.seen == ["failed"]


# 안 기록하기로 하면 진짜 판정을 **아예 안 부른다** — 그것이 이 모드가 싸지는 이유다
def test_not_recording_does_not_call_the_evaluator_at_all():
    inner = _StubEvaluator("sufficient")
    forcing = _ForcingEvaluator(inner, record=False)

    assert forcing.evaluate("질의", []).verdict == "insufficient"
    assert inner.calls == 0
    assert forcing.seen == [None]


def test_reset_clears_the_recorded_verdicts():
    forcing = _ForcingEvaluator(_StubEvaluator("sufficient"), record=True)
    forcing.evaluate("질의", [])

    forcing.reset()

    assert forcing.seen == []


# --- 레코드 조립 -----------------------------------------------------------


class _StubProbe:
    def __init__(self, rounds: list[tuple[str, list[str], list[str], bool]]) -> None:
        self.rounds = rounds


def _item(gold: list[str]) -> QAItem:
    return QAItem(query_id="q1", query="질의", source_doc="시행령", gold_spans=gold)


# 회차 텍스트에서 정답 순위를 잰다. `hit_rank` 를 그대로 쓴다 — 자[尺]가 두 벌이면 안 된다
def test_rank_comes_from_the_round_texts():
    tool = _StubProbe(
        [
            ("질의0", ["c1", "c2"], ["엉뚱한 본문", "정답 조각이 여기"], True),
            ("질의1", ["c3"], ["정답 조각이 여기"], True),
        ]
    )
    state = AgentState(
        original_query="질의",
        current_query="질의1",
        retry_count=1,
        evidence_unchanged_at=[1],
        stop_reason="retries_exhausted",
    )
    forcing = _ForcingEvaluator(_StubEvaluator("insufficient"), record=True)
    forcing.seen = ["insufficient", "insufficient"]

    run = _run_of(_item(["정답 조각"]), state, tool, forcing)

    assert [r.rank for r in run.rounds] == [2, 1]
    assert run.evidence_unchanged_at == [1]


# **회차 수와 `retry_count` 가 어긋나면 멈춘다.** 이 도구는 회차 index 를 `retry_count` 로 읽어
# `evidence_unchanged_at` 과 짝짓는데, 어긋나면 짝이 한 칸씩 밀린 채 표가 멀쩡해 보인다
def test_a_round_count_mismatch_raises_instead_of_shifting_the_pairs():
    tool = _StubProbe([("질의0", ["c1"], ["본문"], True)])
    state = AgentState(original_query="질의", current_query="질의", retry_count=2)
    forcing = _ForcingEvaluator(_StubEvaluator("insufficient"), record=True)

    with pytest.raises(RuntimeError, match="retry_count"):
        _run_of(_item(["정답"]), state, tool, forcing)


# --- 부정 예시 -------------------------------------------------------------


# **부정 예시만 있는 세트는 저장소를 열기도 전에 막는다** — 놓칠 정답이 없어 이 도구의 물음에
# 원리적으로 못 답한다. `run_eval` 이 검색 채점에서 빼는 것과 같은 이유다
def test_a_set_of_only_negatives_stops_before_opening_anything(tmp_path):
    path = tmp_path / "부정만.jsonl"
    path.write_text(
        json.dumps(
            {
                "query_id": "n1",
                "query": "코퍼스에 없는 질문",
                "source_doc": "시행령",
                "gold_spans": [],
                "answerable": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="부정 예시 1건뿐"):
        collect_runs(label="아무거나", fmt="pdf", qa_path=path)


# 세트 파일이 없으면 «부정뿐» 과 다른 말을 한다 — 둘이 같은 문구면 원인을 못 가른다
def test_a_missing_set_says_so(tmp_path):
    with pytest.raises(SystemExit, match="세트가 비어 있습니다"):
        collect_runs(label="아무거나", fmt="pdf", qa_path=tmp_path / "없다.jsonl")


# --- 짝 데이터 모양 --------------------------------------------------------


def test_pair_row_is_serializable():
    row = PairRow(
        query_id="q1",
        n=1,
        rank_n=12,
        rank_next=4,
        seen=True,
        verdict=VERDICT_HELPED,
        would_run_in_production=True,
    )

    assert json.loads(json.dumps(row.__dict__, ensure_ascii=False))["verdict"] == VERDICT_HELPED
