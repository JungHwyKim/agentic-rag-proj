"""정답 질의 세트가 성립하는지 본다.

**두 층으로 나뉜다.** 형식 검사는 늘 돌고, span 검사는 `tests/fixtures/` 에 실파일이 있을
때만 돈다 — 저 폴더는 gitignore 라(바이너리를 커밋하지 않는 방침) CI 나 새 클론에는 없다.
실파일 회귀 테스트 다섯이 쓰는 것과 같은 정책이다.

span 검사를 굳이 테스트로 두는 이유: **세트가 조용히 썩는다.** 로더나 splitter 를 고치면
어제까지 청크 하나에 들어 있던 정답이 오늘 두 청크로 갈릴 수 있고, 그러면 그 질의는 어떤
검색기로도 못 맞히는데 **점수만 떨어지고 원인은 안 보인다.**
"""

from __future__ import annotations

import pytest

from evaluation.documents import DOCUMENT_GROUPS, available_files, format_labels
from evaluation.qa_set import (
    QA_SET_PATH,
    QAItem,
    _distinct_runs,
    content_hash,
    load_qa_set,
    validate_items,
    validate_spans_across_formats,
)
from evaluation.run_eval import split_scorable
from evaluation.scoring import score_query

# 사람이 쓴 질의 세트. 부정 예시가 사는 곳이라 기본 세트와 갈라 본다
HUMAN_SET_PATH = QA_SET_PATH.parent / "qa_set_human.jsonl"

# 세트 규모의 하한. `.claude/wiki/plan_log/평가-세트.md` 2회차의 50~80 중 아래쪽이고 **임시값이다** —
# 비교 축이 여섯이라 이보다 적으면 축마다 유의성을 못 낸다는 것이 근거의 전부다
MIN_ITEMS = 50


# 세트를 한 번만 읽는다
@pytest.fixture(scope="module")
def qa_items():
    return load_qa_set().items


# 세트가 있고 최소 규모를 넘는다
def test_qa_set_is_present_and_large_enough(qa_items):
    assert len(qa_items) >= MIN_ITEMS, f"질의가 {len(qa_items)}개뿐이다 (하한 {MIN_ITEMS})"


# 레코드 형식이 전부 맞다. 문서를 안 읽으므로 fixtures 없이도 돈다
def test_records_are_well_formed(qa_items):
    problems = validate_items(qa_items)
    assert not problems, "\n".join(problems)


# 네 그룹이 모두 질의를 갖는다. 한 문서에 몰리면 비교가 그 문서 얘기가 된다
def test_every_document_group_is_covered(qa_items):
    covered = {item.source_doc for item in qa_items}
    missing = set(DOCUMENT_GROUPS) - covered
    assert not missing, f"질의가 하나도 없는 그룹 {sorted(missing)}"


# 표에서 온 질의가 섞여 있다. 이 코퍼스는 표 중심이고 표 청크는 헤더 반복 때문에
# 검색 성질이 달라, 본문만으로 재면 실제 쓰임과 어긋난다
def test_table_answers_are_represented(qa_items):
    tables = [item for item in qa_items if item.answer_type == "table"]
    assert tables, "answer_type 이 table 인 질의가 하나도 없다"


# 60건 전부 gold_page 가 그 그룹의 모든 포맷 라벨에 대해 채워져 있다.
# 문서를 안 읽으므로 fixtures 없이도 돈다 — 라벨을 바꾸고 세트를 안 고치는 것을 여기서 막는다
def test_gold_page_is_filled_for_every_format(qa_items):
    problems = []
    for item in qa_items:
        expected = set(format_labels(item.source_doc))
        if set(item.gold_page) != expected:
            problems.append(f"{item.query_id}: {sorted(item.gold_page)} != {sorted(expected)}")
        for label, page in item.gold_page.items():
            if not isinstance(page, int) or page < 1:
                problems.append(f"{item.query_id}/{label}: 쪽이 {page!r}")
    assert not problems, "\n".join(problems)


