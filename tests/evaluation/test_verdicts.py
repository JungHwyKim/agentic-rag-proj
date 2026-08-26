"""판정 채점 도구. **키 없이 돈다** — 검색도 판정도 가짜로 갈아끼운다.

여기서 잠그는 것은 둘이다. **기대 verdict 가 세트가 아니라 그 실행의 순위에서 나오는 것**과
**실패가 혼동행렬 칸이 아니라 열로 세어지는 것.** 앞을 놓치면 이 표가 Evaluator 가 아니라
검색기를 재고, 뒤를 놓치면 죽은 Evaluator 가 부정 예시에서 만점을 받는다
(plan_log/검색-생성.md 10·11회차).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import verdicts as verdicts_module
from evaluation.verdicts import VerdictRow, confusion, expected_verdict, score_verdicts
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk

_GOLD = "보존기간은 영구로 한다"


# 세트 두 줄 — 정답이 있는 것 하나, 부정 예시 하나
@pytest.fixture
def qa_path(tmp_path: Path) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "query_id": "pos-01",
                        "query": "보존기간은?",
                        "source_doc": "학교기록물",
                        "gold_spans": [_GOLD],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "query_id": "neg-01",
                        "query": "이 문서에 없는 것",
                        "source_doc": "학교기록물",
                        "gold_spans": [],
                        "answerable": False,
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    return path


# 판정하는 척하는 가짜. 질의별로 다른 답을 주도록 dict 를 받는다
class _FakeEvaluator:
    def __init__(self, answers: dict[str, str], warn: str | None = None) -> None:
        self._answers = answers
        self._warn = warn
        self.warnings: list[str] = []
        self.usage = type("Usage", (), {"reset": lambda self: None})()
        self.seen: list[tuple[str, int]] = []

    # 사유는 고정이다 — 이 도구가 재는 것은 verdict 뿐이라 내용이 판정에 안 쓰인다
    def evaluate(self, query: str, chunks: list[RetrievedChunk]) -> EvaluatorVerdict:
        self.seen.append((query, len(chunks)))
        verdict = self._answers.get(query, "insufficient")
        if verdict == "failed" and self._warn:
            self.warnings.append(self._warn)
        return EvaluatorVerdict(verdict=verdict, reasoning="가짜")


# 저장소는 세는 것과 닫는 것만 한다
class _FakeStore:
    def __init__(self) -> None:
        self.closed = False

    def count(self) -> int:
        return 3

    def close(self) -> None:
        self.closed = True


# 검색·저장소·판정을 전부 갈아끼운다. `hits` 가 그 질의로 올라올 청크 텍스트다
def _wire(mocker, evaluator, hits: dict[str, list[str]]):  # noqa: ANN001, ANN202
    store = _FakeStore()
    mocker.patch.object(
        verdicts_module,
        "load_report",
        return_value={
            "label": "run", "format": "pdf",
            "embedding_profile": "kure-v1", "indexed": [{"chunks": 3}],
        },
    )
    mocker.patch("rag_pipeline.ingestion.embedding.get_embedder", return_value=object())
    mocker.patch("rag_pipeline.ingestion.indexing.get_store", return_value=store)
    mocker.patch("rag_pipeline.retrieval.get_reranker", return_value=None)
    mocker.patch(
        "rag_pipeline.retrieval.build_bm25_index",
        return_value=type("Bm25", (), {"warnings": []})(),
    )
    mocker.patch("rag_pipeline.generation.get_evaluator", return_value=evaluator)
    mocker.patch(
        "rag_pipeline.retrieval.retrieve",
        side_effect=lambda query, **kw: [
            RetrievedChunk(chunk_id=f"d:{i:04d}", document_id="d", text=text)
            for i, text in enumerate(hits.get(query, []))
        ],
    )
    return store


# 부정 예시는 늘 불충분이 옳다. 긍정은 **그 실행에서 정답이 왔는지**가 정한다
def test_expected_comes_from_this_run() -> None:
    assert expected_verdict(answerable=True, rank=1) == "sufficient"
    assert expected_verdict(answerable=True, rank=None) == "insufficient"
    assert expected_verdict(answerable=False, rank=None) == "insufficient"


# 정답이 상위 k 안에 오면 기대가 *충분* 이다
def test_gold_in_results_expects_sufficient(mocker, qa_path) -> None:
    evaluator = _FakeEvaluator({"보존기간은?": "sufficient", "이 문서에 없는 것": "insufficient"})
    _wire(mocker, evaluator, {"보존기간은?": [f"제5조 {_GOLD}."], "이 문서에 없는 것": ["엉뚱한 본문"]})

    rows, meta = score_verdicts(label="run", fmt="pdf", top_k=5, qa_path=qa_path)

    assert [r.expected for r in rows] == ["sufficient", "insufficient"]
    assert all(r.correct for r in rows)
    assert meta["top_k"] == 5


# **정답이 안 왔으면 긍정 질의도 *불충분* 이 옳은 답이다.** 세트에 정답표를 고정하면
# 이 건이 오답으로 세어지고, 그 순간 표가 Evaluator 가 아니라 검색기를 잰다
def test_gold_missing_flips_the_expectation(mocker, qa_path) -> None:
    evaluator = _FakeEvaluator({"보존기간은?": "insufficient", "이 문서에 없는 것": "insufficient"})
    _wire(mocker, evaluator, {"보존기간은?": ["관련은 있지만 답이 없는 본문"]})

    rows, _ = score_verdicts(label="run", fmt="pdf", top_k=5, qa_path=qa_path)

    positive = rows[0]
    assert positive.rank is None
    assert positive.expected == "insufficient"
    assert positive.correct


# **실패는 칸이 아니라 열이다.** 맞은 것으로도 틀린 것으로도 안 세어진다
def test_failure_is_a_column_not_a_cell(mocker, qa_path) -> None:
    evaluator = _FakeEvaluator(
        {"보존기간은?": "failed", "이 문서에 없는 것": "failed"}, warn="키 없음"
    )
    _wire(mocker, evaluator, {"보존기간은?": [f"제5조 {_GOLD}."]})

    rows, _ = score_verdicts(label="run", fmt="pdf", top_k=5, qa_path=qa_path)
    table = confusion(rows)

    assert [r.actual for r in rows] == ["failed", "failed"]
    assert not any(r.correct for r in rows)
    # 부정 예시가 실패로 만점을 받는 일이 없어야 한다
    assert table["insufficient"]["insufficient"] == 0
    assert table["insufficient"]["failed"] == 1
    assert table["sufficient"]["failed"] == 1
    assert [r.warnings for r in rows] == [["키 없음"], ["키 없음"]]


# top_k 가 판정에 실제로 먹는다. 분모가 바뀌는 자리라 조용히 무시되면 안 된다
def test_top_k_reaches_the_evaluator(mocker, qa_path) -> None:
    evaluator = _FakeEvaluator({})
    _wire(mocker, evaluator, {"보존기간은?": ["가", "나", "다"]})

    score_verdicts(label="run", fmt="pdf", top_k=3, qa_path=qa_path)

    assert evaluator.seen[0] == ("보존기간은?", 3)


# 저장소는 반드시 닫힌다. 안 닫으면 다음 실행이 배타 잠금으로 죽는다
def test_store_is_closed_even_on_error(mocker, qa_path) -> None:
    evaluator = _FakeEvaluator({})
    store = _wire(mocker, evaluator, {})
    mocker.patch.object(evaluator, "evaluate", side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        score_verdicts(label="run", fmt="pdf", top_k=5, qa_path=qa_path)

    assert store.closed


# **판정이 꺼져 있으면 조용히 안 넘어간다.** 전건 실패인 표는 멀쩡해 보인다
def test_stops_when_the_evaluator_is_off(mocker, qa_path) -> None:
    _wire(mocker, _FakeEvaluator({}), {})
    mocker.patch("rag_pipeline.generation.get_evaluator", return_value=None)

    with pytest.raises(SystemExit):
        score_verdicts(label="run", fmt="pdf", top_k=5, qa_path=qa_path)


# 표는 기대 두 줄 × 실제 세 칸이다. 빈 칸도 0 으로 있어야 리포트 모양이 실행마다 안 갈린다
def test_confusion_shape_is_stable() -> None:
    table = confusion(
        [
            VerdictRow(
                query_id="a", query="q", source_doc="d", answerable=True,
                rank=1, expected="sufficient", actual="sufficient",
            )
        ]
    )
    assert sorted(table) == ["insufficient", "sufficient"]
    assert sorted(table["sufficient"]) == ["failed", "insufficient", "sufficient"]
