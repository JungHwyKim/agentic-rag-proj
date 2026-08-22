"""한글(.hwpx) 로더 테스트.

주의: fixture는 코드로 만든 합성 HWPX다. 실제 한컴 산출물과 구조가 다를 수 있으므로,
실제 파일을 확보하면 tests/fixtures/ 에 넣고 이 테스트를 다시 검증해야 한다.
"""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.content.base import ContentType, LoadContext
from rag_pipeline.ingestion.loader.document.hwpx import HwpxLoader
from rag_pipeline.schemas.document import ElementType


# 문단과 표가 순서대로 추출되어야 한다
def test_paragraphs_and_table_in_order(hwpx_file: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)
    types = [e.type for e in document.elements]

    assert types == [ElementType.PARAGRAPH, ElementType.PARAGRAPH, ElementType.TABLE]


# 병합 셀이 있어도 열이 밀리지 않아야 한다.
# **되돌리면 값이 왼쪽으로 밀려 열 이름과 값이 틀리게 짝지어진다** — 실측으로 시행령 안내서
# HWPX 표 173행 중 127행이 그 상태였고, 실행항목이 단계 열에 들어가 있었다.
# 세로 병합은 값을 되풀이하고(행이 다른 청크로 갈 수 있다) 가로 병합은 비운다(같은 행에 남는다)
def test_merged_cells_keep_column_alignment(
    hwpx_merged_cells: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_merged_cells)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert table.metadata["rows"] == [
        ["구분", "항목", "담당"],
        ["구분", "가", "김"],
        ["구분", "비고 전체", ""],
    ]


# 한국어 텍스트가 깨지지 않아야 한다
def test_korean_text(hwpx_file: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)

    assert document.elements[0].text == "첫 번째 문단입니다."
    assert document.elements[1].text == "두 번째 문단입니다."


# 표 셀 구조가 복원되어야 한다
def test_table_rows(hwpx_file: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)
    table = document.elements[2]

    assert table.metadata["rows"] == [["이름", "점수"], ["홍길동", "95"]]
    assert "| 홍길동 | 95 |" in table.text


# 표 안의 텍스트가 본문 문단으로 중복 추출되면 안 된다.
# 표 셀 안에도 hp:p 가 들어있어서, 트리를 무심코 훑으면 같은 내용이 두 번 나온다
def test_table_text_not_duplicated_as_paragraph(
    hwpx_file: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)
    paragraphs = [e.text for e in document.elements if e.type is ElementType.PARAGRAPH]

    assert "홍길동" not in " ".join(paragraphs)
    assert "이름" not in " ".join(paragraphs)


# 표 셀 안에 든 그림이 누락되면 안 된다.
# _walk 는 표를 만나면 구조만 뽑고 바로 리턴하기 때문에, 셀 안 그림을 따로 챙기지 않으면
# 통째로 사라진다 (실측: 학교기록물 지침 문서의 그림 20개 중 18개가 표 안에 있었다)
def test_image_inside_table_cell_is_extracted(
    hwpx_with_image_in_table: Path, offline_config: IngestConfig
) -> None:
    loader = HwpxLoader(offline_config)
    ctx = LoadContext(config=offline_config.content)
    regions = loader.detect_regions(hwpx_with_image_in_table, ctx)

    images = [r for r in regions if r.content_type is ContentType.IMAGE]
    tables = [r for r in regions if r.content_type is ContentType.TABLE]

    assert len(tables) == 1
    assert len(images) == 1
    assert images[0].blob
    assert images[0].metadata["binary_item"] == "image1"


# 배치 정보(linesegarray)가 없는 문서는 페이지를 지어내지 않고 None 으로 둔다
def test_page_is_none_without_layout_info(hwpx_file: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)

    assert document.metadata.page_count is None
    assert all(e.page is None for e in document.elements)


