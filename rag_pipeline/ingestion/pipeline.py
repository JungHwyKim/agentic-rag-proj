"""적재 파이프라인의 공개 진입점.

HTTP를 전혀 모르기 때문에 FastAPI 없이 스크립트나 노트북에서도 그대로 쓸 수 있다.

진입점이 둘인 것은 나눠야 해서지 안 합쳐서가 아니다.

- `ingest()` — 파일 하나를 `ParsedDocument` 로 만든다. **반환 타입이 곧 backend 의
  `response_model`** 이라 여기에 청크·벡터를 얹는 것은 파이프라인 결정이 아니라 API 결정이다
- `index_document()` — 그 위에 split → embed → index 를 잇는다. 벡터를 만들고 저장하는
  전체 흐름은 이쪽이다

**둘을 합치지 않는 데는 두 번째 이유가 있고 그쪽이 더 중요하다.** 기본 `pytest` 가
`ingest()` 만 부르므로 임베딩이 호출되지 않고, 그래서 "키가 없으면 예외"라는 embedding 의
설계가 기본 테스트를 깨지 않는다. 합치는 순간 그 지탱이 무너진다 —
근거는 plan_log/embedding.md 와 plan_log/indexing.md 의 "합성 지점" 절.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rag_pipeline.config import IngestConfig
from rag_pipeline.ingestion.cleaner import clean
from rag_pipeline.ingestion.embedding import EmbeddingUsage, get_embedder
from rag_pipeline.ingestion.indexing import document_id_for, get_store, to_records
from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.ingestion.loader.registry import get_loader
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.schemas.document import DocumentMetadata, FileFormat, ParsedDocument


# 색인 한 번의 결과. 무엇을 몇 개 어디에 넣었고 얼마를 썼는지
@dataclass
class IndexResult:
    document_id: str
    collection: str
    chunk_count: int
    indexed_count: int
    usage: EmbeddingUsage
    # 그 색인이 **어떤 적재 위에서 나왔나.** `vision_calls`·`ocr_applied`·`warnings` 가
    # 여기 있다. 없으면 검색 수치가 달라졌을 때 *검색이 변한 것인지 적재가 변한 것인지*
    # 가릴 방법이 없다 — Vision 은 LLM 이라 응답이 실행마다 다르다(plan_log/표-경로.md 12회차).
    # 호출부가 `ingest()` 를 한 번 더 불러 얻는 안은 기각했다: 그 두 번째 파싱이 실제로
    # 색인된 것과 다를 수 있어, 다른 파싱의 카운터는 애초에 쓸모가 없다
    metadata: DocumentMetadata


# 파일 하나를 읽어 ParsedDocument로 만든다. 포맷 판별 -> 로더 라우팅 -> 파싱 -> 정리
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
    document = clean(document, config.cleaner)

    # split -> embed -> index 는 **일부러 여기 없다.** 이 함수의 반환이 곧 공개 응답
    # 스키마라서다. 그 흐름은 아래 index_document() 에 있다 (모듈 docstring 참고)
    return document


# 파일 하나를 끝까지 색인한다. 파싱 -> 청킹 -> 임베딩 -> 저장
#
# 재색인을 delete -> upsert 로 하는 이유: 문서를 고치면 청크가 사라질 수 있는데
# (A B C D E -> A B D) upsert 만으로는 없어진 C·E 가 저장소에 남는다. 그 둘이 계속 검색된다
def index_document(
    path: str | Path,
    *,
    original_name: str | None = None,
    config: IngestConfig | None = None,
) -> IndexResult:
    config = config or IngestConfig()

    document = ingest(path, original_name=original_name, config=config)

    # 문서 키는 파싱 내용이 아니라 **파일 이름**에서 뽑는다. 내용 해시로 잡으면 수정본이
    # 새 키를 갖게 되어 아래 delete 가 옛 청크를 못 찾는다 (indexing/records.py)
    document_id = document_id_for(document.metadata.source_name)
    chunks = split(document, config.splitter, document_key=document_id)

    embedder = get_embedder(config.embedding)
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    records = to_records(chunks, vectors, document_id=document_id)

    store = get_store(config.indexing, config.embedding)
    try:
        # 레코드가 0개여도 delete 는 부른다. 있던 문서가 빈 문서로 바뀌었을 때
        # 건너뛰면 옛 청크가 통째로 남는다. 컬렉션이 없으면 delete 는 조용히 넘어간다
        if records:
            store.ensure_collection(len(records[0].vector))
        store.delete_by_document_id(document_id)
        indexed = store.upsert(records)
        collection = store.collection
    finally:
        # 로컬 파일 모드는 저장 디렉터리에 배타 잠금을 건다. 안 닫으면 다음 색인이 통째로
        # 실패하므로 예외로 빠져나갈 때도 반드시 푼다
        store.close()

    return IndexResult(
        document_id=document_id,
        collection=collection,
        chunk_count=len(chunks),
        indexed_count=indexed,
        usage=embedder.usage,
        metadata=document.metadata,
    )


# 파싱하지 않고 포맷만 확인하고 싶을 때 쓰는 헬퍼
def peek_format(path: str | Path, *, original_name: str | None = None) -> FileFormat:
    return detect_format(Path(path), original_name=original_name)
