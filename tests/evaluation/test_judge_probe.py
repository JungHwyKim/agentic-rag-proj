"""민감도 프로브가 **자기 라벨을 거짓으로 만드는 자리들**을 잠근다.

이 도구의 위험은 `answer_eval` 과 같은 모양인데 한 겹 더 나쁘다 — 여기서는 **정답이 우리가
만든 것**이라, 변조를 잘못 만들면 *"정답은 unsupported"* 라는 라벨 자체가 거짓이 되고
**멀쩡한 채점자가 틀린 것으로 세어진다.** 그래서 재는 것은 대부분 지표가 아니라 그 라벨이
성립하는 조건이다 — 근거에 있는 값을 넣었나, 인용 번호를 사실로 착각했나, 근거 순서를
바꿨나, 실패를 «못 잡았다» 로 접었나.

키 없이 전부 돈다. 외부 호출은 `answer_eval._make_client` 이음새 하나로 갈아끼운다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evaluation import answer_eval, judge_probe
from evaluation.answer_eval import JudgeUsage, build_support_prompt
from evaluation.judge_probe import (
    EXTRA_SENTENCES,
    ProbeRow,
    accounting_problems,
    mutate_sentence,
    mutate_value,
    probe,
    reorder_chunks,
    scope_notes,
    summarize,
)
from rag_pipeline.generation.prompts import NO_EVIDENCE_ANSWER
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import StoredChunk, point_id_for


def rc(chunk_id: str, text: str, doc: str = "안내서.pdf") -> RetrievedChunk:
    return RetrievedChunk(chunk_id=chunk_id, document_id=doc, text=text, metadata={})


def sc(chunk_id: str, text: str, doc: str = "안내서.pdf", **metadata) -> StoredChunk:
    return StoredChunk(
        id=point_id_for(chunk_id),
        chunk_id=chunk_id,
        document_id=doc,
        text=text,
        metadata=metadata,
    )


def prow(query_id: str, level: str, verdict: str) -> ProbeRow:
    return ProbeRow(
        query_id=query_id, query="q", source_doc="d", answerable=True,
        level=level, mutation="x -> y", answer="답 [1]", mutated="답 [1] 변조",
        chunk_ids=["c1"], verdict=verdict,
    )


def empty_unmutated() -> dict[str, list[str]]:
    return {level: [] for level, _ in judge_probe.LEVELS}


# ---------------------------------------------------------------------------
# 1단 — 라벨이 거짓이 되는 자리
# ---------------------------------------------------------------------------


# **근거에 있는 값을 넣으면 «정답은 unsupported» 가 거짓이 된다.** 그러면 채점자가 맞혔는데
# 틀린 것으로 세어지고, 민감도가 실력이 아니라 변조 품질을 잰다
def test_a_replacement_that_lives_in_the_evidence_is_rejected() -> None:
    chunks = [rc("c1", "제25조와 제26조와 제27조에 따른다")]

    mutated, note = mutate_value("제25조에 따라 처리합니다[1].", chunks)

    assert "제28조" in mutated, "26·27 은 근거에 있으므로 건너뛰어야 한다"
    assert note == "제25조 -> 제28조"


# **공백을 지우고 대조한다** — PDF 가 한국어 낱말 사이 공백을 잃어서, 공백을 살려 비교하면
# 근거에 실제로 있는 값을 «없다» 고 보고 그대로 끼워 넣는다
def test_the_evidence_check_ignores_spacing() -> None:
    chunks = [rc("c1", "보존기간은3 1년으로한다")]

    mutated, _ = mutate_value("보존기간은 30년입니다[1].", chunks)

    assert "31년" not in mutated, "공백만 다른 값은 근거에 있는 것이다"
    assert "32년" in mutated


# **인용 번호는 사실이 아니라 근거 번호다.** 바꾸면 재는 것이 지지가 아니라 인용 정합성이 된다
def test_citation_numbers_are_not_mutation_targets() -> None:
    chunks = [rc("c1", "근거 본문이다")]

    assert mutate_value("근거에 따르면 그렇습니다[1].", chunks) is None


# 여러 인용이 붙어도 마찬가지다. 숫자가 인용 안에만 있으면 1단은 성립하지 않는다
def test_many_citations_still_leave_nothing_to_mutate() -> None:
    chunks = [rc("c1", "근거 본문이다")]

    assert mutate_value("그렇습니다[1][2][3].", chunks) is None


# 숫자가 없으면 기관·문서명 축으로 물러선다. 그 축도 «근거에 없는 이름» 이어야 한다
def test_the_name_axis_catches_answers_without_numbers() -> None:
    chunks = [rc("c1", "학교기록관이 이를 관리한다")]

    mutated, note = mutate_value("학교기록관이 관리합니다[1].", chunks)

    assert "학교기록관" not in mutated
    assert squashed_absent(note.split(" -> ")[1], chunks)


def squashed_absent(value: str, chunks: list[RetrievedChunk]) -> bool:
    return judge_probe.squash(value) not in judge_probe.evidence_haystack(chunks)


# **기관명 축도 근거 대조를 탄다.** 위 테스트만으로는 이 규칙에 대해 **빈 테스트**다 —
# 거기 근거에는 가짜 이름이 아예 없어서 대조를 빼도 결과가 같다(변이로 확인했다).
# 여기서는 첫 가짜 이름을 근거에 심어, 건너뛰고 다음 것을 골라야만 통과한다
def test_the_name_axis_refuses_names_that_live_in_the_evidence() -> None:
    chunks = [rc("c1", "학교기록관과 한국기록평가원이 함께 관리한다")]

    mutated, _ = mutate_value("학교기록관이 관리합니다[1].", chunks)

    assert "한국기록평가원" not in mutated, "근거에 있는 이름은 known-bad 가 아니다"
    assert "중앙문서관리청" in mutated


# **낫표로 묶인 법령·문서명도 1단 축이다** — 실측(사람 43건)으로 1단을 못 만든 9건 중
# `「사립학교법」` 처럼 낫표 안에만 고유명사가 있는 답변이 있었다. 축을 「기관·문서명」으로
# 정해 놓고 기관만 구현하면 분모가 이유 없이 작아진다
def test_the_document_axis_replaces_the_name_inside_the_brackets() -> None:
    chunks = [rc("c1", "「사립학교법」에 따라 책임을 묻는다")]

    mutated, note = mutate_value("「사립학교법」에 따릅니다[1].", chunks)

    assert "「사립학교법」" not in mutated
    assert mutated.startswith("「전자기록물표준화법」"), "낫표는 그대로 두고 안쪽만 바꾼다"
    assert squashed_absent(note.split(" -> ")[1], chunks)


# 낫표 축도 근거 대조를 탄다 — 근거에 있는 법령명으로 바꾸면 라벨이 거짓이 된다
def test_the_document_axis_refuses_names_that_live_in_the_evidence() -> None:
    chunks = [rc("c1", "「사립학교법」과 「전자기록물표준화법」을 함께 본다")]

    mutated, _ = mutate_value("「사립학교법」에 따릅니다[1].", chunks)

    assert "전자기록물표준화법" not in mutated
    assert "「기록물보존특례법」" in mutated


# **숫자가 낫표 축보다 먼저다** — 확인 단위 순서가 곧 라벨이 거짓이 될 위험의 순서다
def test_numbers_are_tried_before_the_document_axis() -> None:
    chunks = [rc("c1", "「사립학교법」 제25조에 따른다")]

    _, note = mutate_value("「사립학교법」 제25조에 따릅니다[1].", chunks)

    assert note.startswith("제25조 ->")


# **목록 머리 번호는 사실이 아니다.** `1. 기관 내 …` 를 `18. …` 로 바꿔도 답변이 주장하는
# 내용은 그대로라 채점자가 «뒷받침된다» 고 하는 것이 맞다 — known-bad 가 아니라 잡음이고,
# 민감도를 실력보다 낮게 만든다(2026-08-28 실측: 놓친 8건 중 3건이 이것이었다)
def test_list_markers_are_not_mutation_targets() -> None:
    chunks = [rc("c1", "절차는 다음과 같다")]

    assert mutate_value("절차는 다음과 같습니다. 1. 먼저 확인합니다[1].", chunks) is None


# 괄호형 목록도 마찬가지다
def test_paren_list_markers_are_not_mutation_targets() -> None:
    chunks = [rc("c1", "절차는 다음과 같다")]

    assert mutate_value("절차는 다음과 같습니다. 1) 먼저 확인합니다[1].", chunks) is None


# **날짜까지 막으면 안 된다** — `2026. 3. 1.` 도 «숫자 + 마침표 + 공백» 으로 끝나서,
# 목록 검사를 전체 패턴에 걸면 멀쩡한 날짜 변조가 통째로 막힌다
def test_dates_are_still_mutable_next_to_the_list_marker_rule() -> None:
    chunks = [rc("c1", "2026. 3. 1. 부터 시행한다")]

    mutated, note = mutate_value("2026. 3. 1. 부터 시행합니다[1].", chunks)

    assert note.startswith("2026. 3. 1 ->")
    assert "2027" in mutated


# **낱말 한가운데를 부수면 안 된다.** `기록관리` 안의 `기록관` 을 잡으면 `한국기록평가원리`
# 라는 없는 낱말이 나오는데, 그럴듯한 오답이 아니라 잡음이라 채점자가 무시하는 것이 맞다
# (2026-08-28 실측: 놓친 8건 중 2건). 한국어는 명사에 조사가 붙어 `(?![가-힣])` 로는 못 막는다
def test_the_name_axis_does_not_break_a_longer_word() -> None:
    chunks = [rc("c1", "기록관리 기준을 활용한다")]

    assert mutate_value("기록관리 기준을 활용합니다[1].", chunks) is None


# 조사가 붙은 기관명은 여전히 잡아야 한다 — 위 가드가 이것까지 막으면 축이 통째로 죽는다
@pytest.mark.parametrize(
    "answer",
    [
        "학교기록관이 관리합니다[1].",
        "학교기록관은 관리합니다[1].",
        "학교기록관에서 관리합니다[1].",
        "학교기록관으로 넘깁니다[1].",
        "학교기록관, 그리고 다른 곳[1].",
    ],
)
def test_the_name_axis_still_matches_nouns_wearing_particles(answer: str) -> None:
    chunks = [rc("c1", "학교기록관이 이를 관리한다")]

    assert mutate_value(answer, chunks) is not None


# **변조는 답변을 실제로 바꿔야 한다.** 안 바뀌면 판정 캐시가 본판과 같은 칸을 물어
# «변조본을 쟀다» 가 실은 본판을 되읽은 것이 된다
# (`test_answer_eval.py::test_answer_text_splits_the_cache` 가 그 갈림을 잠근다)
@pytest.mark.parametrize(
    "answer",
    ["제25조에 따릅니다[1].", "보존기간은 30년입니다[1].", "학교기록관이 관리합니다[1]."],
)
def test_every_mutation_actually_changes_the_answer(answer: str) -> None:
    chunks = [rc("c1", "무관한 본문")]

    assert mutate_value(answer, chunks)[0] != answer
    assert mutate_sentence(answer, chunks, seed="q")[0] != answer


# ---------------------------------------------------------------------------
# 2단 — 게이트라서 한 모양만 재면 안 된다
# ---------------------------------------------------------------------------


# **전건에 같은 문장을 쓰면 28개 판정이 통째로 상관돼 게이트가 사실상 한 모양만 잰다.**
# 그런데 이 게이트가 세우려는 것은 *"이 채점자가 명백한 조작을 잡는다"* 라 모양이 하나면
# 표본이 1이다
def test_the_extra_sentence_rotates_across_queries() -> None:
    chunks = [rc("c1", "무관한 본문")]

    used = {mutate_sentence("답[1].", chunks, seed=s)[1] for s in ("a", "b", "c")}

    assert len(used) == len(EXTRA_SENTENCES)


# **`hash()` 로 돌리면 안 된다** — 파이썬이 문자열 해시를 프로세스마다 섞어(PYTHONHASHSEED)
# 같은 리포트가 실행마다 다른 변조를 내는데 **에러가 안 난다**.
#
# **한 프로세스 안에서 두 번 불러 같은지 보는 것으로는 이걸 못 잡는다** — `hash()` 도 그 안에서는
# 안정적이라 그런 테스트는 통과한다(변이로 확인했다). 그래서 값을 못 박는다: 프로세스를 가로질러
# 재현되는 구현만 이 수를 낸다
@pytest.mark.parametrize(("seed", "offset"), [("hsih-01", 0), ("a", 1), ("b", 2), ("c", 0)])
def test_the_rotation_is_reproducible_across_processes(seed: str, offset: int) -> None:
    assert judge_probe._template_offset(seed) == offset


# 템플릿이 전부 근거에 있으면 2단도 못 만든다. 그 경우가 조용히 지나가면 «분모가 28» 이 거짓이 된다
def test_the_sentence_axis_gives_up_when_every_template_is_in_the_evidence() -> None:
    chunks = [rc("c1", " ".join(EXTRA_SENTENCES))]

    assert mutate_sentence("답[1].", chunks, seed="q") is None


# ---------------------------------------------------------------------------
# 근거 되살리기 — 순서가 곧 근거 번호다
# ---------------------------------------------------------------------------


# **qdrant 의 반환 순서는 보장이 없다.** 그 순서가 프롬프트의 근거 번호를 만들므로,
# 안 되돌리면 채점자가 본판과 **다른 번호**의 근거를 본다
def test_evidence_is_restored_in_the_reported_order() -> None:
    shuffled = [sc("c2", "둘째"), sc("c1", "첫째")]

    restored = reorder_chunks(shuffled, ["c1", "c2"])

    assert [chunk.chunk_id for chunk in restored] == ["c1", "c2"]
    prompt = build_support_prompt("질문", "답", restored)
    assert "[1] (안내서.pdf)\n첫째" in prompt
    assert "[2] (안내서.pdf)\n둘째" in prompt


# **`metadata` 가 살아야 근거 블록이 본판과 글자까지 같다** — `source_name` 이 출처 이름을
# 만들어, 빠지면 `document_id` 로 떨어지면서 채점자가 다른 글자를 본다
def test_metadata_survives_the_restore() -> None:
    stored = [sc("c1", "본문", doc="안내서.pdf", source_name="시행령 개정안.pdf")]

    restored = reorder_chunks(stored, ["c1"])

    assert restored[0].metadata["source_name"] == "시행령 개정안.pdf"
    assert "[1] (시행령 개정안.pdf)" in build_support_prompt("질문", "답", restored)


# 못 찾은 id 는 빠진다. **부르는 쪽이 길이를 대조해야 한다** — 조용히 짧아지면 채점자가
# 본판보다 적은 근거를 보는데 판정은 멀쩡히 나온다
def test_a_missing_chunk_shortens_the_restore() -> None:
    assert len(reorder_chunks([sc("c1", "본문")], ["c1", "c9"])) == 1


# ---------------------------------------------------------------------------
# 회계 게이트 — 2단만 게이트이고 1단은 관측이다
# ---------------------------------------------------------------------------


# 회계가 맞으면 통과다. 이 상태에서만 아래 수치를 실력으로 읽는다
def test_a_matching_ledger_passes() -> None:
    rows = [prow("a", "sentence", "unsupported"), prow("a", "value", "unsupported")]

    assert accounting_problems(
        rows, JudgeUsage(api_calls=2), missing_evidence=[]
    ) == []


# **2단을 놓치면 그 채점자로 잰 지지 수치는 못 쓴다.** 근거에 없는 문장이 통째로 들어갔는데
# «뒷받침된다» 고 말하는 채점자라면 본판의 `supported` 28건도 믿을 근거가 없다
def test_a_missed_sentence_mutation_invalidates_the_judge() -> None:
    rows = [prow("a", "sentence", "supported"), prow("b", "sentence", "unsupported")]

    problems = accounting_problems(
        rows, JudgeUsage(api_calls=2), missing_evidence=[]
    )

    assert any("2단" in line and "못 씁니다" in line for line in problems)


# **1단을 놓치는 것은 게이트가 아니다.** 문장의 나머지가 근거와 같아 놓칠 여지가 구조적으로
# 있고, 거기 통과선을 두면 그 숫자의 출처가 없다 — 관측값으로만 남긴다
def test_a_missed_value_mutation_is_not_a_gate() -> None:
    rows = [prow("a", "value", "supported"), prow("b", "value", "supported")]

    assert accounting_problems(
        rows, JudgeUsage(api_calls=2), missing_evidence=[]
    ) == []


# 변조를 못 만든 질의 수가 찍힌다. **조용히 빠지면 «28건 중 20건 잡았다» 가 실은 22건 중
# 20건인 상태가 에러 없이 생긴다**
def test_unmutated_queries_are_reported() -> None:
    unmutated = empty_unmutated()
    unmutated["value"] = ["a", "b"]
    rows = [prow("x", "value", "unsupported")]

    notes = scope_notes(rows, unmutated=unmutated, missing_evidence=[])

    assert any("1단" in line and "1건에서만" in line for line in notes)


# 근거를 못 되살린 질의도 찍힌다 — 컬렉션이 그 실행의 것이 아니면 그 질의는 아예 안 재진다
def test_missing_evidence_is_reported() -> None:
    notes = scope_notes([], unmutated=empty_unmutated(), missing_evidence=["gone"])

    assert any("되살" in line for line in notes)


# **분모가 작아지는 것은 회차를 무효로 만들지 않는다.** 섞으면 게이트가 늑대를 부르고,
# 헛경보를 내는 게이트는 무시하는 습관을 만든다 — 첫 유료 실행(2026-08-28)이 2단 28/28 ·
# 실패 0 인 멀쩡한 회차인데 *"실력으로 읽지 않는다"* 를 띄운 자리다
def test_a_small_denominator_does_not_invalidate_the_run() -> None:
    unmutated = empty_unmutated()
    unmutated["value"] = ["a", "b"]
    rows = [prow("x", "sentence", "unsupported")]

    assert accounting_problems(rows, JudgeUsage(api_calls=1), missing_evidence=["gone"]) == []
    assert scope_notes(rows, unmutated=unmutated, missing_evidence=["gone"])


# **변조본이 하나도 없으면 분모가 작은 것이 아니라 잰 것이 없는 것이다.** 리포트와 컬렉션이
# 다른 실행의 것일 때 전건이 여기로 오는데, 임계값 없이 그것을 잡는 자리이기도 하다
def test_no_mutations_at_all_invalidates_the_run() -> None:
    problems = accounting_problems([], JudgeUsage(), missing_evidence=["a", "b", "c"])

    assert any("하나도 없습니다" in line for line in problems)


# 호출 수가 변조본 수와 어긋나면 지표를 못 읽는다 — Cohere 한도 초과가 품질 저하로 읽힌 선례다
def test_a_short_ledger_is_reported() -> None:
    rows = [prow("a", "sentence", "unsupported"), prow("a", "value", "unsupported")]

    problems = accounting_problems(
        rows, JudgeUsage(api_calls=1), missing_evidence=[]
    )

    assert any("판정 회계" in line for line in problems)


# 캐시 히트도 회계의 한쪽 항이다. 히트만으로 채워도 통과여야 한다
def test_cache_hits_count_toward_the_ledger() -> None:
    rows = [prow("a", "sentence", "unsupported")]

    assert accounting_problems(
        rows, JudgeUsage(cache_hits=1), missing_evidence=[]
    ) == []


# ---------------------------------------------------------------------------
# 실패는 «못 잡았다» 가 아니다
# ---------------------------------------------------------------------------


# **미지지로 접으면 죽은 채점자가 민감도 만점을 받고, «못 잡았다» 로 접으면 반대로 멀쩡한
# 채점자가 0점을 받는다.** 둘 다 실력이 아니라 실패율을 재는 것이다
def test_failure_is_neither_caught_nor_missed() -> None:
    rows = [prow("a", "value", "failed"), prow("b", "sentence", "failed")]

    summary = summarize(rows)

    assert summary["value"] == {
        "probed": 1, "judged": 0, "caught": 0, "missed": 0, "failed": 1, "sensitivity": None
    }
    problems = accounting_problems(
        rows, JudgeUsage(api_calls=2), missing_evidence=[]
    )
    assert any("못 쟀다" in line for line in problems)
    assert not any("못 씁니다" in line for line in problems), "실패는 2단 게이트의 판정이 아니다"


# 실패는 분모에서도 빠진다. 남은 것만으로 낸 비율이 전체인 척하면 안 되므로 회계가 따로 말한다
def test_failures_leave_the_denominator() -> None:
    rows = [prow("a", "value", "unsupported"), prow("b", "value", "failed")]

    assert summarize(rows)["value"]["sensitivity"] == 1.0
    assert summarize(rows)["value"]["probed"] == 2


# ---------------------------------------------------------------------------
# probe() — 저장된 리포트를 읽는 자리
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self, chunks: list[StoredChunk]) -> None:
        self._by_point = {chunk.id: chunk for chunk in chunks}
        self.closed = False

    def count(self) -> int:
        return len(self._by_point)

    # **일부러 뒤집어 돌려준다** — qdrant 가 순서를 보장하지 않는 것을 흉내 낸다
    def fetch_by_ids(self, ids: list[str]) -> list[StoredChunk]:
        found = [self._by_point[i] for i in ids if i in self._by_point]
        return list(reversed(found))

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def report(tmp_path):
    payload = {
        "meta": {"label": "L", "format": "pdf", "embedding_profile": "kure-v1"},
        "rows": [
            {
                "query_id": "ok", "query": "질문", "source_doc": "d", "answerable": True,
                "answer": "제25조에 따라 처리합니다[1].", "chunk_ids": ["c1", "c2"],
                "abstained": False,
            },
            {
                # **저장된 `abstained` 가 낡았다** — 첫 판이 인용 붙은 기권을 판정으로 흘렸다
                "query_id": "stale", "query": "질문", "source_doc": "d", "answerable": False,
                "answer": f"{NO_EVIDENCE_ANSWER} [1][2]", "chunk_ids": ["c1", "c2"],
                "abstained": False,
            },
            {
                "query_id": "gone", "query": "질문", "source_doc": "d", "answerable": True,
                "answer": "답합니다[1].", "chunk_ids": ["c9"], "abstained": False,
            },
        ],
    }
    path = tmp_path / "L-answers-mini-pdf.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def wired(mocker, tmp_path):
    store = FakeStore([sc("c1", "제25조에 따라 처리한다"), sc("c2", "다른 본문")])
    mocker.patch("rag_pipeline.ingestion.indexing.get_store", return_value=store)
    # `.env` 를 못 타게 한다 — 안 막으면 개발자 머신의 실키가 픽스처에 실린다
    mocker.patch.object(
        judge_probe,
        "eval_config",
        return_value=SimpleNamespace(
            indexing=None, embedding=None, generation=SimpleNamespace(openai_api_key="sk-test")
        ),
    )
    mocker.patch.object(judge_probe, "JUDGE_CACHE_DIR", tmp_path / "judge", create=True)
    return store


class FakeClient:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    @property
    def chat(self):  # noqa: D401 - OpenAI SDK 모양을 흉내 낸다
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("M", (), {"content": self._content})()
        return type(
            "R", (), {"choices": [type("C", (), {"message": message})()], "usage": None}
        )()


# **저장된 `abstained` 를 안 믿는다** — 낡은 리포트의 기권 행이 판정으로 새면 «못 찾았습니다»
# 를 변조해 묻게 되는데, 그 문장은 어떤 근거로도 뒷받침되어 민감도가 이유 없이 떨어진다
def test_abstention_is_recomputed_not_trusted(report, wired, mocker) -> None:
    client = FakeClient('{"verdict": "unsupported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    rows, meta = probe(report_path=report, cache_enabled=False)

    assert meta["abstained"] == ["stale"], "인용을 단 기권도 기권이다"
    assert meta["missing_evidence"] == ["gone"]
    assert {row.query_id for row in rows} == {"ok"}
    assert {row.level for row in rows} == {"sentence", "value"}


# 근거는 **리포트 순서로** 채점자에게 간다 — 저장소가 뒤집어 줘도 마찬가지다
def test_the_judge_sees_the_reported_evidence_order(report, wired, mocker) -> None:
    client = FakeClient('{"verdict": "unsupported"}')
    mocker.patch.object(answer_eval, "_make_client", return_value=client)

    probe(report_path=report, cache_enabled=False)

    prompt = client.calls[0]["messages"][1]["content"]
    assert prompt.index("제25조에 따라 처리한다") < prompt.index("다른 본문")


# **저장소를 반드시 닫는다** — 안 닫으면 다음 실행이 배타 잠금으로 통째로 죽는다
def test_the_store_is_closed(report, wired, mocker) -> None:
    mocker.patch.object(
        answer_eval, "_make_client", return_value=FakeClient('{"verdict": "unsupported"}')
    )

    probe(report_path=report, cache_enabled=False)

    assert wired.closed is True


# 키가 없으면 전건이 `failed` 이고 회계가 그것을 «못 쟀다» 로 말한다 — **민감도 0이 아니다**
def test_without_a_key_everything_is_failed(report, wired, mocker) -> None:
    mocker.patch.object(answer_eval, "_make_client", return_value=None)

    rows, meta = probe(report_path=report, cache_enabled=False)

    assert {row.verdict for row in rows} == {"failed"}
    assert summarize(rows)["sentence"]["sensitivity"] is None
    problems = accounting_problems(
        rows, JudgeUsage(**meta["judge_usage"]), missing_evidence=meta["missing_evidence"]
    )
    assert any("못 쟀다" in line for line in problems)

# **채점자 없는 프로필로는 못 돈다.** `DEFAULT_JUDGE_PROFILE` 이 2026-08-29 에 `off` 로
# 내려가면서 실재하게 된 자리다 — 그냥 두면 이 도구가 «채점자를 재는 도구인데 채점자를 안
# 부르는» 상태로 돌고, 표는 전건 `failed` 로 나온다. 그것은 *«죽은 채점자가 만점»* 의
# 거울상이라 수치가 아니라 **멈춤**으로 답한다
def test_a_judgeless_profile_is_refused(report) -> None:
    with pytest.raises(SystemExit) as err:
        probe(report_path=report, judge_name="off", cache_enabled=False)

    assert "채점자 프로필" in str(err.value)


# **이 도구의 기본은 `answer_eval` 과 달라야 한다** — 같은 상수를 쓰면 저쪽 기본이 바뀌는
# 순간 이쪽이 조용히 안 돈다. 그 커플링이 실제로 있었고 위 거부가 그 뒤에 서 있다
def test_the_probe_default_judge_is_a_real_judge() -> None:
    from evaluation.config import DEFAULT_PROBE_JUDGE_PROFILE, judge_profile

    assert judge_profile(DEFAULT_PROBE_JUDGE_PROFILE)["judge_model"]
