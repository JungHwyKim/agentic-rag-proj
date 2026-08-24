"""`index_document()` 전 구간 — 파일 하나가 검색 가능한 벡터가 되기까지.

여기서 확인하는 것은 단계별 계약이 아니라 **이어붙인 결과**다. 특히 둘이 중요하다.

1. **`ingest()` 는 안 바뀌었다.** 업로드 응답 스키마가 그대로여야 하고, 기본 `pytest` 가
   임베딩을 안 불러야 "키 없으면 예외"가 테스트를 안 깬다. 그 둘이 서로를 지탱한다
2. **저장소 잠금이 풀린다.** qdrant 로컬 모드는 디렉터리에 배타 잠금을 걸어, 안 닫으면
   두 번째 색인이 통째로 죽는다. 연달아 두 번 부르는 테스트가 그것을 지킨다

임베딩은 `_make_client()` 를 갈아끼워 가짜로 만든다 — 과금도 네트워크도 없다.
"""

from __future__ import annotations

import pytest

from rag_pipeline.exceptions import EmbeddingError
from rag_pipeline.ingestion.indexing import get_store
from rag_pipeline.ingestion.pipeline import index_document, ingest


# 색인이 끝난 뒤 저장소를 다시 열어 확인한다. index_document() 가 잠금을 풀었어야 열린다
@pytest.fixture
def reopen(offline_config):
    opened = []

    def _open():
        store = get_store(offline_config.indexing, offline_config.embedding)
        opened.append(store)
        return store

    try:
        yield _open
    finally:
        for store in opened:
            store.close()


def test_every_chunk_reaches_the_store(docx_file, offline_config, fake_embedding_api, reopen):
    result = index_document(docx_file, original_name="안내서.docx", config=offline_config)

    assert result.chunk_count > 0
    assert result.indexed_count == result.chunk_count
    assert reopen().count() == result.chunk_count


def test_document_id_is_the_original_filename(docx_file, offline_config, fake_embedding_api):
    result = index_document(docx_file, original_name="안내서.docx", config=offline_config)

    assert result.document_id == "안내서.docx"


def test_chunk_ids_carry_the_document_id(
    docx_file, offline_config, fake_embedding_api, reopen, text_vector
):
    """splitter 의 document_key 자리에 실제 문서 키가 들어갔는지."""
    index_document(docx_file, original_name="안내서.docx", config=offline_config)

    found = reopen().search(text_vector("아무거나"), top_k=1)[0]

    assert found.chunk_id.startswith("안내서.docx:")
    assert found.document_id == "안내서.docx"


def test_reindexing_the_same_file_does_not_grow(
    docx_file, offline_config, fake_embedding_api, reopen
):
    """두 번 부르는 것이 잠금 해제 검증이기도 하다 — 안 풀렸으면 두 번째가 죽는다."""
    first = index_document(docx_file, original_name="안내서.docx", config=offline_config)
    second = index_document(docx_file, original_name="안내서.docx", config=offline_config)

    assert second.chunk_count == first.chunk_count
    assert reopen().count() == first.chunk_count


def test_two_documents_live_side_by_side(
    docx_file, pdf_file, offline_config, fake_embedding_api, reopen
):
    first = index_document(docx_file, original_name="안내서.docx", config=offline_config)
    second = index_document(pdf_file, original_name="지침.pdf", config=offline_config)

    assert reopen().count() == first.chunk_count + second.chunk_count


def test_a_chunk_finds_itself(docx_file, offline_config, fake_embedding_api, reopen, text_vector):
    """자기 텍스트로 검색하면 자기가 1등이어야 한다 — 자리 밀림이 있으면 여기서 걸린다."""
    index_document(docx_file, original_name="안내서.docx", config=offline_config)
    store = reopen()

    sample = store.search(text_vector("무엇이든"), top_k=1)[0]
    again = store.search(text_vector(sample.text), top_k=1)[0]

    assert again.chunk_id == sample.chunk_id


