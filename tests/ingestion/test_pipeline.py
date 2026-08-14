"""파이프라인 진입점 테스트 — 판별부터 파싱까지 한 번에."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rag_pipeline.config import IngestConfig
from rag_pipeline.exceptions import UnsupportedFormatError
from rag_pipeline.ingestion.pipeline import ingest, peek_format
from rag_pipeline.schemas.document import FileFormat


# 포맷별로 ingest 가 끝까지 동작해야 한다
@pytest.mark.parametrize(
    "fixture_name",
    ["docx_file", "pptx_file", "xlsx_file", "pdf_file", "hwpx_file", "csv_file"],
)
def test_ingest_all_formats(fixture_name: str, request, offline_config: IngestConfig) -> None:
    path = request.getfixturevalue(fixture_name)
    document = ingest(path, config=offline_config)

    assert document.elements
    assert document.metadata.size_bytes > 0
    assert document.metadata.parse_duration_ms >= 0


# 원본 파일명이 메타데이터에 보존되어야 한다 (저장은 UUID로 하므로)
def test_original_name_preserved(docx_file: Path, offline_config: IngestConfig) -> None:
    document = ingest(docx_file, original_name="연간보고서.docx", config=offline_config)

    assert document.metadata.source_name == "연간보고서.docx"


# 확장자를 위조해도 실제 포맷의 로더가 붙어야 한다
def test_spoofed_extension_routed_by_content(
    docx_file: Path, tmp_path: Path, offline_config: IngestConfig
) -> None:
    spoofed = tmp_path / "fake.hwpx"
    shutil.copy(docx_file, spoofed)

    document = ingest(spoofed, config=offline_config)

    assert document.metadata.file_format is FileFormat.DOCX
    assert document.elements[0].text == "연간 보고서"


# 알 수 없는 바이너리는 안내 메시지와 함께 거부되어야 한다
def test_unknown_binary_rejected(unknown_binary_file: Path, offline_config: IngestConfig) -> None:
    with pytest.raises(UnsupportedFormatError) as info:
        ingest(unknown_binary_file, config=offline_config)

    assert "지원하지 않는" in info.value.user_message


# full_text 는 요소 텍스트를 순서대로 이어붙인다
def test_full_text_joins_in_order(docx_file: Path, offline_config: IngestConfig) -> None:
    document = ingest(docx_file, config=offline_config)
    text = document.full_text

    assert text.index("연간 보고서") < text.index("첫 번째 문단입니다.")
    assert text.index("첫 번째 문단입니다.") < text.index("표 다음에 오는 문단입니다.")


# 파싱 없이 포맷만 확인하는 헬퍼
def test_peek_format(pdf_file: Path) -> None:
    assert peek_format(pdf_file) is FileFormat.PDF


# rag_pipeline 은 FastAPI를 몰라야 한다. 이 테스트가 계층 경계를 지키는 장치다.
# 별도 프로세스에서 확인해야 pytest가 미리 로드해둔 모듈에 속지 않는다
def test_pipeline_does_not_import_fastapi() -> None:
    import subprocess
    import sys

    script = (
        "import sys; "
        "import rag_pipeline.ingestion.pipeline; "
        "leaked = [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'starlette')]; "
        "print(','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "", f"rag_pipeline이 웹 계층을 끌어들였습니다: {result.stdout}"
