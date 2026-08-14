"""문서 로더의 공통 뼈대.

각 포맷 로더는 detect_regions() 하나만 구현하면 된다 — 문서를 열어 '어디에 무엇이
있는가'를 ContentRegion 목록으로 알려주는 일이다. 그 리전을 어떤 핸들러로 보낼지,
결과를 어떻게 조립할지는 여기 load()가 공통으로 처리한다.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.content.base import ContentRegion, LoadContext
from rag_pipeline.ingestion.loader.content.router import ContentRouter, route_all
from rag_pipeline.schemas.document import DocumentMetadata, FileFormat, ParsedDocument


# 모든 포맷 로더의 부모. 파싱 흐름과 메타데이터 조립을 공통으로 담당한다
class BaseDocumentLoader(ABC):
    # 이 로더가 담당하는 파일 포맷
    file_format: FileFormat

    def __init__(self, config: IngestConfig | None = None) -> None:
        self.config = config or IngestConfig()
        self.router = ContentRouter(self.config.content)

    # 문서를 열어 콘텐츠 리전 목록을 만든다. 포맷별로 구현한다
    @abstractmethod
    def detect_regions(self, path: Path, ctx: LoadContext) -> list[ContentRegion]: ...

    # 제목·작성자·페이지 수 등 포맷 고유 메타데이터. 필요한 로더만 재정의한다
    def extract_metadata(self, path: Path) -> dict[str, object]:
        return {}

    # 파일 하나를 ParsedDocument로 만든다. 하위 클래스가 재정의할 일은 거의 없다
    def load(self, path: Path, *, original_name: str | None = None) -> ParsedDocument:
        path = Path(path)
        started = time.perf_counter()

        ctx = LoadContext(config=self.config.content)
        regions = self.detect_regions(path, ctx)
        elements = route_all(regions, ctx, self.router)
        extra = self.extract_metadata(path)

        metadata = DocumentMetadata(
            source_name=original_name or path.name,
            file_format=self.file_format,
            size_bytes=path.stat().st_size,
            page_count=extra.get("page_count"),  # type: ignore[arg-type]
            title=extra.get("title"),  # type: ignore[arg-type]
            author=extra.get("author"),  # type: ignore[arg-type]
            ocr_applied=ctx.ocr_applied,
            vision_calls=ctx.vision_calls,
            llamaparse_calls=ctx.llamaparse_calls,
            parse_duration_ms=int((time.perf_counter() - started) * 1000),
            warnings=ctx.warnings,
        )
        return ParsedDocument(metadata=metadata, elements=elements)
