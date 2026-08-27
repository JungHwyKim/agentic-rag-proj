"""웹 검색 도구 — **계약만 있고 구현이 없다.**

## 왜 비워 두나

`requirements.txt` 에 검색 라이브러리가 **0개**다. 실제로 붙이려면 외부 서비스 가입 · 키 ·
과금이 함께 오는데, 지금 필요한 것은 *Router 가 도구를 둘 이상 다룰 수 있는가* 하나다.
그 질문에는 이 계약만으로 답이 된다.

**빈 클래스 대신 「구성되지 않았다」를 값으로 돌려주는 것이 요점이다.** 그러면 Router 도
`AgentState` 도 이 도구를 다른 도구와 똑같이 다루고, 나중에 진짜 구현이 들어와도 **호출부가
한 줄도 안 바뀐다.** `NotImplementedError` 를 던지는 안은 기각했다 — `BaseTool.run()` 이
그것을 `success=False` 로 바꾸긴 하지만, 사유 문자열이 `NotImplementedError:` 로 시작해
*아직 안 만들었다* 와 *부르다 죽었다* 가 사람 눈에 안 갈린다.

## 다시 볼 조건

사용자가 코퍼스 밖 질문에 답하기를 원할 때. 그때 정할 것은 제공자(Tavily·Serper 등) 와
**기본 pytest 가 키 없이 통과하도록 만들 모킹 이음새**이고, 이음새는 이 저장소의 관례대로
모듈 레벨 `_make_client()` 다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.base import BaseTool
from rag_pipeline.schemas.agent import ToolInput, ToolOutput

__all__ = ["WebSearchTool"]

# 사람이 읽을 사유. **예외 이름으로 시작하지 않는다** (모듈 docstring 참고)
NOT_CONFIGURED = "웹 검색 도구가 아직 구성되지 않았습니다 (제공자·키 미설정)"


# 코퍼스 밖을 찾는 도구. 지금은 늘 실패를 값으로 돌려준다
class WebSearchTool(BaseTool):
    name = "web_search"
    description = "색인된 문서 밖의 정보를 웹에서 찾는다 (미구성)"

    # 구현이 없다는 것을 실패가 아니라 **구성 없음**으로 알린다
    def _run(self, tool_input: ToolInput) -> ToolOutput:
        return ToolOutput(success=False, error=NOT_CONFIGURED)
