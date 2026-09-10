"""실제 한국어 문서로 색인 전 구간을 돌린다. **API 를 부르지 않는다** — 임베딩은 가짜다.

합성 문서로는 보여줄 수 없는 것이 둘이다.

1. **청크 수백 개에서도 자리가 안 밀리는가.** 합성 문서는 청크가 서너 개라 `zip` 이 밀려도
   우연히 통과할 수 있다. 실제 문서는 수백 개고 제목·표·이미지 텍스트가 섞여 있다
2. **실제 한국어 파일명이 문서 키로 성립하는가.** 픽스처 이름에는 공백·대괄호·`+`·괄호가
   들어 있어 정규화 규칙이 실물에서 도는지가 여기서만 확인된다

`tests/fixtures/` 에 파일이 있을 때만 돌고 없으면 조용히 건너뛴다.
설정은 전부 명시해 넘긴다 — 개발자의 `.env` 가 상한이나 제목 승격을 덮으면 결과가 흔들린다.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from rag_pipeline.config import (
    ContentRoutingConfig,
    EmbeddingConfig,
    IndexingConfig,
    IngestConfig,
    SplitterConfig,
)
from rag_pipeline.ingestion.indexing import get_store
from rag_pipeline.ingestion.pipeline import index_document

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

DOCUMENTS = [
    "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드.pdf",
    # 2026-08-23 에 들어온 한컴 변환본. 2026-08-20 에 뺀 옛 HWPX 파일을 대신한다
    "[DNA+_2019-5]_공공기관_신뢰가능_AI_구현_실용가이드_fixed.hwpx",
    "시행령 개정안 안내서(정보시스템의무사항알림).hwpx",
    "2025년 학교기록물 관리 지침.hwpx",
]


# 외부 API 를 절대 부르지 않고 저장소를 tmp_path 에 두는 설정
@pytest.fixture
def real_config(tmp_path) -> IngestConfig:
    config = IngestConfig(detect_headings=True)
    object.__setattr__(
        config,
        "_content",
        ContentRoutingConfig(
            openai_api_key=None,
            llama_cloud_api_key=None,
            table_pdf_handler="pymupdf",
            image_handler="skip",
            cache_enabled=False,
        ),
    )
    object.__setattr__(
        config,
        "_splitter",
        SplitterConfig(max_chunk_chars=1200, chunk_overlap_chars=150),
    )
    # **`embedding_provider` 를 못 박는 것이 여기서 특히 중요하다.** 이 파일은 실파일로
    # `index_document()` 를 끝까지 돌리는 유일한 자리라, provider 가 기본값을 타면
    # `fake_embedding_api` 가 갈아끼우는 OpenAI 이음새가 **아무 데도 안 꽂히고** 로컬
    # 가중치 2.27GB 를 실제로 적재한다. 실측으로 그 일이 났다 — 2026-08-25 에 기본값이
    # `local` 로 바뀌자 이 폴더가 120초를 넘겼고 `test_every_chunk_is_stored` 에서
    # **Windows access violation** 까지 났다(키 없음으로는 못 막는다. 로컬은 키가 필요 없다)
    object.__setattr__(
        config,
        "_embedding",
        EmbeddingConfig(
            openai_api_key=None,
            embedding_provider="openai",
            embedding_cache_enabled=False,
        ),
    )
    object.__setattr__(config, "_indexing", IndexingConfig(qdrant_path=tmp_path / "qdrant"))
    return config


# 파일이 있으면 경로를, 없으면 건너뛴다
def _fixture(name: str) -> Path:
    path = FIXTURES / name
    if not path.is_file():
        pytest.skip(f"{name} 이(가) 없어 건너뜁니다.")
    return path


# 색인이 끝난 뒤 저장소를 다시 연다. index_document() 가 잠금을 풀었어야 열린다
@pytest.fixture
def reopen(real_config):
    opened = []

    def _open():
        store = get_store(real_config.indexing, real_config.embedding)
        opened.append(store)
        return store

    try:
        yield _open
    finally:
        for store in opened:
            store.close()


@pytest.mark.parametrize("name", DOCUMENTS)
def test_every_chunk_is_stored(name, real_config, fake_embedding_api, reopen):
    """청크 하나가 조용히 빠지면 그 부분은 영원히 검색되지 않는다."""
    result = index_document(_fixture(name), original_name=name, config=real_config)

    assert result.chunk_count > 0
    assert result.indexed_count == result.chunk_count
    assert reopen().count() == result.chunk_count


@pytest.mark.parametrize("name", DOCUMENTS)
def test_document_id_survives_the_real_filename(name, real_config, fake_embedding_api):
    """공백·대괄호·괄호가 든 실제 이름이 그대로 키가 되는지."""
    result = index_document(_fixture(name), original_name=name, config=real_config)

    assert result.document_id == unicodedata.normalize("NFC", name)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_reindexing_a_real_document_does_not_grow(name, real_config, fake_embedding_api, reopen):
    path = _fixture(name)
    first = index_document(path, original_name=name, config=real_config)
    index_document(path, original_name=name, config=real_config)

    assert reopen().count() == first.chunk_count


@pytest.mark.parametrize("name", DOCUMENTS)
def test_every_chunk_finds_itself(name, real_config, fake_embedding_api, reopen, text_vector):
    """**자리 밀림을 잡는 테스트.** 청크 수백 개 전부가 자기 질의에 자기가 1등이어야 한다.

    값을 원소별로 비교하지 않는 이유: 임베딩은 호출 간 비트 재현성이 없다. 순위로 본다.
    """
    index_document(_fixture(name), original_name=name, config=real_config)
    store = reopen()

    # 전부를 훑되 결과가 커지지 않게 청크 목록은 저장소에서 되받는다
    total = store.count()
    stored = store.search(text_vector("아무 질의"), top_k=total)
    assert len(stored) == total

    misplaced = [
        result.chunk_id
        for result in stored
        if store.search(text_vector(result.text), top_k=1)[0].chunk_id != result.chunk_id
    ]

    assert misplaced == []


def test_two_real_documents_do_not_collide(real_config, fake_embedding_api, reopen):
    """같은 저장소에 문서 둘을 넣고 하나만 지웠을 때 나머지가 온전한가."""
    first_name, second_name = DOCUMENTS[0], DOCUMENTS[2]
    first = index_document(_fixture(first_name), original_name=first_name, config=real_config)
    second = index_document(_fixture(second_name), original_name=second_name, config=real_config)

    store = reopen()
    assert store.count() == first.chunk_count + second.chunk_count

    store.delete_by_document_id(first.document_id)
    assert store.count() == second.chunk_count
