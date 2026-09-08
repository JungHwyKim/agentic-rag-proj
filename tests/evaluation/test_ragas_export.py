"""RAGAS 재료 추출기의 규약을 잠근다.

**격리 스크립트(`ragas_probe.py`)는 여기서 못 잠근다** — `ragas` 가 본 `.venv` 에 없어 기본
pytest 가 import 조차 못 하고, 그것이 그 배치의 대가다. 그래서 잠그는 자리를 재료 쪽에 뒀다.
재료가 맞으면 격리 실행이 틀려도 그 사실이 점수 파일에 남고(버전·strictness·회계), 재료가
틀리면 아무것도 못 잡는다.
"""

from __future__ import annotations

import json

import pytest

from evaluation.answer_eval import AnswerRow
from evaluation.ragas_export import build_material, export
from evaluation.ragas_report import bucket_rows, check_rules, summarize
from rag_pipeline.schemas.vector import StoredChunk, point_id_for


# 저장소가 돌려주는 청크 하나
def sc(chunk_id: str, text: str, doc: str = "안내서.pdf") -> StoredChunk:
    return StoredChunk(
        id=point_id_for(chunk_id),
        text=text,
        document_id=doc,
        chunk_id=chunk_id,
        metadata={"source_name": doc},
    )


# **순서를 일부러 섞어 돌려준다** — 실제 qdrant 가 그러기 때문이고, 그것이 이 테스트의 요점이다
class FakeStore:
    def __init__(self, chunks: list[StoredChunk], *, drop: set[str] | None = None) -> None:
        self._by_point = {c.id: c for c in chunks}
        self._drop = drop or set()

    def fetch_by_ids(self, point_ids: list[str]) -> list[StoredChunk]:
        got = [self._by_point[p] for p in point_ids if p in self._by_point]
        got = [c for c in got if c.chunk_id not in self._drop]
        return list(reversed(got))

    def count(self) -> int:
        return len(self._by_point)

    def close(self) -> None:
        return None


def row(query_id: str, *, query: str, answer: str, chunk_ids: list[str], **kw) -> dict:
    base = {
        "query_id": query_id,
        "query": query,
        "source_doc": kw.pop("source_doc", "안내서"),
        "answerable": kw.pop("answerable", True),
        "answer": answer,
        "chunk_ids": chunk_ids,
    }
    base.update(kw)
    return base


def payload(rows: list[dict]) -> dict:
    return {"meta": {"label": "L", "format": "pdf", "qa_set": "qa_set.jsonl"}, "rows": rows}


# --- 순서 계약 ---


# 근거 번호 `[n]` 이 이 순서에서 나온다. 저장소가 섞어 줘도 리포트 순서로 되돌아야 한다
def test_contexts_follow_the_reported_order() -> None:
    store = FakeStore([sc("c1", "첫째"), sc("c2", "둘째")])
    made, _ = build_material(
        payload([row("q1", query="질문", answer="답[1]", chunk_ids=["c1", "c2"])]), store, "r.json"
    )
    assert made[0].retrieved_contexts == ["첫째", "둘째"]


# 청크 하나가 사라지면 그 행을 통째로 버린다. **조용히 짧아지면 재료가 본판보다 적은 근거를
# 담는데, 그 상태로도 점수가 나와서 아무도 안 알아챈다**
def test_a_missing_chunk_drops_the_whole_row() -> None:
    store = FakeStore([sc("c1", "첫째"), sc("c2", "둘째")], drop={"c2"})
    made, dropped = build_material(
        payload([row("q1", query="질문", answer="답", chunk_ids=["c1", "c2"])]), store, "r.json"
    )
    assert made == []
    assert dropped["evidence_missing"] == ["q1"]


# --- 대조군 ---


# 대조군은 **남의 질문**을 물린다. 자기 질문이면 self 와 같은 행이라 대조가 아니다
def test_the_control_borrows_someone_elses_question() -> None:
    store = FakeStore([sc("c1", "가"), sc("c2", "나")])
    made, _ = build_material(
        payload(
            [
                row("q1", query="첫 질문", answer="첫 답", chunk_ids=["c1"], source_doc="A"),
                row("q2", query="둘째 질문", answer="둘째 답", chunk_ids=["c2"], source_doc="B"),
            ]
        ),
        store,
        "r.json",
    )
    controls = [m for m in made if m.variant == "control"]
    assert controls, "대조군 행이 만들어져야 한다"
    for control in controls:
        mine = next(m for m in made if m.variant == "self" and m.query_id == control.query_id)
        assert control.user_input != mine.user_input
        # 답변과 근거는 그대로여야 갈리는 것이 질문 하나다
        assert control.response == mine.response
        assert control.retrieved_contexts == mine.retrieved_contexts


