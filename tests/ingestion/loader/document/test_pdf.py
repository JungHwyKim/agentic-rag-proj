"""PDF 로더 테스트."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pymupdf

from rag_pipeline.config import ContentRoutingConfig, IngestConfig
from rag_pipeline.ingestion.loader.content import table_llamaparse
from rag_pipeline.ingestion.loader.document.pdf import (
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
                ([(0, 0, 100, 10), (100, 0, 200, 10), (200, 0, 300, 10)], ["준비", "B", "C"]),
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
