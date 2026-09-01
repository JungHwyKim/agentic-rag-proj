"""그림자 색인의 계약을 잠근다 — **두 컬렉션이 같은 문서 집합이 되는 것**과
**그림자가 죽어도 주 색인이 산다는 것** 둘이다.

## 왜 이 파일이 필요한가

그림자는 `.claude/wiki/plan_log/embedding.md` 5회차가 *"나중에 두 모델을 견주고 싶어졌을 때 재색인 없이
되게 해 주는 보험"* 으로 켠 것이다. 보험이 성립하려면 두 컬렉션의 문서 집합이 같아야 하는데,
그 성질은 **아무 에러도 안 내며 깨진다** — 그림자 실패를 일부러 삼키기 때문이다.

## 파일 이름

`tests/` 에 `__init__.py` 가 없어 basename 이 곧 모듈명이다. 저장소 전체에서 유일해야 한다.
"""

from __future__ import annotations

from typing import Any

import pytest

from rag_pipeline.config import EmbeddingConfig, IndexingConfig
from rag_pipeline.exceptions import EmbeddingError
from rag_pipeline.ingestion.indexing import get_store
from rag_pipeline.ingestion.pipeline import index_document


# 그림자 저장소 흉내. **upsert 된 레코드를 보관한다** — 주 저장소와 같은 청크가 갔는지
# 보는 것이 이 파일의 검사 하나다
class FakeShadowStore:
    def __init__(self, fail: Exception | None = None) -> None:
        self.collection = "shadow-collection"
        self.records: list[Any] = []
        self.deleted: list[str] = []
        self.ensured: int | None = None
        self.closed = False
        self._fail = fail

    def ensure_collection(self, vector_size: int) -> None:
        self.ensured = vector_size

    def delete_by_document_id(self, document_id: str) -> None:
        self.deleted.append(document_id)

    def upsert(self, records: list[Any]) -> int:
        if self._fail is not None:
            raise self._fail
        self.records.extend(records)
        return len(records)

    def close(self) -> None:
        self.closed = True