# 근거가 겹치는 짝은 대조군이 못 된다 — 「남의 질문」이 실은 내 답변이 답할 수 있는 질문이라
# 높게 나오는 것이 정상이고, 그러면 대조군이 대조군 노릇을 못 한다
def test_a_partner_sharing_evidence_is_not_used() -> None:
    store = FakeStore([sc("c1", "가")])
    made, _ = build_material(
        payload(
            [
                row("q1", query="첫 질문", answer="첫 답", chunk_ids=["c1"]),
                row("q2", query="둘째 질문", answer="둘째 답", chunk_ids=["c1"]),
            ]
        ),
        store,
        "r.json",
    )
    assert [m for m in made if m.variant == "control"] == []


# 짝이 없으면 대조군 행을 아예 안 만든다. 가짜로 채우면 대조가 아니라 잡음이다
def test_a_lonely_row_gets_no_control() -> None:
    store = FakeStore([sc("c1", "가")])
    made, _ = build_material(
        payload([row("q1", query="질문", answer="답", chunk_ids=["c1"])]), store, "r.json"
    )
    assert [m.variant for m in made] == ["self"]


# --- 기권 ---


# 기권 행을 안 뺀다. 부정 예시에서는 기권이 정답이라 빼면 그 관측이 사라진다
def test_abstained_rows_stay_in_the_material() -> None:
    store = FakeStore([sc("c1", "가")])
    made, _ = build_material(
        payload(
            [
                row(
                    "q1",
                    query="질문",
                    answer="문서에서 찾지 못했습니다.",
                    chunk_ids=["c1"],
                    answerable=False,
                )
            ]
        ),
        store,
        "r.json",
    )
    assert [m.variant for m in made] == ["self"]
    assert made[0].abstained is True
    assert made[0].answerable is False


# **실제로 문 자리다** — 기권 문장에 인용 번호가 붙은 답변을 옛 리포트가 `abstained: false` 로
# 담고 있다(실측 2026-09-08: `h-cohere-18w3` 판의 `hsih-05`·`hbas-12`. 그 리포트는
# `support_prompt_version: v1` 이라 인용을 벗겨 판정하는 규칙 이전이다). 저장값을 믿으면
# 부정 갈래가 «기권 11 · 답한 14» 로 세어지는데 현재 규칙으로는 **13 · 12** 다
def test_an_abstention_wearing_citations_is_still_an_abstention() -> None:
    store = FakeStore([sc("c1", "가")])
    made, _ = build_material(
        payload(
            [
                row(
                    "q1",
                    query="질문",
                    answer="문서에서 찾지 못했습니다. [1]",
                    chunk_ids=["c1"],
                    answerable=False,
                    abstained=False,
                )
            ]
        ),
        store,
        "r.json",
    )
    assert made[0].abstained is True


# 저장된 `abstained` 를 안 믿고 되살린 근거로 다시 판정한다 — 저장값은 그 실행의 판정이다
def test_the_stored_abstention_flag_is_recomputed() -> None:
    store = FakeStore([sc("c1", "가")])
    made, _ = build_material(
        payload([row("q1", query="질문", answer="실제 답[1]", chunk_ids=["c1"], abstained=True)]),
        store,
        "r.json",
    )
    assert made[0].abstained is False


# --- 절대 경로 ---


