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
from rag_pipeline.ingestion.embedding import Embedder, EmbeddingUsage, get_embedder
from rag_pipeline.ingestion.indexing import (
    VectorStore,
    document_id_for,
    get_store,
    to_records,
)
from rag_pipeline.ingestion.loader.detect import detect_format
from rag_pipeline.ingestion.loader.registry import get_loader
from rag_pipeline.ingestion.splitter import split
from rag_pipeline.schemas.chunk import Chunk
from rag_pipeline.schemas.document import DocumentMetadata, FileFormat, ParsedDocument


# 그림자 색인 한 번의 결과. **`error` 가 있으면 그 문서는 그림자에 구멍이 났다.**
#
# 주 색인과 달리 실패가 예외로 안 올라오므로(index_document() 참고), 무엇이 빠졌는지
# 아는 유일한 길이 이 값이다. 채우는 방법은 **그 문서를 다시 올리는 것**이다 —
# 색인이 delete -> upsert 라 멱등이고 document_id 가 파일 이름에서 파생된다
@dataclass
class ShadowIndexResult:
    collection: str
    indexed_count: int
    usage: EmbeddingUsage
    error: str | None = None


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
    # 그림자 색인을 안 시켰으면 None. **하나이고 목록이 아니다** — 목록으로 열면
    # 합법값이 하나뿐인 축이 또 생긴다(다중 임베더 융합의 `extra_dense` 가 그래서 죽었다).
    # 셋째 모델이 실제로 생기면 그때 연다
    shadow: ShadowIndexResult | None = None


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


# 주입받은 임베더의 `usage` 를 재는 자[尺]. 앱 수명 동안 누적이라 앞뒤를 빼야
# **이 문서 몫**이 나온다. `reset()` 이 아닌 이유는 `index_document()` 주석에 있다
#
# **입력·출력 토큰을 따로 뜬다.** 2026-08-29 에 `total_tokens` 가 파생 프로퍼티가 되면서
# 아래 `_usage_since()` 가 그것을 **생성자 인자로** 넘기다 깨졌다 — 프로퍼티로 바꾸면
# *읽는* 쪽은 한 줄도 안 바뀌지만 **만드는** 쪽은 바뀐다. 그 둘을 같은 것으로 본 것이
# 이 변경에서 유일하게 놓친 자리다(전체 pytest 가 잡았다)
def _usage_snapshot(embedder: Embedder) -> tuple[int, int, int, int]:
    usage = embedder.usage
    return (usage.api_calls, usage.input_tokens, usage.output_tokens, usage.cache_hits)


# 스냅샷 이후로 늘어난 만큼만 담은 새 `EmbeddingUsage`
def _usage_since(before: tuple[int, int, int, int], embedder: Embedder) -> EmbeddingUsage:
    usage = embedder.usage
    return EmbeddingUsage(
        api_calls=usage.api_calls - before[0],
        input_tokens=usage.input_tokens - before[1],
        output_tokens=usage.output_tokens - before[2],
        cache_hits=usage.cache_hits - before[3],
    )


