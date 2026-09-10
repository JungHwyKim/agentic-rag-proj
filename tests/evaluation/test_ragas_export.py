"""RAGAS 재료 추출기의 규약을 잠근다.

**격리 스크립트(`ragas_probe.py`)는 여기서 못 잠근다** — `ragas` 가 본 `.venv` 에 없어 기본
pytest 가 import 조차 못 하고, 그것이 그 배치의 대가다. 그래서 잠그는 자리를 재료 쪽에 뒀다.
재료가 맞으면 격리 실행이 틀려도 그 사실이 점수 파일에 남고(버전·strictness·회계), 재료가
틀리면 아무것도 못 잡는다.

**읽는 쪽(`ragas_report.py`)도 여기서 잠근다.** 파일을 가르지 않은 이유는 그 모듈의 검사가
이미 이 파일의 「읽는 쪽」 절에 있어서다 — 나누면 한 모듈에 집이 둘이 되고, 다음 사람이 어느
쪽에 넣을지를 매번 고른다.
"""

from __future__ import annotations

import json

import pytest

from evaluation.answer_eval import AnswerRow
from evaluation.qa_set import QAItem
from evaluation.ragas_export import build_material, export
from evaluation.ragas_report import bucket_rows, check_readable, check_rules, summarize
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


# 세트 쪽 재료. **긍정에는 골든이 기본으로 차 있다** — 대부분의 테스트가 재는 것은 골든의
# 유무가 아니라 순서·짝짓기·기권이라, 매번 라벨을 적으면 그 테스트들이 무엇을 재는지 흐려진다.
# 부정은 골든이 없는 것이 정상이라 빈 채로 둔다(`validate_items()` 가 금지하는 자리다)
def qa_for(rows: list[dict], **reference_by_id: str) -> dict[str, QAItem]:
    out: dict[str, QAItem] = {}
    for r in rows:
        qid = r["query_id"]
        answerable = r.get("answerable", True)
        default = f"{qid} 의 산문 골든입니다." if answerable else ""
        out[qid] = QAItem(
            query_id=qid,
            query=r["query"],
            source_doc=r.get("source_doc", "안내서"),
            gold_spans=[],
            answerable=answerable,
            reference=reference_by_id.get(qid, default),
        )
    return out


# --- 순서 계약 ---


# 근거 번호 `[n]` 이 이 순서에서 나온다. 저장소가 섞어 줘도 리포트 순서로 되돌아야 한다
def test_contexts_follow_the_reported_order() -> None:
    store = FakeStore([sc("c1", "첫째"), sc("c2", "둘째")])
    rows = [row("q1", query="질문", answer="답[1]", chunk_ids=["c1", "c2"])]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert made[0].retrieved_contexts == ["첫째", "둘째"]


# 청크 하나가 사라지면 그 행을 통째로 버린다. **조용히 짧아지면 재료가 본판보다 적은 근거를
# 담는데, 그 상태로도 점수가 나와서 아무도 안 알아챈다**
def test_a_missing_chunk_drops_the_whole_row() -> None:
    store = FakeStore([sc("c1", "첫째"), sc("c2", "둘째")], drop={"c2"})
    rows = [row("q1", query="질문", answer="답", chunk_ids=["c1", "c2"])]
    made, dropped = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert made == []
    assert dropped["evidence_missing"] == ["q1"]


# --- 산문 골든 ---


# 골든이 실린다. **`gold_spans` 가 아니라 세트의 `reference` 다** — 이 재료가 재려는 축이
# 「답이 맞나」이고, 그것을 재는 것이 산문 골든이다
def test_the_reference_comes_from_the_set() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [row("q1", query="질문", answer="답", chunk_ids=["c1"])]
    made, _ = build_material(
        payload(rows), store, "r.json", qa_for(rows, q1="공포 후 6개월입니다.")
    )
    assert made[0].reference == "공포 후 6개월입니다."


