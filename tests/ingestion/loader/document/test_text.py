"""텍스트·CSV 로더 테스트 — 한국어 인코딩 자동 감지가 핵심."""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.document.text import TextLoader, decode_text
from rag_pipeline.schemas.document import ElementType, FileFormat


# cp949 로 저장된 한국어가 깨지지 않아야 한다.
# UTF-8로 단정하고 읽으면 한글이 통째로 망가진다
def test_cp949_decoded_correctly(cp949_text_file: Path, offline_config: IngestConfig) -> None:
    document = TextLoader(offline_config, file_format=FileFormat.TEXT).load(cp949_text_file)

    assert document.elements[0].text == "첫 번째 문단입니다."
    assert document.elements[1].text == "두 번째 문단입니다."


# cp949 로 읽었다는 사실을 경고로 남겨야 나중에 추적할 수 있다
def test_encoding_warning(cp949_text_file: Path, offline_config: IngestConfig) -> None:
    document = TextLoader(offline_config, file_format=FileFormat.TEXT).load(cp949_text_file)

    assert any("인코딩" in w for w in document.metadata.warnings)


# 빈 줄 기준으로 문단이 나뉘어야 한다
def test_paragraph_split(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "a.txt"
    path.write_text("첫째\n\n둘째\n\n셋째", encoding="utf-8")

    document = TextLoader(offline_config, file_format=FileFormat.TEXT).load(path)

    assert [e.text for e in document.elements] == ["첫째", "둘째", "셋째"]


# 마크다운 헤딩은 제목 요소로 구분되어야 한다
def test_markdown_heading(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "a.md"
    path.write_text("# 제목입니다\n\n본문입니다.", encoding="utf-8")

    document = TextLoader(offline_config, file_format=FileFormat.MARKDOWN).load(path)

    assert document.elements[0].type is ElementType.TITLE
    assert document.elements[1].type is ElementType.PARAGRAPH


# CSV 는 표 요소 하나로 변환되어야 한다
def test_csv_becomes_table(csv_file: Path, offline_config: IngestConfig) -> None:
    document = TextLoader(offline_config, file_format=FileFormat.CSV).load(csv_file)

    assert len(document.elements) == 1
    assert document.elements[0].type is ElementType.TABLE
    assert "| 이름 | 점수 |" in document.elements[0].text
    assert document.elements[0].metadata["rows"][1] == ["홍길동", "95"]


# 탭 구분 파일도 구분자를 알아서 찾아야 한다
def test_tsv_delimiter_sniffed(tmp_path: Path, offline_config: IngestConfig) -> None:
    path = tmp_path / "a.tsv"
    path.write_text("이름\t점수\n홍길동\t95\n", encoding="utf-8")

    document = TextLoader(offline_config, file_format=FileFormat.CSV).load(path)

    assert document.elements[0].metadata["rows"] == [["이름", "점수"], ["홍길동", "95"]]


# 디코더가 인코딩 이름을 함께 돌려주는지
def test_decode_text_reports_encoding() -> None:
    text, encoding = decode_text("한글".encode("cp949"))

    assert text == "한글"
    assert "949" in encoding or "euc" in encoding.lower()
