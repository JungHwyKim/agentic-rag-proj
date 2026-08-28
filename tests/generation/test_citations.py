"""인용 검사. 순수 함수라 모킹이 없다 — (text, sources) 만 본다 (generation/citations.py)."""

from __future__ import annotations

import pytest

from rag_pipeline.generation import NO_EVIDENCE_ANSWER, check_citations
from rag_pipeline.generation.citations import cited_numbers
from rag_pipeline.schemas.retrieval import RetrievedChunk


# 근거 청크 하나. 번호는 위치에서 나오므로 내용은 아무거나 된다
def _chunk(index: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"안내서.pdf:{index:04d}",
        document_id="안내서.pdf",
        text=f"제{index}조 …",
        metadata={"source_name": "안내서.pdf", "page": index},
        dense_score=0.8,
    )


# 근거 두 개
@pytest.fixture
def two() -> list[RetrievedChunk]:
    return [_chunk(1), _chunk(2)]


# 규칙 3을 지킨 답변은 흔적을 안 남긴다. **None 이 "문제 없음"의 유일한 표현이다**
def test_valid_citations_leave_no_trace(two):
    assert check_citations("위약금은 20% 입니다 [1]. 기한은 30일 [2].", two) is None


# 근거 수를 넘는 번호는 가리킬 것이 없다
def test_out_of_range_number_is_caught(two):
    trace = check_citations("위약금은 20% 입니다 [3].", two)

    assert trace is not None
    assert trace.problem == "out_of_range"
    assert trace.numbers == [3]
    # 화면이 «근거는 2개인데» 를 말하려면 이 값이 있어야 한다
    assert trace.source_count == 2


# **[0] 도 범위 밖이다.** build_evidence_block() 이 1부터 세므로 0번 근거는 없다 —
# `number > len(chunks)` 만 보면 이것이 통과한다
def test_zero_is_out_of_range(two):
    trace = check_citations("근거 [0] 을 보세요.", two)

    assert trace is not None
    assert trace.problem == "out_of_range"
    assert trace.numbers == [0]


# 범위 밖이 여럿이면 **나온 순서대로 중복 없이** 담는다. 사람이 읽을 문구가 되기 때문이다
def test_out_of_range_numbers_keep_order_without_duplicates(two):
    trace = check_citations("[5] 그리고 [3] 또 [5] 입니다.", two)

    assert trace is not None
    assert trace.numbers == [5, 3]


# 근거가 있는데 인용이 하나도 없으면 규칙 3을 통째로 어긴 것이다
def test_missing_citation_is_caught(two):
    trace = check_citations("위약금은 20% 입니다.", two)

    assert trace is not None
    assert trace.problem == "missing"
    # **빈 목록이다** — 범위 밖일 번호가 애초에 없다. 둘은 동시에 못 난다
    assert trace.numbers == []
    assert trace.source_count == 2


# 근거가 0건이면 인용할 것이 없다. generate_answer() 가 모델을 아예 안 부르는 자리다
def test_no_chunks_is_not_checked():
    assert check_citations(NO_EVIDENCE_ANSWER, []) is None


# **근거가 있어도** 모델이 규칙 2를 따라 이 문장을 고를 수 있다. 그때도 인용 0개가 옳다 —
# 여기를 안 가르면 «근거를 못 찾았다» 가 전부 인용 오류로 뜬다. 이 저장소가 정상 경로로
# 못 박아 둔 답변이다(schemas/answer.py — "근거를 못 찾은 것은 실패가 아니다")
def test_no_evidence_answer_is_not_checked_even_with_chunks(two):
    assert check_citations(NO_EVIDENCE_ANSWER, two) is None


# 모델이 그 문장 앞뒤에 공백을 붙여도 같다 — generate_answer() 가 strip 해 내보내지만
# 캐시에서 온 옛 답변까지 그랬다는 보장은 없다
def test_no_evidence_answer_is_matched_after_stripping(two):
    assert check_citations(f"  {NO_EVIDENCE_ANSWER}\n", two) is None


# **한국 공문서 표기를 인용으로 세지 않는다.** [별표 1]·[붙임 2] 는 대괄호 안이 숫자가
# 아니라 안 걸린다 — 걸리면 정상 답변마다 경고가 뜬다
@pytest.mark.parametrize("text", ["[별표 1] 을 보세요 [1].", "[붙임 2] 참고 [1].", "[표1] [1]"])
def test_korean_bracket_notation_is_not_a_citation(text, two):
    assert check_citations(text, two) is None


# 그 문서 표기만 있고 진짜 인용이 없으면 **missing 이다** — 위 규칙의 뒷면이다.
# "대괄호가 있으니 인용했다" 로 세면 이 답변이 통과한다
def test_bracket_notation_alone_is_still_missing(two):
    trace = check_citations("[별표 1] 을 보세요.", two)

    assert trace is not None
    assert trace.problem == "missing"


# 붙여 쓴 인용도 각각 센다. 모델이 [1][2] 로 쓰는 일이 흔하다
def test_adjacent_citations_are_separate():
    assert cited_numbers("근거 [1][2] 입니다.") == [1, 2]


# 같은 번호를 여러 번 써도 한 번만 센다
def test_repeated_citation_counts_once():
    assert cited_numbers("[1] 이고 [1] 이며 [2] 다.") == [1, 2]


# 인용이 없으면 빈 목록이다
def test_no_citation_yields_empty_list():
    assert cited_numbers("아무 표시도 없습니다.") == []