# **두 칸으로 가른다.** 부정은 골든이 없는 것이 정상이고 pending 은 **할 일**이라, 한 칸에
# 담으면 「17/18」이 재료에서 안 보인다. 60건 세트로 넓히면 pending 이 60으로 뛴다
def test_a_missing_reference_splits_into_two_reasons() -> None:
    store = FakeStore([sc("c1", "가"), sc("c2", "나"), sc("c3", "다")])
    rows = [
        row("neg", query="부정", answer="문서에서 찾지 못했습니다.", chunk_ids=["c1"], answerable=False),
        row("pend", query="라벨 전", answer="답", chunk_ids=["c2"]),
        row("ok", query="라벨 참", answer="답", chunk_ids=["c3"]),
    ]
    made, dropped = build_material(payload(rows), store, "r.json", qa_for(rows, pend=""))
    assert dropped["negative"] == ["neg"]
    assert dropped["reference_pending"] == ["pend"]
    assert [m.query_id for m in made if m.variant == "self"] == ["ok"]


# 리포트에 있는 질의가 세트에 없으면 **사유를 내고 죽는다.** 짝이 아닌 둘을 이으면 골든이
# 조용히 비는데, 그 재료는 「라벨이 아직 없다」와 구분이 안 된다
def test_a_report_that_does_not_match_the_set_is_refused() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [row("q1", query="질문", answer="답", chunk_ids=["c1"])]
    with pytest.raises(SystemExit) as caught:
        build_material(payload(rows), store, "r.json", {})
    assert "짝이 아닙니다" in str(caught.value)


# --- 대조군 ---


# 대조군은 **남의 골든**을 물린다. 2026-09-09 에 「남의 질문」에서 갈아끼운 자리이고,
# **갈리는 것이 골든 하나여야** 지표가 무엇 때문에 떨어졌는지 읽힌다
def test_the_control_borrows_someone_elses_reference() -> None:
    store = FakeStore([sc("c1", "가"), sc("c2", "나")])
    rows = [
        row("q1", query="첫 질문", answer="첫 답", chunk_ids=["c1"], source_doc="A"),
        row("q2", query="둘째 질문", answer="둘째 답", chunk_ids=["c2"], source_doc="B"),
    ]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    controls = [m for m in made if m.variant == "control"]
    assert controls, "대조군 행이 만들어져야 한다"
    for control in controls:
        mine = next(m for m in made if m.variant == "self" and m.query_id == control.query_id)
        assert control.reference != mine.reference
        assert control.reference_from and control.reference_from != control.query_id
        # 질문·답변·근거는 그대로여야 갈리는 것이 골든 하나다
        assert control.user_input == mine.user_input
        assert control.response == mine.response
        assert control.retrieved_contexts == mine.retrieved_contexts


# **골든이 없는 행은 짝으로 안 뽑힌다.** 리포트 43행에 그대로 짝짓기를 대면 부정 25건이
# 뽑혀 «골든이 빈 대조군» 이 생기는데, 그건 대조가 아니라 못 잰 행이다
def test_a_row_without_a_reference_is_never_a_control_partner() -> None:
    store = FakeStore([sc("c1", "가"), sc("c2", "나")])
    rows = [
        row("pos", query="긍정", answer="답", chunk_ids=["c1"], source_doc="A"),
        row("neg", query="부정", answer="답", chunk_ids=["c2"], source_doc="B", answerable=False),
    ]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    # 짝이 될 수 있는 행이 자기 하나뿐이라 대조군이 아예 안 생긴다
    assert [m.variant for m in made] == ["self"]


# 근거가 겹치는 짝은 대조군이 못 된다 — 「남의 골든」이 실은 내 답변이 답하는 골든이라
# 높게 나오는 것이 정상이고, 그러면 대조군이 대조군 노릇을 못 한다
def test_a_partner_sharing_evidence_is_not_used() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [
        row("q1", query="첫 질문", answer="첫 답", chunk_ids=["c1"]),
        row("q2", query="둘째 질문", answer="둘째 답", chunk_ids=["c1"]),
    ]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert [m for m in made if m.variant == "control"] == []


