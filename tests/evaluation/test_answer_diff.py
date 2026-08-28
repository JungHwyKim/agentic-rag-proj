"""두 답변 리포트를 짝짓는 도구가 **잘못 짝지어도 그럴듯한 표를 내는 자리들**을 잠근다.

이 도구는 유료 호출을 한 번도 안 하는데, 그래서 위험이 «비싸다» 가 아니라 **«싸게 틀린다»** 다.
축이 어긋난 두 리포트를 짝지어도 표는 멀쩡히 나오고, 근거가 같은 군과 갈린 군을 합쳐도
숫자는 그럴듯하다. 여기서 재는 것은 대부분 지표가 아니라 **그 표를 못 읽게 만드는 조건**이다.

키 없이 전부 돈다 — 리포트 dict 만 읽는다.
"""

from __future__ import annotations

import json

import pytest

from evaluation.answer_diff import (
    PairedRow,
    axis_problems,
    cited_chunk_ids,
    load_answer_report,
    pair_rows,
    reproduction_problems,
    split_by_evidence,
)


def meta(**overrides) -> dict:
    base = {
        "label": "combo-fuse-cohere",
        "format": "pdf",
        "qa_set": "qa_set.jsonl",
        "top_k": 5,
        "embedding_profile": "kure-v1-gpu",
        "rerank_profile": "cohere",
        "rewrite_profile": "off",
        "generation_profile": "prod",
        "judge_profile": "mini",
        "support_prompt_version": "v1",
    }
    base.update(overrides)
    return base