# 겹침이 만든 인접 중복을 반복으로 세지 않는다.
#
# **이 규칙이 없으면 멀쩡한 질의를 버린다.** `chunk_overlap_chars` 가 상한 초과로 쪼갠
# 구간에 겹침을 줘서, 겹친 자리에 걸린 span 은 인접한 두 청크에 정상적으로 나타난다.
# 아래 값은 실측이다 — sih-05 는 PDF 청크 17·18(같은 5쪽)이라 겹침이고,
# sih-08 은 0·4, ai-02 는 5·21·23 이라 진짜 반복이다
@pytest.mark.parametrize(
    ("indices", "expected"),
    [([], 0), ([3], 1), ([17, 18], 1), ([0, 4], 2), ([5, 21, 23], 3), ([5, 6, 21], 2)],
)
def test_overlap_is_not_counted_as_repetition(indices, expected):
    assert _distinct_runs(indices) == expected


# 모든 정답 span 이 그 그룹의 **모든 포맷에서, 청크 하나 안에, 한 자리에만** 있다.
# 마지막 조건이 중복 정답 검사다 — 정답 청크가 여럿이면 그 질의만 쉬워져 그룹 간 비교가
# 어긋난다. 실파일이 없으면 건너뛴다 (모듈 docstring 참고)
def test_spans_survive_every_format(qa_items):
    groups = sorted({item.source_doc for item in qa_items})
    if not all(available_files(group) for group in groups):
        pytest.skip("tests/fixtures/ 에 실파일이 없어 span 검증을 건너뜁니다")

    from evaluation.corpus import build_corpus

    problems = validate_spans_across_formats(qa_items, build_corpus(groups))
    assert not problems, "\n".join(problems)


# 기록된 `gold_page` 가 **PDF 원본의 그 쪽에 실제로 있는지** 본다.
#
# `gold_page` 는 채점에 안 쓰므로 틀려도 지표가 안 움직이고 **아무도 모른다.** 실제로
# 2026-08-22 사람 검수가 세 건(`sih-11`·`sch-13`·`sch-14`)을 손으로 잡아냈다 — 청크의
# `page` 가 시작 쪽이라 쪽 넘긴 청크 뒷부분의 span 이 한 쪽 앞으로 보고됐다.
# 검수자가 잡은 것을 다시 사람 손에 맡기지 않으려고 여기서 본다
def test_gold_page_matches_the_pdf(qa_items):
    groups = sorted({item.source_doc for item in qa_items})
    if not all(available_files(group) for group in groups):
        pytest.skip("tests/fixtures/ 에 실파일이 없어 쪽 검증을 건너뜁니다")

    from evaluation.pages import validate_gold_pages_against_pdf

    problems = validate_gold_pages_against_pdf(qa_items)
    assert not problems, "\n".join(problems)


# 검수 도장이 **그때 본 문장**에 붙어 있다.
#
# 세트에서 제일 비싼 것은 사람의 시간이다. 검수 뒤에 질의나 정답을 고치면
# `reviewed_by="human"` 이 그대로 남아 **검수자가 보지 않은 문장에 도장이 찍힌다** —
# 아무 에러도 안 나고 세트는 통과한다. `reviewed_hash` 가 그것을 막는다
def test_review_stamp_breaks_when_the_content_changes(qa_items):
    import dataclasses

    original = qa_items[0]
    assert original.reviewed_by == "human", "이 검사는 검수된 레코드가 있어야 뜻이 있다"

    tampered = [dataclasses.replace(original, gold_spans=["몰래 바꾼 정답 문장"])]
    problems = validate_items(tampered)
    assert problems, "정답을 바꿨는데 검수 도장이 그대로 통과했다"
    assert "검수 뒤에" in problems[0]

    # 같이 갱신하면 통과한다 — 막는 것이 목적이지 못 고치게 하는 것이 아니다
    spans = ["몰래 바꾼 정답 문장"]
    fixed = [
        dataclasses.replace(
            original, gold_spans=spans, reviewed_hash=content_hash(original.query, spans)
        )
    ]
    assert not validate_items(fixed)


