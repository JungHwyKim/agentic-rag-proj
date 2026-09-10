"""도구 구현체들. 계약은 `base.py`, 스키마는 `schemas/agent.py` 에 있다.

## 이 단계의 규약

- **예외를 밖으로 안 낸다.** 실패는 `ToolOutput(success=False, error=...)` 다. 이 규약을
  구조로 지키려고 `BaseTool.run()` 이 템플릿 메서드다 — 구현체는 `_run()` 만 짠다
- **자원을 스스로 안 연다.** `store`·`embedder` 는 생성자로 받는다. 도구가 `get_store()` 를
  부르면 backend 안에서 자기 자신과 qdrant 배타 잠금으로 부딪히고, `get_embedder()` 를
  부르면 호출마다 KURE 2.27GB 를 새로 적재한다(둘 다 실제로 밟았던 함정이다).
  **시계도 같은 축이다** — `CurrentDateTool` 이 `now` 를 받는다
- **실패 사유는 예외 이름으로 시작하지 않는다.** *아직 구성 안 됐다* 와 *부르다 죽었다* 가
  사람 눈에 갈려야 한다 (`web_search.NOT_CONFIGURED` · `documents.NO_LISTING`)
- **0건은 실패가 아니다.** 못 찾은 것과 도구가 죽은 것은 다르다 — 검색·웹검색·문서목록 셋 다

## 도구 넷이 서로 다른 축에 있다

우연이 아니라 그러라고 함께 넣었다. `BaseTool` 은 그전까지 「진짜 외부 API 를 부르는 도구」를
한 번도 안 겪었고, 계약이 실제로 시험되려면 모양이 갈려야 한다.

| 도구 | 무엇을 주입받나 | 과금 |
|---|---|---|
| `RetrieverTool` | store · embedder · bm25 · reranker · rewriter | 갈래에 따라 |
| `WebSearchTool` | 설정(키) | **질의당 Tavily 크레딧** |
| `CurrentDateTool` | 시계 | 없음 |
| `CalculatorTool` | 없음 | 없음 |
| `DocumentListTool` | store | 없음 |

## 웹 검색만 캐시가 없다

다른 유료 단계는 전부 호출 캐시가 있어 **없는 것이 누락으로 보인다.** 결정이다 — 이 저장소의
캐시 셋은 *같은 입력이면 같은 출력* 을 전제하는데 웹 검색은 그것이 거짓이다.
근거는 `rag_pipeline/config.py` 의 `WebSearchConfig` 주석.

## Router 는 아직 `retriever` 밖에 안 고른다

넷이 실재해도 `agent/router.py` 가 고르는 것은 검색 하나다(*"코퍼스 밖인가를 판정할 실행
시점 신호가 없다"*). **도구를 만드는 것과 에이전트가 그것을 고르는 것은 다른 국면이고**,
뒤엣것은 신호를 세우고 재야 정해진다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.agent.tools.calculator import CalculatorTool
from rag_pipeline.agent.tools.clock import CurrentDateTool
from rag_pipeline.agent.tools.documents import DocumentListTool
from rag_pipeline.agent.tools.retriever import RetrieverTool
from rag_pipeline.agent.tools.web_search import WebSearchTool

__all__ = [
    "BaseTool",
    "CalculatorTool",
    "CurrentDateTool",
    "DocumentListTool",
    "RetrieverTool",
    "WebSearchTool",
]
