"""산문 골든 초안 도구가 **세트 파일을 안 망가뜨리는지** 본다.

이 도구는 사람이 라벨한 파일을 되쓴다. 그래서 재는 것이 «초안이 좋은가» 가 아니라
**«되쓰기가 무엇을 잃는가»** 다 — 초안 품질은 사람이 다듬으므로 틀려도 회복되지만,
되쓰기가 지운 것은 아무 신호 없이 사라진다.
"""

from __future__ import annotations

import json

import pytest

from evaluation.qa_set import QAItem, load_qa_set
from evaluation.reference_draft import draft_from_spans, fill_drafts, needs_reference


def _item(**over) -> QAItem:
    base = dict(
        query_id="d-01",
        query="언제부터 적용되나요?",
        source_doc="시행령-개정안-안내서",
        gold_spans=["(시행)공포 후 6개월의 유예기간(기관별 준비 실시, 2020.10.1.)"],
    )
    base.update(over)
    return QAItem(**base)


def _write_set(tmp_path, records, *, name="qa.jsonl", extra_lines=()):
    path = tmp_path / name
    lines = [*extra_lines, *(json.dumps(r, ensure_ascii=False) for r in records)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path


# ── 초안 규칙 — 임계값이 아니라 표기 정리다 ──────────────────────────────────


# 머리표·분류 딱지·화살표·각주 표시를 벗긴다. 넷 다 **뜻을 안 담는 기호**라 고를 여지가 없다
@pytest.mark.parametrize(
    ("span", "expected"),
    [
        ("(시행)공포 후 6개월", "공포 후 6개월"),
        ("* 범정부EA 기준 중앙행정기관 1,734개 포함", "범정부EA 기준 중앙행정기관 1,734개 포함"),
        ("11.“행정정보 데이터세트”란 …", "“행정정보 데이터세트”란 …"),
        ("시범사업 추진 ⇨ 중앙부처 수요조사", "시범사업 추진, 중앙부처 수요조사"),
        ("관리기준표* 작성 및 준수", "관리기준표 작성 및 준수"),
        ("□ 여러   공백을    줄인다", "여러 공백을 줄인다"),
    ],
)
def test_the_draft_strips_markers_that_carry_no_meaning(span, expected):
    assert draft_from_spans(_item(gold_spans=[span])) == expected


# **본문 괄호는 안 벗긴다.** 안에 값이 들어 있어(`(기관별 준비 실시, 2020.10.1.)`) 벗기면
# 필수 사실이 초안에서 사라진다 — 사람이 그것을 보고 문장을 만든다
def test_inner_parentheses_survive():
    draft = draft_from_spans(_item())
    assert "2020.10.1." in draft
    assert "기관별 준비 실시" in draft


# **뒤에 공백이 있는 괄호는 딱지가 아니다.** `(주) 아무개` 처럼 본문일 수 있어 가른다
def test_a_parenthesis_followed_by_a_space_is_not_a_tag():
    assert draft_from_spans(_item(gold_spans=["(주) 아무개가 제출한다"])) == "(주) 아무개가 제출한다"


# 조각이 여럿이면 쉼표로 잇는다. **문장을 만들지 않는다** — 종결어미가 붙으면 읽지 않고
# 도장만 찍는 길이 생긴다
def test_the_draft_does_not_finish_a_sentence():
    draft = draft_from_spans(_item(gold_spans=["첫째 조각을 담는다", "둘째 조각을 담는다"]))
    assert draft == "첫째 조각을 담는다, 둘째 조각을 담는다"
    assert not draft.endswith(("니다.", "습니다", "한다.", "."))


# ── 대상 고르기 ──────────────────────────────────────────────────────────────


# **부정 예시는 대상이 아니다.** 부정의 골든은 «기권» 이고 negative 버킷이 이미 잰다 —
# `qa_set.validate_items()` 가 막는 자리와 같은 규칙이라 도구도 애초에 안 만든다
def test_a_negative_is_never_a_target():
    assert not needs_reference(_item(answerable=False, gold_spans=[]))


# 이미 찬 칸은 다시 안 채운다
def test_a_filled_reference_is_not_a_target():
    assert not needs_reference(_item(reference="이미 쓴 골든입니다."))
    assert needs_reference(_item())


# ── 되쓰기 — 이 파일에서 제일 중요한 절 ──────────────────────────────────────


# **사람이 쓴 골든을 기계 초안이 못 덮는다.** 도장이 `human` 인데 칸이 비어 있는 상태는
# 실제로 생길 수 있다(사람이 지우고 도장을 안 되돌린 경우). 그때 덮으면 이 세트에서
# 제일 비싼 것(사람의 시간)이 아무 신호 없이 사라진다
def test_a_human_stamp_is_never_overwritten(tmp_path):
    path = _write_set(
        tmp_path,
        [
            {
                "query_id": "d-01",
                "query": "언제부터 적용되나요?",
                "source_doc": "시행령-개정안-안내서",
                "gold_spans": ["(시행)공포 후 6개월의 유예기간"],
                "reference": "",
                "reference_reviewed_by": "human",
            }
        ],
    )
    before = path.read_text(encoding="utf-8")

    filled, protected = fill_drafts(path, write=True)

    assert (filled, protected) == (0, 1)
    assert path.read_text(encoding="utf-8") == before


# **도장을 안 찍는다.** 초안은 제안이고, 사람이 읽기 전에 `human` 이 되면 검수 절차가
# 통째로 사라진다 — 6회차가 도장을 «검수가 끝난 뒤» 백필한 순서 그대로다
def test_the_draft_never_stamps_itself(tmp_path):
    path = _write_set(
        tmp_path,
        [
            {
                "query_id": "d-01",
                "query": "언제부터 적용되나요?",
                "source_doc": "시행령-개정안-안내서",
                "gold_spans": ["(시행)공포 후 6개월의 유예기간"],
            }
        ],
    )

    filled, _ = fill_drafts(path, write=True)
    assert filled == 1

    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["reference"] == "공포 후 6개월의 유예기간"
    assert record.get("reference_reviewed_by", "pending") == "pending"
    assert not record.get("reference_reviewed_hash")
    assert load_qa_set(path).items[0].reference_reviewed_by == "pending"


# **다른 키·키 순서·주석 줄·빈 줄을 그대로 둔다.** 레코드를 통째로 다시 만들면 이 파일이
# 담은 다른 라벨의 순서가 바뀌어 diff 가 통째로 빨개지고, 진짜 변경이 그 안에 묻힌다
def test_rewriting_preserves_everything_else(tmp_path):
    untouched = {
        "query_id": "d-02",
        "query": "다른 질문",
        "source_doc": "시행령-개정안-안내서",
        "gold_spans": ["다른 정답 문장"],
        "reference": "이미 쓴 골든입니다.",
    }
    target = {
        "query_id": "d-01",
        "query": "언제부터 적용되나요?",
        "note": "검수 메모",
        "source_doc": "시행령-개정안-안내서",
        "gold_spans": ["(시행)공포 후 6개월의 유예기간"],
        "must_values": ["6개월"],
        "reviewed_by": "human",
    }
    path = _write_set(tmp_path, [target, untouched], extra_lines=["# 주석 줄", ""])

    fill_drafts(path, write=True)
    lines = path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# 주석 줄", "주석 줄이 사라졌다"
    assert lines[1] == "", "빈 줄이 사라졌다"

    got = json.loads(lines[2])
    # 키가 원래 자리에 있고 `reference` 만 뒤에 붙는다
    assert list(got) == [*target, "reference"], list(got)
    assert got["note"] == "검수 메모"
    assert got["must_values"] == ["6개월"]

    assert json.loads(lines[3]) == untouched, "대상이 아닌 레코드가 바뀌었다"


# `--write` 없이 부르면 파일을 안 건드린다. 세는 것은 그대로 돈다
def test_a_dry_run_touches_nothing(tmp_path):
    path = _write_set(
        tmp_path,
        [
            {
                "query_id": "d-01",
                "query": "언제부터 적용되나요?",
                "source_doc": "시행령-개정안-안내서",
                "gold_spans": ["(시행)공포 후 6개월의 유예기간"],
            }
        ],
    )
    before = path.read_text(encoding="utf-8")

    filled, _ = fill_drafts(path, write=False)

    assert filled == 1
    assert path.read_text(encoding="utf-8") == before


# 조각이 기호뿐이면 초안이 비고, **빈 초안은 안 싣는다** — 빈 문자열을 실으면
# `needs_reference()` 가 여전히 참이라 아무것도 안 바뀐 채 diff 만 생긴다
def test_an_empty_draft_is_not_written(tmp_path):
    path = _write_set(
        tmp_path,
        [
            {
                "query_id": "d-01",
                "query": "질문",
                "source_doc": "시행령-개정안-안내서",
                "gold_spans": ["***"],
            }
        ],
    )
    before = path.read_text(encoding="utf-8")

    filled, _ = fill_drafts(path, write=True)

    assert filled == 0
    assert path.read_text(encoding="utf-8") == before