# ------------------------------------------------------------------ 부정 예시
#
# 여기부터는 **가상 데이터**다. 실파일이 없어도 늘 돌아야 하고, 재는 것이 "세트가 썩었나"가
# 아니라 "규칙이 도는가"라 세트 내용과 묶으면 안 된다.
#
# 부정 예시가 필요한 이유는 `evaluation/qa_set.py` 모듈 docstring 에 있다 — 지금 세트는
# 전부 정답이 실재해서 *"항상 충분"* 이라고만 답하는 Evaluator 가 100점을 받는다.


# 부정 레코드 하나. 기본은 계약을 지키는 모양이고 테스트가 한 칸씩 어긴다
def _negative(**over):
    base = dict(
        query_id="hneg-01",
        query="이 지침의 급식비 단가는 얼마인가요?",
        source_doc="시행령-개정안-안내서",
        gold_spans=[],
        answerable=False,
    )
    base.update(over)
    return QAItem(**base)


# `answerable` 이 없는 레코드는 **긍정으로 읽힌다.** 기본값이 그렇기 때문이고,
# 그래서 기존 60+20건이 이 필드를 안 적고도 그대로 산다
def test_answerable_defaults_to_true():
    assert QAItem.from_json({
        "query_id": "x-01", "query": "질문", "source_doc": "시행령-개정안-안내서",
        "gold_spans": ["어떤 정답 문장"],
    }).answerable is True


# jsonl 에 적힌 `answerable` 이 실제로 실린다
def test_answerable_is_read_from_json():
    assert QAItem.from_json({
        "query_id": "x-02", "query": "질문", "source_doc": "시행령-개정안-안내서",
        "gold_spans": [], "answerable": False,
    }).answerable is False


# 계약을 지킨 부정 레코드는 통과한다. **긍정 쪽 검사가 안 먹는 것**이 이 테스트의 요점이다 —
# 빈 `gold_spans` 도 빈 `gold_page` 도 부정에서는 정상이다
def test_well_formed_negative_passes():
    assert validate_items([_negative()]) == []


# 부정인데 정답이 차 있으면 막는다. **라벨과 내용이 어긋난 것**이고, 그대로 두면
# 채점에서 빠지는 레코드가 정답을 들고 있게 된다
def test_negative_with_gold_spans_is_rejected():
    problems = validate_items([_negative(gold_spans=["실제로는 있는 정답"])])
    assert len(problems) == 1
    assert "gold_spans 가 차 있다" in problems[0]


# 부정인데 쪽이 차 있으면 막는다. 답이 없으면 검수자가 열어 볼 쪽도 없다.
# **「정해야 할 것 넷」에 없던 다섯째 제약이다** — `gold_page` 라벨 검사가 그룹의 모든
# 포맷과 정확히 일치하기를 요구해서, 안 풀면 부정이 통째로 못 들어온다
def test_negative_with_gold_page_is_rejected():
    problems = validate_items([_negative(gold_page={"hwpx": 3, "pdf": 3, "hwpx-가로": 5})])
    assert len(problems) == 1
    assert "gold_page 가 차 있다" in problems[0]


# 긍정 쪽 검사는 그대로 산다 (회귀). `answerable` 을 더하면서 이쪽이 느슨해지면
# 오타·누락이 조용히 통과한다 — 그것이 빈 `gold_spans` 를 부정의 표시로 안 쓴 이유다
def test_answerable_item_still_requires_gold_spans():
    problems = validate_items([_negative(answerable=True, gold_spans=[])])
    assert any("gold_spans 가 비었거나" in p for p in problems)


# 검수 도장은 부정에서도 돈다. **질의 하나에 묶인다** — 사람이 판단한 것이
# *"이 질문의 답이 이 코퍼스에 없다"* 하나라 그것이 정확하다
def test_review_stamp_binds_to_the_query_alone_for_negatives():
    query = "이 지침의 급식비 단가는 얼마인가요?"
    stamped = _negative(reviewed_by="human", reviewed_hash=content_hash(query, []))
    assert validate_items([stamped]) == []

    # 질의를 고치면 도장이 깨진다
    moved = _negative(
        query=query + " 그리고 교통비는요?",
        reviewed_by="human",
        reviewed_hash=content_hash(query, []),
    )
    assert any("검수 뒤에 질의나 정답이 바뀌었다" in p for p in validate_items([moved]))


