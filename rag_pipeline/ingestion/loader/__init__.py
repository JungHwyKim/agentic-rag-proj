"""파일을 읽어 ParsedDocument로 만드는 계층. 파일 포맷 -> 콘텐츠 유형 2단 라우팅."""

from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.ingestion.loader.registry import get_loader

__all__ = ["detect_format", "get_loader"]
