"""재시도 루프 테스트가 나눠 쓰는 가짜들.

**`conftest.py` 가 아니라 이름이 겹치지 않는 모듈이다.** 루트 `tests/conftest.py` 가
`sys.modules["conftest"]` 를 먼저 차지해 하위 폴더에서 `import conftest` 가 엉뚱한 모듈을
집기 때문이고, `tests/hooks/_escape_hatches.py` 가 같은 이유로 있는 자리다.

**두 테스트가 이것을 나눠 쓰는 것이 요점이다.** while 경로(`test_retry_loop.py`)와 그래프
경로(`test_agent_graph.py`)가 각자 가짜를 지으면, 두 경로가 **서로 다른 가짜를 보면서도**
「같다」가 나올 수 있다 — 그러면 동등성 시험이 아무것도 안 재게 된다.

가짜는 넷이고 전부 **호출된 질의를 기록한다.** 무엇으로 검색했고 무엇으로 판정·생성했는지가
이 루프에서 제일 자주 어긋나는 자리이기 때문이다(검색만 재작성 질의를 쓴다).
"""

from __future__ import annotations

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput
from rag_pipeline.schemas.answer import EvaluatorVerdict
from rag_pipeline.schemas.retrieval import RetrievedChunk

QUERY = "데이터세트 이관 절차는"


def chunk(chunk_id: str = "c1") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id, document_id="안내서.pdf", text=f"{chunk_id} 본문", metadata={}
    )


# 루프가 `QueryRewriter` 에서 읽는 것은 `last_rewritten` 하나다. **검색이 돌 때 갱신된다** —
# 실제로도 재작성은 `retrieve()` 안에서 돌고 루프는 그 결과를 읽기만 한다
class StubRewriter:
    def __init__(self, queries: list[str | None] | None = None) -> None:
        self._queries = queries
        self._calls = 0
        self.last_rewritten: str | None = None

    def note_retrieval(self, query: str) -> None:
        if self._queries is None:
            self.last_rewritten = f"{query}+확장{self._calls}"
        else:
            self.last_rewritten = (
                self._queries[self._calls] if self._calls < len(self._queries) else None
            )
        self._calls += 1


# 부른 질의를 기록하는 가짜 검색 도구. 실패는 예외로 내고 `BaseTool.run()` 이 값으로 바꾼다
class StubRetriever(BaseTool):
    name = "retriever"
    description = "테스트용"

    def __init__(
        self,
        *,
        chunks: list[RetrievedChunk] | None = None,
        rewriter: StubRewriter | None = None,
        fails: bool = False,
    ) -> None:
        self._chunks = [chunk()] if chunks is None else chunks
        self._rewriter = rewriter
        self._fails = fails
        self.queries: list[str] = []

    def _run(self, tool_input: ToolInput) -> ToolOutput:
        self.queries.append(tool_input.query)
        if self._fails:
            # 재작성기도 안 돈다 — 1차 검색이 없으면 PRF 가 성립하지 않는다
            raise RuntimeError("저장소 응답 없음")
        if self._rewriter is not None:
            self._rewriter.note_retrieval(tool_input.query)
        return ToolOutput(success=True, data=list(self._chunks))


# 정해둔 판정을 순서대로 낸다. 다 쓰면 마지막 것을 되풀이한다
class StubEvaluator:
    def __init__(self, *verdicts: str) -> None:
        self._verdicts = list(verdicts)
        self.queries: list[str] = []

    def evaluate(self, query: str, chunks: list[RetrievedChunk]) -> EvaluatorVerdict:
        self.queries.append(query)
        index = min(len(self.queries) - 1, len(self._verdicts) - 1)
        return EvaluatorVerdict(verdict=self._verdicts[index], reasoning="테스트")


# 무슨 질의로 생성했는지 기록한다
class StubGenerate:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def __call__(self, query: str, chunks: list[RetrievedChunk]) -> str:
        self.queries.append(query)
        return f"답변({len(chunks)}건)"
