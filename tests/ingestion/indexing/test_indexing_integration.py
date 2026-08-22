"""실제 OpenAI 임베딩 벡터를 qdrant 에 넣어 **가짜가 감춘 전제**를 확인한다.

**이 파일은 실제 과금을 일으킨다.** `@pytest.mark.integration` 이 달려 있어 기본 실행에서
빠지고(`pyproject.toml` 의 `addopts`), `.claude/hooks/paid_test_check.py` 가 `-m integration`
자체를 막는다. 사용자가 `ALLOW_PAID_TESTS=1` 을 **자기 환경에** 설정해야 돈다.

## 왜 필요한가

나머지 색인 테스트는 4~8차원 가짜 벡터를 쓴다. 그래서 **실제 1536차원 벡터로도 같은지는
검증되지 않는다.** 여기서 확인하는 전제는 셋이다.

1. **1536차원 컬렉션이 만들어지고 그 크기로 잠긴다.** 가짜 차원에서는 안 드러난다
2. **Dot 거리가 실제로 코사인과 같은 순위를 준다.** 그 근거였던 "벡터가 단위 길이"가
   저장·검색을 한 바퀴 돈 뒤에도 성립하는지 — score 가 1 근처여야 한다
3. **한국어 질의가 자기 문장을 1등으로 찾는다.** 가짜 벡터는 해시라 의미를 안 담아
   "검색이 말이 되는가"를 아예 못 본다

## 원소별 비교를 쓰지 않는다

임베딩은 호출 간 비트 재현성이 없다(embedding 쪽 통합 테스트에서 실제로 겪었다).
판정은 전부 **순위와 코사인**으로 한다.

## 비용

짧은 한국어 문장 6개만 보낸다. 실파일을 태우지 않는다 — 여기서 볼 것은 차원과 순위뿐이다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.ingestion.embedding import get_embedder
from rag_pipeline.ingestion.indexing import get_store, to_records
from rag_pipeline.schemas.chunk import Chunk, ChunkType

pytestmark = pytest.mark.integration

# 서로 다른 주제의 짧은 문장들. 질의가 엉뚱한 것을 집으면 바로 보인다
SENTENCES = [
    "연차휴가는 1년간 80퍼센트 이상 출근한 근로자에게 15일이 주어진다.",
    "육아휴직은 만 8세 이하 자녀를 둔 근로자가 신청할 수 있다.",
    "개인정보 파기는 보유기간이 끝난 날로부터 5일 이내에 해야 한다.",
    "정보시스템 감리는 사업비 규모에 따라 의무 대상이 정해진다.",
    "회의록은 작성일로부터 3년간 보존하고 그 뒤 폐기한다.",
    "출장비 정산은 복귀일로부터 7일 이내에 신청해야 한다.",
]

# 첫 문장을 겨냥한 질의. 같은 낱말을 그대로 쓰지 않아야 의미 검색인지 보인다
QUERY = "연차 휴가는 며칠이나 쓸 수 있나요?"


# 실제 키를 쓰는 임베딩 설정. 캐시는 끈다 — 켜면 두 번째 실행이 아무것도 검증하지 못한다
@pytest.fixture
def live_embedding() -> EmbeddingConfig:
    config = EmbeddingConfig(embedding_cache_enabled=False)
    if not config.openai_api_key:
        pytest.skip("OPENAI_API_KEY 가 없어 건너뜁니다.")
    return config


# 저장소는 tmp_path 에 둔다. 실제 색인을 건드리지 않는다
@pytest.fixture
def live_store(tmp_path, live_embedding):
    store = get_store(IndexingConfig(qdrant_path=tmp_path / "qdrant"), live_embedding)
    try:
        yield store
    finally:
        store.close()


# 문장들을 청크로. chunk_id 는 splitter 와 같은 규칙이다
def _chunks() -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"규정.pdf:{index:04d}",
            document_key="규정.pdf",
            chunk_index=index,
            type=ChunkType.TEXT,
            text=text,
            source_name="규정.pdf",
        )
        for index, text in enumerate(SENTENCES)
    ]


def test_real_vectors_round_trip_and_rank(live_embedding, live_store, capsys):
    embedder = get_embedder(live_embedding)
    chunks = _chunks()

    vectors = embedder.embed_documents([chunk.text for chunk in chunks])
    records = to_records(chunks, vectors, document_id="규정.pdf")

    dimension = len(records[0].vector)
    live_store.ensure_collection(dimension)
    live_store.upsert(records)

    assert live_store.count() == len(SENTENCES)

    results = live_store.search(embedder.embed_query(QUERY), top_k=3)

    # (3) 의미 검색이 말이 되는가 — 연차 질의가 연차 문장을 집어야 한다
    assert results[0].chunk_id == "규정.pdf:0000"
    assert results[0].text == SENTENCES[0]

    # (2) Dot 이 코사인과 같은 순위를 주려면 단위 길이가 유지돼야 한다.
    # 서로 다른 문장이라 1.0 은 아니지만, 단위 벡터끼리의 내적은 [-1, 1] 안에 있어야 한다
    assert all(-1.0 <= result.score <= 1.0 for result in results)
    assert results[0].score > results[-1].score

    capsys.disabled()
    print(f"\n차원={dimension} 호출={embedder.usage.api_calls} 토큰={embedder.usage.total_tokens}")
    print("  " + "\n  ".join(f"{r.score:.4f} {r.chunk_id} {r.text[:30]}" for r in results))


def test_real_dimension_locks_the_collection(live_embedding, live_store):
    """1536차원으로 만들어진 컬렉션에 다른 차원을 넣으려 하면 막혀야 한다."""
    from rag_pipeline.exceptions import IndexingError

    embedder = get_embedder(live_embedding)
    dimension = len(embedder.embed_query("차원 확인"))
    live_store.ensure_collection(dimension)

    with pytest.raises(IndexingError, match="차원이 다릅니다"):
        live_store.ensure_collection(dimension // 2)