# 쪽을 넘기는 표가 먹은 쪽 수까지 세어야 이후 쪽 번호가 밀리지 않는다.
# vertpos 는 '쪽이 넘어갔다'는 사건만 알려줄 뿐 몇 쪽인지는 못 알려주기 때문에,
# 표 높이를 안 재면 2쪽짜리 표를 1쪽으로 세고 뒤따르는 내용이 전부 한 쪽씩 당겨진다
def test_table_spanning_pages_counts_both(hwpx_paged: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_paged)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)
    last = document.elements[-1]

    assert table.page == 1
    # 표는 1쪽에서 시작해 2쪽까지 이어지므로 뒤 문단은 3쪽이다
    assert last.text == "표 뒤 문단입니다."
    assert last.page == 3
    assert document.metadata.page_count == 3


# 여러 쪽에 걸친 표는 끝 쪽도 남겨야 인용할 때 뒷부분이 엉뚱한 쪽을 가리키지 않는다
def test_spanning_table_records_page_end(hwpx_paged: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_paged)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert table.metadata["page_end"] == 2


# 한 쪽에 들어가는 표는 쪽 경계를 만들면 안 된다.
# 표 높이로 '표가 끝난 위치'를 계산해 다음 문단과 비교하면, 떠 있는 표에서
# 같은 쪽을 새 쪽으로 오인한다. 쪽을 실제로 넘긴 표에서만 쪽을 더해야 한다
def test_fitting_table_does_not_break_page(
    hwpx_floating_table: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_floating_table)

    assert document.metadata.page_count == 1
    assert all(e.page == 1 for e in document.elements)


# 한 쪽에 들어가는 표에는 page_end 를 달지 않는다 (없는 범위를 지어내지 않는다)
def test_non_spanning_table_has_no_page_end(
    hwpx_file: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert "page_end" not in table.metadata


# 한글이 제목·인용을 그리려고 쓴 껍데기 표는 본문 문단으로 내려보내야 한다.
# 표로 두면 "Ⅰ. 개 요" 같은 제목이 마크다운 표 문법 안에 갇혀 검색·임베딩이 나빠진다
def test_layout_boxes_demoted_to_paragraph(
    hwpx_with_layout_boxes: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_with_layout_boxes)
    types = [e.type for e in document.elements]

    # 1칸 상자 / 붙임 머리표 / 인용 상자 3개는 문단, 진짜 데이터 표 1개만 표로 남는다
    assert types == [
        ElementType.PARAGRAPH,
        ElementType.PARAGRAPH,
        ElementType.PARAGRAPH,
        ElementType.TABLE,
    ]
    assert document.elements[0].text == "Ⅰ. 개 요"
    assert document.elements[1].text == "붙임1 관련 법률·시행령"
    assert "|" not in document.elements[0].text


# 강등된 문단은 원래 표였다는 표시를 남긴다. cleaner 가 제목으로 승격할지 판단할 근거다
def test_demoted_box_marked_in_metadata(
    hwpx_with_layout_boxes: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_with_layout_boxes)

    assert document.elements[0].metadata["layout_table"] is True
    assert "layout_table" not in document.elements[-1].metadata


# 가로 쪽은 세로 여유를 width 에서 얻어야 한다.
# 한컴은 가로 쪽에서도 width/height 에 물리 용지(세로) 치수를 그대로 넣고 방향은
# landscape 속성으로만 표시한다. height 를 그냥 쓰면 본문 높이를 53% 과대평가해
# 쪽을 넘긴 표를 한 쪽으로 세고 이후 쪽 번호가 전부 밀린다.
# 같은 표(높이 100000)가 세로에서는 2쪽, 가로에서는 3쪽을 먹는다
def test_landscape_section_uses_width_as_height(
    hwpx_paged_landscape: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_paged_landscape)
    table = next(e for e in document.elements if e.type is ElementType.TABLE)

    assert table.page == 1
    # 세로였다면 page_end 2 / page_count 3 이다
    assert table.metadata["page_end"] == 3
    assert document.metadata.page_count == 4


# 쪽을 넘긴 표 안의 그림도 끝 쪽을 남겨야 한다.
# 그림은 몇 번째 행에 있는지 역산하지 않아 시작 쪽 번호를 받는데, 범위가 없으면
# 3쪽에 있는 그림을 1쪽이라고 단언하게 된다. 틀린 쪽 대신 범위를 남긴다.
# load() 대신 detect_regions() 를 쓰는 이유: offline_config 는 image_handler="ocr" 라
# load() 를 타면 Tesseract 설치 여부에 테스트가 묶인다
def test_picture_in_spanning_table_gets_page_range(
    hwpx_paged_with_image: Path, offline_config: IngestConfig
) -> None:
    loader = HwpxLoader(offline_config)
    ctx = LoadContext(config=offline_config.content)
    regions = loader.detect_regions(hwpx_paged_with_image, ctx)

    picture = next(r for r in regions if r.content_type is ContentType.IMAGE)
    table = next(r for r in regions if r.content_type is ContentType.TABLE)

    # 그림은 실제로 2쪽 행에 있지만 시작 쪽을 받는다 — 대신 page_end 로 범위를 준다
    assert picture.page == 1
    assert picture.metadata["page_end"] == 2
    assert table.metadata["page_end"] == 2


# 그림이 든 상자는 캡션-그림 배치라서 강등하면 안 된다.
# 강등해버리면 캡션 텍스트와 그림의 관계가 끊긴다
def test_box_with_image_stays_a_table(
    hwpx_with_image_in_table: Path, offline_config: IngestConfig
) -> None:
    loader = HwpxLoader(offline_config)
    ctx = LoadContext(config=offline_config.content)
    regions = loader.detect_regions(hwpx_with_image_in_table, ctx)

    # 1행 2열 + 내용 칸 1개라 크기만 보면 강등 대상이지만 그림 때문에 표로 남는다
    assert [r.content_type for r in regions if r.content_type is ContentType.TABLE]


# 본문 섹션이 없는 zip 은 경고를 남기고 빈 결과를 돌려줘야 한다 (예외로 죽지 않는다)
def test_missing_section_warns(tmp_path: Path, offline_config: IngestConfig) -> None:
    import zipfile

    path = tmp_path / "broken.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/content.hpf", "<package/>")

    document = HwpxLoader(offline_config).load(path)

    assert document.elements == []
    assert any("섹션" in w for w in document.metadata.warnings)