# **`score_query()` 는 여전히 빈 `gold_spans` 에 예외를 던진다.**
#
# 이 자물쇠를 풀면 부정 질의가 `hit@k = 0` 으로 **자동 만점**이 되어 검색기를 못 가르고,
# `recall` 은 분모가 0 이 된다. 그래서 부정은 채점 규칙을 고쳐서가 아니라
# **러너가 빼는 것**으로 다룬다 (`split_scorable`)
def test_score_query_still_refuses_empty_gold_spans():
    with pytest.raises(ValueError, match="채점할 수 없다"):
        score_query("hneg-01", ["아무 청크 텍스트"], [], 10)


# 러너가 부정을 채점에서 빼고 그 수를 센다. **센 값이 리포트에 실리는 것**이 요점이라,
# 빼기만 하고 안 세면 세트가 48건인데 지표는 20건 기준인 것이 아무 데도 안 보인다
def test_split_scorable_removes_negatives_and_counts_them():
    items = [
        _negative(query_id="hneg-01"),
        _negative(query_id="hpos-01", answerable=True, gold_spans=["정답 문장 하나"]),
        _negative(query_id="hneg-02"),
    ]
    scorable, skipped = split_scorable(items)
    assert [i.query_id for i in scorable] == ["hpos-01"]
    assert skipped == 2


# 사람 세트가 긍정과 부정을 **둘 다** 들고 있고 통째로 성립한다.
# 위 가상 데이터와 달리 이쪽은 실제로 커밋된 파일을 본다 — 부정을 넣다가 긍정 쪽 레코드를
# 깨뜨리는 것을 여기서 잡는다 (span 검사는 fixtures 가 있을 때만이라 여기서 안 한다)
def test_human_set_carries_both_kinds():
    items = load_qa_set(HUMAN_SET_PATH).items
    assert validate_items(items) == []
    positives = [i for i in items if i.answerable]
    negatives = [i for i in items if not i.answerable]
    assert positives and negatives, f"긍정 {len(positives)} · 부정 {len(negatives)}"
# `must_*` 가 없는 레코드는 **빈 라벨 + pending 으로 읽힌다.** 기본값이 그렇기 때문이고,
# 그래서 아직 라벨을 안 단 98건이 이 필드를 안 적고도 그대로 산다
def test_must_labels_default_to_empty():
    item = QAItem.from_json({
        "query_id": "x-10", "query": "질문", "source_doc": "시행령-개정안-안내서",
        "gold_spans": ["어떤 정답 문장"],
    })
    assert item.must_values == []
    assert item.must_points == []
    assert item.must_reviewed_by == "pending"
    assert item.must_reviewed_hash == ""


# jsonl 에 적힌 라벨이 실제로 실린다. **둘이 따로 실린다는 것**이 요점이다 —
# 한 칸으로 접으면 결정적인 값 축이 LLM 판정에 섞인다(`.claude/wiki/plan_log/평가-세트.md` 13회차)
def test_must_labels_are_read_from_json():
    item = QAItem.from_json({
        "query_id": "x-11", "query": "질문", "source_doc": "시행령-개정안-안내서",
        "gold_spans": ["어떤 정답 문장"],
        "must_values": ["6개월", "2020.10.1."],
        "must_points": ["시행일은 유예기간을 거친 뒤이다"],
    })
    assert item.must_values == ["6개월", "2020.10.1."]
    assert item.must_points == ["시행일은 유예기간을 거친 뒤이다"]


# **13회차 결정을 잠그는 테스트다 — `must_*` 는 `reviewed_hash` 를 안 건드린다.**
# 넣었으면 이미 검수된 43건의 도장이 전부 깨져 재검수를 강요하는데, 그것이
# `.claude/wiki/plan_log/검색-생성.md` 56회차가 *"기대 열이 움직여 혼동행렬 여섯 판이 못 견주게 된다"*
# 며 라벨을 안 건드린 자리다. 도장이 둘인 것은 실수가 아니라 그 이유로 고른 것이다
def test_must_labels_do_not_disturb_the_span_review_stamp():
    base = {
        "query_id": "x-12", "query": "질문", "source_doc": "시행령-개정안-안내서",
        "gold_spans": ["어떤 정답 문장"],
    }
    bare = QAItem.from_json(dict(base))
    labeled = QAItem.from_json({**base, "must_values": ["6개월"], "must_points": ["ㄱ"]})

    stamp = content_hash(bare.query, bare.gold_spans)
    assert content_hash(labeled.query, labeled.gold_spans) == stamp


