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
