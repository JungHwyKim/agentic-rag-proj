"""파일 포맷 -> 문서 로더 매핑 (1차 라우팅의 분기표)."""

from __future__ import annotations

from rag_pipeline.config import IngestConfig
from rag_pipeline.exceptions import UnsupportedFormatError
from rag_pipeline.ingestion.loader.document.base import BaseDocumentLoader
from rag_pipeline.ingestion.loader.document.docx import DocxLoader
from rag_pipeline.ingestion.loader.document.excel import ExcelLoader
from rag_pipeline.ingestion.loader.document.hwp import UnsupportedLegacyLoader
from rag_pipeline.ingestion.loader.document.hwpx import HwpxLoader
from rag_pipeline.ingestion.loader.document.image import ImageLoader
from rag_pipeline.ingestion.loader.document.pdf import PdfLoader
from rag_pipeline.ingestion.loader.document.pptx import PptxLoader
from rag_pipeline.ingestion.loader.document.text import TextLoader
from rag_pipeline.schemas.document import FileFormat

# 감지된 포맷에 대응하는 로더를 만드는 팩토리 표.
# TextLoader/ExcelLoader/UnsupportedLegacyLoader 는 여러 포맷을 겸하므로 인자로 구분한다
_FACTORIES = {
    FileFormat.PDF: lambda cfg: PdfLoader(cfg),
    FileFormat.DOCX: lambda cfg: DocxLoader(cfg),
    FileFormat.PPTX: lambda cfg: PptxLoader(cfg),
    FileFormat.XLSX: lambda cfg: ExcelLoader(cfg, file_format=FileFormat.XLSX),
    FileFormat.XLS: lambda cfg: ExcelLoader(cfg, file_format=FileFormat.XLS),
    FileFormat.HWPX: lambda cfg: HwpxLoader(cfg),
    FileFormat.IMAGE: lambda cfg: ImageLoader(cfg),
    FileFormat.TEXT: lambda cfg: TextLoader(cfg, file_format=FileFormat.TEXT),
    FileFormat.MARKDOWN: lambda cfg: TextLoader(cfg, file_format=FileFormat.MARKDOWN),
    FileFormat.CSV: lambda cfg: TextLoader(cfg, file_format=FileFormat.CSV),
    # 감지는 되지만 파싱은 못 하는 구형 포맷. 안내 메시지와 함께 415로 거부된다
    FileFormat.HWP: lambda cfg: UnsupportedLegacyLoader(cfg, file_format=FileFormat.HWP),
    FileFormat.DOC: lambda cfg: UnsupportedLegacyLoader(cfg, file_format=FileFormat.DOC),
    FileFormat.PPT: lambda cfg: UnsupportedLegacyLoader(cfg, file_format=FileFormat.PPT),
}

# 업로드를 허용하는 확장자 화이트리스트. 매직바이트 판별과 교차 검증한다
SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".xls",
        ".hwpx",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
        ".gif",
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        # 거부 안내를 정확히 주기 위해 업로드 자체는 받는다
        ".hwp",
        ".doc",
        ".ppt",
    }
)


# 감지된 포맷에 맞는 문서 로더를 돌려준다
def get_loader(file_format: FileFormat, config: IngestConfig | None = None) -> BaseDocumentLoader:
    factory = _FACTORIES.get(file_format)
    if factory is None:
        raise UnsupportedFormatError(
            f"no loader for format: {file_format.value}",
            user_message=(
                "지원하지 않는 파일 형식입니다. "
                "PDF, Word(.docx), PowerPoint(.pptx), Excel(.xlsx/.xls), 한글(.hwpx), "
                "이미지, 텍스트 파일을 업로드해 주세요."
            ),
            detected=file_format.value,
        )
    return factory(config or IngestConfig())
