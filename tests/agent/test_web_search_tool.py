"""웹 검색 도구 — 계약만 있고 구현이 없다.

**「미구성」이 예외가 아니라 값으로 나오는지**가 이 파일의 전부다. 예외로 내면 Router 가
이 도구만 다르게 다뤄야 하고, 나중에 진짜 구현이 들어올 때 호출부가 함께 바뀐다.
"""

from __future__ import annotations

from rag_pipeline.agent.tools.web_search import NOT_CONFIGURED, WebSearchTool
from rag_pipeline.schemas.agent import ToolInput


# 부르면 실패를 값으로 돌려준다. 예외가 아니다
def test_returns_not_configured():
    out = WebSearchTool().run(ToolInput(query="2026년 최신 지침"))

    assert out.success is False
    assert out.error == NOT_CONFIGURED


# 사유가 예외 이름으로 시작하면 안 된다 — *아직 안 만들었다* 와 *부르다 죽었다* 가 갈려야 한다
def test_reason_is_not_an_exception_name():
    out = WebSearchTool().run(ToolInput(query="q"))

    assert "Error" not in out.error
    assert "NotImplemented" not in out.error
