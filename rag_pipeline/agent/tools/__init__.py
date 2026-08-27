"""도구 구현체들. 계약은 `base.py`, 스키마는 `schemas/agent.py` 에 있다.

## 이 단계의 규약

- **예외를 밖으로 안 낸다.** 실패는 `ToolOutput(success=False, error=...)` 다. 이 규약을
  구조로 지키려고 `BaseTool.run()` 이 템플릿 메서드다 — 구현체는 `_run()` 만 짠다
- **자원을 스스로 안 연다.** `store`·`embedder` 는 생성자로 받는다. 도구가 `get_store()` 를
  부르면 backend 안에서 자기 자신과 qdrant 배타 잠금으로 부딪히고, `get_embedder()` 를
  부르면 호출마다 KURE 2.27GB 를 새로 적재한다(둘 다 실제로 밟았던 함정이다)
"""

from __future__ import annotations

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.agent.tools.retriever import RetrieverTool
from rag_pipeline.agent.tools.web_search import WebSearchTool

__all__ = ["BaseTool", "RetrieverTool", "WebSearchTool"]
