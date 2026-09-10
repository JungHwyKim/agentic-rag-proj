"""색인된 문서 목록을 돌려주는 도구.

## 목록을 여기서 다시 만들지 않는다

`QdrantStore.list_documents()` 를 그대로 부른다. 한 줄이라도 여기서 다시 짜면 사이드바
(`backend/app/api/routes/query.py`)와 **두 번째 진실**이 생기고, 한쪽만 고쳐져도 에러가 안
난다 — `RetrieverTool` 이 `retrieve()` 를 감싸기만 하는 것과 같은 기준이다.

## `getattr` 로 받는 것이 규약이다

`list_documents()` 는 `VectorStore` Protocol 에 **일부러 안 올라가 있다**
(`ingestion/indexing/base.py`) — 저장소 중립을 지키는 쪽이 그것이고, 두 번째 구현체가
실재할 때 올린다. 그래서 `backend/app/main.py` 와 `routes/query.py` 가 이미 `getattr` 로
받고, 여기도 같은 모양이다. 타입으로 요구하면 Protocol 을 넓히게 되는데 그 결정은 이
도구가 내릴 것이 아니다.

## 자원을 스스로 안 연다

`store` 를 생성자로 받는다. `get_store()` 를 스스로 부르면 qdrant 로컬 파일 모드의 배타
잠금에 **backend 안에서 자기 자신과** 부딪힌다 — `RetrieverTool` docstring 의 이유가
글자 그대로 그대로다.

## 빈 목록은 실패가 아니다

컬렉션이 없으면 `list_documents()` 가 빈 리스트를 준다. 첫 실행에서 아무것도 안 색인된
것은 정상이지 오류가 아니고, 그 판단은 이미 저쪽이 내려 뒀다.
"""

from __future__ import annotations

from typing import Any

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.schemas.agent import ToolInput, ToolOutput

__all__ = ["NO_LISTING", "DocumentListTool"]

# 사람이 읽을 사유. **예외 이름으로 시작하지 않는다** — `web_search.py` 와 같은 규칙이다
NO_LISTING = "이 벡터 저장소는 문서 목록을 제공하지 않습니다"


# 무슨 문서가 색인돼 있나. **질의를 안 읽는다** — 입력이 없는 도구다
class DocumentListTool(BaseTool):
    name = "list_documents"
    description = "색인된 문서 목록을 돌려준다"

    # 자원의 수명은 부르는 쪽이 정한다 (모듈 docstring 참고)
    def __init__(self, *, store: VectorStore) -> None:
        self._store = store

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        # Protocol 에 없는 메서드라 있는지부터 묻는다 (모듈 docstring 참고)
        lister: Any = getattr(self._store, "list_documents", None)
        if not callable(lister):
            return ToolOutput(success=False, error=NO_LISTING)
        return ToolOutput(success=True, data=lister())