# 모르는 키는 **여전히** 막힌다. 필드를 넷 더하면서 이 가드가 헐거워지지 않았는지 본다 —
# 오타 난 `must_value`(단수)가 조용히 무시되면 그 레코드만 라벨 없이 채점된다
def test_a_typo_in_the_new_field_is_still_rejected():
    with pytest.raises(ValueError, match="모르는 키"):
        QAItem.from_json({
            "query_id": "x-13", "query": "질문", "source_doc": "시행령-개정안-안내서",
            "gold_spans": ["어떤 정답 문장"], "must_value": ["6개월"],
        })


# 실제 커밋된 사람 세트가 라벨을 달고도 통째로 성립한다. **라벨이 붙은 레코드는 도장을
# 갖는다** — 사람이 안 본 라벨이 `human` 으로 실리는 것을 여기서 잡는다
def test_labeled_records_in_the_human_set_carry_their_own_stamp():
    items = load_qa_set(HUMAN_SET_PATH).items
    labeled = [i for i in items if i.must_values or i.must_points]
    assert labeled, "라벨이 하나도 없다 — 이 검사가 아무것도 안 보고 있다"
    for item in labeled:
        assert item.must_reviewed_by == "human", item.query_id
        expected = content_hash(item.query, [*item.must_values, *item.must_points])
        assert item.must_reviewed_hash == expected, item.query_id


# **도장을 가른 이유가 이 테스트다.** 같은 문자열을 `must_points` ↔ `must_not_claim` 으로
# 옮기는 것이 뜻이 제일 크게 뒤집히는 편집인데(담아야 할 것 ↔ 말하면 안 되는 것),
# 한 지문에 묶으면 payload 가 이어 붙인 **한 목록**이라 그 편집이 지문에 안 걸린다.
# 마지막 줄이 그 «묶었다면» 을 실제로 찍어 보인다 — 지우면 가른 근거가 사라진다
def test_moving_a_string_into_must_not_claim_is_caught_by_the_stamp():
    kept = QAItem(
        query_id="q1", query="질문", source_doc="시행령-개정안-안내서",
        gold_spans=["어떤 정답 문장"], must_points=["어떤 사실"],
    )
    moved = QAItem(
        query_id="q1", query="질문", source_doc="시행령-개정안-안내서",
        gold_spans=["어떤 정답 문장"], must_not_claim=["어떤 사실"],
    )

    def bundled(item: QAItem) -> str:
        return content_hash(item.query, [*item.must_values, *item.must_points, *item.must_not_claim])

    def apart(item: QAItem) -> tuple[str, str]:
        return (
            content_hash(item.query, [*item.must_values, *item.must_points]),
            content_hash(item.query, item.must_not_claim),
        )

    assert apart(kept) != apart(moved)
    assert bundled(kept) == bundled(moved), "묶은 지문은 이 편집을 못 잡는다"


# **골든 답변 라벨의 도장도 `--check` 가 본다**(2026-08-29). 도장이 넷인 것은 되풀이가 아니라
# 사람이 판단한 것이 넷이기 때문이다 — 여기서는 «필수 사실» 쪽을 고치고 도장을 안 고친 것을 잡는다
def test_the_must_stamp_breaks_when_the_label_changes():
    import dataclasses

    items = load_qa_set(HUMAN_SET_PATH).items
    original = next(i for i in items if i.must_points and i.must_reviewed_by == "human")

    tampered = [dataclasses.replace(original, must_points=["몰래 바꾼 필수 사실"])]
    problems = validate_items(tampered)
    assert problems, "라벨을 바꿨는데 도장이 그대로 통과했다"
    assert "필수 사실 라벨이 바뀌었다" in problems[0]