# run 의 charPrIDRef -> header.xml 의 charPr height 를 이어야 제목 승격이 돈다.
# HWPX 는 스타일에 기댈 수 없다 — 실측으로 스타일이 'Normal' 하나뿐인 문서가 있다
def test_larger_paragraph_becomes_title(
    hwpx_with_heading: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_with_heading)

    titles = [e for e in document.elements if e.type is ElementType.TITLE]
    assert [e.text for e in titles] == ["□ 개요"]
    assert titles[0].metadata["heading_level"] == 1


# 큰 글씨가 본문 크기와 섞인 문단은 제목이 아니라 강조다
def test_mixed_size_paragraph_stays_paragraph(
    hwpx_with_heading: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_with_heading)

    emphasised = [e for e in document.elements if "홍길동" in e.text]
    assert emphasised
    assert all(e.type is ElementType.PARAGRAPH for e in emphasised)


# 판정에만 쓰고 버린다
def test_font_sizes_do_not_leak(
    hwpx_with_heading: Path, offline_config: IngestConfig
) -> None:
    document = HwpxLoader(offline_config).load(hwpx_with_heading)

    assert all("font_sizes" not in e.metadata for e in document.elements)


# header.xml 이 없는 문서(직접 만든 XML 등)에서도 죽지 않고 승격만 꺼진다
def test_missing_header_xml_is_safe(hwpx_file: Path, offline_config: IngestConfig) -> None:
    document = HwpxLoader(offline_config).load(hwpx_file)

    assert document.elements
    assert all(e.type is not ElementType.TITLE for e in document.elements)
