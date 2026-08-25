"""리랭커 해부 도구. **재지 않고 읽는다** 는 계약을 잠근다.

제일 중요한 것 셋이다.

1. **재현이 리포트와 어긋나면 경고가 남는다.** 풀을 다시 만들어 읽는 도구라, 어긋난 채로
   조용히 수치를 내놓으면 *"그때 그 순위"* 가 아닌 것을 그 순위라고 부르게 된다
2. **캐시로 읽었는지 새로 쟀는지가 결과에 실린다.** 안 실으면 *"그때 그 점수"* 와
   *"지금 다시 잰 점수"* 가 구분되지 않는다
3. **BM25 전용 판정은 `dense_score is None` 이다.** 3회차의 융합 재판정 조건이 이 칸을
   보므로, dense 가 뽑은 후보를 BM25 전용으로 세면 재판정이 통째로 뒤집힌다

외부 API 도 모델 가중치도 안 건드린다 — 저장소·임베더·리랭커를 전부 가짜로 준다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluation import rerank_diff
from evaluation.rerank_diff import Displacer, build_row, load_run, make_counter, select_queries
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 정답 판정에 쓸 최소 질의 항목. `QAItem` 의 필드 중 이 도구가 읽는 것만 흉내낸다
class FakeItem:
    def __init__(self, query_id: str, query: str, gold_spans: list[str]) -> None:
        self.query_id = query_id
        self.query = query
        self.gold_spans = gold_spans


def _chunk(chunk_id: str, text: str, *, dense: float | None, bm25: float | None) -> RetrievedChunk:
    chunk = RetrievedChunk(
        chunk_id=chunk_id, document_id="doc.pdf", text=text, dense_score=dense, bm25_score=bm25
    )
    chunk.fused_score = 0.03
    return chunk


# ─────────────────────────────── 질의 고르기 ───────────────────────────────


# 나빠진 것만 고른다. **순위가 없는 것(후보 창 밖)은 제일 뒤로 민다**
def test_worse_only_picks_queries_that_got_worse() -> None:
    before = {"a": 1, "b": 5, "c": 3, "d": None}
    after = {"a": 12, "b": 2, "c": 3, "d": 4}

    picked = select_queries(
        ["a", "b", "c", "d"], before, after, wanted=[], worse_only=True
    )

    # a 만 나빠졌다 — b 는 좋아졌고 c 는 그대로이며 d 는 없음에서 4위로 왔다
    assert picked == ["a"]


# `--query` 가 이긴다. 세트에 없는 이름을 주면 조용히 넘어가지 않는다
def test_named_query_wins_and_unknown_id_stops() -> None:
    assert select_queries(["a", "b"], {}, {}, wanted=["b"], worse_only=True) == ["b"]

    with pytest.raises(SystemExit, match="세트에 없는 질의"):
        select_queries(["a"], {}, {}, wanted=["없는것"], worse_only=False)


# 한쪽 실행에만 있는 질의는 조용히 안 버린다 (`overlap.py` 와 같은 규약)
def test_query_missing_from_one_run_stops() -> None:
    with pytest.raises(SystemExit, match="두 실행에 다 있지"):
        select_queries(["a", "b"], {"a": 1}, {"a": 2, "b": 3}, wanted=[], worse_only=True)


# 고를 방법을 안 주면 멈춘다. 기본값으로 60건을 다 도는 것이 아니다
def test_no_selection_stops() -> None:
    with pytest.raises(SystemExit, match="--worse-only"):
        select_queries(["a"], {}, {}, wanted=[], worse_only=False)


# ─────────────────────────────── 한 행 만들기 ───────────────────────────────


# 재현한 순위가 리포트와 같으면 경고가 없다
def test_no_warning_when_reproduction_matches_report() -> None:
    ranked = [_chunk("c1", "다른 말", dense=0.5, bm25=None), _chunk("c2", "정답", dense=0.4, bm25=1.0)]

    row = build_row(
        item=FakeItem("q1", "질문", ["정답"]),
        before_rank=1,
        after_rank=2,
        pool_fused={"c1": 3, "c2": 1},
        ranked=ranked,
        fused_rank=1,
        reranked_rank=2,
        count=lambda _t: None,
        warnings=[],
    )

    assert row.warnings == []
    assert row.reranked_rank == 2
    assert row.gold_chunk_id == "c2"


# **어긋나면 반드시 남는다.** 풀이 그 실행의 풀이 아니라는 뜻이라, 이 행의 수치를 못 믿는다
def test_warning_when_reproduced_rank_differs_from_report() -> None:
    ranked = [_chunk("c1", "다른 말", dense=0.5, bm25=None), _chunk("c2", "정답", dense=0.4, bm25=None)]

    row = build_row(
        item=FakeItem("q1", "질문", ["정답"]),
        before_rank=1,
        after_rank=9,
        pool_fused={"c1": 1, "c2": 2},
        ranked=ranked,
        fused_rank=1,
        reranked_rank=2,
        count=lambda _t: None,
        warnings=[],
    )

    assert any("풀이 그 실행의 풀이 아닙니다" in w for w in row.warnings)


# 리랭커가 폴백했으면 그것도 행에 실린다 — 안 실으면 "효과 없음" 과 구분이 안 된다
def test_reranker_fallback_warning_is_carried() -> None:
    row = build_row(
        item=FakeItem("q1", "질문", ["정답"]),
        before_rank=1,
        after_rank=1,
        pool_fused={"c1": 1},
        ranked=[_chunk("c1", "정답", dense=0.5, bm25=None)],
        fused_rank=1,
        reranked_rank=1,
        count=lambda _t: None,
        warnings=["cross-encoder 를 올리지 못해 RRF 순서를 씁니다"],
    )

    assert any("리랭커 폴백" in w for w in row.warnings)


# 정답 위에 있던 것만 밀어낸 후보다. 아래 것은 안 센다
def test_displacers_are_only_candidates_above_the_gold() -> None:
    ranked = [
        _chunk("c1", "위1", dense=0.5, bm25=None),
        _chunk("c2", "위2", dense=None, bm25=9.0),
        _chunk("c3", "정답", dense=0.4, bm25=None),
        _chunk("c4", "아래", dense=0.3, bm25=None),
    ]

    row = build_row(
        item=FakeItem("q1", "질문", ["정답"]),
        before_rank=1,
        after_rank=3,
        pool_fused={"c1": 5, "c2": 40, "c3": 1, "c4": 2},
        ranked=ranked,
        fused_rank=1,
        reranked_rank=3,
        count=lambda _t: None,
        warnings=[],
    )

    assert [d.chunk_id for d in row.displacers] == ["c1", "c2"]
    # 융합에서 몇 위였는지를 함께 나른다 — 리랭커가 얼마나 끌어올렸는지가 거기서 보인다
    assert [d.fused_rank for d in row.displacers] == [5, 40]
    assert row.bm25_only_count == 1


# **BM25 전용은 dense 가 안 뽑은 것이다.** 둘 다 있는 후보를 여기 세면 재판정이 뒤집힌다
@pytest.mark.parametrize(
    ("dense", "bm25", "expected"),
    [
        (None, 9.0, True),  # dense 가 안 뽑았고 BM25 만 뽑았다
        (0.5, 9.0, False),  # 둘 다 뽑았다 — 융합이 새로 넣은 후보가 아니다
        (0.5, None, False),  # dense 만 뽑았다
        (None, None, False),  # 둘 다 없다 (있을 수 없지만 전용으로 세면 안 된다)
    ],
)
def test_bm25_only_is_dense_none_and_bm25_present(
    dense: float | None, bm25: float | None, expected: bool
) -> None:
    displacer = Displacer(
        rank=1, fused_rank=1, chunk_id="c1", rerank_score=0.1,
        dense_score=dense, bm25_score=bm25, chars=10, tokens=None, head="머리",
    )

    assert displacer.bm25_only is expected


# 정답을 아예 못 찾았으면 밀어낸 후보를 안 센다 — 0 을 근거로 쓰면 거짓이 된다
def test_no_displacers_when_gold_is_not_in_the_pool() -> None:
    row = build_row(
        item=FakeItem("q1", "질문", ["없는말"]),
        before_rank=None,
        after_rank=None,
        pool_fused={"c1": 1},
        ranked=[_chunk("c1", "다른 말", dense=0.5, bm25=None)],
        fused_rank=None,
        reranked_rank=None,
        count=lambda _t: None,
        warnings=[],
    )

    assert row.displacers == []
    assert row.gold_chunk_id is None
    assert row.warnings == []


# ─────────────────────────────── 부수적인 계약 ───────────────────────────────


# 토큰 세기는 **기본이 꺼짐**이다. 안 그러면 기본 실행이 토크나이저를 받으러 나간다
def test_token_counter_is_off_by_default() -> None:
    assert make_counter("BAAI/bge-reranker-v2-m3", enabled=False)("아무 글") is None
    assert make_counter(None, enabled=True)("아무 글") is None


# 없는 실행을 조용히 넘어가지 않는다
def test_missing_run_stops(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="실행 JSON 이 없습니다"):
        load_run(tmp_path / "없는실행.json")


# 리랭커 없이 돈 실행을 뒤에 두면 멈춘다. **안 막으면 대조군끼리 견주고 "효과 없음" 이 된다**
def test_run_without_reranker_as_after_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    report = {
        "format": "pdf",
        "label": "c1-baseline",
        "k": 50,
        "embedding_profile": "baseline",
        "rerank_profile": "off",
        "per_query": [{"query_id": "q1", "rank": 1}],
        "indexed": [{"chunks": 10}],
        "retriever": {"use_bm25": True},
    }
    monkeypatch.setattr(rerank_diff, "load_run", lambda _n: (report, "c1-baseline-pdf"))
    monkeypatch.setattr(
        rerank_diff, "load_qa_set", lambda: type("S", (), {"items": [FakeItem("q1", "질문", ["정답"])]})()
    )

    with pytest.raises(SystemExit, match="리랭커 없이 돈 실행"):
        rerank_diff.diff_queries(
            before_name="a", after_name="b", wanted=["q1"], worse_only=False
        )
