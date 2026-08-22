"""표 청킹 — 헤더 반복과 행 그룹."""

from __future__ import annotations

from rag_pipeline.config import SplitterConfig
from rag_pipeline.ingestion.loader.content.base import rows_to_markdown
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.ingestion.splitter.tables import normalize_header, split_table
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


# --- 가로 병합 헤더 ---
#
# 로더는 "가로 연장은 빈 칸으로 둔다"는 규칙을 쓰는데, 그 근거(splitter 가 행 단위로만
# 쪼갠다)는 데이터 행에만 성립한다. 헤더에서는 옆으로 이어진 이름이 곧 그 열의 이름이라,
# 그대로 두면 **이름 없는 열이 조각 수만큼 복제된다.**
# 실측: 시행령 24쪽 67행 표에서 이름 있는 열이 0·1·4·5뿐이었다.


# 병합된 헤더 이름이 하위 열까지 채워져야 한다
def test_merged_header_names_are_filled() -> None:
    rows = [
        ["영역", "항목", "", "내용"],
        ["업무 정보", "업무정보(1)", "업무명", "교육운영 관리"],
        ["업무 정보", "업무정보(1)", "업무 내용", "교육을 준비한다"],
    ]

    assert normalize_header(rows)[0] == ["영역", "항목", "항목", "내용"]


# 표 전체에서 비어 있는 열은 버린다. 헤더도 본문도 비어 있어야 한다
def test_column_empty_in_whole_table_is_dropped() -> None:
    rows = [
        ["영역", "항목", "", "내용"],
        ["가", "나", "", "다"],
        ["라", "마", "", "바"],
    ]

    assert normalize_header(rows) == [["영역", "항목", "내용"], ["가", "나", "다"], ["라", "마", "바"]]


# 본문에 값이 하나라도 있으면 버리지 않는다. 버리면 그 값이 사라진다
def test_column_with_any_value_survives() -> None:
    rows = [
        ["영역", "항목", "", "내용"],
        ["가", "나", "", "다"],
        ["라", "마", "종류", "바"],
    ]

    normalized = normalize_header(rows)
    assert normalized[0] == ["영역", "항목", "항목", "내용"]
    assert normalized[2][2] == "종류"


# 채우기보다 버리기가 먼저다. 순서가 바뀌면 헤더가 안 비어 있게 되어 아무 열도 못 버린다
def test_drop_happens_before_fill() -> None:
    rows = [["A", "", ""], ["가", "나", ""], ["다", "라", ""]]

    normalized = normalize_header(rows)
    assert len(normalized[0]) == 2
    assert normalized[0] == ["A", "A"]


# 고칠 것이 없으면 원본 마크다운을 그대로 돌려준다. 무조건 다시 렌더하면 멀쩡한 표가
# 공백까지 미묘하게 달라진다
def test_unchanged_table_keeps_original_markdown() -> None:
    rows = [["이름", "점수"], ["홍길동", "95"]]
    markdown = "| 이름 | 점수 |\n| --- | --- |\n| 홍길동 | 95 |"

    pieces = split_table(markdown, rows, max_chars=1000)

    assert pieces == [(markdown, rows)]


# 쪼갠 조각들이 같은 헤더·같은 열 구성을 받아야 한다. 조각마다 다르면 같은 표로 안 읽힌다
def test_split_pieces_share_normalized_header() -> None:
    rows = [["영역", "항목", "", "내용"]]
    # 3열은 한 행에서만 값을 갖는다 — 표 전체가 비어 있지 않으므로 버려지지 않는다
    rows += [["가", f"항목{i}", "세부" if i == 0 else "", "내" * 40] for i in range(8)]

    pieces = split_table(rows_to_markdown(rows), rows, max_chars=200)

    assert len(pieces) > 1
    headers = {tuple(piece_rows[0]) for _, piece_rows in pieces}
    assert headers == {("영역", "항목", "항목", "내용")}
