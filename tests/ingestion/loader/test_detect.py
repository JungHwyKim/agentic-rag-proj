"""포맷 판별 테스트 — 확장자를 위조해도 실제 포맷을 맞춰야 한다."""

from __future__ import annotations

import shutil
from pathlib import Path

from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.schemas.document import FileFormat


# 각 포맷이 매직바이트로 올바르게 판별되는지
def test_detects_each_format(
    pdf_file: Path,
    docx_file: Path,
    pptx_file: Path,
    xlsx_file: Path,
    hwpx_file: Path,
    korean_image_file: Path,
) -> None:
    assert detect_format(pdf_file) is FileFormat.PDF
    assert detect_format(docx_file) is FileFormat.DOCX
    assert detect_format(pptx_file) is FileFormat.PPTX
    assert detect_format(xlsx_file) is FileFormat.XLSX
    assert detect_format(hwpx_file) is FileFormat.HWPX
    assert detect_format(korean_image_file) is FileFormat.IMAGE


# 확장자를 .hwpx 로 위조한 docx 를 docx 로 잡아내야 한다.
# 이게 안 되면 hwpx 로더가 docx 를 열다가 엉뚱한 에러를 낸다
def test_extension_spoofing_is_ignored(docx_file: Path, tmp_path: Path) -> None:
    spoofed = tmp_path / "disguised.hwpx"
    shutil.copy(docx_file, spoofed)

    assert detect_format(spoofed) is FileFormat.DOCX


# 반대 방향도 확인: hwpx 를 .docx 이름으로 바꿔도 hwpx 로 잡아야 한다
def test_hwpx_disguised_as_docx(hwpx_file: Path, tmp_path: Path) -> None:
    spoofed = tmp_path / "disguised.docx"
    shutil.copy(hwpx_file, spoofed)

    assert detect_format(spoofed) is FileFormat.HWPX


# 텍스트 계열은 내용이 같으므로 확장자로 갈린다
def test_text_formats_use_extension(tmp_path: Path) -> None:
    body = "제목\n\n본문입니다."

    txt = tmp_path / "a.txt"
    txt.write_text(body, encoding="utf-8")
    md = tmp_path / "a.md"
    md.write_text(body, encoding="utf-8")
    csv = tmp_path / "a.csv"
    csv.write_text("가,나\n1,2", encoding="utf-8")

    assert detect_format(txt) is FileFormat.TEXT
    assert detect_format(md) is FileFormat.MARKDOWN
    assert detect_format(csv) is FileFormat.CSV


# cp949 로 저장된 한국어 텍스트도 텍스트로 인식해야 한다
def test_cp949_text_is_detected(cp949_text_file: Path) -> None:
    assert detect_format(cp949_text_file) is FileFormat.TEXT


# 알 수 없는 바이너리는 UNKNOWN
def test_unknown_binary(unknown_binary_file: Path) -> None:
    assert detect_format(unknown_binary_file) is FileFormat.UNKNOWN


# 빈 파일과 없는 파일은 UNKNOWN
def test_empty_and_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")

    assert detect_format(empty) is FileFormat.UNKNOWN
    assert detect_format(tmp_path / "nope.pdf") is FileFormat.UNKNOWN
