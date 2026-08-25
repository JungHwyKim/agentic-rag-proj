"""BM25 토크나이저. 한글은 bigram, 영문·숫자는 낱말이라는 이분법을 지킨다."""

from __future__ import annotations

import pytest

from rag_pipeline.retrieval.tokenize import tokenize


# 이 토크나이저의 존재 이유다 — 조사가 붙어도 토큰을 공유해야 BM25 가 둘을 잇는다.
# 공백으로 자르면 `시행령은` != `시행령` 이라 완전히 못 만난다
def test_josa_does_not_break_the_match():
    assert set(tokenize("시행령은")) & set(tokenize("시행령")) == {"시행", "행령"}


# 한글은 이웃한 두 글자씩. 3글자면 2개다
def test_hangul_becomes_bigrams():
    assert tokenize("시행령") == ["시행", "행령"]


# 한 글자는 그 글자 자체가 토큰이다. 빈 리스트를 주면 `표`·`장` 같은 낱말이 통째로 사라진다
def test_single_syllable_survives():
    assert tokenize("표") == ["표"]


# 영문·숫자는 공백이 이미 낱말을 갈라 주므로 쪼개지 않는다. 소문자로 맞춘다
@pytest.mark.parametrize(
    ("text", "expected"),
    [("AI", ["ai"]), ("BM25", ["bm25"]), ("2026", ["2026"]), ("DNA", ["dna"])],
)
def test_ascii_runs_stay_whole(text: str, expected: list[str]):
    assert tokenize(text) == expected


# 두 갈래가 한 문장에 섞여도 각자 규칙대로 간다
def test_mixed_text():
    assert tokenize("AI 활동") == ["ai", "활동"]


# 구두점·기호는 경계이고 토큰이 되지 않는다. `DNA+` 의 `+` 가 사라지는 것이 감수 사항이다
@pytest.mark.parametrize("text", ["DNA+", "DNA·", "(DNA)", "DNA,"])
def test_punctuation_is_a_boundary(text: str):
    assert tokenize(text) == ["dna"]


# 숫자가 한글 사이에 끼면 구간이 갈린다. `제3조` 는 한 낱말이 아니라 셋이다
def test_digits_split_a_hangul_run():
    assert tokenize("제3조") == ["제", "3", "조"]


# 한글 구간이 bigram 을 넘어 이어지지 않는다 — 공백을 사이에 두면 별개 구간이다
def test_space_separates_runs():
    assert tokenize("시행 령") == ["시행", "령"]


# 질의와 문서에 같은 함수를 쓴다는 것이 계약이다. 대소문자만 다른 질의가 같은 토큰을 낸다
def test_case_insensitive():
    assert tokenize("ai") == tokenize("AI") == tokenize("Ai")


# 토큰이 하나도 안 나오는 입력이 있다. Bm25Index 가 이런 청크를 빼는 근거다
@pytest.mark.parametrize("text", ["", "   ", "!!! ??? ...", "ㄱㄴㄷ"])
def test_no_tokens(text: str):
    assert tokenize(text) == []
