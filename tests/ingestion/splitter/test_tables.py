"""표 청킹 — 헤더 반복과 행 그룹."""

from __future__ import annotations

from rag_pipeline.config import SplitterConfig
from rag_pipeline.ingestion.loader.content.base import rows_to_markdown
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.ingestion.splitter.tables import split_table
from tests.ingestion.splitter.conftest import table, title

PLAIN = SplitterConfig(include_heading_in_text=False)


# 행 20개짜리 표. 마크다운으로 만들면 400자 언저리다
def _big_rows(count: int = 20) -> list[list[str]]:
    header = [["구분", "대상", "금액"]]
    return header + [[f"항목{i}", f"기관{i}", f"{i}억"] for i in range(1, count + 1)]


# 상한 이하 표는 통째로 한 청크다
def test_small_table_stays_whole(make_document) -> None:
    rows = [["이름", "점수"], ["홍길동", "95"]]
    document = make_document([table(rows_to_markdown(rows), rows)])

    chunks = split(document, PLAIN)

    assert len(chunks) == 1
    assert chunks[0].type.value == "table"
    assert chunks[0].metadata["rows"] == rows


# 큰 표는 행 그룹으로 갈리고 **모든 조각이 헤더 행을 다시 받는다**.
# 행 하나만 검색됐을 때 '1억'이 무엇의 금액인지 복원할 수 있어야 한다
def test_large_table_repeats_header_in_every_piece() -> None:
    rows = _big_rows()
    markdown = rows_to_markdown(rows)

    pieces = split_table(markdown, rows, max_chars=200)

    assert pieces is not None
    assert len(pieces) > 1
    for _, piece_rows in pieces:
        assert piece_rows[0] == ["구분", "대상", "금액"]
    # 데이터 행은 중복 없이 정확히 한 번씩만 나온다 — 표에 겹침을 주면 중복 색인된다
    seen = [row for _, piece_rows in pieces for row in piece_rows[1:]]
    assert seen == rows[1:]


# 조각마다 상한을 지킨다
def test_large_table_pieces_respect_cap() -> None:
    rows = _big_rows()
    pieces = split_table(rows_to_markdown(rows), rows, max_chars=200)

    assert pieces is not None
    assert all(len(markdown) <= 200 for markdown, _ in pieces[:-1])


# 헤더 + 행 하나가 이미 상한을 넘어도 행을 쪼개지 않는다. 쪼개면 열 대응이 깨져
# 표의 의미가 사라지므로, 상한을 넘기더라도 행 단위를 지킨다
def test_single_row_wider_than_cap_is_not_broken() -> None:
    rows = [["구분", "내용"], ["가", "아" * 300], ["나", "이" * 300]]

    pieces = split_table(rows_to_markdown(rows), rows, max_chars=100)

    assert pieces is not None
    assert len(pieces) == 2
    assert all(len(piece_rows) == 2 for _, piece_rows in pieces)


# rows 를 못 얻으면(Vision 이 표 배열을 안 줄 때) None 을 돌려줘 호출부가 폴백하게 한다
def test_missing_rows_returns_none() -> None:
    assert split_table("| a |\n| --- |\n" + "x" * 500, None, max_chars=100) is None
    assert split_table("|a|\n" + "x" * 500, "행이 아님", max_chars=100) is None


# 폴백 경로: rows 가 없으면 마크다운을 글자 단위로 자른다. 그래도 상한은 지킨다
def test_table_without_rows_falls_back_to_text_split(make_document) -> None:
    document = make_document([table("| 값 |\n| --- |\n" + "칸 " * 500)])

    chunks = split(document, SplitterConfig(max_chunk_chars=300, include_heading_in_text=False))

    assert len(chunks) > 1
    assert all(c.type.value == "table" for c in chunks)
    assert all(len(c.text) <= 300 for c in chunks)


# 표 조각도 자기가 어느 절 소속인지 본문만 보고 알 수 있어야 한다
def test_table_pieces_carry_heading(make_document) -> None:
    rows = _big_rows()
    document = make_document([title("2. 지원 대상", level=1), table(rows_to_markdown(rows), rows)])

    chunks = split(document, SplitterConfig(max_chunk_chars=300))

    assert len(chunks) > 1
    assert all(c.text.startswith("[2. 지원 대상]\n") for c in chunks)
    assert all(c.heading_path == ["2. 지원 대상"] for c in chunks)


# 쪽을 넘긴 표는 page 만 옮기면 뒷부분이 엉뚱한 쪽을 가리킨다. 끝 쪽을 함께 남긴다
def test_page_end_is_preserved(make_document) -> None:
    rows = [["이름"], ["홍길동"]]
    document = make_document([table(rows_to_markdown(rows), rows, page=13, page_end=14)])

    chunks = split(document, PLAIN)

    assert chunks[0].page == 13
    assert chunks[0].page_end == 14
    # 청크 스키마의 제 칸으로 옮겼으므로 metadata 에 중복해 남기지 않는다
    assert "page_end" not in chunks[0].metadata
