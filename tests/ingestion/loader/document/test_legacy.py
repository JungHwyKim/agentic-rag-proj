"""구형 포맷(.hwp/.doc/.ppt) 거부 동작 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.config import IngestConfig
from rag_pipeline.exceptions import UnsupportedFormatError
from rag_pipeline.ingestion.loader.document.hwp import UnsupportedLegacyLoader
from rag_pipeline.ingestion.loader.registry import get_loader
from rag_pipeline.schemas.document import FileFormat


# .hwp 는 파싱하지 않고, HWPX 로 변환하라는 안내와 함께 거부해야 한다
def test_hwp_rejected_with_conversion_guidance(
    tmp_path: Path, offline_config: IngestConfig
) -> None:
    loader = UnsupportedLegacyLoader(offline_config, file_format=FileFormat.HWP)

    with pytest.raises(UnsupportedFormatError) as info:
        loader.load(tmp_path / "any.hwp")

    message = info.value.user_message
    assert "HWPX" in message
    assert "다른 이름으로 저장" in message
    assert info.value.detected == "hwp"


# .doc / .ppt 도 각자 맞는 안내를 줘야 한다
@pytest.mark.parametrize(
    ("file_format", "expected"),
    [(FileFormat.DOC, "docx"), (FileFormat.PPT, "pptx")],
)
def test_legacy_office_guidance(
    file_format: FileFormat, expected: str, tmp_path: Path, offline_config: IngestConfig
) -> None:
    loader = UnsupportedLegacyLoader(offline_config, file_format=file_format)

    with pytest.raises(UnsupportedFormatError) as info:
        loader.load(tmp_path / "any")

    assert expected in info.value.user_message


# 레지스트리가 구형 포맷을 거부 로더로 연결해야 한다
def test_registry_maps_legacy_formats(offline_config: IngestConfig) -> None:
    for file_format in (FileFormat.HWP, FileFormat.DOC, FileFormat.PPT):
        loader = get_loader(file_format, offline_config)
        assert isinstance(loader, UnsupportedLegacyLoader)


# 알 수 없는 포맷은 레지스트리 단계에서 막혀야 한다
def test_unknown_format_rejected(offline_config: IngestConfig) -> None:
    with pytest.raises(UnsupportedFormatError):
        get_loader(FileFormat.UNKNOWN, offline_config)


# 추후 변환 기능이 들어갈 자리는 아직 비어 있다
def test_conversion_hook_not_implemented_yet(tmp_path: Path, offline_config: IngestConfig) -> None:
    loader = UnsupportedLegacyLoader(offline_config, file_format=FileFormat.HWP)

    assert loader.convert_to_hwpx(tmp_path / "any.hwp") is None
