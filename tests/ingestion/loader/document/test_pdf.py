"""PDF 로더 테스트."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pymupdf

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.loader.content import table_llamaparse
from rag_pipeline.ingestion.loader.content.base import ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.pdf import (
    _BOUNDARY_SPANS_KEY,
    PdfLoader,
    _fill_merged_cells,
    _merge_split_tables,
    _overlaps,
)
from rag_pipeline.schemas.document import ElementType


# 페이지별 텍스트가 추출되고 페이지 번호가 1부터 붙어야 한다
def test_extracts_text_with_page_numbers(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)
    pages = {e.page for e in document.elements}

    assert document.metadata.page_count == 2
    assert pages == {1, 2}
    assert all(e.type is ElementType.PARAGRAPH for e in document.elements)
    # 텍스트가 제대로 추출됐다면 스캔본으로 오인하지 않는다
    assert document.metadata.warnings == []


# 한국어 텍스트가 깨지지 않아야 한다
def test_korean_text_intact(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)

    assert "첫째 쪽 내용입니다." in document.elements[0].text


# 괘선이 있는 표를 찾아내고 셀 구조를 복원해야 한다
def test_detects_table(pdf_with_table_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_with_table_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    rows = table.metadata["rows"]
    flat = [cell for row in rows for cell in row]
    assert "홍길동" in flat
    assert "95" in flat


# 표 영역 안의 글자가 본문 문단으로 중복 추출되면 안 된다
def test_table_text_not_duplicated(pdf_with_table_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_with_table_file)
    paragraphs = " ".join(e.text for e in document.elements if e.type is ElementType.PARAGRAPH)

    assert "홍길동" not in paragraphs
    assert "아래는 성적표입니다." in paragraphs


# PDF 표는 LlamaParse로 라우팅되어야 한다 (docx와 달리 네이티브 구조가 없다)
def test_pdf_table_routed_to_llamaparse(mocker, pdf_with_table_file: Path) -> None:
    fake_result = SimpleNamespace(markdown="| 이름 | 점수 |\n| --- | --- |\n| 홍길동 | 95 |")
    parse = mocker.Mock(return_value=fake_result)
    mocker.patch.object(
        table_llamaparse,
        "_make_client",
        return_value=SimpleNamespace(parsing=SimpleNamespace(parse=parse)),
    )

    config = IngestConfig()
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            llama_cloud_api_key="test-key",
            openai_api_key=None,
            table_pdf_handler="llamaparse",
            image_handler="skip",
            cache_enabled=False,
        ),
    )

    document = PdfLoader(config).load(pdf_with_table_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert table.metadata["table_source"] == "llamaparse"
    assert document.metadata.llamaparse_calls == 1
    # 문서 전체가 아니라 표 영역만 잘라서 보내야 과금 페이지가 줄어든다
    sent = parse.call_args.kwargs["upload_file"]
    assert sent[0].endswith(".pdf")
    assert len(sent[1]) < pdf_with_table_file.stat().st_size * 3


# 텍스트가 거의 없는 페이지는 스캔본으로 보고 이미지 경로로 보내야 한다
def test_blank_page_treated_as_scan(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "blank.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(str(path))
    document.close()

    parsed = PdfLoader(offline_config).load(path)

    assert any("이미지로 처리" in w for w in parsed.metadata.warnings)


# order 는 문서 전체에 걸쳐 빈틈없이 증가해야 한다
def test_order_is_sequential(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)
    orders = [e.order for e in document.elements]

    assert orders == sorted(orders)
    assert orders == list(range(len(orders)))


# 겹침 판정: 표 영역 안에 완전히 들어간 텍스트 블록은 걸러져야 한다
def test_overlap_detection() -> None:
    table = (0.0, 0.0, 100.0, 100.0)

    assert _overlaps((10.0, 10.0, 50.0, 50.0), table) is True
    assert _overlaps((200.0, 200.0, 250.0, 250.0), table) is False
    # 살짝만 걸치는 블록은 표에 속하지 않은 것으로 본다
    assert _overlaps((90.0, 90.0, 190.0, 190.0), table) is False


# 외부 API 키가 없으면 LlamaParse를 부르지 않아야 한다
def test_no_llamaparse_without_key(pdf_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_file)

    assert document.metadata.llamaparse_calls == 0


# span 의 글자 크기가 실려야 제목 승격이 돈다. 이 로더가 크기를 안 실으면 승격 규칙이
# 멀쩡해도 PDF 에서는 아무 제목도 안 나온다
def test_larger_line_becomes_title(
    pdf_with_heading_file: Path, offline_config: IngestConfig
) -> None:
    document = PdfLoader(offline_config).load(pdf_with_heading_file)

    titles = [e for e in document.elements if e.type is ElementType.TITLE]
    assert [e.text for e in titles] == ["1. 지원 대상"]
    assert titles[0].metadata["heading_level"] == 1


# 판정에만 쓰고 버린다
def test_font_sizes_do_not_leak(
    pdf_with_heading_file: Path, offline_config: IngestConfig
) -> None:
    document = PdfLoader(offline_config).load(pdf_with_heading_file)

    assert all("font_sizes" not in e.metadata for e in document.elements)


# 꺼두면 승격하지 않는다
def test_detect_headings_can_be_disabled(pdf_with_heading_file: Path) -> None:
    config = IngestConfig(detect_headings=False)
    object.__setattr__(config, "_content", ContentRoutingConfig(
        openai_api_key=None, llama_cloud_api_key=None,
        table_pdf_handler="pymupdf", image_handler="ocr", cache_enabled=False,
    ))

    document = PdfLoader(config).load(pdf_with_heading_file)

    assert all(e.type is not ElementType.TITLE for e in document.elements)


# 되붙이기 판정용 표 하나. rows 는 열 수만 맞으면 되므로 내용은 아무거나 둔다
def _table(y0: float, y1: float, cols: int, x0: float = 50.0, x1: float = 500.0):
    return ((x0, y0, x1, y1), [[f"c{i}" for i in range(cols)]])


# 한 표가 조각난 것을 되붙인다. **되돌리면 열 이름이 첫 조각에만 남고 나머지 조각은
# 데이터 행이 헤더 자리에 온다** — 실측으로 시행령 안내서 4쪽 표가 5조각이었다
def test_merge_split_tables_joins_fragments() -> None:
    merged = _merge_split_tables(
        [_table(100.0, 180.0, 4), _table(186.8, 350.0, 4), _table(356.8, 520.0, 4)]
    )

    assert len(merged) == 1
    bbox, rows = merged[0]
    assert bbox == (50.0, 100.0, 500.0, 520.0)
    assert len(rows) == 3


# 간격이 벌어지면 별개 표다. 실측으로 조각은 6.8, 별개 표·상자는 18.8 이상이었다
def test_merge_split_tables_keeps_distant_tables() -> None:
    merged = _merge_split_tables([_table(100.0, 180.0, 2), _table(198.8, 260.0, 2)])

    assert len(merged) == 2


# 열 수가 다르면 붙이지 않는다. 2026 중등 계획 31쪽이 x·간격은 가깝지만 6->3->2->3 이다
def test_merge_split_tables_keeps_different_column_counts() -> None:
    merged = _merge_split_tables([_table(100.0, 180.0, 6), _table(186.0, 260.0, 3)])

    assert len(merged) == 2


# 좌우 끝이 어긋나면 붙이지 않는다
def test_merge_split_tables_keeps_misaligned_tables() -> None:
    merged = _merge_split_tables(
        [_table(100.0, 180.0, 3), _table(186.0, 260.0, 3, x0=120.0, x1=400.0)]
    )

    assert len(merged) == 2


# PyMuPDF 표를 흉내낸다. cells 의 None 이 병합에 덮인 자리이고 extract() 는 그 자리에 빈 문자열을 준다
def _fake_table(rows: list[tuple[list, list[str]]]):
    return SimpleNamespace(
        rows=[SimpleNamespace(cells=cells) for cells, _ in rows],
        extract=lambda: [list(values) for _, values in rows],
    )


# 세로로 이어진 칸은 값을 되풀이한다. **되돌리면 행 하나만 검색됐을 때 어느 구분인지 알 수 없다**
def test_fill_merged_cells_repeats_vertical_span() -> None:
    filled = _fill_merged_cells(
        _fake_table(
            [
                # 첫 칸의 사각형이 **세 행 전체(y 0~30)** 를 덮는다. 실제 세로 병합이 그렇다 —
                # 한 행 높이로 만들면 가짜가 병합을 표현하지 못해 검증이 헛돈다
                ([(0, 0, 100, 30), (100, 0, 200, 10), (200, 0, 300, 10)], ["준비", "B", "C"]),
                ([None, (100, 10, 200, 20), (200, 10, 300, 20)], ["", "D", "E"]),
                ([None, (100, 20, 200, 30), (200, 20, 300, 30)], ["", "F", "G"]),
            ]
        )
    )

    assert filled == [["준비", "B", "C"], ["준비", "D", "E"], ["준비", "F", "G"]]


# 가로로 이어진 칸은 비운다. **위에 앵커가 있어도 가로가 이긴다** — 실측으로 위에 앵커가 있는
# None 칸 661개 중 226개(34.2%)가 이 경우라, 위만 보고 채우면 그만큼 엉뚱한 값이 들어간다
def test_fill_merged_cells_leaves_horizontal_span_empty() -> None:
    filled = _fill_merged_cells(
        _fake_table(
            [
                ([(0, 0, 100, 10), (100, 0, 200, 10), (200, 0, 300, 10)], ["A", "B", "C"]),
                ([(0, 10, 300, 20), None, None], ["전체", "", ""]),
            ]
        )
    )

    assert filled == [["A", "B", "C"], ["전체", "", ""]]


# 채울 근거가 없으면 그대로 둔다
def test_fill_merged_cells_keeps_plain_empty_cells() -> None:
    filled = _fill_merged_cells(
        _fake_table(
            [
                ([(0, 0, 100, 10), (100, 0, 200, 10)], ["A", ""]),
                ([(0, 10, 100, 20), (100, 10, 200, 20)], ["B", ""]),
            ]
        )
    )

    assert filled == [["A", ""], ["B", ""]]


# 쪽을 가로지르는 표는 리전 하나로 합쳐지고 되풀이된 헤더는 버려져야 한다.
# 사이에 있는 쪽 번호("- 1 -")는 한글이 없어 병합을 막지 않는다
def test_page_crossing_table_merged(
    pdf_page_crossing_table_file: Path, offline_config: IngestConfig
) -> None:
    document = PdfLoader(offline_config).load(pdf_page_crossing_table_file)
    tables = [e for e in document.elements if e.type is ElementType.TABLE]

    assert len(tables) == 1
    rows = tables[0].metadata["rows"]
    # 1쪽 3행 + 2쪽 3행 - 되풀이된 헤더 1행
    assert len(rows) == 5
    assert [cell.strip() for cell in rows[0]] == ["구분", "항목", "내용"]
    assert [row[1].strip() for row in rows[1:]] == ["첫째 항목", "둘째 항목", "셋째 항목", "넷째 항목"]
    # 끝 쪽은 HWPX 와 같은 키로 남긴다 — splitter 가 Chunk.page_end 로 나른다
    assert tables[0].metadata["page_end"] == 2


# 쪽 경계에서 끊긴 세로 병합은 앞 쪽 마지막 행에서 이어받되, 값이 나오면 멈춰야 한다.
# 멈추지 않으면 "같은 열 위에 값이 있으면 채운다"로 되돌아가 안 덮어야 할 칸을 덮는다
def test_page_crossing_carries_merge_anchor(
    pdf_page_crossing_table_file: Path, offline_config: IngestConfig
) -> None:
    document = PdfLoader(offline_config).load(pdf_page_crossing_table_file)
    rows = next(e for e in document.elements if e.type is ElementType.TABLE).metadata["rows"]

    # 2쪽 첫 데이터 행의 빈 칸이 1쪽 마지막 행 값으로 채워진다
    assert rows[3][0].strip() == "가 영역"
    # 값이 있던 칸은 덮이지 않는다
    assert rows[4][0].strip() == "나 영역"


# 이어 붙인 표는 blob 을 잃어야 한다. 쪽을 가로지르는 표는 단일 쪽 크롭으로 표현할 수 없어,
# 남겨 두면 유료 경로가 조각 하나만 보낸 결과를 표 전체인 줄 안다
def test_page_crossing_table_drops_blob(
    pdf_page_crossing_table_file: Path, offline_config: IngestConfig
) -> None:
    loader = PdfLoader(offline_config)
    regions = loader.detect_regions(pdf_page_crossing_table_file, LoadContext(offline_config.content))
    tables = [r for r in regions if r.content_type is ContentType.TABLE]

    assert len(tables) == 1
    assert tables[0].blob is None


# 헤더가 똑같이 되풀이돼도 **사이에 한글 본문이 있으면** 별개 표다.
# 이 테스트가 없으면 규칙이 '헤더 반복'으로 퇴화해도 아무도 모른다 —
# 실측으로 그 규칙은 쪽을 가로지르는 인접 쌍 11개에서 오검출 4건을 냈다
def test_separate_tables_with_repeated_header_not_merged(
    pdf_separate_tables_file: Path, offline_config: IngestConfig
) -> None:
    document = PdfLoader(offline_config).load(pdf_separate_tables_file)
    tables = [e for e in document.elements if e.type is ElementType.TABLE]

    assert len(tables) == 2
    assert all(len(e.metadata["rows"]) == 3 for e in tables)
    assert all(e.metadata.get("page_end") is None for e in tables)


# 쪽 경계가 셀 하나를 가로지르면 두 조각을 이어야 한다.
# 실측(시행령 24~25쪽): `업무정보` + `(4)` 가 갈려 `(4)` 만 남은 행은 몇 번째인지 알 수 없다
def test_split_cell_is_joined(pdf_split_cell_file: Path, offline_config: IngestConfig) -> None:
    document = PdfLoader(offline_config).load(pdf_split_cell_file)
    tables = [e for e in document.elements if e.type is ElementType.TABLE]

    assert len(tables) == 1
    rows = tables[0].metadata["rows"]
    joined = "앞조각\n뒷조각"
    # 두 조각이 덮던 행 **전부** — 앞 쪽 마지막 행과 다음 쪽 병합 셀이 덮던 세 행
    assert [row[1] for row in rows[1:]] == [joined, joined, joined, joined]


# 값이 같아도 **병합이 아니면** 이으면 안 된다.
# 이 테스트가 없으면 판정이 '값 반복'으로 퇴화해도 안 걸린다 — 실측으로 그 규칙은 쪽 경계
# 열 쌍 10개에서 오검출 2건을 냈다(`구분` 열의 `선택`/`선택`, `필수`/`필수`)
def test_repeated_values_are_not_joined(
    pdf_split_cell_file: Path, offline_config: IngestConfig
) -> None:
    document = PdfLoader(offline_config).load(pdf_split_cell_file)
    rows = next(e for e in document.elements if e.type is ElementType.TABLE).metadata["rows"]

    assert [row[2] for row in rows[1:]] == ["필수", "필수", "필수", "선택"]


# 경계 span 을 나르는 사설 키는 병합이 끝나면 사라져야 한다.
# 남으면 공개 스키마(ParsedDocument)로 새어 나간다
def test_boundary_spans_key_is_dropped(
    pdf_split_cell_file: Path, offline_config: IngestConfig
) -> None:
    loader = PdfLoader(offline_config)
    regions = loader.detect_regions(pdf_split_cell_file, LoadContext(offline_config.content))

    assert all(_BOUNDARY_SPANS_KEY not in r.metadata for r in regions)
