"""검색 도구가 `retrieve()` 를 **감싸기만 하는지** 본다.

이 파일이 지키는 것 셋이다.

1. **검색 로직을 다시 안 짰다** — 가짜 저장소·임베더로 진짜 `retrieve()` 가 돌고, 그 결과가
   그대로 `ToolOutput.data` 에 실린다. 도구 안에서 다시 정렬하거나 자르면 여기서 깨진다
2. **0건은 실패가 아니다** — 근거를 못 찾은 것과 도구가 죽은 것은 사용자가 할 일이 다르다
3. **저장소가 죽으면 값으로 내려온다** — `BaseTool` 의 계약이 실제 구현체에서도 사는지

모델 가중치도 외부 API 도 안 건드린다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.retriever import RetrieverTool
from rag_pipeline.schemas.agent import ToolInput
from rag_pipeline.schemas.retrieval import RetrievedChunk
from rag_pipeline.schemas.vector import SearchResult


# 어떤 질의든 같은 벡터를 준다
class FakeEmbedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


# 받은 top_k 를 기록한다. 도구가 폭을 어떻게 넘겼는지 보는 자리다
class FakeStore:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.top_ks: list[int] = []

    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        self.top_ks.append(top_k)
        return self.results[:top_k]

    def iter_chunks(self):  # noqa: ANN201 - Iterator[StoredChunk]
        return iter([])


# 부르면 죽는 저장소. 도구가 예외를 값으로 바꾸는지 보는 자리다
class BrokenStore(FakeStore):
    def search(self, query_vector: list[float], top_k: int = 5) -> list[SearchResult]:
        raise ConnectionError("저장소가 응답하지 않습니다")


def _result(chunk_id: str, score: float) -> SearchResult:
    return SearchResult(
        id=f"uuid-{chunk_id}",
        score=score,
        text=f"{chunk_id} 본문",
        document_id="안내서.pdf",
        chunk_id=chunk_id,
        metadata={"source_name": "안내서.pdf"},
    )


def _tool(store: FakeStore, **kwargs) -> RetrieverTool:
    return RetrieverTool(store=store, embedder=FakeEmbedder(), **kwargs)


# 검색 결과가 그대로 실려 나온다. 도구가 다시 자르거나 정렬하지 않는다
def test_returns_chunks_from_retrieve():
    store = FakeStore([_result("c1", 0.9), _result("c2", 0.8)])

    out = _tool(store, top_k=2).run(ToolInput(query="데이터세트 이관 절차는"))

    assert out.success is True
    assert out.error is None
    assert [c.chunk_id for c in out.data] == ["c1", "c2"]
    assert all(isinstance(c, RetrievedChunk) for c in out.data)


# 근거 0건은 성공이다 — 실패로 만들면 화면이 "찾지 못했습니다"와 "도구가 죽었다"를 못 가른다
def test_no_hit_is_success():
    out = _tool(FakeStore([])).run(ToolInput(query="코퍼스에 없는 질문"))

    assert out.success is True
    assert out.data == []


# metadata 의 top_k 가 실제 검색 폭을 바꾼다. 없으면 생성자 값으로 돌아간다
def test_top_k_comes_from_metadata_then_constructor():
    store = FakeStore([_result(f"c{i}", 1.0 - i / 10) for i in range(10)])
    tool = _tool(store, top_k=3)

    tool.run(ToolInput(query="질문"))
    tool.run(ToolInput(query="질문", metadata={"top_k": 7}))
    tool.run(ToolInput(query="질문", metadata={"top_k": "많이"}))

    assert store.top_ks == [3, 7, 3]


# 저장소가 죽어도 예외가 밖으로 안 나간다 — BaseTool 의 계약이 실제 구현체에서도 산다
def test_store_failure_becomes_failed_output():
    out = _tool(BrokenStore([])).run(ToolInput(query="질문"))

    assert out.success is False
    assert "ConnectionError" in out.error
    assert out.data is None