# 같은 규칙이 「단정 금지」쪽에도 있고, **지문이 서로 독립이라 한쪽만 움직인다.**
# 묶었다면 여기서 두 도장이 함께 깨져 무엇이 바뀌었는지 못 읽는다
def test_the_must_not_claim_stamp_moves_alone():
    import dataclasses

    items = load_qa_set(HUMAN_SET_PATH).items
    original = next(i for i in items if i.must_not_claim and i.must_points)

    tampered = dataclasses.replace(original, must_not_claim=["몰래 바꾼 금지 항목"])
    problems = validate_items([tampered])
    assert len(problems) == 1, problems
    assert "단정 금지 라벨이 바뀌었다" in problems[0]

    # 같이 갱신하면 통과한다 — 막는 것이 목적이지 못 고치게 하는 것이 아니다(6회차)
    fixed = dataclasses.replace(
        tampered, must_not_claim_reviewed_hash=content_hash(tampered.query, tampered.must_not_claim)
    )
    assert not validate_items([fixed])


# **부정 예시에 골든 답변 라벨을 달면 막는다.** 13회차가 «달면 *답할 수 없다* 가 두 곳에
# 적힌다» 로 정한 것인데 **결정만 기록에 있고 코드가 안 보던 자리**였다
def test_a_negative_may_not_carry_golden_answer_labels():
    problems = validate_items([_negative(must_points=["담아야 할 것"])])
    assert any("골든 답변 라벨이 달려 있다" in p for p in problems)

    problems = validate_items([_negative(must_not_claim=["단정하면 안 되는 것"])])
    assert any("골든 답변 라벨이 달려 있다" in p for p in problems)


# **`pending` 은 문제가 아니다** — 기계가 제안하고 사람이 거르는 절차라 검수 대기가 정상이다.
# 여기서 막으면 라벨을 제안하는 순간 `--check` 가 죽어 제안 자체를 못 한다
def test_a_pending_label_is_not_a_problem():
    item = QAItem(
        query_id="x-14", query="질문", source_doc="시행령-개정안-안내서",
        gold_spans=["어떤 정답 문장"], gold_page={"hwpx": 1, "pdf": 1, "hwpx-가로": 1},
        reviewed_by="pending", must_not_claim=["아직 안 읽은 항목"],
    )

    assert not [p for p in validate_items([item]) if "단정 금지" in p]


# 라벨이 붙은 레코드는 **자기 도장을 갖는다.** 사람이 안 본 라벨이 커밋되는 것과, 검수 뒤에
# 문구를 고치고 도장을 안 고친 것을 함께 잡는다(`must_reviewed_*` 쪽 형제와 같은 모양이다).
#
# **새 라벨을 제안하면 사람이 도장을 찍기 전까지 이 검사가 빨갛다 — 그것이 의도다.**
# 기계가 문구를 제안하는 것이 이 칸의 절차라(16회차), 제안이 그대로 굳는 것을 여기서 막는다
def test_must_not_claim_records_carry_their_own_stamp():
    items = load_qa_set(HUMAN_SET_PATH).items
    labeled = [i for i in items if i.must_not_claim]
    assert labeled, "라벨이 하나도 없다 — 이 검사가 아무것도 안 보고 있다"
    for item in labeled:
        assert item.must_not_claim_reviewed_by == "human", (
            f"{item.query_id}: 사람이 읽고 도장을 찍거나, 라벨을 빼라"
        )
        expected = content_hash(item.query, item.must_not_claim)
        assert item.must_not_claim_reviewed_hash == expected, (
            f"{item.query_id}: 문구가 바뀌었다 — 다시 읽고 도장을 갱신하거나 pending 으로 되돌려라"
        )


# ── 산문 골든 `reference` (17회차) ──────────────────────────────────────────
#
# **넷째 독립 도장이다.** 같은 payload 에 넣었으면 `reviewed_by` 가 `human` 인 103건
# (사람 43 + 자동 60)이 전부 깨져 재검수를 강요한다 — `must_*` 가 이미 피한 자리다.