# 짝이 없으면 대조군 행을 아예 안 만든다. 가짜로 채우면 대조가 아니라 잡음이다
def test_a_lonely_row_gets_no_control() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [row("q1", query="질문", answer="답", chunk_ids=["c1"])]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert [m.variant for m in made] == ["self"]


# 재료가 **자기 대조군 축을 밝힌다.** 읽는 쪽이 이것을 안 보면 `reference` 를 안 읽는 지표에
# 이 재료를 먹여 «차 0» 을 지표 탓으로 읽는다 (아래 `check_readable` 테스트가 짝이다)
def test_the_material_declares_its_control_axis(tmp_path, monkeypatch) -> None:
    made = _export_meta(tmp_path, monkeypatch)
    assert made["meta"]["control_axis"] == "reference"


# --- 기권 ---


# **기권 행을 안 뺀다 — 다만 이제 그 대상이 긍정 쪽이다.** 부정 25건은 골든이 없어 재료에서
# 통째로 빠지고, 남는 기권은 «답할 수 있는데 파이프라인이 못 찾은» 행이다(실측: 사람 세트
# 긍정 18건 중 `hsih-12`·`hsch-12` 둘이 `문서에서 찾지 못했습니다.` 로 나왔다).
# 빼면 «생성이 답을 못 냈다» 는 관측이 사라지고, 그냥 두면 «틀렸다» 로 읽힌다 — 그래서
# 싣고 `abstained` 로 갈래를 남긴다
def test_an_answerable_abstention_stays_in_the_material() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [row("q1", query="질문", answer="문서에서 찾지 못했습니다.", chunk_ids=["c1"])]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert [m.variant for m in made] == ["self"]
    assert made[0].abstained is True
    assert made[0].answerable is True
    assert made[0].reference, "골든은 그대로 실려야 «못 찾았다» 를 채점할 수 있다"


# **실제로 문 자리다** — 기권 문장에 인용 번호가 붙은 답변을 옛 리포트가 `abstained: false` 로
# 담고 있다(실측 2026-09-08: `h-cohere-18w3` 실행의 `hsih-05`·`hbas-12`. 그 리포트는
# `support_prompt_version: v1` 이라 인용을 벗겨 판정하는 규칙 이전이다). 저장값을 믿으면
# 갈래가 틀리게 세어진다
def test_an_abstention_wearing_citations_is_still_an_abstention() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [
        row(
            "q1",
            query="질문",
            answer="문서에서 찾지 못했습니다. [1]",
            chunk_ids=["c1"],
            abstained=False,
        )
    ]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert made[0].abstained is True


# 저장된 `abstained` 를 안 믿고 되살린 근거로 다시 판정한다 — 저장값은 그 실행의 판정이다
def test_the_stored_abstention_flag_is_recomputed() -> None:
    store = FakeStore([sc("c1", "가")])
    rows = [row("q1", query="질문", answer="실제 답[1]", chunk_ids=["c1"], abstained=True)]
    made, _ = build_material(payload(rows), store, "r.json", qa_for(rows))
    assert made[0].abstained is False


# --- 절대 경로 ---