def row(query_id: str, chunk_ids: list[str], cited: list[int], **overrides) -> dict:
    base = {
        "query_id": query_id,
        "query": "질문",
        "source_doc": "안내서.pdf",
        "answerable": True,
        "answer": "답변 [1]",
        "chunk_ids": chunk_ids,
        "rewritten": None,
        "abstained": False,
        "citation_problem": None,
        "cited": cited,
        "cited_gold_hits": 1,
        "cited_count": len(cited),
        "support": "supported",
        "support_reasoning": "",
        "unsupported_sentences": [],
        "control_from": None,
        "control": None,
        "warnings": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 축 게이트 — 다른 실험 둘을 짝지어도 표는 멀쩡히 나온다
# ---------------------------------------------------------------------------


# 견줄 축(재작성)만 다르면 통과한다. 이것이 정상 경로다
def test_only_the_rewrite_axis_may_differ() -> None:
    assert axis_problems(meta(rewrite_profile="off"), meta(rewrite_profile="fuse")) == []


# **`top_k` 가 다르면 멈춘다.** 생성이 보는 근거 수가 곧 그 값이라, 5와 10을 짝지으면
# 표가 재작성이 아니라 근거 개수를 재게 되는데 **에러가 안 난다**
def test_a_different_top_k_stops_the_pairing() -> None:
    problems = axis_problems(
        meta(rewrite_profile="off"), meta(rewrite_profile="fuse", top_k=10)
    )

    assert len(problems) == 1
    assert "top_k" in problems[0]


# **리랭커가 다르면 멈춘다** — 「흡수가 리랭커 종류를 탄다」(39회차)라 갈래를 섞으면
# 차이에 리랭커와 재작성이 함께 들어간다
def test_a_different_reranker_stops_the_pairing() -> None:
    problems = axis_problems(
        meta(rewrite_profile="off"), meta(rewrite_profile="fuse", rerank_profile="local-gpu")
    )

    assert len(problems) == 1
    assert "rerank_profile" in problems[0]


# **재작성이 같으면 멈춘다** — 견줄 축이 없어 그 짝은 같은 조건의 두 실행이다.
# 온도 잡음만 재게 되는데 표는 «조건 비교» 처럼 보인다
def test_the_same_rewrite_profile_stops_the_pairing() -> None:
    problems = axis_problems(meta(), meta())

    assert len(problems) == 1
    assert "재작성 프로필이 같습니다" in problems[0]


# 어긋난 축이 여럿이면 전부 돌려준다 — 하나만 고치고 다시 부딪히는 것을 막는다
def test_every_mismatched_axis_is_reported() -> None:
    problems = axis_problems(
        meta(rewrite_profile="off"),
        meta(rewrite_profile="fuse", top_k=10, judge_profile="luna"),
    )

    assert len(problems) == 2


# ---------------------------------------------------------------------------
# 짝짓기 — 한쪽에만 있는 질의를 조용히 버리면 두 표의 분모가 달라진다
# ---------------------------------------------------------------------------


def test_pairing_matches_by_query_id() -> None:
    a = [row("q1", ["c1"], [1]), row("q2", ["c2"], [1])]
    b = [row("q2", ["c2"], [1]), row("q1", ["c1"], [1])]

    pairs, problems = pair_rows(a, b)

    assert problems == []
    assert [pair.query_id for pair in pairs] == ["q1", "q2"]


# **짝이 없는 질의를 조용히 버리지 않는다.** 버리면 «60건을 견줬다» 고 적은 표가 실은
# 58건이고, 그 사실이 어디에도 안 남는다
def test_an_unpaired_query_is_reported_not_dropped() -> None:
    pairs, problems = pair_rows([row("q1", ["c1"], [1])], [row("q2", ["c2"], [1])])

    assert pairs == []
    assert len(problems) == 1
    assert "짝이 없는 질의" in problems[0]


# ---------------------------------------------------------------------------
# 군 나누기 — 잡음 바닥과 신호를 가르는 자리
# ---------------------------------------------------------------------------


# **순서까지 본다.** 프롬프트의 번호가 순서에서 나오므로, 같은 청크가 다른 순서로 들어오면
# 생성 입력이 다른 것이다 — 집합으로만 보면 그 질의가 잡음 바닥에 섞인다
def test_the_evidence_split_looks_at_order_too() -> None:
    same = PairedRow("q1", row("q1", ["c1", "c2"], [1]), row("q1", ["c1", "c2"], [1]))
    reordered = PairedRow("q2", row("q2", ["c1", "c2"], [1]), row("q2", ["c2", "c1"], [1]))

    groups = split_by_evidence([same, reordered])

    assert [pair.query_id for pair in groups["same"]] == ["q1"]
    assert [pair.query_id for pair in groups["changed"]] == ["q2"]


# 인용은 번호가 아니라 **청크 신원**으로 견준다 — 같은 `[1]` 이 두 실행에서 다른 청크다
def test_citations_are_compared_by_chunk_not_by_number() -> None:
    pair = PairedRow("q1", row("q1", ["c1", "c2"], [1]), row("q1", ["c9", "c2"], [1]))

    assert pair.cited_changed


# 번호가 달라도 같은 청크를 가리키면 안 갈린 것이다
def test_different_numbers_pointing_at_the_same_chunk_are_not_a_change() -> None:
    pair = PairedRow("q1", row("q1", ["c1", "c2"], [2]), row("q1", ["c2", "c1"], [1]))

    assert not pair.cited_changed


# **범위 밖 번호는 가리킬 것이 없어 뺀다** — `score_citations()` 가 분모에서 빼는 것과 같은
# 규칙이다. 안 빼면 `[9]` 같은 헛번호가 «인용이 갈렸다» 를 만든다
def test_out_of_range_citations_are_ignored() -> None:
    assert cited_chunk_ids(row("q1", ["c1", "c2"], [1, 9])) == frozenset({"c1"})


# ---------------------------------------------------------------------------
# 재현 게이트 — 「그때 그 순위를 다시 밟았다」의 유일한 증거
# ---------------------------------------------------------------------------


def groups_of(same: int, changed: int) -> dict[str, list[PairedRow]]:
    def pairs(count: int, prefix: str) -> list[PairedRow]:
        def one(query_id: str) -> PairedRow:
            return PairedRow(query_id, row(query_id, ["c1"], [1]), row(query_id, ["c1"], [1]))

        return [one(f"{prefix}{i}") for i in range(count)]

    return {"same": pairs(same, "s"), "changed": pairs(changed, "d")}


# 기대값을 안 주면 게이트가 없다 — **이 도구의 성질이 아니라 세트와 리랭커에 딸린 관측값**이라
# 못 박아 두지 않았다(모듈 docstring)
def test_without_an_expectation_there_is_no_gate() -> None:
    assert reproduction_problems(groups_of(same=3, changed=57), None) == []


def test_the_expected_count_passes() -> None:
    assert reproduction_problems(groups_of(same=49, changed=11), 49) == []


# **어긋나면 멈춰 세운다.** 근거가 같은 질의 수가 그때와 다르면 검색이 그때 그 순위를 다시
# 밟지 않은 것이고, 그러면 두 군의 뜻 자체가 무너진다 — 그런데 표는 멀쩡히 나온다
def test_a_mismatched_count_is_reported_with_both_numbers() -> None:
    problems = reproduction_problems(groups_of(same=40, changed=20), 49)

    assert len(problems) == 1
    assert "40건" in problems[0] and "49건" in problems[0]


# ---------------------------------------------------------------------------
# 리포트 읽기
# ---------------------------------------------------------------------------


# **답변 리포트가 아닌 것을 받으면 멈춘다** — `run_eval` 리포트도 같은 폴더에 같은 꼴로 있어
# 손이 미끄러지기 쉽고, 그대로 진행하면 `KeyError` 가 한참 뒤에 난다
def test_a_run_eval_report_is_refused(tmp_path) -> None:
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"label": "L", "per_query": []}), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        load_answer_report(str(path))

    assert "답변 리포트가 아닙니다" in str(excinfo.value)


def test_a_missing_report_stops_with_its_name(tmp_path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        load_answer_report(str(tmp_path / "없는것.json"))

    assert "리포트가 없습니다" in str(excinfo.value)