# 재료에 머신의 파일 배치가 박히면 안 된다. 답변 캐시 지문이 `model_dump()` 로 절대 경로를
# 통째로 삼켰던 자리와 같은 모양이라, **필드 이름이 아니라 «경로가 값으로 새는가» 를 잰다.**
#
# **재는 범위는 «우리가 쓴 칸» 뿐이다** — `retrieved_contexts` 는 코퍼스 본문이고 거기에는
# 진짜 `C:\DATA\...` 가 들어 있다(실측: 학교기록물 문서가 파일 경로 목록을 싣는다). 전체
# 문자열에 걸면 그 68건이 걸려 검사가 **문서 내용을 우리 실수로 읽는다**
def test_no_absolute_path_leaks_into_the_material(tmp_path, monkeypatch) -> None:
    store = FakeStore([sc("c1", "가"), sc("c2", "나")])
    report = tmp_path / "run-answers-mini-pdf.json"
    report.write_text(
        json.dumps(
            payload(
                [
                    row("q1", query="첫 질문", answer="첫 답", chunk_ids=["c1"], source_doc="A"),
                    row("q2", query="둘 질문", answer="둘 답", chunk_ids=["c2"], source_doc="B"),
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import evaluation.ragas_export as mod

    monkeypatch.setattr(mod, "load_report", lambda label, fmt: {"indexed": [{"chunks": 2}]})
    monkeypatch.setattr(mod, "require_fresh_store", lambda *a, **k: None)
    config = type("Cfg", (), {"indexing": object(), "embedding": object()})()
    monkeypatch.setattr(mod, "eval_config", lambda **k: config)
    monkeypatch.setitem(
        __import__("sys").modules,
        "rag_pipeline.ingestion.indexing",
        type("M", (), {"get_store": staticmethod(lambda *a, **k: store)}),
    )

    payload_out = export([report])
    authored = json.dumps(
        {
            "meta": payload_out["meta"],
            "rows": [
                {k: v for k, v in r.items() if k != "retrieved_contexts"}
                for r in payload_out["rows"]
            ],
        },
        ensure_ascii=False,
    )
    for needle in (":\\", ":/", str(tmp_path), tmp_path.name):
        assert needle not in authored, f"재료에 절대 경로가 샜다: {needle}"
    # 리포트는 **이름**으로만 실린다 — 그것이 경로 대신 남는 신원이다
    assert payload_out["rows"][0]["report"] == report.name


# --- 읽는 쪽 ---


# 못 잰 행을 0 으로 접지 않는다. 접으면 실패가 «관련 없음» 이라는 점수로 둔갑한다
def test_unscored_rows_are_counted_not_zeroed() -> None:
    bucket = summarize("x", [1.0, None, 0.5])
    assert (bucket.rows, bucket.scored, bucket.unscored) == (3, 2, 1)
    assert bucket.median == 0.75


# 사람 세트의 부정은 기권 여부로 다시 갈린다 — 그 둘이 이 탐침의 핵심 관측이다
def test_the_human_negatives_split_by_abstention() -> None:
    scored = {
        "meta": {"source": [{"report": "h.json", "qa_set": "qa_set_human.jsonl"}]},
        "rows": [
            {"report": "h.json", "variant": "self", "answerable": True, "answer_relevancy": 0.9},
            {
                "report": "h.json",
                "variant": "self",
                "answerable": False,
                "abstained": True,
                "answer_relevancy": 0.0,
            },
            {
                "report": "h.json",
                "variant": "self",
                "answerable": False,
                "abstained": False,
                "answer_relevancy": 0.8,
            },
            {"report": "h.json", "variant": "control", "answerable": True, "answer_relevancy": 0.1},
        ],
    }
    buckets = bucket_rows(scored)
    assert set(buckets) == {
        "사람 세트 · 긍정",
        "사람 세트 · 부정 · 기권 (기권이 정답)",
        "사람 세트 · 부정 · 답함 (오답)",
        "대조군 (남의 질문)",
    }


# 규칙 셋이 수를 먼저 찍고 판정을 붙인다. 뒤집힌 경우를 문장으로 잠근다
@pytest.mark.parametrize(
    ("answered", "refused", "expected"),
    [(0.8, 0.0, "사람 세트에는 안 씀"), (0.0, 0.8, "뒤집히지 않았다")],
)
def test_rule_three_reads_the_negative_flip(answered: float, refused: float, expected: str) -> None:
    buckets = {
        "사람 세트 · 부정 · 답함 (오답)": summarize("답함", [answered]),
        "사람 세트 · 부정 · 기권 (기권이 정답)": summarize("기권", [refused]),
    }
    line = next(x for x in check_rules(buckets) if x.startswith("규칙 3"))
    assert expected in line


# 대조군이 없으면 «잴 수 없다» 라고 말한다 — 없는 것을 통과로 접으면 게이트가 사라진다
def test_a_missing_control_is_said_out_loud() -> None:
    buckets = {"LLM 세트 · a.json": summarize("a", [0.7])}
    assert "잴 수 없다" in next(x for x in check_rules(buckets) if x.startswith("규칙 1"))


# `AnswerRow` 가 재료의 입력 스키마다 — 리포트 행을 그대로 먹일 수 있어야 한다
def test_report_rows_load_as_answer_rows() -> None:
    made = AnswerRow(**row("q1", query="질문", answer="답", chunk_ids=["c1"]))
    assert made.query_id == "q1"
