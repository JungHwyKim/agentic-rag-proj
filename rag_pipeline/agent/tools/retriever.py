"""검색을 도구 모양으로 감싼다.

**검색 로직이 여기 하나도 없다.** `retrieval.retrieve()` 를 그대로 부르고 결과를
`ToolOutput` 에 담을 뿐이다. 한 줄이라도 여기서 다시 짜면 hybrid·RRF·리랭커·재작성이
**두 번째 진실**을 갖게 되고, 평가 러너가 `retrieve()` 를 직접 부르므로 그 두 번째 경로는
**아무도 안 재게 된다.**

## 왜 자원을 생성자로 받나

`answer_question()` 이 `store`·`embedder`·`bm25`·`reranker`·`rewriter` 를 인자로 받는 것과
**같은 이유이고 같은 함정**이다.

- `store` 를 스스로 열면 qdrant 로컬 파일 모드의 배타 잠금에 자기 자신과 부딪힌다 —
  backend 는 startup 에 하나 열어 앱 수명 내내 들고 있다
- `embedder` 를 스스로 만들면 **호출마다 KURE-v1 2.27GB 를 새로 적재한다**.
  `get_embedder()` 는 메모이즈를 안 한다(실측: 업로드 65초 중 파싱은 0.801초였다)

즉 **수명을 정하는 것은 도구가 아니라 도구를 만드는 쪽**이다.

## `top_k` 를 `metadata` 로도 받는 이유

Router 가 같은 도구를 상황에 따라 다른 폭으로 부를 수 있어야 한다(첫 검색은 넓게, 재시도는
좁게). 그렇다고 `ToolInput` 에 필드를 더하면 **모든 도구가 검색 전용 칸을 갖게 된다** —
`metadata` 가 있는 이유가 그것이다. 값이 없거나 정수가 아니면 생성자 기본값을 쓴다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.config import RetrievalConfig
from rag_pipeline.ingestion.embedding.base import Embedder
from rag_pipeline.ingestion.indexing.base import VectorStore
from rag_pipeline.retrieval import Bm25Index, QueryRewriter, Reranker, retrieve
from rag_pipeline.schemas.agent import ToolInput, ToolOutput

__all__ = ["RetrieverTool"]


# 색인된 문서에서 근거 청크를 찾는 도구. retrieve() 를 감싸기만 한다
class RetrieverTool(BaseTool):
    name = "retriever"
    description = "색인된 문서에서 질문과 관련된 청크를 찾는다"

    # 자원의 수명은 부르는 쪽이 정한다 (모듈 docstring 참고)
    def __init__(
        self,
        *,
        store: VectorStore,
        embedder: Embedder,
        top_k: int = 5,
        bm25: Bm25Index | None = None,
        reranker: Reranker | None = None,
        rewriter: QueryRewriter | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._bm25 = bm25
        self._reranker = reranker
        self._rewriter = rewriter
        self._config = config

    # 질의 하나로 검색한다. 0건은 실패가 아니다 — 근거를 못 찾은 것과 도구가 죽은 것은 다르다
    def _run(self, tool_input: ToolInput) -> ToolOutput:
        chunks = retrieve(
            tool_input.query,
            store=self._store,
            embedder=self._embedder,
            top_k=self._resolve_top_k(tool_input),
            bm25=self._bm25,
            reranker=self._reranker,
            rewriter=self._rewriter,
            config=self._config,
        )
        return ToolOutput(success=True, data=chunks)

    # metadata 의 top_k 를 쓰되 정수가 아니면 생성자 값으로 돌아간다
    def _resolve_top_k(self, tool_input: ToolInput) -> int:
        value = tool_input.metadata.get("top_k")
        if isinstance(value, int) and value > 0:
            return value
        return self._top_k
