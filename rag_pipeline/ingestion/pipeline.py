"""적재 파이프라인의 공개 진입점.

HTTP를 전혀 모르기 때문에 FastAPI 없이 스크립트나 노트북에서도 그대로 쓸 수 있다.
이후 cleaner/splitter/embedding/indexing 이 붙어도 backend가 부르는 함수는 그대로다.
"""

from __future__ import annotations

from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.ingestion.loader.registry import get_loader
from rag_pipeline.schemas.document import FileFormat, ParsedDocument


# 파일 하나를 읽어 ParsedDocument로 만든다. 포맷 판별 -> 로더 라우팅 -> 파싱
def ingest(
    path: str | Path,
    *,
    original_name: str | None = None,
    config: IngestConfig | None = None,
) -> ParsedDocument:
    path = Path(path)
    config = config or IngestConfig()

    file_format = detect_format(path, original_name=original_name)
    loader = get_loader(file_format, config)
    document = loader.load(path, original_name=original_name)

    # 다음 단계에서 여기에 추가된다:
    #   document = clean(document, config.cleaner)
    #   chunks   = split(document, config.splitter)
    #   vectors  = embed(chunks, config.embedding)
    #   index(vectors, config.indexing)
    return document


# 파싱하지 않고 포맷만 확인하고 싶을 때 쓰는 헬퍼
def peek_format(path: str | Path, *, original_name: str | None = None) -> FileFormat:
    return detect_format(Path(path), original_name=original_name)