# 칸을 더한 것이 **기존 도장을 한 건도 안 건드린다.** 이 테스트가 이 축의 전제다 —
# 깨지면 골든 답변 작업이 아니라 세트 전체가 재검수 대상이 된다
def test_reference_does_not_disturb_the_span_review_stamp():
    import dataclasses

    for path in (QA_SET_PATH, HUMAN_SET_PATH):
        items = load_qa_set(path).items
        stamped = [i for i in items if i.reviewed_by == "human"]
        assert stamped, f"{path.name}: 도장 찍힌 레코드가 없다 — 이 검사가 무의미해진다"

        with_reference = [
            dataclasses.replace(i, reference="사람이 쓴 산문 골든입니다.") for i in stamped
        ]
        problems = [p for p in validate_items(with_reference) if "질의나 정답이 바뀌었다" in p]
        assert not problems, f"{path.name}: reference 를 달았더니 span 도장이 깨졌다 — {problems[:2]}"


# 라벨을 고치고 도장을 안 고친 것을 잡는다. **셋과 독립이라 이것만 움직인다** —
# 묶였다면 여기서 네 도장이 함께 깨져 무엇이 바뀌었는지 못 읽는다
def test_the_reference_stamp_moves_alone():
    import dataclasses

    items = load_qa_set(HUMAN_SET_PATH).items
    base = next(i for i in items if i.must_points and i.must_reviewed_by == "human")
    labeled = dataclasses.replace(
        base,
        reference="원래 쓴 산문 골든입니다.",
        reference_reviewed_by="human",
        reference_reviewed_hash=content_hash(base.query, ["원래 쓴 산문 골든입니다."]),
    )
    assert not validate_items([labeled])

    tampered = dataclasses.replace(labeled, reference="몰래 바꾼 산문 골든입니다.")
    problems = validate_items([tampered])
    assert len(problems) == 1, problems
    assert "산문 골든 라벨이 바뀌었다" in problems[0]

    fixed = dataclasses.replace(
        tampered, reference_reviewed_hash=content_hash(tampered.query, [tampered.reference])
    )
    assert not validate_items([fixed])


# **부정 예시에는 안 단다.** 부정의 골든은 «기권» 이고 negative 버킷이 이미 잰다 —
# 산문으로 한 번 더 적으면 *«답할 수 없다»* 가 두 곳에 적히고 한쪽이 조용히 낡는다
def test_a_negative_may_not_carry_a_reference():
    problems = validate_items([_negative(reference="이 질문에는 답할 수 없습니다.")])
    assert any("골든 답변 라벨이 달려 있다" in p for p in problems)


# 오타 난 키는 여전히 막힌다. **필드를 더하면 이 가드가 헐거워지기 쉽다** —
# dataclass 를 먼저 고치고 jsonl 을 나중에 고치는 순서를 강제하는 것이 이 검사다
def test_a_typo_in_the_reference_field_is_still_rejected():
    with pytest.raises(ValueError, match="모르는 키"):
        QAItem.from_json(
            {
                "query_id": "x-90",
                "query": "질문",
                "source_doc": "시행령-개정안-안내서",
                "gold_spans": ["어떤 정답 문장"],
                "referance": "오타 난 키",
            }
        )