# 파일 하나를 끝까지 색인한다. 파싱 -> 청킹 -> 임베딩 -> 저장
#
# 재색인을 delete -> upsert 로 하는 이유: 문서를 고치면 청크가 사라질 수 있는데
# (A B C D E -> A B D) upsert 만으로는 없어진 C·E 가 저장소에 남는다. 그 둘이 계속 검색된다
def index_document(
    path: str | Path,
    *,
    original_name: str | None = None,
    config: IngestConfig | None = None,
    store: VectorStore | None = None,
    embedder: Embedder | None = None,
    shadow: tuple[VectorStore, Embedder] | None = None,
) -> IndexResult:
    config = config or IngestConfig()

    document = ingest(path, original_name=original_name, config=config)

    # 문서 키는 파싱 내용이 아니라 **파일 이름**에서 뽑는다. 내용 해시로 잡으면 수정본이
    # 새 키를 갖게 되어 아래 delete 가 옛 청크를 못 찾는다 (indexing/records.py)
    document_id = document_id_for(document.metadata.source_name)
    chunks = split(document, config.splitter, document_key=document_id)

    # **임베더도 주입받는다 — `store` 와 같은 이유는 아니다.** 저쪽은 배타 잠금이고
    # 이쪽은 **모델 적재**다. `get_embedder()` 는 메모이즈하지 않아 부를 때마다 새
    # `LocalEmbedder` 가 되는데, 로컬 갈래는 임베딩 캐시가 빗나갈 때 비로소 가중치를
    # 올리므로 **새 문서 하나마다 KURE-v1 2.27GB 를 처음부터 적재한다.**
    # 실측(2026-08-26): 25청크 첫 임베딩 23.629초 vs 적재 후 같은 크기 0.164초.
    # 이미 올린 문서를 다시 색인할 때는 전량 캐시 히트라 모델이 안 올라와 **이 비용이
    # 안 보인다** — 그래서 재색인만 재면 멀쩡해 보이고 새 문서만 느리다.
    #
    # 안 넘기면 `config.embedding` 을 그대로 쓰는 것은 `store` 와 같은 계약이다.
    # **닫지는 않는다** — 임베더에 `close()` 가 없고, 그래서 소유권 규칙도 안 딸려온다
    owns_embedder = embedder is None
    if embedder is None:
        embedder = get_embedder(config.embedding)

    # 주입받은 임베더는 앱 수명 동안 살아 여러 문서를 거치므로 `usage` 가 **누적**이다.
    # `IndexResult.usage` 는 *이 문서의* 비용이라 앞뒤를 빼서 이번 몫만 담는다.
    # **`usage.reset()` 을 부르면 안 된다** — 같은 인스턴스를 질의 쪽도 쓰고
    # `evaluation/run_eval.py` 는 루프를 다 돈 뒤 `total_tokens` 를 읽으므로
    # (`retrieval/dense.py` docstring), 여기서 0으로 만들면 **그 회계가 조용히 사라진다**
    before = _usage_snapshot(embedder)
    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    usage = embedder.usage if owns_embedder else _usage_since(before, embedder)
    records = to_records(chunks, vectors, document_id=document_id)

    # **소유권 규칙: 연 쪽이 닫는다.** 인자로 받은 store 는 호출자(backend 의 startup
    # 싱글턴)가 앱 수명 동안 들고 있으므로 여기서 닫으면 다음 요청이 죽는다. 반대로 우리가
    # 연 것은 반드시 닫아야 한다 — evaluation/run_eval.py 가 색인 루프 **직후** 자기
    # get_store() 를 열어 검색하는데, 안 닫으면 거기서 배타 잠금으로 죽는다.
    # store 를 안 넘겼을 때 config.indexing 을 그대로 쓰는 것도 계약이다 — 평가가 실행마다
    # qdrant_path 를 갈라 주는데 기본 경로로 떨어지면 실행들이 한 디렉터리를 물어
    # 기준선이 에러 없이 수치로만 오염된다
    owns_store = store is None
    if store is None:
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
        # 실패하므로 예외로 빠져나갈 때도 반드시 푼다. **우리가 연 것만** 닫는다
        if owns_store:
            store.close()

    return IndexResult(
        document_id=document_id,
        collection=collection,
        chunk_count=len(chunks),
        indexed_count=indexed,
        usage=usage,
        metadata=document.metadata,
        shadow=_index_shadow(shadow, chunks, document_id),
    )


# 같은 청크를 두 번째 임베더로 한 벌 더 색인한다. **비교 데이터를 쌓아두는 것이 목적**이고
# (`plan_log/embedding.md` 5회차), 지금은 갈아타기 전 기본값이던 OpenAI 가 그 자리다.
#
# ## 왜 여기이고 호출부가 아닌가
#
# 파싱·청킹이 **한 번만** 돌아야 한다. backend 가 `index_document()` 를 두 번 부르는 안은
# 파싱 캐시에 기대게 되고, 더 나쁘게는 *"두 컬렉션이 같은 문서 집합"* 이라는 성질이
# 호출부로 새어 나간다. 같은 `chunks` 를 쓰는 것이 그 성질을 구조로 만든다.
#
# ## 왜 실패를 삼키나 — 이 저장소에서 드문 쪽이다
#
# embedding·indexing 은 실패를 **예외로** 올린다. 그 규약의 근거가 *"내려갈 폴백 층이
# 없다"* 인데, **여기는 주 컬렉션이 이미 성공한 뒤라 층이 실재한다.** 그림자가 죽었다고
# 업로드를 통째로 막으면 OpenAI 장애가 서비스를 멈춘다 — 주 모델은 멀쩡한데도.
#
# 대신 **구멍이 드러나야 한다.** 삼킨 이유를 `ShadowIndexResult.error` 에 담아 올리고,
# backend 가 그것을 화면 경고와 목록 표시로 낸다. 조용히 없어지는 것과 갈리는 자리다
def _index_shadow(
    shadow: tuple[VectorStore, Embedder] | None,
    chunks: list[Chunk],
    document_id: str,
) -> ShadowIndexResult | None:
    if shadow is None:
        return None

    shadow_store, shadow_embedder = shadow
    try:
        vectors = shadow_embedder.embed_documents([chunk.text for chunk in chunks])
        records = to_records(chunks, vectors, document_id=document_id)
        if records:
            shadow_store.ensure_collection(len(records[0].vector))
        shadow_store.delete_by_document_id(document_id)
        indexed = shadow_store.upsert(records)
    except Exception as exc:  # noqa: BLE001 - 주 색인이 이미 성공한 폴백 자리다 (위 docstring)
        return ShadowIndexResult(
            collection=shadow_store.collection,
            indexed_count=0,
            usage=shadow_embedder.usage,
            error=f"{type(exc).__name__}: {exc}",
        )

    return ShadowIndexResult(
        collection=shadow_store.collection,
        indexed_count=indexed,
        usage=shadow_embedder.usage,
    )


# 파싱하지 않고 포맷만 확인하고 싶을 때 쓰는 헬퍼
def peek_format(path: str | Path, *, original_name: str | None = None) -> FileFormat:
    return detect_format(Path(path), original_name=original_name)