# 그림자 임베더 흉내. 차원을 주 임베더와 다르게 두어 **두 벌이 섞이지 않는지** 본다
class FakeShadowEmbedder:
    def __init__(self, dim: int = 3, fail: Exception | None = None) -> None:
        self.dim = dim
        self.texts: list[str] = []
        self._fail = fail

    @property
    def usage(self):  # noqa: ANN201 - EmbeddingUsage 흉내. 리포트가 읽기만 한다
        from rag_pipeline.ingestion.embedding import EmbeddingUsage

        return EmbeddingUsage()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._fail is not None:
            raise self._fail
        self.texts = list(texts)
        return [[1.0] * self.dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dim


# --- 계약: 두 컬렉션이 같은 문서 집합이 된다 ---


# **주 색인과 그림자가 같은 청크를 본다.** 파싱·청킹이 한 번만 돌기 때문에 성립하는
# 성질이고, 호출부가 index_document() 를 두 번 부르는 방식이었다면 못 지킨다
def test_shadow_indexes_the_same_chunks(docx_file, offline_config, fake_embedding_api):
    shadow_store = FakeShadowStore()
    shadow_embedder = FakeShadowEmbedder()

    result = index_document(
        docx_file,
        original_name="안내서.docx",
        config=offline_config,
        shadow=(shadow_store, shadow_embedder),
    )

    assert result.shadow is not None
    assert result.shadow.error is None
    # 그림자에 간 청크 수가 주 색인과 같다 — 여기가 갈리면 보험이 성립 안 한다
    assert result.shadow.indexed_count == result.chunk_count
    assert len(shadow_embedder.texts) == result.chunk_count


# **그림자도 delete -> upsert 다.** 안 지우면 재색인이 옛 청크를 남겨 두 컬렉션의
# 청크 수가 갈린다 (주 색인과 같은 규약)
def test_shadow_deletes_before_upserting(docx_file, offline_config, fake_embedding_api):
    shadow_store = FakeShadowStore()

    index_document(
        docx_file,
        original_name="안내서.docx",
        config=offline_config,
        shadow=(shadow_store, FakeShadowEmbedder()),
    )

    assert shadow_store.deleted == ["안내서.docx"]


# 그림자 컬렉션 차원은 **그림자 임베더**가 정한다. 주 임베더 차원을 쓰면
# ensure_collection 이 틀린 크기로 만들어 첫 upsert 가 죽는다
def test_shadow_collection_uses_its_own_dimension(docx_file, offline_config, fake_embedding_api):
    shadow_store = FakeShadowStore()

    index_document(
        docx_file,
        original_name="안내서.docx",
        config=offline_config,
        shadow=(shadow_store, FakeShadowEmbedder(dim=3)),
    )

    assert shadow_store.ensured == 3


# 안 넘기면 그림자가 없다. **기본 동작이 지금까지와 글자 그대로 같아야 한다**
def test_no_shadow_by_default(docx_file, offline_config, fake_embedding_api):
    result = index_document(docx_file, original_name="안내서.docx", config=offline_config)
    assert result.shadow is None


# --- 계약: 그림자가 죽어도 주 색인은 산다 ---


# **이 저장소에서 드문 쪽이다** — embedding·indexing 은 실패를 예외로 올린다.
# 여기서 삼키는 근거는 *"주 컬렉션이 이미 성공해 폴백 층이 실재한다"* 이고,
# 되돌리면 OpenAI 장애가 업로드를 통째로 막는다(주 모델은 멀쩡한데도)
@pytest.mark.parametrize(
    "failure",
    [
        EmbeddingError("키 없음", user_message="임베딩 키가 없습니다."),
        RuntimeError("네트워크"),
    ],
)
def test_shadow_failure_does_not_break_indexing(
    docx_file, offline_config, fake_embedding_api, failure
):
    result = index_document(
        docx_file,
        original_name="안내서.docx",
        config=offline_config,
        shadow=(FakeShadowStore(), FakeShadowEmbedder(fail=failure)),
    )

    # 주 색인은 그대로 성공했다
    assert result.indexed_count == result.chunk_count
    # 그리고 **구멍이 드러난다** — 조용히 없어지는 것과 갈리는 자리다
    assert result.shadow is not None
    assert result.shadow.error is not None
    assert result.shadow.indexed_count == 0


# 저장소 쪽 실패도 같다. 임베딩만 감싸면 upsert 실패에서 그대로 터진다
def test_shadow_store_failure_is_also_swallowed(docx_file, offline_config, fake_embedding_api):
    result = index_document(
        docx_file,
        original_name="안내서.docx",
        config=offline_config,
        shadow=(FakeShadowStore(fail=RuntimeError("잠금")), FakeShadowEmbedder()),
    )

    assert result.indexed_count == result.chunk_count
    assert result.shadow is not None and "잠금" in result.shadow.error


# --- sibling_for(): 두 번째 컬렉션을 여는 유일한 길 ---


# **형제는 주인의 클라이언트를 빌린다.** 새로 열면 qdrant 로컬 파일 모드가 같은 프로세스
# 안에서 자기 자신과 배타 잠금 충돌을 낸다 — 그것이 이 메서드가 있는 이유 전부다
def test_sibling_shares_the_client(tmp_path):
    indexing = IndexingConfig(qdrant_path=tmp_path / "qdrant")
    primary = get_store(indexing, EmbeddingConfig(embedding_model="text-embedding-3-small"))
    try:
        sibling = primary.sibling_for(EmbeddingConfig(embedding_model="other-model"))
        assert sibling.client is primary.client
        # 컬렉션은 갈린다 — 모델 이름에서 파생되기 때문이다
        assert sibling.collection != primary.collection
    finally:
        primary.close()


# **형제를 닫아도 주인은 안 죽는다.** 소유권 규칙이 *"연 쪽이 닫는다"* 이고 형제는 연 적이
# 없다. 되돌리면 그림자를 닫는 순간 backend 가 다음 요청에서 죽는다
def test_closing_a_sibling_does_not_close_the_owner(tmp_path):
    indexing = IndexingConfig(qdrant_path=tmp_path / "qdrant")
    primary = get_store(indexing, EmbeddingConfig(embedding_model="text-embedding-3-small"))
    try:
        sibling = primary.sibling_for(EmbeddingConfig(embedding_model="other-model"))
        client = primary.client
        sibling.close()
        # 주인은 같은 클라이언트를 그대로 쓴다
        assert primary.client is client
    finally:
        primary.close()


# **게으름이 유지된다** — 형제를 만들기만 하고 안 쓰면 파일이 안 열린다.
# lifespan 이 지켜온 성질이라 여기서 깨지면 색인 없는 상태에서 서버가 안 뜬다
def test_sibling_does_not_open_the_file_until_used(tmp_path, mocker):
    from rag_pipeline.ingestion.indexing import qdrant_store

    make = mocker.spy(qdrant_store, "_make_client")
    indexing = IndexingConfig(qdrant_path=tmp_path / "qdrant")
    primary = qdrant_store.QdrantStore(indexing, EmbeddingConfig())

    primary.sibling_for(EmbeddingConfig(embedding_model="other-model"))

    assert make.call_count == 0