def test_usage_is_reported(docx_file, offline_config, fake_embedding_api):
    """비용이 안 보이면 얼마 썼는지 알 길이 없다."""
    result = index_document(docx_file, original_name="안내서.docx", config=offline_config)

    assert result.usage.api_calls > 0
    assert result.usage.total_tokens > 0


def test_collection_name_is_reported(docx_file, offline_config, fake_embedding_api):
    result = index_document(docx_file, original_name="안내서.docx", config=offline_config)

    assert "text-embedding-3-small" in result.collection


# --- ingest() 는 안 바뀐다 ---


def test_ingest_never_calls_embedding(docx_file, offline_config, fake_embedding_api):
    """이게 깨지면 키 없는 환경에서 기본 pytest 가 통째로 무너진다."""
    document = ingest(docx_file, original_name="안내서.docx", config=offline_config)

    assert document.elements
    assert fake_embedding_api.calls == []


def test_index_document_without_a_key_raises(docx_file, offline_config):
    """가짜를 안 끼우면 키가 없어 실패해야 한다. 빈 벡터로 넘어가면 안 된다."""
    with pytest.raises(EmbeddingError):
        index_document(docx_file, original_name="안내서.docx", config=offline_config)


# ── store 주입 ─────────────────────────────────────────────────────────────
# **소유권 규칙: 연 쪽이 닫는다.** 이 두 테스트가 규칙의 양쪽 끝을 잠근다 —
# 거꾸로 잡으면 한쪽이 반드시 죽는다. 주입받은 것을 닫으면 backend 가 다음 요청에서 죽고,
# 자기가 연 것을 안 닫으면 evaluation/run_eval.py 가 색인 직후 검색하려다 잠금으로 죽는다.
# 실파일 테스트(test_indexing_real.py)가 뒤엣것을 이미 지키지만 **픽스처가 없으면 조용히
# skip** 하므로, 합성으로도 잠가 둔다


# 주입한 store 는 닫지 않는다. 호출자가 앱 수명 동안 들고 있는 것이라 여기서 닫으면
# 다음 요청이 배타 잠금으로 죽는다
def test_injected_store_is_not_closed(docx_file, offline_config, fake_embedding_api):
    store = get_store(offline_config.indexing, offline_config.embedding)
    try:
        result = index_document(
            docx_file, original_name="안내서.docx", config=offline_config, store=store
        )

        assert result.indexed_count == result.chunk_count
        # 닫혔으면 이 호출이 죽는다 — 같은 인스턴스를 계속 쓸 수 있어야 한다
        assert store.count() == result.chunk_count
    finally:
        store.close()


# 주입한 store 로 연달아 두 번 색인해도 산다. backend 가 실제로 밟는 경로다 —
# 요청마다 새로 열지 않고 같은 싱글턴을 계속 넘긴다
def test_injected_store_survives_repeated_indexing(docx_file, offline_config, fake_embedding_api):
    store = get_store(offline_config.indexing, offline_config.embedding)
    try:
        first = index_document(
            docx_file, original_name="안내서.docx", config=offline_config, store=store
        )
        second = index_document(
            docx_file, original_name="안내서.docx", config=offline_config, store=store
        )

        # 같은 문서를 다시 넣은 것이라 재색인이다. 청크가 두 배가 되면 delete 가 안 돈 것이다
        assert second.chunk_count == first.chunk_count
        assert store.count() == first.chunk_count
    finally:
        store.close()


# store 를 안 넘기면 예전 그대로 자기가 열고 **반드시 닫는다**. 평가 러너가 색인 루프 직후
# 자기 get_store() 를 열어 검색하므로 이 닫힘에 기대고 있다
def test_own_store_is_still_closed(docx_file, offline_config, fake_embedding_api, reopen):
    index_document(docx_file, original_name="안내서.docx", config=offline_config)

    # 닫혔어야 열린다. 안 닫혔으면 여기서 RuntimeError 로 죽는다
    assert reopen().count() > 0