# 같은 문자열을 `must_points` ↔ `reference` 로 **옮긴 편집**이 잡히나.
# 뜻이 크게 달라지는 편집인데(담아야 할 것 ↔ 답 자체) 도장을 묶었으면 안 걸린다
def test_moving_a_string_between_reference_and_must_points_is_caught():
    text = "행정정보 데이터세트 관리기준표를 시스템별로 작성한다"
    common = dict(
        query_id="x-91",
        query="질문",
        source_doc="시행령-개정안-안내서",
        gold_spans=["어떤 정답 문장"],
        gold_page={"hwpx": 1, "pdf": 1, "hwpx-가로": 1},
    )
    as_point = QAItem(
        **common,
        must_points=[text],
        must_reviewed_by="human",
        must_reviewed_hash=content_hash("질문", [text]),
    )
    as_reference = QAItem(
        **common,
        reference=text,
        reference_reviewed_by="human",
        reference_reviewed_hash=content_hash("질문", [text]),
    )

    # 각각은 제 도장으로 성립한다
    assert not [p for p in validate_items([as_point]) if "라벨이 바뀌었다" in p]
    assert not [p for p in validate_items([as_reference]) if "라벨이 바뀌었다" in p]

    # 도장을 그대로 둔 채 옮기면 **양쪽 다** 깨진다 — 묶었으면 payload 가 같아 안 걸린다
    moved = QAItem(
        **common,
        reference=text,
        reference_reviewed_by="human",
        reference_reviewed_hash=content_hash("질문", []),
        must_reviewed_by="human",
        must_reviewed_hash=content_hash("질문", [text]),
    )
    problems = validate_items([moved])
    assert any("필수 사실 라벨이 바뀌었다" in p for p in problems), problems
    assert any("산문 골든 라벨이 바뀌었다" in p for p in problems), problems


# ── 부분 답 `partial` (17회차 뒤, 2026-09-09) ────────────────────────────────
#
# 긍정/부정 둘로는 안 담기는 질의가 실재한다 — 문서가 절반만 답하는 것들이다.
# 긍정으로 두면 골든이 질문에 답한 척하고, 부정으로 두면 답한 절반을 버린다.


def _partial(**over):
    base = dict(
        query_id="p-01",
        query="어떤 처벌을 받게 되나요?",
        source_doc="시행령-개정안-안내서",
        gold_spans=["손상시킨 자는 벌칙 대상이다"],
        gold_page={"hwpx": 1, "pdf": 1, "hwpx-가로": 1},
        partial=True,
        must_not_claim=["구체적 처벌 내용"],
    )
    base.update(over)
    return QAItem(**base)


# **절반은 답하므로 긍정이다.** 통째로 없으면 그건 부분 답이 아니라 부정이다 —
# 둘을 섞으면 「답이 없다」와 「답이 반만 있다」가 같은 칸에 앉는다
def test_a_partial_answer_stays_answerable():
    assert not validate_items([_partial()])

    problems = validate_items([_partial(answerable=False, gold_spans=[], gold_page={})])
    assert any("partial 인데 answerable 이 거짓이다" in p for p in problems), problems


# **무엇이 비는지를 적어야 갈래가 관측이 된다.** 안 적으면 이름만 남고 «어디가 비었나» 를
# 아무도 못 되짚는다 — 부정 예시가 라벨을 못 다는 것의 짝이다(저쪽은 적을 것이 없다)
def test_a_partial_answer_must_say_what_is_missing():
    problems = validate_items([_partial(must_not_claim=[])])
    assert any("must_not_claim 이 비었다" in p for p in problems), problems


# **도장 넷 어느 것도 안 건드린다** — `answerable` 과 같은 자리라 지문 밖이다.
# 켰다고 사람이 정답을 다시 볼 이유가 없다
def test_partial_does_not_disturb_any_stamp():
    import dataclasses

    items = load_qa_set(HUMAN_SET_PATH).items
    base = next(i for i in items if i.must_not_claim and i.reviewed_by == "human")
    flipped = dataclasses.replace(base, partial=not base.partial)

    assert not [p for p in validate_items([flipped]) if "바뀌었다" in p]


# 커밋된 세트가 이 갈래를 실제로 담고 있고 계약을 지키나. **0건이면 이 검사가 아무것도 안
# 보고 있다** — 갈래를 열어 놓고 아무도 안 쓰는 상태를 잡는 자리다
def test_the_committed_set_carries_partial_answers():
    items = load_qa_set(HUMAN_SET_PATH).items
    partial = [i for i in items if i.partial]
    assert partial, "부분 답이 하나도 없다 — 갈래만 열고 안 쓰고 있다"

    for item in partial:
        assert item.answerable, f"{item.query_id}: 부분 답인데 부정으로 적혀 있다"
        assert item.must_not_claim, f"{item.query_id}: 무엇이 비는지가 안 적혀 있다"
        assert item.gold_spans, f"{item.query_id}: 답한 절반의 근거가 없다"
