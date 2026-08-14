"""표 -> 마크다운 직렬화 테스트."""

from __future__ import annotations

from rag_pipeline.ingestion.loader.content.base import content_hash, rows_to_markdown


# 기본 형태: 첫 행이 헤더, 그 아래 구분선
def test_basic_table() -> None:
    markdown = rows_to_markdown([["이름", "점수"], ["홍길동", "95"]])

    assert markdown == "| 이름 | 점수 |\n| --- | --- |\n| 홍길동 | 95 |"


# 셀 안의 파이프는 이스케이프해야 표가 깨지지 않는다
def test_pipe_in_cell_is_escaped() -> None:
    markdown = rows_to_markdown([["a|b", "c"]])

    assert "a\\|b" in markdown


# 셀 안의 개행은 공백으로 바꿔야 행 구조가 유지된다
def test_newline_in_cell_becomes_space() -> None:
    markdown = rows_to_markdown([["첫 줄\n둘째 줄", "값"]])

    assert "첫 줄 둘째 줄" in markdown
    assert markdown.count("\n") == 1


# 행마다 열 수가 다르면 짧은 쪽을 빈 칸으로 채운다
def test_ragged_rows_are_padded() -> None:
    markdown = rows_to_markdown([["a", "b", "c"], ["1"]])
    lines = markdown.split("\n")

    assert lines[0].count("|") == lines[2].count("|")


# 전부 빈 행은 버린다
def test_blank_rows_dropped() -> None:
    markdown = rows_to_markdown([["a", "b"], ["", ""], ["1", "2"]])

    assert markdown.count("\n") == 2


# 빈 입력은 빈 문자열
def test_empty_input() -> None:
    assert rows_to_markdown([]) == ""
    assert rows_to_markdown([[""], [""]]) == ""


# None 셀은 빈 문자열로 처리한다
def test_none_cells() -> None:
    markdown = rows_to_markdown([["a", None], ["1", "2"]])  # type: ignore[list-item]

    assert "| a |  |" in markdown


# 같은 내용은 같은 해시, 다른 salt는 다른 해시 (캐시 키가 모델별로 분리돼야 한다)
def test_content_hash_is_salted() -> None:
    data = b"same-bytes"

    assert content_hash(data, salt="vision") == content_hash(data, salt="vision")
    assert content_hash(data, salt="vision") != content_hash(data, salt="llamaparse")