# `export()` 를 저장소 없이 한 번 돌려 meta 를 얻는다. 두 테스트가 같은 배선을 쓴다
def _export_meta(tmp_path, monkeypatch) -> dict:
    store = FakeStore([sc("c1", "가"), sc("c2", "나")])
    rows = [
        row("q1", query="첫 질문", answer="첫 답", chunk_ids=["c1"], source_doc="A"),
        row("q2", query="둘 질문", answer="둘 답", chunk_ids=["c2"], source_doc="B"),
    ]
    report = tmp_path / "run-answers-mini-pdf.json"
    report.write_text(json.dumps(payload(rows), ensure_ascii=False), encoding="utf-8")

    import evaluation.ragas_export as mod

    monkeypatch.setattr(mod, "load_report", lambda label, fmt: {"indexed": [{"chunks": 2}]})
    monkeypatch.setattr(mod, "require_fresh_store", lambda *a, **k: None)
    monkeypatch.setattr(mod, "load_set_for", lambda payload, name: qa_for(rows))
    config = type("Cfg", (), {"indexing": object(), "embedding": object()})()
    monkeypatch.setattr(mod, "eval_config", lambda **k: config)
    monkeypatch.setitem(
        __import__("sys").modules,
        "rag_pipeline.ingestion.indexing",
        type("M", (), {"get_store": staticmethod(lambda *a, **k: store)}),
    )
    out = export([report])
    out["_report_name"] = report.name
    return out


# 재료에 머신의 파일 배치가 박히면 안 된다. 답변 캐시 지문이 `model_dump()` 로 절대 경로를
# 통째로 삼켰던 자리와 같은 모양이라, **필드 이름이 아니라 «경로가 값으로 새는가» 를 잰다.**
#
# **재는 범위는 «우리가 쓴 칸» 뿐이다** — `retrieved_contexts` 는 코퍼스 본문이고 거기에는
# 진짜 `C:\DATA\...` 가 들어 있다(실측: 학교기록물 문서가 파일 경로 목록을 싣는다). 전체
# 문자열에 걸면 그 68건이 걸려 검사가 **문서 내용을 우리 실수로 읽는다**
def test_no_absolute_path_leaks_into_the_material(tmp_path, monkeypatch) -> None:
    payload_out = _export_meta(tmp_path, monkeypatch)
    report_name = payload_out.pop("_report_name")
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
    assert payload_out["rows"][0]["report"] == report_name


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


# --- 읽는 쪽의 문 ---


# 옛 조합은 그대로 읽힌다. `control_axis` 가 없는 것은 **옛 재료**라는 뜻이고 그때는 축이
# «남의 질문» 하나뿐이었다 — 없는 것을 거부하면 2026-09-08 재료가 못 읽힌다
def test_the_old_pairing_still_reads() -> None:
    check_readable({"metric": "answer_relevancy"})
    check_readable({"metric": "answer_relevancy", "control_axis": "question"})


# **다른 지표를 먹이면 사유를 내고 죽는다.** 안 막으면 `row.get(METRIC_NAME)` 이 전부 `None`
# 을 돌려줘 「행 34 · 못잼 34 · 규칙 전부 잴 수 없다」라는 멀쩡해 보이는 표가 나온다
def test_another_metric_is_refused_with_a_reason() -> None:
    with pytest.raises(SystemExit) as caught:
        check_readable({"metric": "factual_correctness"})
    said = str(caught.value)
    assert "answer_relevancy" in said and "factual_correctness" in said


# **«남의 골든» 축도 사유를 내고 죽는다.** `answer_relevancy` 는 `reference` 를 안 읽어
# 그 대조군이 self 와 입력이 글자까지 같아지고, 규칙 1 이 «차 0» 을 낸다 — 그것은 지표가
# 아니라 재료가 만든 수다
def test_the_reference_control_axis_is_refused_with_a_reason() -> None:
    with pytest.raises(SystemExit) as caught:
        check_readable({"metric": "answer_relevancy", "control_axis": "reference"})
    assert "남의 골든" in str(caught.value)


# `AnswerRow` 가 재료의 입력 스키마다 — 리포트 행을 그대로 먹일 수 있어야 한다
def test_report_rows_load_as_answer_rows() -> None:
    made = AnswerRow(**row("q1", query="질문", answer="답", chunk_ids=["c1"]))
    assert made.query_id == "q1"
