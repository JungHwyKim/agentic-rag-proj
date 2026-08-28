"""문서 목록 도구 — 저장소가 준 것을 그대로 넘기는지, 없을 때 어떻게 실패하는지.

`list_documents()` 는 `VectorStore` Protocol 에 **일부러 안 올라가 있어** 이 도구가
`getattr` 로 받는다. 그래서 「없는 저장소」가 실재하는 경우이고, 그때 죽지 않는 것이 계약이다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.documents import NO_LISTING, DocumentListTool
from rag_pipeline.schemas.agent import ToolInput

_DOCS = [
    {"document_id": "d1", "source_name": "시행령 안내서.pdf", "chunks": 118},
    {"document_id": "d2", "source_name": "학교기록물.hwpx", "chunks": 105},
]


class FakeStore:
    def __init__(self, docs):
        self.docs = docs

    def list_documents(self):
        return self.docs


# Protocol 만 만족하고 목록은 못 주는 저장소. 실재할 수 있는 모양이다
class ListlessStore:
    pass


def _run(store):
    return DocumentListTool(store=store).run(ToolInput(query=""))


# 저장소가 준 것을 **그대로** 넘긴다. 여기서 다시 만들면 사이드바와 두 번째 진실이 생긴다
def test_passes_the_listing_through():
    out = _run(FakeStore(_DOCS))

    assert out.success is True
    assert out.data == _DOCS


# 빈 목록은 실패가 아니다 — 첫 실행에서 아무것도 안 색인된 것은 정상이다
def test_empty_listing_is_success():
    out = _run(FakeStore([]))

    assert out.success is True
    assert out.data == []
    assert out.error is None


# 목록을 못 주는 저장소에서도 안 죽는다. 사유는 예외 이름으로 시작하지 않는다
def test_store_without_listing_fails_with_a_readable_reason():
    out = _run(ListlessStore())

    assert out.success is False
    assert out.error == NO_LISTING
    assert "Error" not in out.error


# 저장소가 던져도 예외가 밖으로 안 나간다
def test_store_exception_becomes_failed_output():
    class Exploding:
        def list_documents(self):
            raise RuntimeError("컬렉션을 확인하지 못했습니다")

    out = _run(Exploding())

    assert out.success is False
    assert "RuntimeError" in out.error


# 질의를 안 읽는다 — 입력이 없는 도구다
def test_query_is_ignored():
    store = FakeStore(_DOCS)

    assert _run(store).data == DocumentListTool(store=store).run(ToolInput(query="뭐가 있어?")).data
