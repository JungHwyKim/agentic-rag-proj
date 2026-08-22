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
    _distinct_runs,
    content_hash,
    load_qa_set,
    validate_items,
    validate_spans_across_formats,
)

# 세트 규모의 하한. `plan_log/평가-세트.md` 2회차의 50~80 중 아래쪽이고 **임시값이다** —
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
